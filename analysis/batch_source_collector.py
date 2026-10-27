#!/usr/bin/env python3
"""Reproducible, fail-isolated source-native batch collection. Stdlib only.

This STAGES raw source data; it does not merge data into the research warehouse.
"""
import argparse, csv, hashlib, io, json, os, pathlib, sys, time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

ALLOWED_HOSTS = {'api.worldbank.org', 'data.bls.gov', 'api.census.gov'}
P0 = {'P0','P1','P2'}
WDI = {
    'NY.GDP.MKTP.KD.ZG':'real_gdp_growth_pct',
    'NY.GDP.PCAP.KD.ZG':'real_gdp_per_capita_growth_pct',
    'NE.GDI.FTOT.ZS':'gross_fixed_capital_formation_pct_gdp',
    'GC.TAX.TOTL.GD.ZS':'tax_revenue_pct_gdp',
    'GC.XPN.TOTL.GD.ZS':'expense_pct_gdp',
    'SL.UEM.TOTL.ZS':'unemployment_pct_ilo_modeled',
    'SL.TLF.CACT.ZS':'labor_force_participation_pct_ilo_modeled',
    'SI.POV.GINI':'gini_index',
    'SP.POP.TOTL':'population',
    'FP.CPI.TOTL.ZG':'consumer_price_inflation_pct',
    'BN.CAB.XOKA.GD.ZS':'current_account_balance_pct_gdp',
    'NE.EXP.GNFS.ZS':'exports_pct_gdp',
}

def sha(b): return hashlib.sha256(b).hexdigest()
def dump(path,obj): path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
def fetch(url, *, maximum=25_000_000):
    host=urlsplit(url).hostname
    if host not in ALLOWED_HOSTS or urlsplit(url).scheme!='https':raise ValueError('Unapproved endpoint: '+url)
    for attempt in range(3):
        try:
            with urlopen(Request(url,headers={'User-Agent':'GlobalEconomicPolicyResearch/2.0','Accept':'application/json,text/csv'}),timeout=75) as f:
                if urlsplit(f.geturl()).hostname!=host:raise ValueError('Cross-host redirect')
                data=f.read(maximum+1)
                if len(data)>maximum or not data:raise ValueError('Empty/oversized payload')
                return data
        except (HTTPError, URLError, TimeoutError, OSError) as e:
            if attempt==2:raise RuntimeError(f'{type(e).__name__}: {str(e)[:180]}') from e
            time.sleep(2*(attempt+1))

def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    return {'file':str(path.relative_to(path.parents[1])),'sha256':sha(path.read_bytes()),'rows':len(rows),'bytes':path.stat().st_size}

def catalog_audit(path):
    with path.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    if not rows:raise ValueError('Catalog is empty')
    required={'dataset','priority','official_source_url','theme'}
    if not required.issubset(rows[0]):raise ValueError('Catalog missing required columns')
    seen=set()
    for n,r in enumerate(rows,2):
        if None in r or any(v is None for v in r.values()):raise ValueError(f'Malformed catalog row {n}')
        key=r['dataset'].strip().casefold()
        if not key or key in seen or r['priority'] not in P0:raise ValueError(f'Invalid/duplicate catalog row {n}')
        seen.add(key)
    return rows

def worldbank(out):
    records=[];requests=[]
    for indicator,name in WDI.items():
        page=1
        while True:
            url=f'https://api.worldbank.org/v2/country/all/indicator/{indicator}?'+urlencode({'format':'json','per_page':20000,'page':page})
            blob=fetch(url);body=json.loads(blob)
            if not isinstance(body,list) or len(body)!=2 or not isinstance(body[1],list):raise ValueError('World Bank API schema changed: '+indicator)
            meta=body[0];requests.append({'url':url,'sha256':sha(blob),'records':len(body[1])})
            for r in body[1]:
                c=r.get('country') or {}
                records.append({'indicator':indicator,'indicator_label':name,'country_id':r.get('countryiso3code') or r.get('country',{}).get('id'),'country_name':c.get('value'),'year':r.get('date'),'value':r.get('value'),'unit':r.get('unit') or '','obs_status':r.get('obs_status') or ''})
            if page>=int(meta.get('pages',1)):break
            page+=1
            if page>100:raise ValueError('World Bank page limit reached')
    if len(records)<10000:raise ValueError('Unexpectedly few World Bank observations')
    save=write_csv(out/'world_bank_wdi_expanded_native.csv',['indicator','indicator_label','country_id','country_name','year','value','unit','obs_status'],records)
    return dict(source='World Bank WDI expanded indicators',catalog_entry='World Bank World Development Indicators (32 selected)',status='STAGED_NOT_IN_WAREHOUSE',requests=requests,**save)

