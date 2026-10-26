"""IMF DataMapper v2 indicator-at-a-time extension.

Provider currently ignores country/period filters. Fetch full indicator once,
then select countries and time bounds locally, retaining the provider-native
units, vintages and projection metadata. No causal interpretation is asserted.
"""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

BASE = 'https://www.imf.org/external/datamapper/api/v2'
INDICATORS = ('NGDPD','NGDPDPC','PPPGDP','PPPPC','LP','BCA_NGDPD',
              'PCPIEPCH','prim_exp','ie','pb','d','rgc','rltir')
PILOT_INDICATORS = ('NGDPD','NGDPDPC','prim_exp')
PILOT_COUNTRIES = ('USA','FRA','DEU','KEN','GHA')
PANEL_COLUMNS = ('provider','country_iso3','year','period','series','value',
                 'unit','frequency','disaggregation','obs_status','source_url','retrieved_utc')


def get_json_bytes(url, *, fetcher=None, max_bytes=5_000_000, attempts=4):
    if not url.startswith(BASE+'/') or '?' in url:
        raise ValueError('Only canonical, unfiltered IMF DataMapper v2 URLs allowed')
    if fetcher is not None:
        raw=fetcher(url)
        if not raw or len(raw)>max_bytes:raise ValueError('Invalid bounded response size')
        json.loads(raw)
        return raw
    request=urllib.request.Request(url,headers={
        'User-Agent':'GlobalEconomicPolicyTool/1.0 (public academic policy research)',
        'Accept':'application/json,text/csv,*/*','Accept-Encoding':'identity'})
    last=None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request,timeout=65) as response:
                if response.geturl().split('/')[2].lower()!='www.imf.org':
                    raise ValueError('IMF endpoint redirected to another host')
                raw=response.read(max_bytes+1)
                if not raw or len(raw)>max_bytes:raise ValueError('Empty/oversized IMF response')
                data=json.loads(raw)
                if not isinstance(data,dict):raise ValueError('IMF JSON top level not object')
                return raw
        except urllib.error.HTTPError as err:
            if err.code not in (429,500,502,503,504): raise
            last=err
        except (urllib.error.URLError,TimeoutError,ValueError,UnicodeDecodeError,json.JSONDecodeError) as err:
            last=err
        if attempt+1<attempts:time.sleep(min(2**attempt,8))
    raise ValueError('IMF endpoint failed after retries: '+type(last).__name__+': '+str(last)[:100])


def parse_full_indicator(raw, indicator, metadata, allowed, start, end, *, when):
    """Ignore API request filters, which IMF diagnostics showed are not honored."""
    payload=json.loads(raw)
    v=payload.get('values') if isinstance(payload,dict) else None
    series=v.get(indicator) if isinstance(v,dict) else None
    if not isinstance(series,dict) or not series:
        raise ValueError('Missing or empty full indicator: '+indicator)
    projected=metadata.get('projection-year')
    try:projected=int(projected) if projected is not None else None
    except (TypeError,ValueError):projected=None
    rows=[];countries_seen=set();keys=set();extraneous=0
    for geo,years in series.items():
        if geo not in allowed or not re.fullmatch(r'[A-Z]{3}',geo):
            extraneous+=1;continue
        if not isinstance(years,dict):raise ValueError('Unexpected series for '+geo)
        for year_string, val in years.items():
            try:year=int(year_string)
            except (TypeError,ValueError):raise ValueError('Bad source year for '+geo)
            if not start<=year<=end:continue
            if val is None or val=='':continue
            try:value=float(val)
            except (TypeError,ValueError) as e:raise ValueError('Non-numeric source value') from e
            if not math.isfinite(value):raise ValueError('Nonfinite source value')
            key=(geo,year)
            if key in keys:raise ValueError('Repeated source observation')
            keys.add(key);countries_seen.add(geo)
            disagg={'source_indicator_code':indicator,
              'imf_dataset':metadata.get('dataset'),
              'indicator_label':metadata.get('label'),
              'source_vintage':metadata.get('source'),
              'projection_year_metadata':projected,
              'retrieval_filter':'full_indicator_download_then_local_filter',
              'country_code_from_imf_catalog':True}
            status=('projection' if projected is not None and year>=projected
                else 'pre_projection_year' if projected is not None
                else 'projection_status_not_in_metadata')
            rows.append(dict(provider='IMF DataMapper v2 extension',country_iso3=geo,
                year=year,period=str(year),series='IMF_DATAMAPPER.'+indicator,
                value=value,unit=metadata.get('unit') or 'source_defined_unit_unverified',
                frequency='annual',disaggregation=json.dumps(disagg,sort_keys=True),
                obs_status=status,source_url=BASE+'/'+indicator,retrieved_utc=when))
    return rows, {'geographies_in_full_response':len(series),
                  'geographies_outside_admitted_country_catalog':extraneous,
                  'admitted_geographies_with_observations':len(countries_seen)}


