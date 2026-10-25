"""Auditable public economic series downloader: USA FRED CSV and Eurostat JSON-stat.

Uses standard library; does not equate series across sources or assert that coverage is complete.
Historical US and EU series have mixed date ranges and definitions. Offline unit tested.
"""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path

FRED_BASE = 'https://fred.stlouisfed.org/graph/fredgraph.csv'
EUROSTAT_BASE = 'https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data'
# A policy-relevant starter set. No claim of complete U.S. coverage.
FRED = {
    'PAYEMS': ('US nonfarm payroll employment', 'monthly'),
    'USPRIV': ('US private nonfarm payroll employment', 'monthly'),
    'UNRATE': ('US unemployment rate', 'monthly'),
    'CIVPART': ('US labor force participation rate', 'monthly'),
    'GDPC1': ('US real GDP', 'quarterly'),
    'GDP': ('US nominal GDP', 'quarterly'),
    'CPIAUCSL': ('US consumer price index', 'monthly'),
    'FEDFUNDS': ('US effective federal funds rate', 'monthly'),
    'GS10': ('US 10-year Treasury yield', 'monthly'),
    'GFDEGDQ188S': ('US federal debt as share of GDP', 'quarterly'),
    'INDPRO': ('US industrial production', 'monthly'),
    'USREC': ('US recession indicator (NBER dating)', 'monthly'),
    'JTSJOL': ('US job openings', 'monthly'),
}
# 27 EU members + UK and 3 EFTA economies + Iceland.
EU = 'AT BE BG HR CY CZ DK EE FI FR DE EL HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE UK NO CH IS'.split()
EU_ISO3 = dict(zip(EU, 'AUT BEL BGR HRV CYP CZE DNK EST FIN FRA DEU GRC HUN IRL ITA LVA LTU LUX MLT NLD POL PRT ROU SVK SVN ESP SWE GBR NOR CHE ISL'.split()))
# A minimal, intentionally finite Eurostat catalog. Exact code/filter labels
# need ongoing provider metadata reconciliation before cross-source joins.
EUROSTAT = {
    'gdp_nominal': ('nama_10_gdp', {'na_item':'B1G','unit':'CP_MEUR'}, 'annual', 'current prices million euros'),
    'gdp_real_growth': ('nama_10_gdp', {'na_item':'B1G','unit':'CLV_PCH_PRE'}, 'annual', 'chain-linked real GDP annual percent change'),
    'unemployment_rate': ('une_rt_a', {'sex':'T','age':'Y15-74','unit':'PC_ACT'}, 'annual', 'share of economically active aged 15-74, percent'),
}


def fetch_bytes(url, retries=3, timeout=60):
    for attempt in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'OpenEconomicResearch/0.9 (public policy research)','Accept':'text/csv,application/json,*/*'})
            with urllib.request.urlopen(req,timeout=timeout) as r: return r.read()
        except Exception:
            if attempt==retries-1: raise
            time.sleep(2*(attempt+1))
    raise RuntimeError('unreachable')


def parse_fred(raw, series, start=1870, end=2026):
    text=raw.decode('utf-8-sig'); reader=csv.DictReader(io.StringIO(text))
    head=reader.fieldnames or []
    date_col='observation_date' if 'observation_date' in head else 'DATE'
    if date_col not in head or series not in head: raise ValueError(f'FRED CSV columns invalid for {series}: {head!r}')
    rows=[]; skipped=0
    for record in reader:
        date=(record.get(date_col) or '').strip()
        try: year=int(date[:4]); dt.date.fromisoformat(date)
        except (ValueError,TypeError): skipped+=1;continue
        if not start <= year <= end: continue
        number=(record.get(series) or '').strip()
        if not number or number=='.': skipped+=1;continue
        try: value=float(number)
        except ValueError: skipped+=1;continue
        if not math.isfinite(value): skipped+=1;continue
        rows.append({'country_iso3':'USA','region':'United States','year':year,'period':date,
         'series':series,'value':value,'unit':'FRED_series_defined_verify_metadata',
         'frequency':FRED[series][1],'provider':'FRED (St. Louis Fed; original series provider varies)',
         'observation_kind':'source_observation_or_estimate'})
    return rows,skipped


