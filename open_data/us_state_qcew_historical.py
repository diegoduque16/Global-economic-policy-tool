"""BLS official QCEW historical statewide totals, 1975-2020, SIC and NAICS.

Source-native annual all-ownership, all-industry total records only. Suppressed
geographies/measurements are omitted, never inferred, summed or zero-imputed.
Source annual archives and schema/vintage hashes are audited, not redistributed.
"""
from __future__ import annotations
import argparse, csv, datetime as dt, hashlib, io, json, math, re, time
import urllib.error, urllib.parse, urllib.request, zipfile
from pathlib import Path
from open_data.us_state_qcew import STATES

YEARS=tuple(range(1975,2021))
AREAS={fips+'000':abbr for abbr,fips in STATES.items()}
assert len(AREAS)==51
COLUMNS=('provider','country_iso3','year','period','series','value','unit','frequency','disaggregation','obs_status','source_url','retrieved_utc')
MEASURES={'annual_avg_emplvl':'covered_jobs_annual_monthly_average',
          'annual_avg_estabs_count':'establishments_annual_average',
          'total_annual_wages':'nominal_usd_annual_total',
          'annual_avg_wkly_wage':'nominal_usd_per_week',
          'avg_annual_pay':'nominal_usd_per_year'}

def archive_url(year):
    assert year in YEARS
    if year<=2000:return f'https://data.bls.gov/cew/data/files/{year}/sic/csv/sic_{year}_annual_by_industry.zip'
    return f'https://data.bls.gov/cew/data/files/{year}/csv/{year}_annual_by_industry.zip'

def fetch(year, retries=3, max_bytes=500_000_000):
    url=archive_url(year)
    for attempt in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'GlobalEconomicPolicyTool/1.0 (academic research)', 'Accept':'application/zip,application/octet-stream,*/*', 'Accept-Encoding':'identity'})
            with urllib.request.urlopen(req,timeout=240) as response:
                parsed=urllib.parse.urlsplit(response.geturl())
                if parsed.scheme!='https' or parsed.hostname not in ('data.bls.gov','www.bls.gov'):
                    raise ValueError('Unexpected source redirect')
                raw=response.read(max_bytes+1)
            if not 200<len(raw)<=max_bytes or not zipfile.is_zipfile(io.BytesIO(raw)):
                raise ValueError('Bad, empty or oversized source ZIP')
            return raw
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            if attempt==retries-1:raise
            time.sleep(3*(attempt+1))

def numeric(s):
    v=str('' if s is None else s).strip().replace(',','')
    if v.upper() in ('','N','NA','-','(D)','D','S','—'):return None
    x=float(v)
    if not math.isfinite(x) or x<0:raise ValueError('Nonfinite/negative source measurement')
    return x

def parse(raw, year, timestamp):
    if not zipfile.is_zipfile(io.BytesIO(raw)):raise ValueError('Not a source ZIP')
    candidates=[];source_rows={};missing=[]
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        members=[m for m in z.infolist() if not m.is_dir() and m.filename.lower().endswith('.csv')]
        if not 1<=len(members)<=20000:raise ValueError('Unexpected annual CSV count')
        for m in members:
            base=m.filename.replace('\\','/').split('/')[-1]
            if re.search(r'(?:\btotal\b|all industries|\b10\s*\(|\b10\s*\.)',base,re.I):
                if m.file_size>80_000_000:raise ValueError('Excessive expanded member size')
                candidates.append(m)
        if not 1<=len(candidates)<=30:raise ValueError('Total-industry member selection failed')
        for m in candidates:
            with z.open(m) as file:raw_csv=file.read(80_000_001)
            if len(raw_csv)>80_000_000:raise ValueError('Member exceeds safe limit')
            reader=csv.DictReader(io.StringIO(raw_csv.decode('utf-8-sig')))
            req={'area_fips','year','qtr','own_code','size_code','agglvl_code','industry_code','disclosure_code','annual_avg_emplvl','total_annual_wages'}
            if not req.issubset(reader.fieldnames or []):raise ValueError('Source CSV header changed')
            for r in reader:
                area=str(r['area_fips']).strip()
                if area not in AREAS or str(r['own_code']).strip()!='0' or str(r['size_code']).strip()!='0':continue
                ag=str(r.get('agglvl_code') or '').strip().lstrip('0')
                if int(ag or '0') != (18 if year<=2000 else 50):continue
                if int(r['year'])!=year or str(r['qtr']).strip().upper()!='A':raise ValueError('Reference period mismatch')
                key=(area,year)
                if key in source_rows:raise ValueError('Duplicate state-year total '+str(key))
                code=str(r['industry_code']).strip().upper()
                if not code:raise ValueError('Missing source total industry code')
                disclosure=str(r.get('disclosure_code') or '').strip().upper()
                if disclosure not in ('','N'):raise ValueError('Unexpected disclosure status '+disclosure)
                source_rows[key]=(r,m.filename,code,disclosure)
    missing_states=sorted(set(AREAS)-{a for a,_ in source_rows})
    if len(source_rows)<47:raise ValueError(f'{year} insufficient published state-total source records: {len(source_rows)}')
    observations=[];suppressed=[];omitted=0;zero=0;valid_emp=0
    for area in sorted(AREAS):
        record=source_rows.get((area,year))
        if not record:continue
        row, member, code, disclosure=record
        if disclosure=='N':
            suppressed.append(area);continue
        emp=numeric(row.get('annual_avg_emplvl'))
        if emp is None or emp<=0:
            suppressed.append(area);continue
        valid_emp+=1
        dim={'state_abbr':AREAS[area],'state_fips':area[:2],'state_qcew_area_fips':area,
             'industry_code':code,'ownership_code':'0','ownership_scope':'all_ownerships',
             'industry_scope':'all_industries','source_classification':'SIC' if year<=2000 else 'NAICS',
             'source_aggregation_level':18 if year<=2000 else 50,'source_archive_member':member}
        for field,unit in MEASURES.items():
            lookup=field if field in row else 'annual_avg_estabs' if field=='annual_avg_estabs_count' else field
            value=numeric(row.get(lookup))
            if value is None:omitted+=1;continue
            if value==0:zero+=1
            observations.append(dict(provider='BLS QCEW',country_iso3='USA',year=year,period=str(year),
                series='BLS_QCEW_HIST.'+field,value=str(value),unit=unit,frequency='annual',
                disaggregation=json.dumps(dim,sort_keys=True),obs_status='source_observed_descriptive_not_causal',
                source_url=archive_url(year),retrieved_utc=timestamp))
    if valid_emp<47 or len(observations)<valid_emp*3:
        raise ValueError(f'{year}: insufficient unsuppressed state totals: {valid_emp}/51, obs={len(observations)}')
    report={'year':year,'archive_url':archive_url(year),'archive_bytes':len(raw),
            'archive_sha256':hashlib.sha256(raw).hexdigest(),
            'archive_total_members':len(candidates),'source_state_records':len(source_rows),
            'valid_employment_states':valid_emp,'suppressed_states':suppressed,
            'absent_states':missing_states,'omitted_numeric_fields':omitted,'explicit_zero_measurements':zero,
            'observations':len(observations)}
    return observations,report

