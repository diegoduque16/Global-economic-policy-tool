"""BEA state annual GDP and income from public, official regional ZIP releases.

Source-separated outcomes only; not identified state fiscal treatments or causal effects.
Source archive checksums identify data revisions. CSVs remain in BEA native units.
No BEA API key, authentication, non-official mirror or undisclosed imputation.
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
import zipfile

# The BLS QCEW state panel already provides the exact official USPS -> FIPS map.
if __package__:
    from open_data.us_state_qcew import STATES
else:
    from us_state_qcew import STATES

BASE = 'https://apps.bea.gov/regional/zip'
SOURCES = {'SAGDP': 'SAGDP1', 'SAINC': 'SAINC1'}
LINE_CODES = {
    ('SAGDP1', '1'): ('BEA.SAGDP1.REAL_GDP', 'annual_real_gdp_chained_dollars_source_unit'),
    ('SAGDP1', '3'): ('BEA.SAGDP1.NOMINAL_GDP', 'annual_gdp_current_dollars_source_unit'),
    ('SAINC1', '1'): ('BEA.SAINC1.PERSONAL_INCOME', 'personal_income_current_dollars_source_unit'),
    ('SAINC1', '2'): ('BEA.SAINC1.POPULATION', 'resident_population_persons'),
    ('SAINC1', '3'): ('BEA.SAINC1.PER_CAPITA_INCOME', 'personal_income_per_capita_current_dollars'),
}
COLUMNS = ('provider','country_iso3','year','period','series','value','unit',
           'frequency','disaggregation','obs_status','source_url','retrieved_utc')
STATE_BY_FIPS = {v+'000': k for k,v in STATES.items()}
assert len(STATE_BY_FIPS) == 51


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def download_archive(source: str, max_bytes: int=350_000_000, attempts: int=3):
    if source not in SOURCES:
        raise ValueError('Unknown official BEA archive')
    url = f'{BASE}/{source}.zip'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'GlobalEconomicPolicyTool/1.0 (public economic research)',
        'Accept': 'application/zip,application/octet-stream,*/*',
        'Accept-Encoding': 'identity'})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                host = urllib.parse.urlsplit(response.geturl()).hostname
                if host not in ('apps.bea.gov', 'www.bea.gov', 'bea.gov'):
                    raise ValueError('Unexpected BEA redirect origin')
                raw = response.read(max_bytes+1)
                if not raw or len(raw)>max_bytes:
                    raise ValueError('BEA ZIP empty or exceeds configured download limit')
                if not zipfile.is_zipfile(io.BytesIO(raw)):
                    raise ValueError('BEA archive is not a valid ZIP')
                return raw, url
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt==attempts-1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt==attempts-1:
                raise
        time.sleep(min(2**attempt, 8))
    raise RuntimeError('BEA archive download retries exhausted')


def choose_member(archive: bytes, table: str):
    """Select precisely one all-areas summary table; no guessing between vintages."""
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        choices = [info for info in z.infolist()
                   if not info.is_dir() and re.search(rf'^{re.escape(table)}__ALL_AREAS_[0-9]{{4}}_[0-9]{{4}}\.csv$',
                                     Path(info.filename).name, re.I)]
        if len(choices)!=1:
            raise ValueError(f'Expected one {table} ALL_AREAS file, found {len(choices)}: '
                             + repr([i.filename for i in choices[:6]]))
        info=choices[0]
        if info.file_size>60_000_000:
            raise ValueError('Selected BEA summary CSV exceeds decompression limit')
        data=z.read(info)
        if len(data)!=info.file_size or not data:
            raise ValueError('Selected BEA CSV empty or invalid')
        return data, info.filename


def number(raw):
    raw=str(raw or '').strip().replace(',','')
    if raw in ('','(NA)','(D)','(NM)','(L)','(T)','(S)','(X)','--','...','N/A'):
        return None
    try:
        n=float(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError('Unexpected BEA numerical cell: '+repr(raw[:30])) from exc
    if not math.isfinite(n):
        raise ValueError('Nonfinite BEA cell')
    return n


def parse_table(csv_bytes: bytes, table: str, source_url: str, member: str,
                when: str, start: int, end: int, states: set[str]):
    if table not in SOURCES.values():
        raise ValueError('Unknown BEA table')
    reader=csv.DictReader(io.StringIO(csv_bytes.decode('utf-8-sig', errors='strict')))
    fields=set(reader.fieldnames or [])
    required={'GeoFIPS','GeoName','TableName','LineCode','Description','Unit'}
    if not required.issubset(fields):
        raise ValueError('BEA all-areas CSV schema changed: '+repr(sorted(required-fields)))
    years=[str(y) for y in range(start,end+1) if str(y) in fields]
    if not years:
        raise ValueError(f'No usable requested years found in {table}')
    included=[];seen=set();missing=0;geos=set();series=set()
    for row in reader:
        if not row or not (row.get('GeoFIPS') or '').strip().strip('"').isdigit():
            continue # footer, source notes, region label, blank
        fips=str(row['GeoFIPS']).strip().strip('"').zfill(5)
        state=STATE_BY_FIPS.get(fips)
        if state is None or state not in states:
            continue # exclude national totals, BEA regions, counties and territories
        if str(row['TableName']).strip()!=table:
            raise ValueError('BEA table label disagrees with chosen file')
        code=str(row['LineCode']).strip().lstrip('0') or '0'
        metric=LINE_CODES.get((table,code))
        if not metric:
            continue
        series_name,unit_role=metric
        native_unit=(row.get('Unit') or '').strip()
        if not native_unit:
            raise ValueError('Missing original BEA unit: '+str(metric))
        description=(row.get('Description') or '').strip()
        if not description:
            raise ValueError('Missing BEA line description')
        for year in years:
            val=number(row.get(year))
            if val is None:
                missing+=1
                continue # missing is never imputed as zero
            if val<=0: # GDP, income, population, per-capita annual level should be positive
                raise ValueError(f'Non-positive BEA state level: {state} {year} {series_name}')
            key=(state,int(year),series_name)
            if key in seen:
                raise ValueError('Duplicate state-year-series in BEA source '+repr(key))
            seen.add(key);geos.add(state);series.add(series_name)
            dim={'state_abbr':state,'state_fips':STATES[state],
                 'state_bea_geo_fips':fips,'state_name':str(row.get('GeoName','')).strip(),
                 'bea_table':table,'bea_line_code':code,'bea_description':description,
                 'original_source_unit':native_unit,'unit_role':unit_role,
                 'source_zip_member':member}
            included.append({'provider':'BEA Regional Economic Accounts', 'country_iso3':'USA',
                'year':int(year),'period':year,'series':series_name,'value':val,
                'unit':native_unit,'frequency':'annual','disaggregation':json.dumps(dim,sort_keys=True),
                'obs_status':'published_or_revised_source_vintage',
                'source_url':source_url,'retrieved_utc':when})
    return included, {'table':table,'zip_member':member,
        'state_count':len(geos),'series':sorted(series), 'missing_or_suppressed_cells':missing,
        'observations':len(included),'year_columns_present':len(years)}


def collect(out='data/us_state_bea',mode='pilot',get=download_archive):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    when=dt.datetime.now(dt.timezone.utc).isoformat()
    report={'source':'US BEA official state GDP and personal-income public archives',
        'status':'NOT_VALIDATED','mode':mode,'errors':[],'conflicts':[],
        'research_ready':False,'is_causal_policy_shock':False,'retrieved_utc':when,
        'limitations':['Real state GDP uses source chain-dollar base year and must not be summed by industry.',
          'State income, GDP and resident population differ in economic concepts.',
          'BEA revises history; ZIP digests record vintages and do not guarantee reproducibility at a mutable URL.',
          'Source missing and suppressed cells are omitted, never zero-filled.',
          'These are outcomes; they do not identify causal state tax or spending policy shocks.']}
    try:
        if mode not in ('pilot','full'):
            raise ValueError('Bad mode')
        states=set(('CA','CT','FL','NY','TX') if mode=='pilot' else STATES)
        start,end=(2021,2025) if mode=='pilot' else (1960,2025)
        rows=[];manifests=[];unique=set()
        for source,table in SOURCES.items():
            raw,url=get(source)
            extracted,member=choose_member(raw,table)
            parsed,stats=parse_table(extracted,table,url,member,when,start,end,states)
            if stats['state_count']!=len(states):
                raise ValueError(f'{source}: missing state coverage ({stats["state_count"]} vs {len(states)})')
            expected_measures=2 if source=='SAGDP' else 3
            if len(stats['series'])!=expected_measures:
                raise ValueError(f'{source}: missing required measures')
            if source=='SAGDP' and stats['observations']<len(states)*3:
                raise ValueError('GDP year coverage unexpectedly sparse')
            if source=='SAINC' and stats['observations']<len(states)*6:
                raise ValueError('Income year coverage unexpectedly sparse')
            for record in parsed:
                dim=json.loads(record['disaggregation'])
                key=(dim['state_abbr'],record['year'],record['series'])
                if key in unique:
                    raise ValueError('Overlapping source keys')
                unique.add(key)
            rows.extend(parsed)
            manifests.append({'source_url':url,'archive':source,'zip_sha256':sha256(raw),
                              'zip_bytes':len(raw),'selected_csv_sha256':sha256(extracted),
                              'selected_csv_bytes':len(extracted),**stats})
        source_series={r['series'] for r in rows}
        state_count=len({json.loads(r['disaggregation'])['state_abbr'] for r in rows})
        if len(source_series)!=5 or state_count!=len(states):
            raise ValueError('Incomplete BEA state panel')
        if mode=='pilot':
            if len(rows)<70:
                raise ValueError('BEA pilot unexpectedly sparse')
        else:
            if len(rows)<5000:
                raise ValueError('BEA full history unexpectedly sparse')
        rows.sort(key=lambda r:(json.loads(r['disaggregation'])['state_abbr'],r['year'],r['series']))
        p=out/'processed'/'us_state_bea_panel.csv';p.parent.mkdir(parents=True,exist_ok=True)
        with p.open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=COLUMNS,lineterminator=chr(10));w.writeheader();w.writerows(rows)
        report.update({'status':'VALIDATED_DESCRIPTIVE_STATE_MACRO_DATA_NOT_CAUSAL_SHOCKS',
            'observations':len(rows),'state_count':state_count,'series_count':len(source_series),
            'earliest_year':min(r['year'] for r in rows), 'latest_year':max(r['year'] for r in rows),
            'data_year_bounds_requested':[start,end],'source_requests':2,
            'panel_sha256':sha256(p.read_bytes()),'sources':manifests})
    except Exception as exc:
        report['status']='VALIDATION_FAILED'
        report['errors'].append({'type':type(exc).__name__, 'message':str(exc)[:400]})
    (out/'run_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({k:report.get(k) for k in ('status','mode','observations','state_count','series_count',
                    'source_requests','errors')},indent=2))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('pilot','full'),default='pilot')
    p.add_argument('--out',default='data/us_state_bea')
    a=p.parse_args()
    report=collect(a.out,a.mode)
    if report['errors']:
        raise SystemExit('BEA state GDP and personal-income source failed validation')
