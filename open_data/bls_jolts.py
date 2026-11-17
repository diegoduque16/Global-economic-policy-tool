"""Download source-coded US BLS JOLTS monthly series for fiscal/AI labor research.

Source: https://download.bls.gov/pub/time.series/JT/ . National industries and
state/region total-nonfarm series; seasonally adjusted levels/rates retained
separately. All quantities are survey estimates, not policy-shock instruments.
"""
from __future__ import annotations
import argparse
import csv
import tempfile
import datetime as dt
import hashlib
import io
import json
import math
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

BASE='https://download.bls.gov/pub/time.series/JT/'
TARGET={'JO':'job_openings', 'HI':'hires', 'TS':'total_separations',
        'QU':'quits', 'LD':'layoffs_and_discharges'}
COLUMNS=('provider','country_iso3','year','period','series','value','unit',
         'frequency','disaggregation','status_flag','source_url','retrieved_utc')
SERIES_REGEX=re.compile(r'^JT[SU][A-Z0-9]{6}[A-Z0-9]{2}[A-Z0-9]{5}[A-Z0-9]{2}(JO|HI|TS|QU|LD)[LR]$')


def retrieve(filename,*, max_size, retries=3):
    url=BASE+filename
    req=urllib.request.Request(url,headers={
        'User-Agent':'GlobalEconomicPolicyResearch/1.0 (public academic research)',
        'Accept':'text/plain,application/octet-stream,*/*',
        'Accept-Encoding':'identity'})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=130) as response:
                payload=response.read(max_size+1)
            if len(payload)>max_size:raise ValueError(f'{filename} exceeded size cap')
            if len(payload)<200 or payload.lstrip().lower().startswith((b'<html',b'<!doctype')):
                raise ValueError(f'{filename}: invalid/empty BLS response')
            return payload
        except (TimeoutError,urllib.error.HTTPError,urllib.error.URLError) as exc:
            if attempt+1==retries or isinstance(exc,urllib.error.HTTPError) and exc.code in (400,401,403,404):raise
            time.sleep(2+2*attempt)


def read_bls_rows(payload):
    raw=io.StringIO(payload.decode('utf-8-sig'))
    reader=csv.DictReader(raw,delimiter='\t')
    if not reader.fieldnames:raise ValueError('No BLS header')
    reader.fieldnames=[field.strip() for field in reader.fieldnames]
    return reader