def collect(out='data/imf_extension',mode='pilot',*, fetcher=None,throttle=.35):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    checkpoints=out/'checkpoints';checkpoints.mkdir(exist_ok=True)
    when=dt.datetime.now(dt.timezone.utc).isoformat()
    report={'source':'IMF DataMapper v2 source-separated extension',
        'status':'NOT_VALIDATED','mode':mode,'retrieved_utc':when,'research_ready':False,
        'errors':[],'conflicts':[],'method':'One whole indicator per request; local country/year selection',
        'limitations':['IMF API ignores country and year filters in documented October 2026 tests.',
           'Economic indicators are not exogenous shocks or causal estimates.',
           'Geographic codes come from IMF catalog and can include non-sovereign aggregates.',
           'IMF forecasts and provider units are not harmonized across sources.']}
    try:
        if mode not in ('pilot','full'):raise ValueError('Mode must be pilot or full')
        codes=PILOT_INDICATORS if mode=='pilot' else INDICATORS
        start,end=(2017,2026) if mode=='pilot' else (1960,2026)
        metadata_bytes=get_json_bytes(BASE+'/indicators',fetcher=fetcher)
        countries_bytes=get_json_bytes(BASE+'/countries',fetcher=fetcher)
        meta=json.loads(metadata_bytes).get('indicators')
        catalog=json.loads(countries_bytes).get('countries')
        if not isinstance(meta,dict) or not isinstance(catalog,dict) or len(catalog)<150:
            raise ValueError('IMF indicator/country metadata missing or too sparse')
        if any(code not in meta for code in codes):
            raise ValueError('Missing official IMF indicators: '+repr(sorted(set(codes)-set(meta))))
        country_codes=set(PILOT_COUNTRIES if mode=='pilot' else catalog)
        if any(code not in catalog for code in country_codes):
            raise ValueError('Required pilot country is not in official country catalog')
        meta_hash=hashlib.sha256(metadata_bytes).hexdigest()
        cat_hash=hashlib.sha256(countries_bytes).hexdigest()
        rows=[];all_ids=set();indicators={};hashes=[];cache_reuse=0
        for code in codes:
            url=BASE+'/'+code
            # Strict vintage-keyed cache prevents cross-edition mixing.
            key=hashlib.sha256((url+'|'+meta_hash+'|'+cat_hash).encode()).hexdigest()
            target=checkpoints/(key+'.json')
            if target.is_file():
                raw=target.read_bytes();cache_reuse+=1
                if not raw:raise ValueError('Empty cached source')
            else:raw=get_json_bytes(url,fetcher=fetcher)
            selected,details=parse_full_indicator(raw,code,meta[code],country_codes,start,end,when=when)
            if not selected:raise ValueError('No admitted source observations: '+code)
            if not target.is_file():target.write_bytes(raw)
            for row in selected:
                unique=(row['country_iso3'],row['year'],row['series'])
                if unique in all_ids:raise ValueError('Duplicate source observation')
                all_ids.add(unique)
            rows.extend(selected)
            indicators[code]=len(selected)
            hashes.append(dict(indicator=code,request_url=url,
               raw_sha256=hashlib.sha256(raw).hexdigest(),
               source_bytes=len(raw),included_rows=len(selected),**details))
            if throttle and fetcher is None:time.sleep(throttle)
        rows.sort(key=lambda r:(r['country_iso3'],r['year'],r['series']))
        countries=len({x['country_iso3'] for x in rows})
        if mode=='pilot':
            if countries<5 or len(rows)<45:raise ValueError('Pilot coverage unexpectedly sparse')
        elif countries<100 or len(rows)<10_000:
            raise ValueError('Full source coverage unexpectedly sparse')
        processed=out/'processed';processed.mkdir(exist_ok=True)
        panel=processed/'imf_extension_panel.csv'
        with panel.open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=PANEL_COLUMNS);w.writeheader();w.writerows(rows)
        report.update({'status':'VALIDATED_MACRO_DATA_NOT_CAUSAL_POLICY_SHOCKS',
           'observations':len(rows),'country_count':countries,
           'indicator_count':len(indicators),'indicator_rows':indicators,
           'earliest_year':min(r['year'] for r in rows),
           'latest_year':max(r['year'] for r in rows),
           'source_requests':len(codes)+2,'reused_checkpoints':cache_reuse,
           'indicator_source_checksums':hashes,
           'indicator_metadata_sha256':meta_hash,
           'country_metadata_sha256':cat_hash,
           'panel_sha256':hashlib.sha256(panel.read_bytes()).hexdigest()})
    except Exception as e:
        report['status']='VALIDATION_FAILED'
        report['errors'].append({'type':type(e).__name__,'message':str(e)[:400]})
    (out/'run_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({k:report.get(k) for k in ('status','observations','country_count',
          'indicator_count','indicator_rows','source_requests','reused_checkpoints','errors')},indent=2))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('pilot','full'),default='pilot')
    p.add_argument('--out',default='data/imf_extension')
    p.add_argument('--throttle',type=float,default=.35)
    a=p.parse_args()
    report=collect(a.out,a.mode,throttle=a.throttle)
    if report['errors']:raise SystemExit('IMF extension invalid; see run_report.json')