def collect(years=YEARS, out='data/us_state_qcew_historical', downloader=fetch):
    years=tuple(years)
    if not years or len(set(years))!=len(years) or any(year not in YEARS for year in years):raise ValueError('Invalid year selection')
    root=Path(out);(root/'processed').mkdir(parents=True,exist_ok=True)
    stamp=dt.datetime.now(dt.timezone.utc).isoformat();results=[];reports=[];errors=[];digest=None
    try:
        for y in years:
            raw=downloader(y)
            rows,audit=parse(raw,y,stamp)
            results.extend(rows);reports.append(audit)
            print('YEAR_VALIDATED',json.dumps({'year':y,'observations':len(rows),'states':audit['valid_employment_states'],'suppressed':audit['suppressed_states'],'absent':audit['absent_states']}),flush=True)
            del raw
        assert len(reports)==len(years)
        if sum(a['valid_employment_states'] for a in reports)<.95*len(years)*51:
            raise ValueError('Excessive aggregate geographic missingness')
        keys=set()
        for r in results:
            d=json.loads(r['disaggregation']);key=(r['year'],d['state_fips'],r['series'])
            if key in keys:raise ValueError('Duplicate output measurement '+str(key))
            keys.add(key)
        panel=root/'processed'/'us_state_qcew_historical_panel.csv'
        with panel.open('w',newline='',encoding='utf-8') as file:
            wr=csv.DictWriter(file,fieldnames=COLUMNS,lineterminator='\n');wr.writeheader();wr.writerows(results)
        digest=hashlib.sha256(panel.read_bytes()).hexdigest()
    except Exception as e:
        errors.append({'type':type(e).__name__,'message':str(e)[:1200], 'last_completed_year':reports[-1]['year'] if reports else None})
    status='VALIDATED_DESCRIPTIVE_NOT_CAUSAL' if not errors else 'FAILED_CLOSED'
    report={'status':status,'source':'Official BLS QCEW annual SIC/NAICS by-industry archives',
            'source_url':'https://www.bls.gov/cew/downloadable-data-files.htm',
            'years_requested':list(years),'years_validated':[x['year'] for x in reports],
            'year_audits':reports,'observations':len(results) if not errors else 0,
            'panel_sha256':digest,'errors':errors,'conflicts':[], 'research_ready':False,
            'causal_inference_ready':False,'limitations':[
             'Suppressed state-year records omitted, never filled with zero; per-year source gaps are audited.',
             'Only all-ownership, all-industry statewide totals; no industry sector summing.',
             'Historical SIC and NAICS, other collection definitions and revisions may change.',
             'Nominal wages are not inflation-adjusted; these are outcomes not policy shocks.']}
    (root/'run_report.json').write_text(json.dumps(report,indent=2)+'\n')
    print('COLLECTOR_REPORT',json.dumps({'status':status,'observations':report['observations'],'validated_years':len(reports),'errors':errors}),flush=True)
    if errors:raise RuntimeError(str(errors))
    return report

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--mode',choices=('pilot','full'),default='full');ap.add_argument('--out',default='data/us_state_qcew_historical');args=ap.parse_args()
    collect(years=(1975,1978,1988,2000,2001,2010,2020) if args.mode=='pilot' else YEARS,out=args.out)