def qcew(out):
    # U.S. national QCEW aggregate, quarterly source-native files, not state/county panels.
    parts=[];requests=[]
    for year in range(2020,2026):
        for quarter in range(1,5):
            url=f'https://data.bls.gov/cew/data/api/{year}/{quarter}/area/US000.csv'
            try: blob=fetch(url,maximum=12_000_000)
            except Exception as e:
                requests.append({'url':url,'error':str(e),'status':'missing_period'});continue
            reader=csv.DictReader(io.StringIO(blob.decode('utf-8-sig')))
            if not reader.fieldnames or 'year' not in reader.fieldnames or 'qtr' not in reader.fieldnames:raise ValueError('QCEW schema changed')
            rows=list(reader)
            for r in rows:r['requested_year']=year;r['requested_quarter']=quarter
            parts.extend(rows);requests.append({'url':url,'sha256':sha(blob),'records':len(rows)})
    if len(parts)<100:raise ValueError('Too few QCEW source rows')
    columns=sorted({k for row in parts for k in row})
    save=write_csv(out/'bls_qcew_us_national_2020_2025_native.csv',columns,parts)
    return dict(source='BLS Quarterly Census of Employment and Wages',catalog_entry='BLS Quarterly Census of Employment and Wages (QCEW)',status='STAGED_NATIONAL_ONLY_NOT_IN_WAREHOUSE',requests=requests,**save)

def cbp(out):
    # Select US state CBP measures, documenting each vintage and disclosure flags.
    rows=[];requests=[]
    for year in range(2019,2024):
        url=f'https://api.census.gov/data/{year}/cbp?'+urlencode({'get':'NAME,EMP,ESTAB,PAYANN','for':'state:*'})
        try:blob=fetch(url)
        except Exception as e:
            requests.append({'url':url,'error':str(e),'status':'missing_year'});continue
        body=json.loads(blob)
        if not isinstance(body,list) or len(body)<10:raise ValueError('Unexpected CBP response')
        header=body[0]
        for values in body[1:]:rows.append(dict(zip(['year']+header,[year]+values)))
        requests.append({'url':url,'sha256':sha(blob),'records':len(body)-1})
    if len(rows)<50:raise ValueError('Too few CBP state observations')
    save=write_csv(out/'census_cbp_states_native.csv',['year','NAME','EMP','ESTAB','PAYANN','state'],rows)
    return dict(source='Census County Business Patterns',catalog_entry='Census County Business Patterns (CBP)',status='STAGED_STATE_ONLY_NOT_IN_WAREHOUSE',requests=requests,**save)

COLLECTORS={'worldbank_wdi_expanded':worldbank,'bls_qcew_us_national':qcew,'census_cbp_states':cbp}

def run(args):
    out=pathlib.Path(args.output);out.mkdir(parents=True,exist_ok=True)
    rows=catalog_audit(pathlib.Path(args.catalog))
    selected=args.sources.split(',') if args.sources else list(COLLECTORS)
    unknown=set(selected)-set(COLLECTORS)
    if unknown:raise ValueError('Unknown collectors: '+','.join(sorted(unknown)))
    results=[];failures=[]
    for name in selected:
        try:
            result=COLLECTORS[name](out/'source_native');result['collector']=name;results.append(result)
            print('STAGED',name,result['rows'],flush=True)
        except Exception as e:
            failures.append({'collector':name,'error':str(e),'status':'FAILED_NOT_STAGED'})
            print('FAILED',name,str(e),flush=True)
    covered={x['catalog_entry'] for x in results}
    queue=[{'dataset':r['dataset'],'priority':r['priority'],'status':'NEW_SOURCE_STAGED_NOT_IN_WAREHOUSE' if r['dataset'] in covered else 'PENDING_OR_PREVIOUSLY_INGESTED_NOT_REASSESSED','theme':r['theme']} for r in rows]
    write_csv(out/'catalog_queue.csv',['dataset','priority','status','theme'],queue)
    audit={'created_utc':datetime.now(timezone.utc).isoformat(),'catalog_entries':len(rows),'catalog_p0':sum(r['priority']=='P0' for r in rows),'selected':selected,'successful':results,'failures':failures,'warehouse_modified':False,'note':'Collection ≠ warehouse integration; staged scopes are not full catalog coverage. Original 24-family warehouse is untouched.'}
    dump(out/'audit.json',audit)
    if not results:raise SystemExit('No collectors succeeded')
    if failures and args.require_all:raise SystemExit('Some sources failed; partial artifact preserved')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--catalog',default='open_data/expanded_source_catalog.csv');p.add_argument('--output',default='batch_source_results');p.add_argument('--sources',default='');p.add_argument('--require-all',action='store_true');run(p.parse_args())
