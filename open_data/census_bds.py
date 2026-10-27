"""Census 2023 Business Dynamics Statistics (BDS) release: selected long-form panels.

Official public-use CSVs (no API token). This is descriptive outcome data,
not fiscal policy treatment or causal evidence. Preserve source-native values.
"""
from __future__ import annotations
import argparse,csv,datetime as dt,hashlib,io,json,math,time,urllib.request
from collections import Counter
from pathlib import Path

BASE='https://www2.census.gov/programs-surveys/bds/tables/time-series/2023/'
TABLES={
 'state':'bds2023_st.csv',
 'state_firm_age':'bds2023_st_fa.csv',
 'state_sector':'bds2023_st_sec.csv',
}
# Exact published measure names, plus source-specific aliases used in legacy bulk CSVs.
MEASURES={
 'emp','firm','estab','estabs','denom',
 'estabs_entry','estabs_entry_rate','estabs_exit','estabs_exit_rate',
 'job_creation','job_creation_births','job_creation_continuers',
 'job_creation_rate','job_creation_rate_births',
 'job_destruction','job_destruction_deaths','job_destruction_continuers',
 'job_destruction_rate','job_destruction_rate_deaths',
 'net_job_creation','net_job_creation_rate',
 'firmdeath_emp','firmdeath_estabs','firmdeath_firms',
}
COUNT_METRICS={m for m in MEASURES if not m.endswith('_rate') and '_rate_' not in m and 'rate' not in m}
MISSING={'','NA','N/A','NULL','NONE','-','--','*','D','N','S','X','(D)','(N)','(S)','(X)'}
FIELDS=['country_iso3','year','period','series','value','unit','provider',
        'source','source_url','frequency','disaggregation','data_status']

def retrieve(url,*,fetch=None):
    if fetch is not None:return fetch(url)
    if not url.startswith(BASE):raise ValueError('Unexpected BDS URL')
    error=None
    for attempt in range(4):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'EconomicPolicyResearch/1.0','Accept':'text/csv,text/plain,*/*'})
            with urllib.request.urlopen(req,timeout=65) as resp:
                if resp.geturl()!=url:raise ValueError('Unexpected source redirect')
                raw=resp.read(23_000_001)
                if len(raw)>23_000_000:raise ValueError('CSV larger than safety limit')
                if len(raw)<3_000:raise ValueError('Unexpectedly short BDS file')
                return raw
        except (OSError,TimeoutError) as exc:
            error=exc
            if attempt<3:time.sleep(3*(attempt+1))
    raise RuntimeError(f'Census file not retrievable: {error}')

def clean_column(name):
    return str(name or '').strip().lower().replace(' ','_').replace('-','_')

def convert_number(value):
    s=str(value or '').strip()
    if s.upper() in MISSING:return None
    try:v=float(s.replace(',',''))
    except ValueError:return None
    return v if math.isfinite(v) else None

