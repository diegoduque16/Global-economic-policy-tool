"""Annual Census state-government tax collections (historical fiscal-year panel).

Official public XLSX. Preserve source categories and original fiscal-year labels.
This is not a policy-shock, tax-rate, or state-and-local revenue series.
"""
import argparse
import collections
import csv
import datetime as dt
import hashlib
import io
import json
import math
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

if __package__:
    from open_data.us_state_qcew import STATES
else:
    from us_state_qcew import STATES

URL='https://www2.census.gov/programs-surveys/stc/datasets/historical/STC-Historical-DB.xlsx'
COLUMNS=('provider','country_iso3','year','period','series','value','unit',
         'frequency','disaggregation','obs_status','source_url','retrieved_utc')
NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
RELNS='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKGREL='http://schemas.openxmlformats.org/package/2006/relationships'
MISSING={'','X','N','NA','N/A','(NA)','(D)','(X)','(S)','(L)','-','--','...','.'}
EXAMPLES={'C105':'total state-government tax collections',
          'T01':'property tax','T09':'general sales tax',
          'T40':'individual income tax','T41':'corporation net income tax'}


def download(url=URL, attempts=3):
    if url!=URL: raise ValueError('Unapproved source URL')
    req=urllib.request.Request(url,headers={'User-Agent':'GlobalEconomicPolicyTool/1.0 academic-source-audit'})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                if urllib.parse.urlsplit(response.geturl()).hostname not in ('www2.census.gov','www.census.gov'):
                    raise ValueError('Unexpected download host')
                raw=response.read(12_000_001)
            if not raw or len(raw)>12_000_000 or not zipfile.is_zipfile(io.BytesIO(raw)):
                raise ValueError('Invalid or oversized XLSX')
            return raw
        except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError):
            if attempt==attempts-1: raise
            time.sleep(2**attempt)
    raise RuntimeError('Download failed')


