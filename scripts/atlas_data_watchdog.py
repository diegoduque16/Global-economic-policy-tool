#!/usr/bin/env python3
"""Evidence-first automation for the Global Economic Policy Atlas.

Observes existing ingestion, validates actual saved numeric values, and backs up
completed batch artifacts. Never sums source-native, canonical and joined data
as though mutually exclusive. Never publishes private observation-level data.
Python standard library; rclone and gh are external commands in GitHub Actions.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tarfile
from typing import Any

REMOTE_ROOT = 'onedrive:Diego/economic research database'
ILOSTAT_ROOT = REMOTE_ROOT + '/atlas/ilostat-bulk-v1'
CANONICAL_SHA256 = '8f6ae9a3674045851c0726aa6415014389082a6309e784c77753542d7147072f'
ILOSTAT_WORKFLOW = 'atlas-ilostat-bulk-resumable-ingestion.yml'
BATCH_WORKFLOW = 'expand-economic-sources-batch.yml'
WDI_WORKFLOW = 'world-bank.yml'
WDI_REMOTE_ROOT = REMOTE_ROOT + '/atlas/wdi-verified-source-archives-v1'
WDI_MIN_ROWS = 100_000
CANONICAL_EXPECTED = 2_780_117
WID_EXPECTED = 21_430
ISO3 = re.compile(r'[A-Z]{3}\Z')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], *, timeout: int = 1800) -> str:
    out = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if out.returncode:
        raise RuntimeError(f'{cmd[0]} {cmd[1]} failed ({out.returncode}): {out.stderr[-600:]}')
    return out.stdout


def load_json(path: Path) -> dict:
    result = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(result, dict):
        raise ValueError(f'Expected JSON object in {path.name}')
    return result


def ensure_sha(sha: str) -> str:
    s = sha.strip().lower()
    if not SHA256.fullmatch(s):
        raise ValueError('Invalid SHA256 evidence')
    return s


def check_file_manifest(path: Path, manifest: dict, remote_name: str) -> None:
    record = manifest.get(remote_name)
    if isinstance(record, dict):
        expected = record.get('sha256')
    else:
        expected = record
    if not expected or digest(path) != ensure_sha(str(expected)):
        raise ValueError(f'Actual source bytes do not match pinned SHA256: {remote_name}')


def iso_time(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError('Expected ISO timestamp')
    x = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if x.tzinfo is None:
        raise ValueError('Timestamp lacks UTC offset')
    return x.astimezone(timezone.utc)


def verify_ilostat(state: dict, now: datetime) -> dict:
    if state.get('schema_version') != 1 or not isinstance(state.get('downloaded'), dict):
        raise ValueError('Unsupported ILOSTAT source checkpoint')
    toc = state.get('latest_toc', {})
    if not isinstance(toc, dict) or not isinstance(toc.get('entries'), int) or toc['entries'] < 200:
        raise ValueError('ILOSTAT official TOC count unavailable')
    datasets = state['downloaded']
    count = 0
    total_source_rows = 0
    total_bytes = 0
    for key, row in datasets.items():
        if not re.fullmatch(r'[A-Za-z0-9_]{4,100}', key) or not isinstance(row, dict):
            raise ValueError('Invalid table ID/source evidence in ILOSTAT checkpoint')
        if row.get('state') != 'NATIVE_NOT_HARMONIZED':
            raise ValueError('Unexpected ILOSTAT harmonization status')
        n = row.get('records_validated')
        b = row.get('compressed_bytes')
        if not isinstance(n, int) or n < 1 or not isinstance(b, int) or b < 1:
            raise ValueError('No validated physical source-row evidence')
        ensure_sha(row.get('sha256', ''))
        remote = row.get('remote', '')
        if not isinstance(remote, str) or not remote.startswith(ILOSTAT_ROOT + '/indicator-data/' + key + '/'):
            raise ValueError('ILOSTAT source location outside expected private root')
        total_source_rows += n
        total_bytes += b
        count += 1
    if count > toc['entries']:
        raise ValueError('More verified files than TOC entries')
    stamp = iso_time(state['last_run_utc'])
    if stamp > now + timedelta(minutes=10):
        raise ValueError('Future ILOSTAT checkpoint timestamp')
    age = (now - stamp).total_seconds() / 3600
    return {
        'status': 'VERIFIED_RAW_SOURCE_FILES_NOT_HARMONIZED',
        'verified_compressed_files': count,
        'official_bulk_files': toc['entries'],
        'remaining_bulk_files': toc['entries'] - count,
        'validated_raw_csv_rows_NOT_all_numeric': total_source_rows,
        'verified_compressed_bytes': total_bytes,
        'checkpoint_age_hours': round(age, 2),
        'latest_checkpoint_utc': stamp.isoformat(),
        'raw_values_joined_to_warehouse': False,
    }


def verify_wid(db_path: Path, sha_manifest: dict, validation: dict) -> dict:
    check_file_manifest(db_path, sha_manifest, 'wid_23country_values.sqlite')
    with sqlite3.connect(f'file:{db_path.resolve()}?mode=ro', uri=True) as db:
        n = db.execute('SELECT COUNT(*) FROM wid_numeric_observations').fetchone()[0]
        actual_reals = db.execute("SELECT COUNT(*) FROM wid_numeric_observations WHERE typeof(value_fraction)='real'").fetchone()[0]
        countries = db.execute('SELECT COUNT(DISTINCT iso3) FROM wid_numeric_observations').fetchone()[0]
        pairs = db.execute('SELECT COUNT(*) FROM wid_distribution_panel WHERE posttax_minus_pretax_percentage_points IS NOT NULL').fetchone()[0]
        integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
    if n != WID_EXPECTED or actual_reals != n or countries != 23 or pairs != 4408 or integrity != 'ok':
        raise ValueError(f'WID actual numerical database unexpectedly changed: {n}/{actual_reals}/{countries}/{pairs}')
    for field, expected in [('actual_numeric_observation_rows_in_sqlite', n), ('country_count', countries), ('actual_matched_pretax_posttax_pairs', pairs)]:
        if validation.get(field) != expected:
            raise ValueError('Private WID validation manifest contradicts current SQLite content')
    return {'status': 'ACTUAL_SQLITE_NUMERIC_VALIDATED', 'observations': n, 'countries': countries, 'pretax_posttax_pairs': pairs, 'real_sqlite_type': True}


def verify_joined(path: Path, sha_manifest: dict, report: dict) -> dict:
    check_file_manifest(path, sha_manifest, 'wid_wdi_country_year_numeric.csv')
    countries: set[str] = set()
    unique: set[tuple[str, int]] = set()
    cells = 0
    years: set[int] = set()
    with path.open(newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        if len(set(fields)) != len(fields) or not {'iso3', 'year', 'top1_pretax_income_share', 'gdp_growth_pct'}.issubset(fields):
            raise ValueError('Expected real WID/WDI measurement columns missing')
        measures = [x for x in fields if x not in ('iso3', 'year')]
        for row in reader:
            c = row['iso3']
            y = row['year']
            if not ISO3.fullmatch(c) or not re.fullmatch(r'\d{4}', y):
                raise ValueError('Invalid country/year in combined measurement')
            k = (c, int(y))
            if k in unique:
                raise ValueError('Duplicate combined country/year')
            unique.add(k)
            countries.add(c)
            years.add(int(y))
            actual = 0
            for metric in measures:
                v = row.get(metric)
                if v is None:
                    raise ValueError('Malformed joined numeric CSV')
                if v != '':
                    try:
                        x = float(v)
                    except ValueError as e:
                        raise ValueError('Nonnumeric joined value') from e
                    if not math.isfinite(x):
                        raise ValueError('Nonfinite joined value')
                    cells += 1
                    actual += 1
            if actual < 2:
                raise ValueError('Joined record lacks two separate observed values')
    if (len(countries), len(unique), cells) != (report.get('countries_with_both_sources'), report.get('country_year_rows_with_both_sources'), report.get('total_matched_numeric_cells')):
        raise ValueError('Physical joined values differ from published audit report')
    if report.get('policy_reforms_joined') != 0 or report.get('causal_effects_estimated') != 0:
        raise ValueError('Joined panel incorrectly labels descriptive analysis as causal')
    if not years or len(countries) < 10 or cells < 100:
        raise ValueError('Unexpectedly small joined numeric coverage')
    return {'status': 'ACTUAL_JOINED_NUMERIC_CSV_VALIDATED_NOT_CAUSAL', 'countries': len(countries), 'country_year_records': len(unique), 'observed_numeric_cells': cells, 'year_min': min(years), 'year_max': max(years)}


def verify_canonical_source_marker(path: Path) -> dict:
    s = path.read_text().strip().split()[0].lower()
    if s != CANONICAL_SHA256:
        raise ValueError('Canonical source archive checksum marker differs from original verified snapshot')
    return {'status': 'PINNED_SOURCE_CHECKSUM_MARKER_ONLY', 'canonical_observations_previously_verified': CANONICAL_EXPECTED, 'source_archive_sha256': s, 'full_archive_bytes_checked_this_run': False}


def choose_ingestion_repair(runs: list, now: datetime) -> dict:
    """Avoid overlapping or repeatedly dispatching a known active import."""
    if not isinstance(runs, list):
        raise ValueError('Invalid workflow run list')
    candidates = [r for r in runs if isinstance(r, dict) and r.get('event') in ('schedule', 'workflow_dispatch', 'push') and r.get('created_at')]
    if not candidates:
        return {'action': 'dispatch_missing', 'reason': 'No recorded ILOSTAT run'}
    newest = max(candidates, key=lambda x: iso_time(x['created_at']))
    age = (now - iso_time(newest['created_at'])).total_seconds() / 3600
    if age < -0.1:
        raise ValueError('Future scheduled ILOSTAT run')
    if newest.get('status') in ('queued', 'in_progress', 'waiting', 'requested', 'pending'):
        return {'action': 'none', 'reason': 'ILOSTAT ingestion already active', 'latest_run_age_hours': round(age, 2)}
    if age < 32:
        return {'action': 'none', 'reason': 'Existing ILOSTAT scheduler recently ran', 'latest_run_age_hours': round(age, 2)}
    return {'action': 'dispatch_stale', 'reason': 'No ingestion run for more than 32 hours', 'latest_run_age_hours': round(age, 2)}


def sample_ilostat_remote(state: dict, max_bytes: int = 16 * 1024 * 1024) -> dict:
    eligible = [(key, val) for key, val in sorted(state['downloaded'].items())
                if val['compressed_bytes'] <= max_bytes]
    if not eligible:
        return {'status': 'NO_SMALL_FILE_AVAILABLE', 'samples': 0}
    key, item = eligible[0]
    proc = subprocess.Popen(['rclone', 'cat', item['remote']], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    h = hashlib.sha256()
    total = 0
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            proc.kill()
            raise ValueError('Sample file exceeded previously verified file-size bound')
        h.update(chunk)
    err = proc.stderr.read() if proc.stderr else b''
    if proc.wait(timeout=120) != 0:
        raise RuntimeError('Private source sample could not be read from OneDrive: ' + err.decode(errors='replace')[-300:])
    if total != item['compressed_bytes'] or h.hexdigest() != item['sha256']:
        raise ValueError('Private ILOSTAT gzip source hash or byte count changed')
    return {'status': 'SAMPLED_PRIVATE_BYTES_SHA256_VALIDATED', 'samples': 1, 'dataset_id': key, 'bytes_checked': total}


def github_runs(repo: str, workflow: str, count: int = 15) -> list:
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Unexpected GitHub repository path')
    if workflow not in (ILOSTAT_WORKFLOW, BATCH_WORKFLOW, WDI_WORKFLOW):
        raise ValueError('Unapproved workflow')
    response = json.loads(run(['gh', 'api', f'repos/{repo}/actions/workflows/{workflow}/runs?per_page={count}'], timeout=90))
    if not isinstance(response, dict) or not isinstance(response.get('workflow_runs'), list):
        raise ValueError('GitHub API did not return workflow run list')
    return response['workflow_runs']


def dispatch_ilostat(repo: str) -> None:
    run(['gh', 'api', '--method', 'POST', f'repos/{repo}/actions/workflows/{ILOSTAT_WORKFLOW}/dispatches', '-f', 'ref=main', '-f', 'inputs[batch_size]=20'], timeout=90)


def verify_batch_archive(folder: Path, run_id: int) -> tuple[Path, Path, dict]:
    archive = folder / f'economic-batch-{run_id}.tar.gz'
    sha = folder / f'economic-batch-{run_id}.tar.gz.sha256'
    if not archive.is_file() or not sha.is_file():
        raise ValueError('Missing actual batch archive or checksum')
    line = sha.read_text().strip().split()
    if len(line) != 2 or line[0].lower() != digest(archive) or line[1] != archive.name:
        raise ValueError('Batch archive SHA256 mismatch')
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        if not members or len(members) > 500:
            raise ValueError('Suspicious source-batch archive count')
        for m in members:
            p = Path(m.name)
            # GNU tar includes its top-level directory as a separate member.
            # Permit that exact directory only; retain strict validation for every child.
            if m.name == 'batch_source_results' and m.isdir():
                continue
            if p.is_absolute() or '..' in p.parts or not m.name.startswith('batch_source_results/') or not (m.isfile() or m.isdir()):
                raise ValueError('Unsafe source-batch archive member')
        audit = [m for m in members if m.name == 'batch_source_results/audit.json' and m.isfile()]
        if len(audit) != 1:
            raise ValueError('Missing single batch source audit')
        with tar.extractfile(audit[0]) as file:
            data = json.load(file)
        if not isinstance(data, dict) or not isinstance(data.get('successful'), list):
            raise ValueError('Unrecognized batch source audit')
    return archive, sha, {'successful_source_files': len(data['successful']), 'raw_archive_sha256': digest(archive)}


def backup_recent_batch(repo: str, now: datetime, output: Path) -> dict:
    recent = github_runs(repo, BATCH_WORKFLOW)
    success = [x for x in recent if x.get('status') == 'completed' and x.get('conclusion') == 'success']
    if not success:
        return {'status': 'NO_SUCCESSFUL_BATCH_RUN_FOUND', 'note': 'Monthly collector exists, but no successful artifact is available in recent runs'}
    latest = max(success, key=lambda x: iso_time(x['created_at']))
    age = (now - iso_time(latest['created_at'])).total_seconds() / 86400
    if age > 70:
        return {'status': 'NO_RECENT_BATCH_TO_BACK_UP', 'age_days': round(age, 1)}
    rid = latest.get('id')
    if not isinstance(rid, int) or rid <= 0:
        raise ValueError('Invalid source run id')
    remote = f'{REMOTE_ROOT}/source-native-batch-archives/run-{rid}'
    # Fast path only after checking backed-up archive is present and accompanied by a checksum.
    try:
        listed = json.loads(run(['rclone', 'lsjson', remote, '--files-only'], timeout=90))
    except RuntimeError as exc:
        if 'directory not found' in str(exc).lower() or 'path not found' in str(exc).lower():
            listed = []  # A genuinely missing backup folder is normal on first archive.
        else:
            raise
    expected = {f'economic-batch-{rid}.tar.gz', f'economic-batch-{rid}.tar.gz.sha256'}
    if isinstance(listed, list) and expected.issubset({z.get('Name') for z in listed}):
        sha_text = run(['rclone', 'cat', remote + '/' + f'economic-batch-{rid}.tar.gz.sha256'], timeout=90)
        parts = sha_text.strip().split()
        if len(parts) != 2 or parts[1] != f'economic-batch-{rid}.tar.gz' or not SHA256.fullmatch(parts[0].lower()):
            raise ValueError('Private backup source checksum is malformed')
        return {'status': 'PREVIOUSLY_BACKED_UP_SHA_MARKER_PRESENT', 'run_id': rid, 'sha256_marker': parts[0].lower(), 'note': 'Previously backed-up archive not redownloaded on this run'}
    # Treat a missing folder as empty; rclone lsjson on missing remote may exit nonzero.
    out = output / f'batch_{rid}'
    out.mkdir(parents=True, exist_ok=True)
    run(['gh', 'run', 'download', str(rid), '--repo', repo, '--name', f'economic-batch-archive-{rid}', '--dir', str(out)], timeout=600)
    archive, sha, audit = verify_batch_archive(out, rid)
    for file in (archive, sha):
        dest = remote + '/' + file.name
        run(['rclone', 'copyto', str(file), dest, '--retries', '3'], timeout=1800)
        # Round-trip full archive bytes, rather than trusting server file listings.
        h = hashlib.sha256()
        proc = subprocess.Popen(['rclone', 'cat', dest], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdout is not None
        for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b''):
            h.update(chunk)
        err = proc.stderr.read() if proc.stderr else b''
        if proc.wait(timeout=240) or h.hexdigest() != digest(file):
            raise RuntimeError('Remote source-batch SHA256 round-trip failed: ' + err.decode(errors='replace')[-100:])
    return {'status': 'NEW_SOURCE_NATIVE_ARCHIVE_BACKED_UP_AND_SHA256_VERIFIED', 'run_id': rid, **audit}



def select_wdi_run(runs: list, now: datetime) -> dict | None:
    """Only successful production scheduled WDI runs; reject smoke/test branches."""
    eligible = [x for x in runs
                if x.get('event') == 'schedule'
                and x.get('head_branch') == 'main'
                and x.get('status') == 'completed'
                and x.get('conclusion') == 'success']
    if not eligible:
        return None
    latest = max(eligible, key=lambda x: iso_time(x['created_at']))
    age = (now - iso_time(latest['created_at'])).total_seconds() / 86400
    if age < -0.01:
        raise ValueError('Future WDI source run')
    if age > 70:
        return None
    rid, number = latest.get('id'), latest.get('run_number')
    if type(rid) is not int or rid <= 0 or type(number) is not int or number <= 0:
        raise ValueError('Invalid WDI workflow run identity')
    return {'id': rid, 'run_number': number}


def validate_wdi_download(folder: Path) -> dict:
    """Check actual WDI API pages, value types, panel keys and source manifest."""
    folder = Path(folder)
    report = load_json(folder / 'run_report.json')
    if report.get('errors') != [] or report.get('conflicts') != []:
        raise ValueError('WDI source run reported retrieval errors or conflicts')
    if report.get('country_query') != 'all' or report.get('start') != 1960 or report.get('end', 0) < 2026:
        raise ValueError('WDI backup requires full historical global scheduled scope')
    manifest = report.get('manifest')
    if not isinstance(manifest, list) or not manifest or len(manifest) > 10000:
        raise ValueError('Invalid WDI raw API page manifest')
    if type(report.get('raw_pages')) is not int or len(manifest) != report['raw_pages']:
        raise ValueError('WDI raw API page count contradicts manifest')
    if type(report.get('observations')) is not int or report['observations'] < WDI_MIN_ROWS:
        raise ValueError('WDI numeric coverage fell below existing full-global floor')
    urls, names, indicators = set(), set(), set()
    raw_values = {}
    raw_source_rows = 0
    raw_skipped_rows = 0
    for entry in manifest:
        if not isinstance(entry, dict):
            raise ValueError('Malformed WDI raw page entry')
        name, url, indicator, sha = (entry.get(k) for k in ('file','url','indicator','sha256'))
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{5,160}',name) or not name.endswith('.json'):
            raise ValueError('Unsafe WDI source page filename')
        if not isinstance(url,str) or not url.startswith('https://api.worldbank.org/v2/country/'):
            raise ValueError('Unexpected WDI official raw source URL')
        if not isinstance(indicator,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{2,100}',indicator):
            raise ValueError('Invalid WDI indicator identifier')
        if name in names or url in urls:
            raise ValueError('Duplicate WDI raw page identity')
        names.add(name); urls.add(url); indicators.add(indicator)
        p = folder / 'raw' / name
        sha = ensure_sha(sha)
        match = re.fullmatch(re.escape(indicator) + r'_(\d+)_' + re.escape(sha[:12]) + r'\.json', name)
        if not match:
            raise ValueError('WDI raw filename does not bind page, indicator and source SHA')
        if p.is_symlink() or not p.is_file() or digest(p) != sha:
            raise ValueError('WDI original API response SHA256 mismatch')
        if type(entry.get('returned')) is not int or entry['returned'] < 0:
            raise ValueError('Invalid WDI raw page row count')
        payload = json.loads(p.read_bytes())
        if (not isinstance(payload,list) or len(payload)!=2
                or not isinstance(payload[0],dict) or not isinstance(payload[1],list)
                or type(payload[0].get('page')) is not int
                or payload[0]['page'] != int(match.group(1))
                or len(payload[1]) != entry['returned']):
            raise ValueError('WDI original API page response conflicts with manifest')
        raw_source_rows += len(payload[1])
        for source_row in payload[1]:
            if not isinstance(source_row,dict) or not isinstance(source_row.get('indicator'),dict) or source_row['indicator'].get('id') != indicator:
                raise ValueError('WDI source observation belongs to unexpected indicator')
            code, year, value = (source_row.get(k) for k in ('countryiso3code','date','value'))
            # Mirror existing WDI ingestion's legitimate exclusion of missing
            # values and non-ISO3-format records, without filling them with zero.
            if (not isinstance(code,str) or len(code)!=3 or not code.isalpha()
                    or not isinstance(year,str) or len(year)!=4 or not year.isdigit()
                    or value is None):
                raw_skipped_rows += 1
                continue
            try:
                numeric = float(value)
            except (TypeError,ValueError,OverflowError) as exc:
                raise ValueError('Invalid WDI original numeric source observation') from exc
            if not math.isfinite(numeric):
                raise ValueError('Nonfinite WDI original numeric source observation')
            key=(code.upper(),int(year),indicator)
            if key in raw_values and raw_values[key]!=numeric:
                raise ValueError('Conflicting WDI original numeric observation keys')
            raw_values[key]=numeric
    rawdir = folder / 'raw'
    if {p.name for p in rawdir.iterdir()} != names or any(p.is_symlink() for p in rawdir.iterdir()):
        raise ValueError('Untracked WDI raw source file present')
    panel = folder / 'processed' / 'wdi_panel.csv'
    if panel.is_symlink() or not panel.is_file():
        raise ValueError('Missing WDI normalized numeric source panel')
    count, keys, geographies, years = 0, set(), set(), set()
    with panel.open(encoding='utf-8',newline='') as fh:
        reader = csv.DictReader(fh)
        required = {'country_iso3','year','indicator','value','unit','source','source_url','downloaded_at_utc'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError('WDI normalized panel missing source provenance columns')
        for row in reader:
            code, indicator = row['country_iso3'], row['indicator']
            if not ISO3.fullmatch(code or '') or indicator not in indicators:
                raise ValueError('Invalid WDI country or unmanifested series')
            try:
                year = int(row['year'])
                value = float(row['value'])
            except (TypeError,ValueError,OverflowError) as exc:
                raise ValueError('WDI invalid numeric row') from exc
            if not report['start'] <= year <= report['end'] or not math.isfinite(value):
                raise ValueError('WDI numeric row out of range or not finite')
            if not row['unit'] or row['source'] != 'World Bank Indicators API' or row['source_url'] not in urls:
                raise ValueError('WDI normalized row has missing/unknown provenance')
            key=(code,year,indicator)
            if key in keys:
                raise ValueError('Duplicate WDI country-year-indicator observation')
            if key not in raw_values or raw_values[key]!=value:
                raise ValueError('WDI processed numeric row is not backed by original API value')
            keys.add(key); count+=1
            geographies.add(code); years.add(year)
    if count != report['observations'] or count != len(raw_values) or keys != set(raw_values):
        raise ValueError('WDI normalized panel is not identical to recorded nonmissing source values')
    regular = {str(p.relative_to(folder)) for p in folder.rglob('*') if p.is_file()}
    expected = {'run_report.json','processed/wdi_panel.csv'} | {'raw/'+name for name in names}
    if regular != expected or any(p.is_symlink() for p in folder.rglob('*')):
        raise ValueError('Untracked WDI artifact payload')
    return {'status':'WDI_SOURCE_NATIVE_NUMERIC_CHECKS_PASSED_NOT_CANONICAL',
            'observations':count,'raw_pages':len(names),'indicators_with_raw_pages':len(indicators),
            'geography_codes':len(geographies),
            'first_observed_year':min(years),'last_observed_year':max(years),
            'raw_source_rows_examined':raw_source_rows,
            'raw_rows_missing_or_excluded_by_native_normalizer':raw_skipped_rows,
            'all_output_numeric_keys_and_values_matched_original_raw':True,
            # WLD is the World Bank world aggregate, not a country. Preserve all
            # source-native rows; classify *all* geographies against official
            # country/aggregate metadata before any country-only research use.
            'world_aggregate_present':'WLD' in geographies,
            'country_only_geographies_verified':False,
            'geography_type_audit':'OFFICIAL_COUNTRY_VS_AGGREGATE_METADATA_STILL_REQUIRED',
            'report_sha256':digest(folder/'run_report.json'),
            'panel_sha256':digest(panel), 'canonical_integration_verified':False}


def remote_digest(remote: str) -> str:
    """Independently hash private restored bytes; never print actual data."""
    proc = subprocess.Popen(['rclone','cat',remote],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    h=hashlib.sha256()
    assert proc.stdout is not None
    for chunk in iter(lambda:proc.stdout.read(1024*1024),b''):
        h.update(chunk)
    error=proc.stderr.read()[-300:] if proc.stderr else b''
    if proc.wait(timeout=300) != 0:
        raise RuntimeError('WDI private archive could not be retrieved: '+error.decode(errors='replace')[-120:])
    return h.hexdigest()


def backup_recent_wdi(repo: str, now: datetime, output: Path) -> dict:
    """Versioned private backup for latest completed full WDI source collector."""
    picked=select_wdi_run(github_runs(repo,WDI_WORKFLOW),now)
    if picked is None:
        return {'status':'NO_RECENT_SUCCESSFUL_FULL_WDI_SCHEDULED_RUN'}
    rid, number=picked['id'], picked['run_number']
    remote=f'{WDI_REMOTE_ROOT}/run-{rid}'
    archive_name=f'wdi-source-{rid}.tar.gz'
    expected={archive_name, archive_name+'.sha256', 'source_validation.json'}
    try:
        listing=json.loads(run(['rclone','lsjson',remote,'--files-only'],timeout=90))
    except RuntimeError as exc:
        if 'directory not found' in str(exc).lower() or 'path not found' in str(exc).lower():
            listing=[]
        else:
            raise
    if not isinstance(listing,list):
        raise ValueError('Unexpected WDI private archive directory listing')
    present={x.get('Name') for x in listing if isinstance(x,dict)}
    if present:
        if not expected.issubset(present):
            raise ValueError('Partial WDI private backup: reconcile existing private files before writing')
        marker=run(['rclone','cat',remote+'/'+archive_name+'.sha256'],timeout=90).strip().split()
        if len(marker)!=2 or marker[1]!=archive_name:
            raise ValueError('Unexpected private WDI archive checksum marker')
        pinned=ensure_sha(marker[0])
        data=json.loads(run(['rclone','cat',remote+'/source_validation.json'],timeout=90))
        if data.get('source_run_id')!=rid or data.get('archive_sha256')!=pinned:
            raise ValueError('WDI private archive metadata contradicts checksum')
        if remote_digest(remote+'/'+archive_name)!=pinned:
            raise ValueError('Previously backed up WDI archive byte checksum changed')
        return {'status':'PREVIOUS_WDI_PRIVATE_ARCHIVE_BYTES_VERIFIED',
                'run_id':rid,'archive_sha256':pinned,
                'observations':data.get('observations'),
                'canonical_integration_verified':False}
    work=Path(output)/f'wdi_source_{rid}'
    work.mkdir(parents=True,exist_ok=False)
    source=work/'payload'
    source.mkdir()
    run(['gh','run','download',str(rid),'--repo',repo,
         '--name',f'world-bank-wdi-{number}','--dir',str(source)],timeout=900)
    quality=validate_wdi_download(source)
    archive=work/archive_name
    with tarfile.open(archive,'w:gz') as tar:
        for p in sorted(source.rglob('*')):
            if p.is_file() and not p.is_symlink():
                tar.add(p,arcname='wdi_source_artifact/'+str(p.relative_to(source)),recursive=False)
    archive_sha=digest(archive)
    marker=work/(archive_name+'.sha256')
    marker.write_text(archive_sha+'  '+archive_name+'\n',encoding='ascii')
    metadata=work/'source_validation.json'
    metadata.write_text(json.dumps({**quality,'source_run_id':rid,
                                    'source_run_number':number,'archive_sha256':archive_sha},
                                   indent=2,sort_keys=True)+'\n',encoding='utf-8')
    # All private copies are read back and independently SHA-verified.
    for p in (archive,marker,metadata):
        dest=remote+'/'+p.name
        run(['rclone','copyto',str(p),dest,'--retries','3'],timeout=1800)
        if remote_digest(dest)!=digest(p):
            raise RuntimeError('WDI private source copy does not match local SHA256')
    return {'status':'NEW_WDI_SOURCE_ARCHIVE_PRIVATE_BYTES_VERIFIED',
            'run_id':rid,'archive_sha256':archive_sha,
            'observations':quality['observations'],
            'raw_pages':quality['raw_pages'],
            'canonical_integration_verified':False}


def safe_backup_recent_wdi(repo: str, now: datetime, output: Path) -> dict:
    """Record source-backup failures without suppressing the independent health audit."""
    try:
        return backup_recent_wdi(repo, now, output)
    except Exception as exc:
        # A WDI cloud outage must not suppress normal WID/ILOSTAT coverage reports.
        # Never turn a failed private backup into a success claim.
        print(f'::warning::WDI private source backup NOT VERIFIED ({type(exc).__name__}); '
              'inspect the WDI archive and watchdog logs',file=sys.stderr)
        return {'status':'WDI_PRIVATE_BACKUP_NOT_VERIFIED',
                'error_type':type(exc).__name__,
                'canonical_integration_verified':False,
                'next_gate':'INSPECT_SOURCE_ARTIFACT_AND_PRIVATE_ARCHIVE'}


def make_ledger(ilostat: dict, wid: dict, joined: dict, canonical: dict, batch: dict, wdi: dict | None = None) -> list[dict]:
    rows = [
        {'source': 'canonical_warehouse', 'stage': 'validated_real_numeric_sqlite_previous_run', 'physical_values': canonical['canonical_observations_previously_verified'], 'numeric_rows_this_run_verified': 'no', 'additive_to_other_rows': 'no'},
        {'source': 'WID_23country', 'stage': 'actual_numeric_sqlite_sha_and_schema_verified', 'physical_values': wid['observations'], 'numeric_rows_this_run_verified': 'yes', 'additive_to_other_rows': 'no'},
        {'source': 'WID_WDI_join', 'stage': 'actual_joined_numeric_csv_values_verified', 'physical_values': joined['observed_numeric_cells'], 'numeric_rows_this_run_verified': 'yes', 'additive_to_other_rows': 'no'},
        {'source': 'ILOSTAT_bulk', 'stage': 'raw_official_csv_gzip_counted_not_harmonized', 'physical_values': ilostat['validated_raw_csv_rows_NOT_all_numeric'], 'numeric_rows_this_run_verified': 'no', 'additive_to_other_rows': 'no'},
        {'source': 'monthly_source_batch', 'stage': batch['status'], 'physical_values': '', 'numeric_rows_this_run_verified': 'no', 'additive_to_other_rows': 'no'},
    ]
    if wdi is not None:
        rows.append({'source':'WorldBank_WDI_weekly_source',
                     'stage':wdi['status'],
                     'physical_values':wdi.get('observations',''),
                     'numeric_rows_this_run_verified':'no',
                     'additive_to_other_rows':'no'})
    return rows



def audit_wid_only(source: Path, output: Path) -> dict:
    """Isolated manual WID revision audit; no ILOSTAT, GitHub, rclone or repair.

    The caller downloads only SHA-pinned WID files via the existing workflow.
    Never writes numeric values to output artifacts or edits the source SQLite.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from research.audit_wid_revisions import compare as compare_wid

    wid = verify_wid(source / 'wid.sqlite',
                     load_json(source / 'wid_sha.json'),
                     load_json(source / 'wid_validation.json'))
    revisions = compare_wid(
        source / 'wid.sqlite', source / 'wid_refreshed',
        Path('research/wid_23_source_manifest.json'),
        Path('research/wid_refresh_20261103_source_fingerprints.json'),
    )
    report = {
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'WID_ONLY_EXACT_KEY_AND_VALUE_COMPARISON',
        'wid': wid,
        'wid_revision_comparison': revisions,
        'ilostat_status': 'NOT_ACCESSED_IN_WID_ONLY_MODE',
        'canonical_status': 'NOT_ACCESSED_IN_WID_ONLY_MODE',
        'wid_wdi_join_status': 'NOT_ACCESSED_IN_WID_ONLY_MODE',
        'onedrive_backup': 'NOT_ATTEMPTED_MANUAL_READ_ONLY',
        'auto_ingestion_dispatch': False,
        'sum_all_source_values_is_valid': False,
        'note': 'Source-native numeric comparison, NOT harmonized or causal; source values remain private.',
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / 'source_coverage_audit.json').write_text(json.dumps(report, indent=2) + '\n')
    rows = [{'source': 'WID_23country',
             'stage': 'actual_numeric_sqlite_sha_and_schema_verified',
             'physical_values': wid['observations'],
             'numeric_rows_this_run_verified': 'yes',
             'additive_to_other_rows': 'no'},
            {'source': 'WID_20country_refresh',
             'stage': 'private_exact_key_and_value_comparison_not_new_warehouse_values',
             'physical_values': revisions['totals']['refreshed'],
             'numeric_rows_this_run_verified': 'yes',
             'additive_to_other_rows': 'no'}]
    with (output / 'source_numeric_coverage_ledger.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    manifest = {p.name: digest(p) for p in output.iterdir() if p.is_file()}
    (output / 'output_checksums.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'status': report['status'],
                      'original_wid_numeric_rows': wid['observations'],
                      'revision_audit_country_count': revisions['country_count'],
                      'counts': revisions['totals'],
                      'ilostat_accessed': False, 'ingestion_dispatched': False}))
    return report


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--repo', default='diegoduque16/Global-economic-policy-tool')
    p.add_argument('--skip-external', action='store_true', help='Offline audit only, no gh or rclone execution')
    p.add_argument('--auto-repair', action='store_true', help='Dispatch existing ILOSTAT ingestion only when stale')
    p.add_argument('--compare-wid-revisions', action='store_true', help='Compare WID revisions in comprehensive watchdog mode')
    p.add_argument('--wid-only-revisions', action='store_true', help='Private, read-only WID comparison independent of ILOSTAT/joins/canonical')
    a = p.parse_args(argv)
    d = a.input
    if a.wid_only_revisions:
        if a.auto_repair or a.compare_wid_revisions:
            p.error('--wid-only-revisions cannot be combined with ingestion repair or full-source comparison')
        return audit_wid_only(d, a.out)
    a.out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    state = load_json(d / 'ilostat_state.json')
    ilostat = verify_ilostat(state, now)
    wid = verify_wid(d / 'wid.sqlite', load_json(d / 'wid_sha.json'), load_json(d / 'wid_validation.json'))
    joined = verify_joined(d / 'joined_numeric.csv', load_json(d / 'joined_sha.json'), load_json(d / 'joined_report.json'))
    canonical = verify_canonical_source_marker(d / 'canonical_archive.sha256')
    if a.compare_wid_revisions:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from research.audit_wid_revisions import compare as compare_wid
        refreshed_dir = d / 'wid_refreshed'
        if not refreshed_dir.is_dir():
            raise ValueError('Requested WID revision comparison, but refreshed private inputs are unavailable')
        # verify_wid above validates SQLite bytes against the saved private SHA manifest.
        wid_revision_comparison = compare(
            d / 'wid.sqlite', refreshed_dir,
            Path('research/wid_23_source_manifest.json'),
            Path('research/wid_refresh_20261103_source_fingerprints.json'),
        )
    else:
        wid_revision_comparison = {'status': 'NOT_RUN_MANUAL_OPT_IN_ONLY'}
    if not a.skip_external:
        ilostat['sampled_source_integrity'] = sample_ilostat_remote(state)
        runs = github_runs(a.repo, ILOSTAT_WORKFLOW)
        repair = choose_ingestion_repair(runs, now)
        if a.auto_repair and repair['action'].startswith('dispatch_'):
            dispatch_ilostat(a.repo)
            repair['action'] = 'dispatched_existing_bounded_20_file_ingestion'
        batch = backup_recent_batch(a.repo, now, a.out)
        wdi_source = safe_backup_recent_wdi(a.repo, now, a.out)
    else:
        repair = {'action': 'offline_checks_only'}
        batch = {'status': 'NOT_CHECKED_OFFLINE'}
        wdi_source = {'status': 'NOT_CHECKED_OFFLINE'}
    ledger = make_ledger(ilostat, wid, joined, canonical, batch, wdi_source)
    report = {
        'timestamp_utc': now.isoformat(),
        'status': 'AUDITED_SOURCE_COVERAGE_NO_NEW_HARMONIZATION_OR_CAUSAL_INFERENCE',
        'canonical': canonical,
        'wid': wid,
        'wid_worldbank_join': joined,
        'wid_revision_comparison': wid_revision_comparison,
        'ilostat_bulk': ilostat,
        'source_batch_backup': batch,
        'wdi_source_backup': wdi_source,
        'ingestion_scheduler_repair': repair,
        'sum_all_source_values_is_valid': False,
        'separate_existing_ilostat_cron': '17 2,10,18 * * *',
        'note': 'Source-native rows, WID values, canonical observations, and joined cells overlap and are NOT additive. This watchdog does not produce economic policy estimates.',
    }
    (a.out / 'source_coverage_audit.json').write_text(json.dumps(report, indent=2) + '\n')
    with (a.out / 'source_numeric_coverage_ledger.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(ledger[0]))
        w.writeheader()
        w.writerows(ledger)
    evidence = {p.name: digest(p) for p in a.out.iterdir() if p.is_file()}
    (a.out / 'output_checksums.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'wid_values': wid['observations'], 'joined_numeric_cells': joined['observed_numeric_cells'], 'ilostat_verified_files': ilostat['verified_compressed_files'], 'ilostat_pending': ilostat['remaining_bulk_files'], 'repair': repair['action'], 'batch_backup': batch['status'], 'wdi_backup': wdi_source['status']}))


if __name__ == '__main__':
    main()