def build(*,out='data/bls_jolts',start=2000,end=2026, fetch=retrieve):
    if not (2000<=start<=end<=2100):raise ValueError('Invalid year bounds')
    output=Path(out);(output/'processed').mkdir(parents=True,exist_ok=True)
    (output/'raw').mkdir(parents=True,exist_ok=True)
    timestamp=dt.datetime.now(dt.timezone.utc).isoformat()
    manifest=[]
    def get(filename,cap):
        b=fetch(filename,max_size=cap)
        manifest.append({'name':filename,'source_url':BASE+filename,
                         'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b)})
        (output/'raw'/filename).write_bytes(b)
        return b

    meta_payload=get('jt.series',1_500_000)
    meta={}; rejected={}
    for record in read_bls_rows(meta_payload):
        sid=(record.get('series_id') or '').strip()
        if not SERIES_REGEX.fullmatch(sid):continue
        if not all(k in record for k in ('seasonal','industry_code','state_code','area_code',
               'sizeclass_code','dataelement_code','ratelevel_code')):
            raise ValueError('BLS metadata missing required dimension column')
        dims={k:(record.get(k) or '').strip() for k in (
            'seasonal','industry_code','state_code','area_code','sizeclass_code',
            'dataelement_code','ratelevel_code')}
        if dims['seasonal'] not in ('S','U') or dims['ratelevel_code'] not in ('L','R'):
            continue
        if dims['dataelement_code'] not in TARGET:continue
        if dims['area_code']!='00000' or dims['sizeclass_code']!='00':continue
        # National industry rows; state/regional estimates only for total nonfarm.
        if dims['state_code']!='00' and dims['industry_code']!='000000':continue
        # Keep only seasonally adjusted for the first JOLTS connector; never mix SA and NSA.
        if dims['seasonal']!='S':continue
        if sid in meta:raise ValueError('Duplicate series in JOLTS metadata')
        meta[sid]=dims

    if len(meta)<40 or not any(d['state_code']=='00' and d['industry_code']=='000000' for d in meta.values()):
        raise ValueError(f'Unexpectedly few series selected: {len(meta)}')
    # BLS files contain M13 annual averages. Those are excluded from this monthly panel.
    data_payload=get('jt.data.1.AllItems',45_000_000)
    rows=[]; keys=set(); skipped_missing=0; invalid=0; year_limits={}
    for r in read_bls_rows(data_payload):
        sid=(r.get('series_id') or '').strip()
        dims=meta.get(sid)
        if not dims:continue
        year_text=(r.get('year') or '').strip()
        period=(r.get('period') or '').strip()
        if not(year_text.isdecimal() and re.fullmatch(r'M(0[1-9]|1[0-2])',period)):
            continue
        year=int(year_text)
        if year<start or year>end:continue
        val_text=(r.get('value') or '').strip().replace(',','')
        if not val_text or val_text in ('-','NA','N/A'):
            skipped_missing+=1
            continue
        try:value=float(val_text)
        except ValueError:
            invalid+=1
            continue
        if not math.isfinite(value) or value<0:raise ValueError(f'Bad value {sid} {year} {period}')
        key=(sid,year,period)
        if key in keys:raise ValueError(f'Duplicate JOLTS monthly observation: {key}')
        keys.add(key)
        month=int(period[1:])
        geography='national_industry' if dims['state_code']=='00' else (
            'census_region' if dims['state_code'] in ('NE','MW','SO','WE') else 'state')
        measure=TARGET[dims['dataelement_code']]
        unit='percent' if dims['ratelevel_code']=='R' else (
            'thousands_of_vacancies' if dims['dataelement_code']=='JO' else 'thousands_of_workers')
        extra=dict(dims,series_id=sid,geography_level=geography,
                   annual_average_excluded=True,reference_month=period)
        rows.append(dict(provider='US Bureau of Labor Statistics JOLTS',country_iso3='USA',
            year=year,period=f'{year:04d}-{month:02d}',
            series=f'JOLTS.{measure}.{dims["ratelevel_code"]}.SA',
            value=value,unit=unit,frequency='monthly',
            disaggregation=json.dumps(extra,sort_keys=True),
            status_flag=(r.get('footnote_codes') or '').strip(),
            source_url=BASE+'jt.data.1.AllItems',retrieved_utc=timestamp))
        year_limits[sid]=(min(year,year_limits.get(sid,(year,year))[0]),
                          max(year,year_limits.get(sid,(year,year))[1]))

    rows.sort(key=lambda x:(json.loads(x['disaggregation'])['series_id'],x['period']))
    national={json.loads(x['disaggregation'])['dataelement_code'] for x in rows
              if json.loads(x['disaggregation'])['state_code']=='00'
              and json.loads(x['disaggregation'])['industry_code']=='000000'
              and json.loads(x['disaggregation'])['ratelevel_code']=='L'}
    years={int(r['year']) for r in rows}
    if len(rows)<5000 or len(years)<15 or set(TARGET)-national:
        raise ValueError(f'Insufficient JOLTS coverage: rows={len(rows)}, years={len(years)}, missing={set(TARGET)-national}')
    panel=output/'processed'/'bls_jolts_panel.csv'
    with panel.open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=COLUMNS);writer.writeheader();writer.writerows(rows)
    report={
        'dataset':'BLS Job Openings and Labor Turnover Survey','source':'jt.data.1.AllItems',
        'retrieved_utc':timestamp,'requested_years':[start,end],
        'observations':len(rows),'first_year':min(years),'last_year':max(years),
        'series_ids':len(year_limits),'geographic_codes':sorted({json.loads(r['disaggregation'])['state_code'] for r in rows}),
        'industry_codes':sorted({json.loads(r['disaggregation'])['industry_code'] for r in rows}),
        'measures':sorted({x['series'] for x in rows}),
        'omitted_non_numeric':skipped_missing,'omitted_invalid_numeric':invalid,
        'manifest':manifest,'panel_sha256':hashlib.sha256(panel.read_bytes()).hexdigest(),
        'errors':[],'conflicts':[],'research_ready':False,
        'limitations':['JOLTS estimates have sampling error and undergo revisions.',
          'Rates and levels are different units and cannot be pooled or added.',
          'Regional, state, and national/industry cells overlap: do not sum across geographic levels.',
          'Job openings are vacancies measured at month-end; hires/separations are monthly flows.',
          'Seasonally adjusted only; latest release is subject to revision.',
          'Not a causal estimate of the effects of AI, taxes or any policy.']}
    (output/'run_report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


def verify_release(out='data/bls_jolts'):
    """Independently replay the exact saved official BLS bytes against every numeric row.

    Checks per-file hashes and exact provider dimensions, units, flags, observed
    values and order. Does not call the public network or trust a green workflow.
    """
    root=Path(out)
    report=json.loads((root/'run_report.json').read_text(encoding='utf-8'))
    manifest=report.get('manifest',[])
    expected={'jt.series','jt.data.1.AllItems'}
    if {m['name'] for m in manifest}!=expected or len(manifest)!=len(expected):
        raise ValueError('JOLTS original-source manifest is incomplete')
    originals={}
    for item in manifest:
        filename=item['name']
        if item['source_url']!=BASE+filename:
            raise ValueError('BLS original-source URL changed')
        file=root/'raw'/filename
        if not file.is_file() or file.is_symlink():
            raise ValueError('Original BLS source missing or unsafe')
        data=file.read_bytes()
        if (hashlib.sha256(data).hexdigest()!=item['sha256']
            or len(data)!=item['bytes']):
            raise ValueError('Original BLS source SHA256 mismatch')
        originals[filename]=data
    panel=root/'processed'/'bls_jolts_panel.csv'
    if hashlib.sha256(panel.read_bytes()).hexdigest()!=report['panel_sha256']:
        raise ValueError('BLS processed panel SHA256 mismatch')
    with tempfile.TemporaryDirectory() as temp:
        def offline_fetch(filename,*,max_size,retries=3):
            data=originals[filename]
            if len(data)>max_size:
                raise ValueError('Original BLS replay exceeds configured size cap')
            return data
        recreated=build(out=temp,start=report['requested_years'][0],
                        end=report['requested_years'][1],fetch=offline_fetch)
        checks=('observations','first_year','last_year','series_ids',
                'geographic_codes','industry_codes','measures',
                'omitted_non_numeric','omitted_invalid_numeric')
        if any(recreated[k]!=report[k] for k in checks):
            raise ValueError('Independent BLS source numeric coverage changed')
        with panel.open(newline='',encoding='utf-8') as f:
            original_rows=list(csv.DictReader(f))
        with (Path(temp)/'processed'/'bls_jolts_panel.csv').open(newline='',encoding='utf-8') as f:
            replay_rows=list(csv.DictReader(f))
        if len(original_rows)!=recreated['observations']:
            raise ValueError('BLS source panel row count mismatch')
        for a,b in zip(original_rows,replay_rows):
            if {k:v for k,v in a.items() if k!='retrieved_utc'} != {
                    k:v for k,v in b.items() if k!='retrieved_utc'}:
                raise ValueError('Independent BLS original numeric/metadata replay failed')
        if len(original_rows)!=len(replay_rows):
            raise ValueError('BLS original source/panel lengths mismatch')
    return {'status':'BLS_JOLTS_ORIGINAL_NUMERIC_REPLAY_VERIFIED',
            'source_files':len(originals),'observations':report['observations'],
            'series_ids':report['series_ids'],'first_year':report['first_year'],
            'last_year':report['last_year'],'panel_sha256':report['panel_sha256']}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',default='data/bls_jolts')
    p.add_argument('--start',type=int,default=2000)
    p.add_argument('--end',type=int,default=2026)
    p.add_argument('--verify-only',action='store_true')
    a=p.parse_args()
    if a.verify_only:
        print(json.dumps(verify_release(a.out),indent=2))
    else:
        x=build(out=a.out,start=a.start,end=a.end)
        print(json.dumps({'status':'VALIDATED_EXTRACT','observations':x['observations'],
             'first_year':x['first_year'],'last_year':x['last_year'],
             'geographies':len(x['geographic_codes']),'industries':len(x['industry_codes']),
             'series_ids':x['series_ids'],'errors':x['errors']},indent=2))
