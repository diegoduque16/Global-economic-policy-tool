"""Auditable IMF DataMapper + OECD Global Revenue Statistics downloader.

No tokens or API keys needed. Separate source panels; country/indicator metadata retained.
Always archive raw JSON/CSV and checksums. No causal claims or silent imputation.
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

IMF_BASE = 'https://www.imf.org/external/datamapper/api/v2'
OECD_BASE = 'https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_REV_COMP_GLOBAL@DF_RSGLOBAL,2.1/..S13._T..PT_B1GQ.A'
# DataMapper IMF World Economic Outlook data + separate historical public finances series.
# Value metadata determines actual units/edition; don't guess an observation type.
IMF_INDICATORS = (
    'NGDP_RPCH',     # GDP growth, real
    'PCPIPCH',       # inflation, average CPI
    'LUR',           # unemployment rate
    'GGXCNL_NGDP',   # budget net lending/borrowing, % GDP
    'GGXWDG_NGDP',   # gross public debt, % GDP
    'rev',           # historical public revenue, % GDP (FPP)
    'exp',           # historical public expenditure, % GDP (FPP)
)
SMOKE_IMF = ('NGDP_RPCH', 'GGXWDG_NGDP')
SMOKE_COUNTRIES = ('USA', 'FRA', 'DEU')
FIELDS = ('provider', 'country_code', 'year', 'indicator', 'value', 'unit', 'series_label',
          'dataset', 'source_edition', 'data_status', 'source_url', 'retrieved_utc')


def get_bytes(url: str, retries: int = 3, timeout: int = 90) -> bytes:
    request = urllib.request.Request(url, headers={
        'User-Agent':'GlobalEconomicPolicyTool/1.0 (public academic policy research)',
        'Accept':'application/json,text/csv,*/*',
    })
    for n in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
                if not data: raise ValueError('Empty response')
                return data
        except Exception:
            if n + 1 == retries: raise
            time.sleep(2 ** (n+1))
    raise RuntimeError('fetch unreachable')


def parse_imf(raw: bytes, indicator: str, metadata: dict, *, start: int,
              end: int, allowed: set[str] | None, url: str, timestamp: str):
    obj = json.loads(raw)
    series = obj.get('values', {}).get(indicator)
    if not isinstance(series, dict):
        raise ValueError(f'IMF API schema changed or indicator missing: {indicator}')
    meta = metadata.get(indicator, {})
    if not isinstance(meta, dict): meta = {}
    projected_from = meta.get('projection-year')
    try: projected_from = int(projected_from) if projected_from is not None else None
    except (ValueError,TypeError): projected_from = None
    rows=[]
    for geo, years in series.items():
        if not isinstance(years, dict): continue
        if allowed is not None and geo not in allowed: continue
        # The /countries metadata defines the admitted IMF country IDs.
        for year_text, value in years.items():
            try:
                year = int(year_text);number = float(value)
            except (ValueError,TypeError):continue
            if not (start <= year <= end) or not math.isfinite(number): continue
            rows.append(dict(provider='IMF DataMapper', country_code=geo, year=year,
                indicator=indicator, value=number, unit=meta.get('unit') or 'source_defined',
                series_label=meta.get('label') or indicator,
                dataset=meta.get('dataset') or 'unknown',
                source_edition=meta.get('source') or 'edition_unknown',
                data_status=('projection' if projected_from is not None and year>=projected_from
                             else 'historical_or_current'), source_url=url,retrieved_utc=timestamp))
    return rows


def imf_urls(indicator: str, *, scope: str):
    suffix = '/' + '/'.join(SMOKE_COUNTRIES) if scope == 'smoke' else ''
    return f'{IMF_BASE}/{urllib.parse.quote(indicator, safe="")}{suffix}'


def oecd_url(*, start: int, end: int):
    params={'startPeriod':str(start),'endPeriod':str(end),
            'dimensionAtObservation':'AllDimensions','format':'csvfile'}
    return OECD_BASE + '?' + urllib.parse.urlencode(params)


def parse_oecd(raw: bytes, *, start: int, end: int, url: str, timestamp: str):
    data = raw.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(data))
    cols = set(reader.fieldnames or [])
    essential = {'REF_AREA','TIME_PERIOD','OBS_VALUE'}
    if not essential.issubset(cols):
        raise ValueError(f'OECD CSV fields changed: expected {sorted(essential)}, got {sorted(cols)}')
    # Server filters should be exact. Reject unexpected fiscal categories/units.
    conditions={'SECTOR':'S13','STANDARD_REVENUE':'_T','UNIT_MEASURE':'PT_B1GQ'}
    for field in conditions:
        if field not in cols: raise ValueError(f'Missing OECD required dimension: {field}')
    records=[]
    for record in reader:
        if any((record.get(field) or '').strip()!=expected for field,expected in conditions.items()):
            continue
        geo=(record.get('REF_AREA') or '').strip()
        if not geo: continue
        try:
            year=int((record.get('TIME_PERIOD') or '').strip())
            value=float((record.get('OBS_VALUE') or '').strip())
        except (ValueError,TypeError):continue
        if not(start<=year<=end) or not math.isfinite(value):continue
        records.append(dict(provider='OECD Global Revenue Statistics',country_code=geo, year=year,
           indicator='TAX_TOTAL_PCT_GDP',value=value,unit='percent of GDP',
           series_label='General government total tax revenue, percent of GDP',
           dataset='DSD_REV_COMP_GLOBAL@DF_RSGLOBAL version 2.1',
           source_edition='OECD provider current release; retain retrieval date',
           data_status='source_reported_or_estimated',source_url=url,retrieved_utc=timestamp))
    return records


def write_panel(path:Path, records:list[dict]):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader();writer.writerows(records)


def run(out:Path | str, scope='smoke', source='both', *,fetch=get_bytes,
        start=None, end=2026):
    if scope not in ('smoke','full') or source not in ('both','imf','oecd'):
        raise ValueError('Invalid source/scope')
    if start is None:start=2018 if scope=='smoke' else 1960
    if not (1900<=start<=end<=2100):raise ValueError('Invalid year bounds')
    out=Path(out);raw_dir=out/'raw';processed=out/'processed'
    raw_dir.mkdir(parents=True,exist_ok=True);processed.mkdir(parents=True,exist_ok=True)
    now=datetime.now(timezone.utc).isoformat()
    manifest=[];errors=[];records=[];provider_report={}
    def archive(label,url,payload):
        digest=hashlib.sha256(payload).hexdigest()
        fname=f'{label}_{digest[:12]}.raw'
        (raw_dir/fname).write_bytes(payload)
        manifest.append({'file':fname,'url':url,'sha256':digest,'bytes':len(payload),'retrieved_utc':now})
    if source in ('both','imf'):
        try:
            metadata_url=f'{IMF_BASE}/indicators'; countries_url=f'{IMF_BASE}/countries'
            meta_raw=fetch(metadata_url);archive('imf_indicators',metadata_url,meta_raw)
            metadata=json.loads(meta_raw).get('indicators',{})
            if not isinstance(metadata,dict):raise ValueError('IMF indicator metadata invalid')
            country_raw=fetch(countries_url);archive('imf_countries',countries_url,country_raw)
            countries=json.loads(country_raw).get('countries',{})
            if not isinstance(countries,dict) or not countries:raise ValueError('IMF countries metadata invalid')
            allowed=set(countries)
            series=SMOKE_IMF if scope=='smoke' else IMF_INDICATORS
            for indicator in series:
                try:
                    if indicator not in metadata:raise ValueError('Not in IMF indicator metadata')
                    url=imf_urls(indicator,scope=scope)
                    payload=fetch(url);archive('imf_'+indicator,url,payload)
                    parsed=parse_imf(payload,indicator,metadata,start=start,end=end,allowed=allowed,url=url,timestamp=now)
                    if not parsed:raise ValueError('No IMF observations available for request')
                    records.extend(parsed)
                except Exception as e:errors.append({'source':'imf','indicator':indicator,'error':str(e)})
        except Exception as e: errors.append({'source':'imf_metadata','error':str(e)})
    if source in ('both','oecd'):
        url=oecd_url(start=max(1990,start) if scope=='full' else max(2020,start),end=end)
        try:
            payload=fetch(url);archive('oecd_tax_to_gdp',url,payload)
            parsed=parse_oecd(payload,start=start,end=end,url=url,timestamp=now)
            if not parsed:raise ValueError('No OECD observations from expected total-tax-to-GDP query')
            records.extend(parsed)
        except Exception as e: errors.append({'source':'oecd','error':str(e)})
    unique={}; conflicts=[]
    for row in records:
        key=(row['provider'],row['country_code'],row['year'],row['indicator'])
        if key in unique and unique[key]['value']!=row['value']:
            conflicts.append({'key':key,'a':unique[key]['value'],'b':row['value']})
        unique[key]=row
    clean=sorted(unique.values(),key=lambda x:(x['provider'],x['indicator'],x['country_code'],x['year']))
    write_panel(processed/'imf_oecd_panel.csv',clean)
    for provider in sorted({r['provider'] for r in clean}):
        rs=[r for r in clean if r['provider']==provider]
        provider_report[provider]={'observations':len(rs),'geographies':len({r['country_code'] for r in rs}),
           'indicators':sorted({r['indicator'] for r in rs}),
           'earliest_year':min(r['year'] for r in rs),'latest_year':max(r['year'] for r in rs)}
    report={'scope':scope,'source':source,'requested_start':start,'requested_end':end,
      'retrieved_utc':now,'observations':len(clean),'coverage':provider_report,
      'errors':errors,'conflicts':conflicts,'manifest':manifest,
      'limitations':['OECD tax-to-GDP is a measured ratio, not a change to a tax rate or exogenous policy shock.',
      'IMF WEO has forecast observations; projection years come from provider metadata, not inferred solely from calendar.',
      'IMF and OECD series retain source definitions/units; not harmonized with WDI yet.',
      'No causal attribution or automated policy-event coding is performed.']}
    (out/'run_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scope',choices=['smoke','full'],default='smoke')
    p.add_argument('--source',choices=['both','imf','oecd'],default='both')
    p.add_argument('--out',default='data/imf_oecd')
    args=p.parse_args();report=run(args.out,scope=args.scope,source=args.source)
    print(json.dumps({k:v for k,v in report.items() if k!='manifest'},indent=2))
    if report['errors'] or report['conflicts'] or not report['observations']:
        raise SystemExit('Data validation failed; see run_report.json for details')
    expected=(('IMF DataMapper',) if args.source=='imf' else
              ('OECD Global Revenue Statistics',) if args.source=='oecd' else
              ('IMF DataMapper','OECD Global Revenue Statistics'))
    if any(x not in report['coverage'] for x in expected):
        raise SystemExit('Missing required provider; see run_report.json')
    if args.scope == 'full' and args.source == 'both':
        import subprocess, sys
        subprocess.run([sys.executable, str(Path(__file__).with_name('imf_gfs.py')),
                        '--out', str(Path(args.out).parent/'imf_gfs'),
                        '--start', '1970', '--end', '2025'], check=True)
        # Run validated US job-flow and OECD corporate tax collectors during full builds.
        subprocess.run([sys.executable, str(Path(__file__).with_name('bls_bed.py'))], check=True)
        subprocess.run([sys.executable, str(Path(__file__).with_name('oecd_cit.py'))], check=True)
        subprocess.run([sys.executable, str(Path(__file__).with_name('bls_jolts.py'))], check=True)
        from optional_sources import run_historical_turnover
        run_historical_turnover(Path(__file__).with_name('historical_manufacturing_turnover.py'),
                                Path(args.out).parent)
        subprocess.run([sys.executable, str(Path(__file__).with_name('bocconi_fiscal_plans.py'))], check=True)
        subprocess.run([sys.executable, str(Path(__file__).with_name('imf_extension.py')), '--mode', 'full'], check=True)

if __name__=='__main__':main()
