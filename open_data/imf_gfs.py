"""IMF GFS Statement of Operations: historical fiscal ratios, source-coded and auditable.

IMF STA GFS_SOO SDMX 3.0, annual 1970-2025.  Does not harmonize government
levels, policy shocks, accounting regimes, or revisions across economies.
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
import time
import urllib.error
import urllib.request

ROOT = 'https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.STA/GFS_SOO/12.0.0'
COUNTRIES = ('USA', 'CAN', 'GBR', 'FRA', 'DEU', 'ITA', 'ESP', 'NLD',
             'SWE', 'NOR', 'AUS', 'JPN', 'KOR', 'MEX', 'BRA', 'COL',
             'IND', 'ZAF', 'GHA', 'KEN', 'NGA', 'CHN', 'IDN', 'TUR')
SECTORS = ('S13', 'S1311')  # general government; central government
INDICATORS = {
    'revenue': [('G1', 'G1_T')],
    'expenditure': [('G2M', 'G2_T'), ('G2M', 'G2M_T')],
}
FIELDS = ('provider', 'country_iso3', 'year', 'period', 'series', 'value',
          'unit', 'frequency', 'disaggregation', 'source_status',
          'observation_kind', 'source_url', 'retrieved_utc')
SOURCE='IMF GFS Statement of Operations'


def download(url, *, tries=2, max_bytes=2_000_000):
    req = urllib.request.Request(url, headers={
        'Accept': 'text/csv', 'Accept-Encoding': 'identity',
        'User-Agent': 'GlobalEconomicPolicyResearch/1.0 (public economic research)'})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=50) as response:
                payload = response.read(max_bytes + 1)
                if len(payload)>max_bytes:
                    raise ValueError(f'Response exceeds {max_bytes} bytes, refusing unbounded download')
                if 'csv' not in response.headers.get('Content-Type', '').lower():
                    raise ValueError('IMF API did not return CSV')
                return payload
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            if attempt + 1 >= tries or (isinstance(e, urllib.error.HTTPError) and e.code in (400, 403, 404)):
                raise
            time.sleep(1.0 + attempt)


def parse(payload, *, country, sector, group, indicator, start, end, url, timestamp):
    source = csv.DictReader(io.StringIO(payload.decode('utf-8-sig')))
    expected = {'COUNTRY','SECTOR','GFS_GRP','INDICATOR','TYPE_OF_TRANSFORMATION',
                'FREQUENCY','TIME_PERIOD','OBS_VALUE', 'UNIT'}
    if not expected.issubset(source.fieldnames or []):
        raise ValueError(f'Unexpected IMF SDMX CSV columns: {source.fieldnames}')
    results=[]
    for row in source:
        # IMF currently ignores some startPeriod/endPeriod URL filters: enforce locally.
        if any((row.get(k) or '').strip() != v for k,v in (
            ('COUNTRY', country), ('SECTOR', sector), ('GFS_GRP', group),
            ('INDICATOR', indicator), ('TYPE_OF_TRANSFORMATION', 'POGDP_PT'),
            ('FREQUENCY', 'A'))):
            continue
        y=(row.get('TIME_PERIOD') or '').strip()
        if not (len(y)==4 and y.isdecimal() and start<=int(y)<=end):
            continue
        value_text=(row.get('OBS_VALUE') or '').strip()
        if not value_text:
            continue # Missing != numerical zero
        try: val=float(value_text)
        except ValueError:continue
        if not math.isfinite(val):continue
        if (row.get('UNIT') or '').strip() != 'PT':
            raise ValueError('Unexpected IMF unit; cannot safely interpret as percentage points')
        scale=(row.get('SCALE') or '0').strip()
        if scale not in ('', '0'):
            raise ValueError(f'Unexpected scale {scale!r}; no silent scaling')
        classification={k:row.get(k,'') for k in (
            'SECTOR','GFS_GRP','INDICATOR','TYPE_OF_TRANSFORMATION','UNIT','SCALE',
            'STATUS','NATURE_OF_DATA','BASES_OF_RECORDING_CASH_NON_CASH',
            'BASES_OF_RECORDING_GROSS_NET','DERIVATION_TYPE','OVERLAP','VALUATION')}
        status=row.get('STATUS') or ''
        results.append({'provider':SOURCE, 'country_iso3':country,
            'year':int(y), 'period':y,
            'series':f'GFS_SOO.{sector}.{group}.{indicator}.POGDP_PT.A',
            'value':val, 'unit':'percent_of_gdp', 'frequency':'annual',
            'disaggregation':json.dumps(classification, sort_keys=True),
            'source_status':status, 'observation_kind':row.get('NATURE_OF_DATA') or 'unclassified',
            'source_url':url,'retrieved_utc':timestamp})
    years=[r['year'] for r in results]
    if len(set(years))!=len(years):
        raise ValueError(f'Duplicate IMF annual observation for {country} {sector} {group} {indicator}')
    return results


def run(*, out, start=1970, end=2025, countries=COUNTRIES, get_bytes=download):
    if not (1900<=start<=end<=2100): raise ValueError('Invalid historical year range')
    folder=Path(out);(folder/'processed').mkdir(parents=True,exist_ok=True)
    stamp=dt.datetime.now(dt.timezone.utc).isoformat()
    rows=[];audit=[];failures=[];manifest=[]
    for country in countries:
        if not (len(country)==3 and country.isalpha() and country.upper()==country):
            raise ValueError(f'Invalid country code {country!r}')
        for sector in SECTORS:
            for measure,options in INDICATORS.items():
                found=False
                for group,indicator in options:
                    key=f'{country}.{sector}.{group}.{indicator}.POGDP_PT.A'
                    url=f'{ROOT}/{key}?startPeriod={start}&endPeriod={end}'
                    try:
                        raw=get_bytes(url)
                        subset=parse(raw,country=country,sector=sector,
                                     group=group,indicator=indicator,start=start,end=end,
                                     url=url,timestamp=stamp)
                        if not subset:
                            reason='no numeric records within year range'
                            continue
                        sha=hashlib.sha256(raw).hexdigest()
                        manifest.append({'key':key,'sha256':sha,'bytes':len(raw),'url':url,
                                         'years':[min(r['year'] for r in subset),max(r['year'] for r in subset)],
                                         'observations':len(subset)})
                        rows.extend(subset)
                        audit.append({'country':country,'sector':sector,'measure':measure,
                                      'indicator':indicator,'observations':len(subset),
                                      'earliest_year':min(r['year'] for r in subset),
                                      'latest_year':max(r['year'] for r in subset)})
                        found=True
                        break
                    except (urllib.error.HTTPError,urllib.error.URLError, TimeoutError,
                            ValueError, UnicodeError, csv.Error) as e:
                        reason=f'{type(e).__name__}: {str(e)[:150]}'
                if not found:
                    failures.append({'country':country,'sector':sector,'measure':measure,
                                     'reason':reason})
    rows.sort(key=lambda r:(r['country_iso3'],r['series'],r['year']))
    names={(r['country_iso3'],r['series'],r['year']) for r in rows}
    if len(names)!=len(rows):raise ValueError('Duplicate row keys across IMF series')
    panel=folder/'processed'/'imf_gfs_panel.csv'
    with panel.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader();writer.writerows(rows)
    coverage={m:len([r for r in audit if r['measure']==m]) for m in INDICATORS}
    report={'dataset':'IMF.STA:GFS_SOO','version':'12.0.0','requested_years':[start,end],
      'retrieved_utc':stamp,'countries_requested':list(countries),
      'countries_observed':sorted({r['country_iso3'] for r in rows}),
      'earliest_year_observed':min((r['year'] for r in rows),default=None),
      'latest_year_observed':max((r['year'] for r in rows),default=None),
      'observations':len(rows),'series_observed':len(audit),
      'measures':coverage,'successful_series':audit,'missing_or_unavailable_series':failures,
      'manifest':manifest,'panel_sha256':hashlib.sha256(panel.read_bytes()).hexdigest(),
      'errors':[],'conflicts':[],'research_ready':False,
      'notes':['1970-2025 enforced locally because IMF API may ignore date URL filters.',
               'General versus central government kept as distinct series; never automatically aggregate.',
               'Some country-series combinations genuinely absent; missing != 0.',
               'Percent-of-GDP transformations are not identified exogenous policy shocks.',
               'Original IMF classification codes preserved in disaggregation JSON.',
               'Source revisions and original IMF terms must be reviewed before redistribution.']}
    if len(rows)<100 or len(report['countries_observed'])<3 or any(v==0 for v in coverage.values()):
        report['errors'].append({'error':'Coverage insufficient for expanded validated import',
           'requirements':'at least 100 annual rows, 3 countries, nonempty revenue and expenditure'})
    (folder/'run_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',default='data/imf_gfs')
    p.add_argument('--start',type=int,default=1970)
    p.add_argument('--end',type=int,default=2025)
    p.add_argument('--countries',default=','.join(COUNTRIES))
    a=p.parse_args()
    countries=tuple(x.strip().upper() for x in a.countries.split(',') if x.strip())
    report=run(out=a.out,start=a.start,end=a.end,countries=countries)
    print(json.dumps({'observations':report['observations'],
      'countries':len(report['countries_observed']),
      'years':[report['earliest_year_observed'],report['latest_year_observed']],
      'measures':report['measures'],'unavailable':len(report['missing_or_unavailable_series']),
      'errors':report['errors']},indent=2))
    if report['errors']:raise SystemExit('IMF GFS coverage validation failed; see run_report.json')

if __name__=='__main__':main()
