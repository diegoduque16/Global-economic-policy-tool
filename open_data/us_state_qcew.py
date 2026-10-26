"""Official BLS QCEW state annual area-slice panel; outcomes, not policy shocks.

Retains all-ownership statewide industry total and private-ownership NAICS sectors.
Never sums overlapping industry groups or silently imputes suppressed measures.
"""
from __future__ import annotations
import argparse
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

BASE='https://data.bls.gov/cew/data/api'
STATES={
'AL':'01','AK':'02','AZ':'04','AR':'05','CA':'06','CO':'08','CT':'09','DE':'10','DC':'11','FL':'12','GA':'13',
'HI':'15','ID':'16','IL':'17','IN':'18','IA':'19','KS':'20','KY':'21','LA':'22','ME':'23','MD':'24','MA':'25',
'MI':'26','MN':'27','MS':'28','MO':'29','MT':'30','NE':'31','NV':'32','NH':'33','NJ':'34','NM':'35','NY':'36',
'NC':'37','ND':'38','OH':'39','OK':'40','OR':'41','PA':'42','RI':'44','SC':'45','SD':'46','TN':'47','TX':'48',
'UT':'49','VT':'50','VA':'51','WA':'53','WV':'54','WI':'55','WY':'56'}
assert len(STATES)==51
PILOT_STATES=('CA','CT','FL','NY','TX')
SECTORS=frozenset(('11','21','22','23','31-33','42','44-45','48-49','51','52','53','54','55','56','61','62','71','72','81','99'))
MEASURES={'annual_avg_emplvl':'covered_jobs_annual_monthly_average',
 'annual_avg_estabs':'establishments_annual_average',
 'total_annual_wages':'nominal_usd_annual_total',
 'annual_avg_wkly_wage':'nominal_usd_per_week',
 'avg_annual_pay':'nominal_usd_per_year'}
COLUMNS=('provider','country_iso3','year','period','series','value','unit','frequency','disaggregation','obs_status','source_url','retrieved_utc')
REQUIRED={'area_fips','year','qtr','own_code','industry_code','disclosure_code','size_code',*MEASURES}

def source_url(year,abbr):
    if abbr not in STATES or not 2021<=int(year)<=2025:raise ValueError('Unsupported BLS area slice')
    return f'{BASE}/{int(year)}/a/area/{STATES[abbr]}000.csv'

def fetch(url,attempts=4,max_bytes=15_000_000):
    if not re.fullmatch(r'https://data\.bls\.gov/cew/data/api/20\d\d/a/area/\d{5}\.csv',url):
        raise ValueError('Unexpected BLS source URL')
    request=urllib.request.Request(url,headers={
        'User-Agent':'GlobalEconomicPolicyTool/1.0 (public research)',
        'Accept':'text/csv,text/plain,*/*','Accept-Encoding':'identity'})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request,timeout=55) as response:
                host=(urllib.parse.urlsplit(response.geturl()).hostname or '').lower()
                if host not in ('data.bls.gov','www.bls.gov'):raise ValueError('Unexpected source redirect')
                raw=response.read(max_bytes+1)
                if not raw or len(raw)>max_bytes:raise ValueError('Empty/oversized BLS CSV')
                if raw.lstrip()[:30].lower().startswith((b'<html',b'<!doctype')):raise ValueError('HTML, not source CSV')
                if b'annual_avg_emplvl' not in raw[:3500].lower():raise ValueError('Missing source header')
                return raw
        except urllib.error.HTTPError as err:
            if err.code not in (429,500,502,503,504) or attempt+1==attempts:raise
        except (urllib.error.URLError,TimeoutError):
            if attempt+1==attempts:raise
        time.sleep(min(2**attempt,10))
    raise RuntimeError('BLS fetch exhausted')

def number(raw):
    value=str(raw if raw is not None else '').strip().replace(',','')
    if value.upper() in ('','N','-','NA','(D)','D','S','—'):return None
    try: v=float(value)
    except (ValueError,TypeError) as e:raise ValueError('Invalid BLS numeric measurement') from e
    if not math.isfinite(v) or v<0:raise ValueError('Nonfinite/negative BLS numeric measurement')
    return v