def parse_jsonstat(raw, name, start=1990, end=2026, eligible=None):
    """Decode JSON-stat 2.0 flattened multidimensional values and country codes.

    See Eurostat JSON-stat API; dimension sizes dictate flattened index stride.
    """
    payload=json.loads(raw)
    dims=payload.get('id');sizes=payload.get('size');values=payload.get('value')
    if not isinstance(dims,list) or not isinstance(sizes,list) or len(dims)!=len(sizes) or 'geo' not in dims or 'time' not in dims or not isinstance(values,(dict,list)):
        raise ValueError('Eurostat response is not expected JSON-stat country-time observations')
    if math.prod(sizes)>5_000_000:raise ValueError('Unexpectedly large Eurostat response')
    eligible=set(eligible or EU)
    decoded=[]
    for dimension,size in zip(dims,sizes):
        raw_index=payload.get('dimension',{}).get(dimension,{}).get('category',{}).get('index')
        if isinstance(raw_index,dict):
            if len(raw_index)!=size:raise ValueError(f'Bad dimension index {dimension}')
            codes=[None]*size
            for code,i in raw_index.items():
                if not isinstance(i,int) or not 0<=i<size or codes[i] is not None:raise ValueError('Bad index values')
                codes[i]=code
        elif isinstance(raw_index,list):
            if len(raw_index)!=size: raise ValueError(f'Bad {dimension} size')
            codes=raw_index
        else: raise ValueError(f'Missing index for {dimension}')
        decoded.append(codes)
    strides=[math.prod(sizes[i+1:]) for i in range(len(sizes))]
    observations=values.items() if isinstance(values,dict) else enumerate(values)
    result=[]; skipped=0
    for flat,value in observations:
        if value is None:continue
        try:
            flat=int(flat); indexes=[(flat//stride)%size for stride,size in zip(strides,sizes)]
            parts=dict(zip(dims,(codes[index] for codes,index in zip(decoded,indexes))))
            geo=parts['geo'];yr=int(parts['time']);val=float(value)
        except (ValueError,IndexError,TypeError,KeyError):skipped+=1;continue
        if geo not in eligible or not start<=yr<=end or not math.isfinite(val):continue
        result.append({'country_iso3':EU_ISO3[geo],'region':geo,'year':yr,'period':str(yr),
         'series':name,'value':val,'unit':EUROSTAT[name][3],
         'frequency':'annual','provider':'Eurostat','observation_kind':'source_observation_or_estimate'})
    return result,skipped


def eu_url(name,start):
    dataset,params,*_=EUROSTAT[name]
    query={**params,'geoLevel':'country','sinceTimePeriod':str(start),'lang':'EN','format':'JSON'}
    return EUROSTAT_BASE+'/'+dataset+'?'+urllib.parse.urlencode(query)


def fred_url(series):
    return FRED_BASE+'?'+urllib.parse.urlencode({'id':series})


def run(out,scope='smoke',sources='all',start=1870,end=2026,fetch=fetch_bytes):
    if not 1800<=start<=end<=2100:raise ValueError('Invalid date range')
    if scope not in ('smoke','full') or sources not in ('all','us','europe'):raise ValueError('Unknown options')
    out=Path(out);(out/'raw').mkdir(parents=True,exist_ok=True);(out/'processed').mkdir(parents=True,exist_ok=True)
    stamp=dt.datetime.now(dt.timezone.utc).isoformat()
    tasks=[]
    if sources in ('all','us'):
        tasks += [('us',code,fred_url(code)) for code in (list(FRED)[:2] if scope=='smoke' else FRED)]
    if sources in ('all','europe'):
        tasks += [('europe',code,eu_url(code,max(1990,start))) for code in (['gdp_nominal'] if scope=='smoke' else EUROSTAT)]
    records=[];manifest=[];errors=[]
    for provider,series,url in tasks:
        try:
            raw=fetch(url)
            if not raw:raise ValueError('Empty source body')
            parsed,skipped=(parse_fred(raw,series,start,end) if provider=='us' else parse_jsonstat(raw,series,max(1990,start),end))
            if not parsed:raise ValueError('No usable observations returned')
            digest=hashlib.sha256(raw).hexdigest()
            filename=f'{provider}_{series}_{digest[:12]}.raw'
            (out/'raw'/filename).write_bytes(raw)
            manifest.append({'source':provider,'series':series,'url':url,'sha256':digest,'filename':filename,
                'retrieved_utc':stamp,'observations':len(parsed),'skipped_or_missing':skipped})
            for record in parsed:record.update({'source_url':url,'retrieved_utc':stamp})
            records+=parsed
        except Exception as exc: errors.append({'source':provider,'series':series,'error':str(exc)})
    # Distinct source/geography/date/series, preserving source vintages and avoiding imputation.
    unique={}; conflicts=[]
    for item in records:
        key=(item['provider'],item['country_iso3'],item['period'],item['series'])
        if key in unique and unique[key]['value']!=item['value']:conflicts.append(key)
        unique[key]=item
    fields=['country_iso3','region','year','period','series','value','unit','frequency','provider','observation_kind','source_url','retrieved_utc']
    with (out/'processed'/'us_europe_panel.csv').open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(sorted(unique.values(),key=lambda r:(r['provider'],r['country_iso3'],r['series'],r['period'])))
    coverage={}
    for provider in ('FRED (St. Louis Fed; original series provider varies)','Eurostat'):
        subset=[r for r in unique.values() if r['provider']==provider]
        coverage[provider]={'observations':len(subset),'countries':len({r['country_iso3'] for r in subset}),
          'country_iso3':sorted({r['country_iso3'] for r in subset}), 'series':sorted({r['series'] for r in subset}),
          'earliest_year':min((r['year'] for r in subset),default=None),'latest_year':max((r['year'] for r in subset),default=None)}
    report={'scope':scope,'sources_requested':sources,'retrieved_utc':stamp,'observations':len(unique),'coverage':coverage,
      'tasks_requested':len(tasks),'tasks_passed':len(manifest),'errors':errors,'conflicts':conflicts,
      'cautions':['FRED source units require metadata crosswalk before merging across providers.',
        'Eurostat series may include revisions and country concept changes. European country selection is 31 economies, not all geographic Europe.',
        'GDP, payroll employment and unemployment have different frequencies; aggregation/harmonization not yet implemented.',
        'Data download success does not imply causal identification or exhaustive policy coverage.'],
      'manifest':manifest}
    (out/'run_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scope',choices=('smoke','full'),default='smoke')
    p.add_argument('--sources',choices=('all','us','europe'),default='all')
    p.add_argument('--start',type=int,default=1870)
    p.add_argument('--end',type=int,default=2026)
    p.add_argument('--out',default='data/us_europe')
    args=p.parse_args();r=run(args.out,args.scope,args.sources,args.start,args.end)
    print(json.dumps({k:v for k,v in r.items() if k!='manifest'},indent=2))
    if r['errors'] or r['conflicts'] or r['observations']==0:raise SystemExit(1)

if __name__=='__main__':main()
