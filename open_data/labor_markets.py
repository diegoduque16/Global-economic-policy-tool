"""Source-separated BLS, Eurostat and ILOSTAT labor data downloader.

Python stdlib only. Observation provenance retained and source dimensions never
collapsed into spurious country-year duplicates. API behavior is *unverified live*
until a GitHub Actions smoke test passes.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import time
import urllib.parse
import urllib.request

BLS_API = 'https://api.bls.gov/publicAPI/v1/timeseries/data/'
ILOSTAT_API = 'https://rplumber.ilo.org/data/indicator/'
EUROSTAT_API = 'https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/lfsi_emp_a'
# BLS publishes the units in each series description; preserve here with no conversion.
BLS_SERIES = {
    'CES0000000001': ('Total nonfarm payroll jobs', 'thousands of jobs'),
    'CES0500000001': ('Total private payroll jobs', 'thousands of jobs'),
    'LNS14000000': ('Unemployment rate', 'percent'),
    'CES0500000003': ('Average hourly earnings, total private', 'USD per hour'),
    'CES0500000002': ('Average weekly hours, total private', 'hours per week'),
}
ILOSTAT_SERIES = {
    'UNE_2UNE_SEX_AGE_NB_A': 'Unemployment, number (modelled or source-defined)',
    'EMP_2EMP_SEX_AGE_NB_A': 'Employment, number (modelled or source-defined)',
}
# Same European countries already targeted in the repository's first Eurostat connector.
EU_TO_ISO3 = dict(zip(
    'AT BE BG HR CY CZ DK EE FI FR DE EL HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE UK NO CH IS'.split(),
    'AUT BEL BGR HRV CYP CZE DNK EST FIN FRA DEU GRC HUN IRL ITA LVA LTU LUX MLT NLD POL PRT ROU SVK SVN ESP SWE GBR NOR CHE ISL'.split()))
FIELDS = ['provider','country_iso3','period','year','series','value','unit','frequency',
          'disaggregation','status_flag','source_url','retrieved_utc']


def fetch_bytes(url, *, method='GET', body=None, retries=3, timeout=70):
    headers={'User-Agent':'GlobalEconomicPolicyTool/1.1 public research',
             'Accept':'application/json,text/csv,*/*'}
    if method == 'POST': headers['Content-Type']='application/json'
    for attempt in range(retries):
        try:
            req=urllib.request.Request(url,data=body,method=method,headers=headers)
            with urllib.request.urlopen(req,timeout=timeout) as response:
                data=response.read()
                if not data: raise ValueError('Empty response')
                return data
        except Exception:
            if attempt+1==retries:raise
            time.sleep(2**attempt)
    raise RuntimeError('Could not fetch')


def bls_url():
    return BLS_API


def bls_body(series,start,end):
    return json.dumps({'seriesid':list(series),'startyear':str(start),'endyear':str(end)}).encode()


def parse_bls(raw, *, allowed, start, end, url, stamp):
    obj=json.loads(raw)
    if obj.get('status')!='REQUEST_SUCCEEDED':
        raise ValueError(f"BLS rejected request: {obj.get('status')}, {obj.get('message')}")
    arrays=obj.get('Results',{}).get('series')
    if not isinstance(arrays,list):raise ValueError('Missing BLS Results.series')
    result=[]
    for series in arrays:
        sid=series.get('seriesID')
        if sid not in allowed:continue
        for point in series.get('data',[]):
            year=int(point.get('year','0'));month=point.get('period','')
            # Skip annual averages (M13) and other non-monthly periods.
            if not start<=year<=end or not (len(month)==3 and month[0]=='M' and month[1:].isdigit() and 1<=int(month[1:])<=12):continue
            # The BLS API may return '-' or another missing-value marker.
            # Omit missing observations; never treat them as zero.
            try:
                value=float(str(point.get('value','')).strip().replace(',',''))
            except (ValueError, TypeError):
                continue
            if not math.isfinite(value):continue
            result.append({'provider':'US BLS public API v1','country_iso3':'USA','period':f'{year}-{int(month[1:]):02d}',
                'year':year,'series':sid,'value':value,'unit':BLS_SERIES[sid][1],
                'frequency':'monthly','disaggregation':'{}',
                'status_flag':json.dumps(point.get('footnotes',[]),ensure_ascii=False),
                'source_url':url,'retrieved_utc':stamp})
    return result


def ilostat_url(series, *, start, end, country=None):
    query={'id':series,'timefrom':str(start),'timeto':str(end),'format':'.csv','type':'code'}
    if country:query['ref_area']=country
    return ILOSTAT_API+'?'+urllib.parse.urlencode(query)


def parse_ilostat(raw, *, indicator, start, end, url, stamp):
    reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    fields=set(reader.fieldnames or [])
    needed={'ref_area','time','obs_value'}
    if not needed.issubset(fields):
        raise ValueError(f'ILOSTAT CSV unexpected columns {sorted(fields)}, need {sorted(needed)}')
    rows=[]
    # All classification dimensions and source tags are retained, not collapsed
    # into a purported country-year series. Many records are not totals.
    omit={'ref_area','time','obs_value','indicator','obs_status','note_obs','note_classif','note_indicator'}
    for point in reader:
        geo=(point.get('ref_area') or '').strip()
        tm=(point.get('time') or '').strip()
        if not (len(geo)==3 and geo.isalpha() and len(tm)==4 and tm.isdigit()):continue
        year=int(tm)
        if not start<=year<=end:continue
        try:value=float(str(point.get('obs_value') or '').strip().replace(',',''))
        except ValueError:continue
        if not math.isfinite(value):continue
        disagg={k:v for k,v in point.items() if k not in omit and v not in ('',None)}
        rows.append({'provider':'ILOSTAT','country_iso3':geo.upper(),'period':tm,'year':year,
             'series':indicator,'value':value,'unit':'source_defined_confirm_ILOSTAT_metadata',
             'frequency':'annual','disaggregation':json.dumps(disagg,sort_keys=True),
             'status_flag':json.dumps({k:point.get(k) for k in ('obs_status','note_obs') if point.get(k)},sort_keys=True),
             'source_url':url,'retrieved_utc':stamp})
    return rows


def eurostat_url(start):
    params={'freq':'A','indic_em':'EMP_LFS','sex':'T','age':'Y20-64','unit':'PC_POP',
            'sinceTimePeriod':str(max(start,2003)),'geoLevel':'country','lang':'EN','format':'JSON'}
    return EUROSTAT_API+'?'+urllib.parse.urlencode(params)


def parse_eurostat(raw, *, start, end, url, stamp):
    obj=json.loads(raw)
    dims=obj.get('id');sizes=obj.get('size');data=obj.get('value')
    if not isinstance(dims,list) or not isinstance(sizes,list) or len(dims)!=len(sizes) or 'geo' not in dims or 'time' not in dims or not isinstance(data,(list,dict)):
        raise ValueError('Unexpected Eurostat JSON-stat layout')
    if math.prod(sizes)>3_000_000:raise ValueError('Too many Eurostat values for selected slice')
    decoded=[]
    for dimension,size in zip(dims,sizes):
        index=obj.get('dimension',{}).get(dimension,{}).get('category',{}).get('index')
        if isinstance(index,dict):
            values=[None]*size
            for code,i in index.items():
                if not isinstance(i,int) or not 0<=i<size or values[i] is not None:raise ValueError('Bad Eurostat index')
                values[i]=code
        elif isinstance(index,list) and len(index)==size: values=index
        else:raise ValueError(f'Eurostat missing dimension index {dimension}')
        if any(v is None for v in values):raise ValueError('Incomplete Eurostat index')
        decoded.append(values)
    strides=[math.prod(sizes[i+1:]) for i in range(len(sizes))]
    statuses=obj.get('status',{})
    result=[]
    iterator=data.items() if isinstance(data,dict) else enumerate(data)
    for key,value in iterator:
        if value is None:continue
        ix=int(key)
        if not 0<=ix<math.prod(sizes):raise ValueError('Eurostat index out of bounds')
        detail={name:codes[(ix//stride)%size] for name,codes,stride,size in zip(dims,decoded,strides,sizes)}
        geo=detail['geo'];yr=detail['time']
        if geo not in EU_TO_ISO3 or not str(yr).isdigit():continue
        year=int(yr)
        if not start<=year<=end:continue
        expected={'freq':'A','indic_em':'EMP_LFS','sex':'T','age':'Y20-64','unit':'PC_POP'}
        if any(detail.get(k)!=v for k,v in expected.items()):continue
        num=float(value)
        if not math.isfinite(num):continue
        flag=statuses.get(str(key),'') if isinstance(statuses,dict) else ''
        result.append({'provider':'Eurostat EU LFS','country_iso3':EU_TO_ISO3[geo],
            'period':str(year),'year':year,'series':'EMP_RATE_20_64',
            'value':num,'unit':'percent of population aged 20-64','frequency':'annual',
            'disaggregation':json.dumps({'age':'Y20-64','sex':'T'}),
            'status_flag':str(flag),'source_url':url,'retrieved_utc':stamp})
    return result


def run(out='data/labor', *, scope='smoke', source='all', fetch=fetch_bytes, end=2026):
    if scope not in ('smoke','full') or source not in ('all','bls','ilostat','eurostat'):
        raise ValueError('Unsupported scope or source')
    out=Path(out);(out/'raw').mkdir(parents=True,exist_ok=True);(out/'processed').mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).isoformat()
    manifest=[];errors=[];rows=[]
    def process(label,url,parser,*,body=None,**kwargs):
        try:
            if body is None:raw=fetch(url)
            else:raw=fetch(url,method='POST',body=body)
            h=hashlib.sha256(raw).hexdigest();file=f'{label}_{h[:12]}.raw'
            (out/'raw'/file).write_bytes(raw)
            parsed=parser(raw,url=url,stamp=stamp,**kwargs)
            manifest.append({'task':label,'url':url,'sha256':h,'file':file,'observations':len(parsed)})
            if not parsed: raise ValueError('No valid observations for this request')
            rows.extend(parsed)
        except Exception as e: errors.append({'task':label,'error':str(e)})
    if source in ('all','bls'):
        sid=['CES0000000001','LNS14000000'] if scope=='smoke' else list(BLS_SERIES)
        start=2024 if scope=='smoke' else max(end-9,2000)
        # BLS API v1: no key, at most 10 calendar years per request.
        process('bls',bls_url(),parse_bls,body=bls_body(sid,start,end),allowed=set(sid),start=start,end=end)
    if source in ('all','eurostat'):
        start=2020 if scope=='smoke' else 2003
        process('eurostat_employment',eurostat_url(start),parse_eurostat,start=start,end=end)
    if source in ('all','ilostat'):
        chosen=['UNE_2UNE_SEX_AGE_NB_A'] if scope=='smoke' else list(ILOSTAT_SERIES)
        for sid in chosen:
            start=2020 if scope=='smoke' else 1990
            process('ilostat_'+sid,ilostat_url(sid,start=start,end=end,country='USA' if scope=='smoke' else None),
                    parse_ilostat,indicator=sid,start=start,end=end)
    unique={};conflicts=[]
    for row in rows:
        key=(row['provider'],row['country_iso3'],row['period'],row['series'],row['disaggregation'])
        if key in unique and unique[key]['value']!=row['value']:conflicts.append({'key':key})
        unique[key]=row
    cleaned=sorted(unique.values(),key=lambda r:(r['provider'],r['country_iso3'],r['series'],r['period'],r['disaggregation']))
    with (out/'processed/labor_panel.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader();writer.writerows(cleaned)
    coverage={}
    for p in sorted({r['provider'] for r in cleaned}):
        group=[r for r in cleaned if r['provider']==p]
        coverage[p]={'observations':len(group),'geographies':len({r['country_iso3'] for r in group}),
                     'series':sorted({r['series'] for r in group}),
                     'first_year':min(r['year'] for r in group),'last_year':max(r['year'] for r in group)}
    report={'scope':scope,'source':source,'observations':len(cleaned),'coverage':coverage,
            'errors':errors,'conflicts':conflicts,'manifest':manifest,'retrieved_utc':stamp,
            'limitations':['BLS API v1 is limited to 10 years per query and can rate limit unregistered calls.',
              'ILOSTAT classifications and unit metadata require a separate codebook crosswalk.',
              'Eurostat employment rates are not headcounts or payroll job creation.',
              'No automatic cross-source joining, causal inference, or seasonality conversion.']}
    (out/'run_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--scope',choices=['smoke','full'],default='smoke')
    p.add_argument('--source',choices=['all','bls','ilostat','eurostat'],default='all')
    p.add_argument('--out',default='data/labor')
    args=p.parse_args()
    r=run(args.out,scope=args.scope,source=args.source)
    print(json.dumps({k:v for k,v in r.items() if k!='manifest'},indent=2))
    expected={'bls':'US BLS public API v1','ilostat':'ILOSTAT','eurostat':'Eurostat EU LFS'}
    required=expected.keys() if args.source=='all' else (args.source,)
    if r['errors'] or r['conflicts'] or not r['observations'] or any(expected[p] not in r['coverage'] for p in required):
        raise SystemExit('Data validation failed; see data/labor/run_report.json')

if __name__=='__main__':main()