def parse_slice(raw,year,state,when):
    url=source_url(year,state); fips=STATES[state]+'000';year=int(year)
    data=csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    missing=REQUIRED-set(data.fieldnames or [])
    if missing:raise ValueError('QCEW annual-area schema mismatch: '+repr(sorted(missing)))
    rows=[];seen=set();baseline=False;suppressed=0;private_sectors=set()
    for line in data:
        if line['area_fips'].strip()!=fips or int(line['year'])!=year or line['qtr'].strip().upper()!='A':
            raise ValueError('Wrong source geography/year/period')
        if (line['size_code'] or '').strip() not in ('','0'):continue
        own=str(line['own_code']).strip();industry=(line['industry_code'] or '').strip()
        keep=(own=='0' and industry=='10') or (own=='5' and industry in SECTORS)
        if not keep:continue
        key=(own,industry)
        if key in seen:raise ValueError('Duplicate ownership-industry source record')
        seen.add(key)
        disc=(line.get('disclosure_code') or '').strip().upper()
        if disc not in ('','N'):raise ValueError('Unexpected QCEW disclosure status')
        if disc=='N':suppressed+=len(MEASURES);continue
        dim={'state_abbr':state,'state_fips':STATES[state],
          'state_qcew_area_fips':fips,'industry_code':industry,
          'ownership_code':own,'ownership_scope':'all_ownerships' if own=='0' else 'private_only',
          'industry_scope':'all_industries' if own=='0' else 'naics_sector',
          'source_table':'QCEW_annual_state_area_slice'}
        found=0
        for m,unit in MEASURES.items():
            val=number(line.get(m))
            if val is None:suppressed+=1;continue
            found+=1
            rows.append({'provider':'BLS QCEW','country_iso3':'USA','year':year,'period':str(year),
              'series':'BLS_QCEW.'+m,'value':val,'unit':unit,'frequency':'annual',
              'disaggregation':json.dumps(dim,sort_keys=True),'obs_status':'published',
              'source_url':url,'retrieved_utc':when})
            if own=='0' and industry=='10' and m=='annual_avg_emplvl' and val>0:baseline=True
        if own=='5' and found:private_sectors.add(industry)
    if not baseline:raise ValueError('Missing positive statewide all-ownership employment baseline')
    if len(private_sectors)<3:raise ValueError('State industry coverage unexpectedly sparse; refusing aggregate-only panel')
    return rows,{'included_source_categories':len(seen),'private_sectors':len(private_sectors),
                 'missing_or_suppressed_values':suppressed}

def collect(out='data/us_state_qcew',mode='pilot',get=fetch,throttle=.1):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    report={'source':'BLS QCEW annual area slices with all-owner total and private NAICS sectors',
      'mode':mode,'status':'NOT_VALIDATED','errors':[],'conflicts':[],
      'research_ready':False,'causal_inference_ready':False,
      'limitations':['All-ownership total and private industry sectors overlap; never sum these.',
        'State industry data here is private-sector ownership only, not all ownerships.',
        'Suppressed/missing values excluded rather than zero-imputed.',
        'Nominal wages and NAICS industry revisions require handling for causal designs.',
        'These are employment outcomes, not exogenous fiscal-policy shocks.']}
    requests=[];rows=[];seen=set();when=dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        if mode not in ('pilot','full'):raise ValueError('Invalid mode')
        states=PILOT_STATES if mode=='pilot' else tuple(STATES)
        years=(2024,) if mode=='pilot' else tuple(range(2021,2026))
        for year in years:
            for state in states:
                url=source_url(year,state)
                raw=get(url)
                parsed,stats=parse_slice(raw,year,state,when)
                for rec in parsed:
                    d=json.loads(rec['disaggregation'])
                    unique=(year,state,d['ownership_code'],d['industry_code'],rec['series'])
                    if unique in seen:raise ValueError('Duplicate annual ownership/industry/measure key')
                    seen.add(unique)
                rows.extend(parsed)
                requests.append({'state':state,'year':year,'url':url,'bytes':len(raw),
                  'sha256':hashlib.sha256(raw).hexdigest(),'rows':len(parsed),**stats})
                if get is fetch and throttle:time.sleep(throttle)
        expected={(s,y) for s in states for y in years}
        pairs={(json.loads(x['disaggregation'])['state_abbr'],x['year']) for x in rows}
        if pairs!=expected:raise ValueError('Incomplete U.S. state-year coverage')
        if len(rows)<(75 if mode=='pilot' else 5000):raise ValueError('State-industry panel unexpectedly sparse')
        dest=out/'processed'/'us_state_qcew_panel.csv';dest.parent.mkdir(parents=True,exist_ok=True)
        with dest.open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=COLUMNS,lineterminator=chr(10));w.writeheader();w.writerows(rows)
        report.update({'status':'VALIDATED_DESCRIPTIVE_STATE_LABOR_DATA',
          'observations':len(rows),'state_count':len(states),'state_year_count':len(pairs),
          'years':list(years),'source_requests':len(requests),'series_count':len(MEASURES),
          'private_sector_details':True,
          'panel_sha256':hashlib.sha256(dest.read_bytes()).hexdigest(),
          'source_manifest':requests})
    except Exception as exc:
        report['status']='SOURCE_FAILED_VALIDATION'
        report['errors'].append({'type':type(exc).__name__,'message':str(exc)[:350],
           'successful_source_requests':len(requests)})
    (out/'run_report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report.get(k) for k in ('status','observations','state_count',
      'state_year_count','source_requests','errors')},indent=2))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('pilot','full'),default='pilot')
    p.add_argument('--out',default='data/us_state_qcew')
    a=p.parse_args();result=collect(a.out,a.mode)
    if result['errors']:raise SystemExit('QCEW collector failed validation')
