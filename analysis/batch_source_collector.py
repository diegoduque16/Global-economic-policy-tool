#!/usr/bin/env python3
"""Audited, fail-isolated public-data batch collector (stdlib only).

Stages source-native data for later harmonization. It does not alter the
existing research warehouse or infer causal effects from raw records.
"""
import argparse
import csv
import hashlib
import io
import json
import pathlib
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

ALLOWED_HOSTS = {'api.worldbank.org', 'data.bls.gov', 'api.census.gov', 'www2.census.gov'}
P0 = {'P0', 'P1', 'P2'}
WDI = {
    'NY.GDP.MKTP.KD.ZG': 'real_gdp_growth_pct',
    'NY.GDP.PCAP.KD.ZG': 'real_gdp_per_capita_growth_pct',
    'NE.GDI.FTOT.ZS': 'gross_fixed_capital_formation_pct_gdp',
    'GC.TAX.TOTL.GD.ZS': 'tax_revenue_pct_gdp',
    'GC.XPN.TOTL.GD.ZS': 'expense_pct_gdp',
    'SL.UEM.TOTL.ZS': 'unemployment_pct_ilo_modeled',
    'SL.TLF.CACT.ZS': 'labor_force_participation_pct_ilo_modeled',
    'SI.POV.GINI': 'gini_index',
    'SP.POP.TOTL': 'population',
    'FP.CPI.TOTL.ZG': 'consumer_price_inflation_pct',
    'BN.CAB.XOKA.GD.ZS': 'current_account_balance_pct_gdp',
    'NE.EXP.GNFS.ZS': 'exports_pct_gdp',
}


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def fetch(url, *, maximum=25_000_000):
    parts = urlsplit(url)
    host = parts.hostname
    if host not in ALLOWED_HOSTS or parts.scheme != 'https':
        raise ValueError('Unapproved endpoint: ' + url)
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={
                'User-Agent': 'GlobalEconomicPolicyResearch/2.1 (public-data scientific audit)',
                'Accept': 'application/json,text/csv,application/zip',
            }), timeout=75) as response:
                if urlsplit(response.geturl()).hostname != host:
                    raise ValueError('Cross-host source redirect')
                data = response.read(maximum + 1)
                if len(data) > maximum or not data:
                    raise ValueError('Empty/oversized payload')
                return data
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            if attempt == 2:
                raise RuntimeError(f'{type(error).__name__}: {str(error)[:180]}') from error
            time.sleep(2 * (attempt + 1))


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as dst:
        writer = csv.DictWriter(dst, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    return {'file': str(path.relative_to(path.parents[1])), 'sha256': file_sha(path),
            'rows': len(rows), 'bytes': path.stat().st_size}


def catalog_audit(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError('Catalog is empty')
    required = {'dataset', 'priority', 'official_source_url', 'theme'}
    if not required.issubset(rows[0]):
        raise ValueError('Catalog missing required columns')
    seen = set()
    for index, row in enumerate(rows, 2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f'Malformed catalog row {index}')
        key = row['dataset'].strip().casefold()
        if not key or key in seen or row['priority'] not in P0:
            raise ValueError(f'Invalid/duplicate catalog row {index}')
        seen.add(key)
    return rows


def worldbank(out):
    records, requests = [], []
    for indicator, name in WDI.items():
        indicator_row_count = 0
        page = 1
        while True:
            url = f'https://api.worldbank.org/v2/country/all/indicator/{indicator}?' + urlencode(
                {'format': 'json', 'per_page': 20000, 'page': page,
                 'date': f'1960:{datetime.now(timezone.utc).year}'})
            blob = fetch(url)
            body = json.loads(blob)
            if not isinstance(body, list) or len(body) != 2 or not isinstance(body[1], list):
                raise ValueError('World Bank API schema changed: ' + indicator)
            meta = body[0]
            requests.append({'url': url, 'sha256': sha(blob), 'records': len(body[1])})
            indicator_row_count += len(body[1])
            for record in body[1]:
                c = record.get('country') or {}
                records.append({
                    'indicator': indicator, 'indicator_label': name,
                    'country_id': record.get('countryiso3code') or c.get('id'),
                    'country_name': c.get('value'), 'year': record.get('date'),
                    'value': record.get('value'), 'unit': record.get('unit') or '',
                    'obs_status': record.get('obs_status') or '',
                })
            if page >= int(meta.get('pages', 1)):
                break
            page += 1
            if page > 100:
                raise ValueError('World Bank page limit reached')
        if indicator_row_count < 5000:
            raise ValueError(f'World Bank {indicator}: unexpectedly short historical coverage ({indicator_row_count} rows)')
    if len(records) < 10000:
        raise ValueError('Unexpectedly few World Bank source rows')
    saved = write_csv(out / 'world_bank_wdi_expanded_native.csv', [
        'indicator', 'indicator_label', 'country_id', 'country_name',
        'year', 'value', 'unit', 'obs_status'], records)
    return dict(source='World Bank WDI expanded indicators',
                catalog_entry='World Bank World Development Indicators (32 selected)',
                status='STAGED_PARTIAL_INDICATORS_NOT_IN_WAREHOUSE',
                note='Source rows include missing values and country aggregates; not 193k valid country-year estimates.',
                requests=requests, **saved)


def qcew(out):
    parts, requests = [], []
    for year in range(2020, 2026):
        for quarter in range(1, 5):
            url = f'https://data.bls.gov/cew/data/api/{year}/{quarter}/area/US000.csv'
            try:
                blob = fetch(url, maximum=12_000_000)
            except Exception as error:
                requests.append({'url': url, 'error': str(error), 'status': 'missing_period'})
                continue
            reader = csv.DictReader(io.StringIO(blob.decode('utf-8-sig')))
            if not reader.fieldnames or 'year' not in reader.fieldnames or 'qtr' not in reader.fieldnames:
                raise ValueError('QCEW schema changed')
            rows = list(reader)
            for record in rows:
                record['requested_year'] = year
                record['requested_quarter'] = quarter
            parts.extend(rows)
            requests.append({'url': url, 'sha256': sha(blob), 'records': len(rows)})
    if len(parts) < 100:
        raise ValueError('Too few QCEW source rows')
    columns = sorted({key for row in parts for key in row})
    saved = write_csv(out / 'bls_qcew_us_national_2020_2025_native.csv', columns, parts)
    return dict(source='BLS Quarterly Census of Employment and Wages',
                catalog_entry='BLS Quarterly Census of Employment and Wages (QCEW)',
                status='STAGED_NATIONAL_ONLY_NOT_IN_WAREHOUSE',
                requests=requests, **saved)


def inspect_cbp_zip(blob, year):
    """Validate original Census state release without filtering/double-counting its categories."""
    if not blob.startswith(b'PK'):
        raise ValueError(f'CBP {year}: expected Census ZIP, got another response type')
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            members = [member for member in archive.infolist()
                       if not member.is_dir() and member.filename.lower().endswith('.csv')]
            expected = f'cbp{year % 100:02d}st.csv'
            exact = [item for item in members if pathlib.PurePosixPath(item.filename).name.lower() == expected]
            if len(exact) == 1:
                member = exact[0]
            elif len(members) == 1:
                member = members[0]
            else:
                raise ValueError(f'CBP {year}: ambiguous CSV members {[m.filename for m in members[:5]]}')
            if member.file_size < 10_000 or member.file_size > 350_000_000:
                raise ValueError(f'CBP {year}: unexpected uncompressed size {member.file_size}')
            if member.flag_bits & 0x1:
                raise ValueError(f'CBP {year}: encrypted CSV member')
            with archive.open(member) as source:
                csvreader = csv.reader(io.TextIOWrapper(source, encoding='utf-8-sig', newline=''))
                header = next(csvreader)
                fields = [name.strip().lower() for name in header]
                if len(fields) < 6 or len(set(fields)) != len(fields):
                    raise ValueError(f'CBP {year}: invalid CSV column layout')
                if 'emp' not in fields or not any('naics' in field for field in fields):
                    raise ValueError(f'CBP {year}: missing employment/industry fields: {fields[:16]}')
                count = 0
                for record in csvreader:
                    if len(record) != len(header):
                        raise ValueError(f'CBP {year}: ragged CSV record at data row {count+1}')
                    count += 1
            if count < 500:
                raise ValueError(f'CBP {year}: unexpectedly few source rows ({count})')
            return {'csv_member': member.filename, 'rows': count,
                    'fields': header, 'uncompressed_csv_bytes': member.file_size}
    except (zipfile.BadZipFile, UnicodeError, EOFError) as error:
        raise ValueError(f'CBP {year}: ZIP/CSV unreadable: {type(error).__name__}') from error


def cbp(out, *, download=fetch, years=range(2019, 2024)):
    """Archive original state-industry CBP releases, not invalidly summed state totals.

    Free official bulk ZIPs avoid the Census API-key requirement. Archives carry
    industry, LFO, and employment-size detail; do NOT sum rows as state totals.
    """
    out.mkdir(parents=True, exist_ok=True)
    target = out / 'census_cbp_state_raw_2019_2023.zip'
    requests, total = [], 0
    with tempfile.TemporaryDirectory(dir=out) as staging_dir:
        staged = pathlib.Path(staging_dir) / target.name
        with zipfile.ZipFile(staged, 'w', compression=zipfile.ZIP_STORED,
                             allowZip64=True) as bundle:
            for year in years:
                name = f'cbp{year % 100:02d}st.zip'
                url = f'https://www2.census.gov/programs-surveys/cbp/datasets/{year}/{name}'
                blob = download(url, maximum=19_000_000)
                summary = inspect_cbp_zip(blob, year)
                bundle.writestr(name, blob)
                requests.append({'year': year, 'url': url, 'member': name,
                                 'raw_zip_sha256': sha(blob), 'raw_zip_bytes': len(blob),
                                 **summary})
                total += summary['rows']
        if not requests:
            raise ValueError('CBP archive contained no years')
        # One validated output file: the existing workflow hashes this entire
        # bundle, including all original year-specific source ZIP archives.
        with zipfile.ZipFile(staged) as bundle:
            for entry in requests:
                if sha(bundle.read(entry['member'])) != entry['raw_zip_sha256']:
                    raise ValueError(f"CBP {entry['year']}: bundled source checksum mismatch")
        staged.replace(target)
    return {'source': 'Census County Business Patterns',
            'catalog_entry': 'Census County Business Patterns (CBP)',
            'status': 'STAGED_STATE_INDUSTRY_SOURCE_NATIVE_NOT_IN_WAREHOUSE',
            'scope': 'Full Census state-level, industry-detail releases 2019–2023; unfiltered raw data',
            'note': 'Rows include industry, legal form and size classes; these are NOT additive state employment totals.',
            'file': str(target.relative_to(target.parents[1])),
            'sha256': file_sha(target), 'bytes': target.stat().st_size,
            'rows': total, 'requests': requests}


COLLECTORS = {'worldbank_wdi_expanded': worldbank,
              'bls_qcew_us_national': qcew,
              'census_cbp_states': cbp}


def run(args):
    out = pathlib.Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    rows = catalog_audit(pathlib.Path(args.catalog))
    selected = [name.strip() for name in args.sources.split(',') if name.strip()] if args.sources else list(COLLECTORS)
    unknown = set(selected) - set(COLLECTORS)
    if unknown:
        raise ValueError('Unknown collectors: ' + ','.join(sorted(unknown)))
    results, failures = [], []
    for name in selected:
        try:
            report = COLLECTORS[name](out / 'source_native')
            report['collector'] = name
            results.append(report)
            print('STAGED', name, report['rows'], flush=True)
        except Exception as error:
            failures.append({'collector': name, 'error': str(error),
                             'status': 'FAILED_NOT_STAGED'})
            print('FAILED', name, str(error), flush=True)
    covered = {item['catalog_entry'] for item in results}
    queue = [{'dataset': row['dataset'], 'priority': row['priority'],
              'status': 'NEW_SOURCE_STAGED_NOT_IN_WAREHOUSE' if row['dataset'] in covered
                        else 'PENDING_OR_PREVIOUSLY_INGESTED_NOT_REASSESSED',
              'theme': row['theme']} for row in rows]
    write_csv(out / 'catalog_queue.csv', ['dataset', 'priority', 'status', 'theme'], queue)
    audit = {'created_utc': datetime.now(timezone.utc).isoformat(),
             'catalog_entries': len(rows),
             'catalog_p0': sum(row['priority'] == 'P0' for row in rows),
             'selected': selected, 'successful': results, 'failures': failures,
             'warehouse_modified': False,
             'note': 'Collection is not warehouse integration; staged scopes are not full catalog coverage.'}
    dump(out / 'audit.json', audit)
    if not results:
        raise SystemExit('No collectors succeeded')
    if failures and args.require_all:
        raise SystemExit('Some sources failed; partial artifact preserved')


def self_test():
    """Deterministic tests; never downloads external material."""
    from tempfile import TemporaryDirectory

    def fake_download(url, *, maximum):
        year = int(url.split('/datasets/')[1].split('/')[0])
        content = ('fipstate,naics,emp,est,payann,lfo,empflag\n' +
                   ''.join(f'06,00,{i},1,100,001,N\n' for i in range(520))).encode()
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr(f'cbp{year%100:02d}st.csv', content)
        return mem.getvalue()

    with TemporaryDirectory() as td:
        dest = pathlib.Path(td) / 'source_native'
        report = cbp(dest, download=fake_download)
        assert report['rows'] == 2600
        assert len(report['requests']) == 5
        assert file_sha(dest / pathlib.Path(report['file']).name) == report['sha256']
        with zipfile.ZipFile(dest / pathlib.Path(report['file']).name) as bundle:
            assert len(bundle.namelist()) == 5
            for item in report['requests']:
                assert sha(bundle.read(item['member'])) == item['raw_zip_sha256']
        try:
            inspect_cbp_zip(b'<html>Invalid key</html>', 2023)
        except ValueError:
            pass
        else:
            raise AssertionError('Non-ZIP payload accepted')
    print('PASS offline Census ZIP, 5-year archive and integrity tests', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--catalog', default='open_data/expanded_source_catalog.csv')
    parser.add_argument('--output', default='batch_source_results')
    parser.add_argument('--sources', default='')
    parser.add_argument('--require-all', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    options = parser.parse_args()
    if options.self_test:
        self_test()
    else:
        run(options)