def xlsx_rows(blob):
    """Stream a single-sheet XLSX without a spreadsheet-library dependency."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names=set(z.namelist())
        if not {'xl/workbook.xml','xl/_rels/workbook.xml.rels'}.issubset(names):
            raise ValueError('Missing Excel workbook manifest')
        wb=ET.fromstring(z.read('xl/workbook.xml'))
        sheets=wb.findall(f'{{{NS}}}sheets/{{{NS}}}sheet')
        if len(sheets)!=1: raise ValueError('Unexpected number of Census worksheets')
        rid=sheets[0].attrib.get(f'{{{RELNS}}}id')
        rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        relmap={x.attrib.get('Id'):x.attrib.get('Target','') for x in rels}
        name=relmap.get(rid,'')
        if name.startswith('/'): name=name.lstrip('/')
        elif not name.startswith('xl/'): name='xl/'+name
        if '..' in name.split('/') or name not in names: raise ValueError('Unsafe or missing worksheet member')
        if z.getinfo(name).file_size>100_000_000: raise ValueError('Oversized sheet')
        shared=[]
        if 'xl/sharedStrings.xml' in names:
            tree=ET.fromstring(z.read('xl/sharedStrings.xml'))
            for entry in tree.findall(f'{{{NS}}}si'):
                shared.append(''.join(t.text or '' for t in entry.iter(f'{{{NS}}}t')))
        with z.open(name) as stream:
            for _,row in ET.iterparse(stream,events=('end',)):
                if row.tag!=f'{{{NS}}}row': continue
                values={}
                for cell in row.findall(f'{{{NS}}}c'):
                    col=re.match('[A-Z]+',cell.attrib.get('r',''))
                    if col is None: continue
                    val=cell.find(f'{{{NS}}}v')
                    raw=val.text if val is not None else None
                    if cell.attrib.get('t')=='s' and raw is not None:
                        index=int(raw)
                        if index<0 or index>=len(shared): raise ValueError('Bad shared string index')
                        raw=shared[index]
                    elif cell.attrib.get('t')=='inlineStr':
                        raw=''.join(t.text or '' for t in cell.iter(f'{{{NS}}}t'))
                    if raw is not None: values[col.group(0)]=str(raw).strip()
                yield values
                row.clear()


def parse_number(raw):
    s=str(raw or '').strip().replace(',','')
    if s.upper() in MISSING: return None
    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)',s):
        raise ValueError('Unknown tax receipt cell '+repr(s[:50]))
    x=float(s)
    if not math.isfinite(x): raise ValueError('Nonfinite tax receipt')
    return x


def parse(raw, states=None, start=1951, end=2025, now=None):
    states=set(states or STATES)
    if not states.issubset(STATES): raise ValueError('Unknown state')
    rows=iter(xlsx_rows(raw))
    headers=next(rows,None); titles=next(rows,None)
    if not headers or not titles or tuple(headers.get(c) for c in ('A','B','C','D'))!=('Year','State','Name','FY Ending Date'):
        raise ValueError('Census column schema changed')
    codes={col:code for col,code in headers.items() if re.fullmatch(r'(?:T\d{2}|C\d{3})',code)}
    if not set(EXAMPLES).issubset(set(codes.values())): raise ValueError('Missing essential Census tax codes')
    if len(codes)<20 or len(codes)!=len(set(codes.values())): raise ValueError('Duplicate or missing tax columns')
    now=now or dt.datetime.now(dt.timezone.utc).isoformat()
    data=[];seen=set();years=set();present=collections.defaultdict(set);missing=collections.Counter();zero_count=0
    skipped_national=0;future_rows=0;year_row_count=collections.Counter();has_dc=False
    for row in rows:
        yr=(row.get('A') or '').strip()
        if not re.fullmatch(r'\d{4}(?:\.0)?',yr):continue
        year=int(float(yr))
        if year>end:
            future_rows+=1
            continue
        if year<start: continue
        name=(row.get('C') or '').strip()
        abbr=name.split()[0].upper() if name else ''
        if abbr not in STATES:
            skipped_national+=1
            continue
        if abbr not in states: continue
        fy_end=row.get('D','')
        years.add(year);year_row_count[year]+=1
        if abbr=='DC':has_dc=True
        for col,code in codes.items():
            cell=row.get(col,'')
            numeric=parse_number(cell)
            if numeric is None:
                missing[str(cell or '').upper()]+=1
                continue
            key=(year,abbr,code)
            if key in seen: raise ValueError('Duplicate state-year-tax code '+repr(key))
            seen.add(key);present[year].add(abbr)
            if numeric==0:zero_count+=1
            disagg={'state_abbr':abbr,'state_fips':STATES[abbr],
                'census_state_code':(row.get('B') or '').strip(),
                'original_state_name':name,'fiscal_year_end_raw':fy_end,
                'tax_code':code,'tax_label':titles.get(col,'').strip(),
                'code_kind':'Census composite' if code.startswith('C') else 'Census category',
                'classification_era':'pre_2005' if year<2005 else '2005_onward',
                'state_government_only':True,'not_legislative_tax_change':True}
            data.append({'provider':'US Census Annual Survey of State Government Tax Collections',
                'country_iso3':'USA','year':year,'period':str(year),
                'series':'CENSUS.STC.'+code,'value':numeric,'unit':'thousands_of_us_dollars_current',
                'frequency':'annual','disaggregation':json.dumps(disagg,sort_keys=True),
                'obs_status':'observed_source_figure','source_url':URL,'retrieved_utc':now})
    if not data:raise ValueError('No state tax data parsed')
    return data,{'year_min':min(years),'year_max':max(years),'year_count':len(years),
        'tax_codes':sorted(codes.values()),'series_count':len({r['series'] for r in data}),
        'state_count':len({json.loads(r['disaggregation'])['state_abbr'] for r in data}),
        'has_dc':has_dc,'states_1951':len(present.get(1951,set())),
        'states_2025':len(present.get(2025,set())),'rows_by_year':dict(year_row_count),
        'source_missing_cells':dict(missing),'explicit_zero_cells':zero_count,
        'ignored_post_2025_rows':future_rows,'skipped_nonstate_rows':skipped_national,
        'observations':len(data)}


def collect(mode='pilot', out='data/us_state_tax', get=download):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    report={'status':'NOT_VALIDATED','mode':mode,'errors':[],'conflicts':[],
        'research_ready':False,'is_causal_policy_shock':False,
        'source_url':URL,'retrieved_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
        'limitations':['State government collections only; not state+local total.',
         'Taxes collected are not tax rates or enacted tax-law changes.',
         'Fiscal years differ among states; source FY ending date is preserved.',
         '2005 classification boundary is retained; do not blindly compare all subcategories across vintages.',
         'Nominal $ thousands; no inflation/price normalization.',
         'Tax composite codes overlap with components and must not be summed together.',
         '2026 labels are excluded pending confirmed full-year data.',
         'Missing and X markers are not zero-filled.']}
    try:
        if mode not in ('pilot','full'):raise ValueError('Invalid mode')
        blob=get();allowed=('CA','CT','FL','NY','TX') if mode=='pilot' else None
        start=2023 if mode=='pilot' else 1951
        data,stats=parse(blob,allowed,start,2025,report['retrieved_utc'])
        if mode=='pilot':
            if stats['year_min']!=2023 or stats['year_max']!=2025 or stats['state_count']!=5:
                raise ValueError('Pilot missing requested states or years')
            if stats['observations']<100:raise ValueError('Pilot too small')
        else:
            if stats['year_min']!=1951 or stats['year_max']!=2025 or stats['year_count']<70:
                raise ValueError('Historical coverage incomplete')
            if stats['states_1951']<45 or stats['states_2025']<49 or stats['series_count']<20:
                raise ValueError('Historical state/code coverage insufficient')
            if stats['observations']<70_000:raise ValueError('Unexpectedly sparse historical panel')
        path=out/'processed'/'us_state_tax_panel.csv';path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=COLUMNS);writer.writeheader();writer.writerows(data)
        report.update(stats)
        report.update({'status':'VALIDATED_HISTORIC_STATE_TAX_COLLECTIONS_DESCRIPTIVE',
            'source_sha256':hashlib.sha256(blob).hexdigest(),
            'panel_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    except Exception as exc:
        report['status']='VALIDATION_FAILED';report['errors']=[{'type':type(exc).__name__,'message':str(exc)}]
    (out/'run_report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2)[:18000])
    return 1 if report['errors'] else 0


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['pilot','full'],default='pilot')
    p.add_argument('--out',default='data/us_state_tax')
    a=p.parse_args();raise SystemExit(collect(a.mode,a.out))