def parse_table(raw,table,url):
    data=io.StringIO(raw.decode('utf-8-sig'))
    reader=csv.DictReader(data)
    if not reader.fieldnames:raise ValueError(f'{table}: empty header')
    originals=reader.fieldnames
    columns={clean_column(x):x for x in originals}
    if len(columns)!=len(originals):raise ValueError(f'{table}: duplicate normalized columns')
    year_col=next((x for x in ('year','yr') if x in columns),None)
    state_col=next((x for x in ('st','state','state_fips','fips') if x in columns),None)
    if not year_col or not state_col:raise ValueError(f'{table}: cannot identify year/state in headers {originals}')
    # Firm-age and sector fields may be named fage / naics / sector. Keep entire row's dimensions.
    eligible=[k for k in columns if k in MEASURES]
    if not {'job_creation','job_destruction'}.issubset(eligible):
        raise ValueError(f'{table}: missing required job-flow variables; actual headers {originals}')
    dimcols=[k for k in columns if k not in MEASURES and not k.endswith(('_f','_flag','_flags'))]
    seen=set();out=[];row_count=0;withheld=Counter();years=set();states=set()
    for row in reader:
        row_count+=1
        year_raw=(row[columns[year_col]] or '').strip()
        if not year_raw.isdecimal():raise ValueError(f'{table}: invalid year line {row_count+1}: {year_raw}')
        year=int(year_raw)
        if not 1978<=year<=2023:raise ValueError(f'{table}: outside 2023-release vintage: {year}')
        state=str(row[columns[state_col]] or '').strip()
        if not state:raise ValueError(f'{table}: missing state on line {row_count+1}')
        years.add(year);states.add(state)
        dims={k:str(row[columns[k]] or '').strip() for k in dimcols if k!=year_col}
        dims.update({'table':table,'census_bds_release':'2023','state_id_source_field':state_col})
        disagg=json.dumps(dims,sort_keys=True,ensure_ascii=False)
        for metric in eligible:
            for ext in ('_f','_flag','_flags'):
                flagcol=metric+ext
                if flagcol in columns:
                    flag=str(row[columns[flagcol]] or '').strip().upper()
                    if flag not in ('','0','NULL','NONE'):
                        withheld['flagged_'+flag]+=1
                        break
            else:
                value=convert_number(row[columns[metric]])
                if value is None:
                    withheld['blank_or_non_numeric']+=1
                    continue
                # Dimension + source measure, NOT aggregate across age/sector panels.
                identity=(year,table,metric,disagg)
                if identity in seen:raise ValueError(f'{table}: duplicate source dimension/metric at line {row_count+1}')
                seen.add(identity)
                out.append({'country_iso3':'USA','year':year,'period':str(year),
                    'series':f'census_bds_{table}_{metric}', 'value':format(value,'.15g'),
                    'unit':'source_count' if metric in COUNT_METRICS else 'source_defined_rate',
                    'provider':'US Census Bureau BDS','source':'2023 BDS public-use CSV',
                    'source_url':url,'frequency':'annual','disaggregation':disagg,
                    'data_status':'source_native_observed'})
    if row_count<2_000:raise ValueError(f'{table}: anomalously few rows: {row_count}')
    if len(states)<50 or len(years)<40 or min(years)!=1978 or max(years)!=2023:
        raise ValueError(f'{table}: insufficient state/year coverage: states={len(states)}, years={sorted(years)}')
    if len(out)<10_000:raise ValueError(f'{table}: unexpectedly few valid numeric measures: {len(out)}')
    return out,{'table':table,'raw_source_rows':row_count,'observations':len(out),
                'states':len(states),'first_year':min(years),'last_year':max(years),
                'metrics':eligible,'missing_or_flagged':dict(withheld),'columns':originals}

def run(out,*,fetch=None):
    out=Path(out);(out/'processed').mkdir(parents=True,exist_ok=True)
    (out/'raw').mkdir(parents=True,exist_ok=True)
    stamp=dt.datetime.now(dt.timezone.utc).isoformat()
    source_details=[];all_rows=[];keyset=set()
    for table,file in TABLES.items():
        url=BASE+file
        raw=retrieve(url,fetch=fetch)
        if not raw.startswith(b'year,') and not raw.lower().startswith((b'"year"',b'"yr"',b'yr,',b'\xef\xbb\xbfyear,',b'\xef\xbb\xbf"year"')):
            # Header ordering is unrestricted, but reject HTML error pages and synthetic CSV-less responses.
            if raw[:100].lstrip().lower().startswith((b'<!doctype',b'<html',b'<?xml')):
                raise ValueError(f'{url}: received markup instead of public-use CSV')
        (out/'raw'/file).write_bytes(raw)
        rows,details=parse_table(raw,table,url)
        details.update({'url':url,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)})
        for record in rows:
            key=(record['year'],record['series'],record['disaggregation'])
            if key in keyset:raise ValueError('Duplicate observations across source tables')
            keyset.add(key)
        source_details.append(details);all_rows.extend(rows)
        print(f'{table}: {len(rows):,} observations from {details["raw_source_rows"]:,} source rows',flush=True)
    if len(all_rows)<100_000:raise ValueError(f'BDS combined source unexpectedly small: {len(all_rows)}')
    panel=out/'processed'/'census_bds_panel.csv'
    with panel.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader();writer.writerows(all_rows)
    report={'dataset':'census_bds_2023_public_use','downloaded_at_utc':stamp,
            'observations':len(all_rows),'source_tables':source_details,
            'source_url':BASE,'release_vintage':2023,'years':'1978-2023',
            'errors':[],'conflicts':[],'research_ready':False,'causal_claims_supported':False,
            'notes':['Descriptive employment and firm dynamics only; not a fiscal shock.',
                     'Overlapping state, age, and industry stratifications must not be summed.',
                     'Suppressed/missing cells were skipped, never converted to numeric zeros.',
                     'Units preserved as published; rate definitions require codebook checks.',
                     'No country-year model harmonization or causal identification performed.',
                     'Original source columns and flags retained as source metadata.']}
    (out/'run_report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',default='data/census_bds')
    a=p.parse_args();r=run(a.out);print(json.dumps({'observations':r['observations'],
                                  'tables':[x['table'] for x in r['source_tables']]},indent=2))
