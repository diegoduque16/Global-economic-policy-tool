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
from pathlib import Path
import re
import sqlite3
import subprocess
import tarfile
from typing import Any

REMOTE_ROOT = 'onedrive:Diego/economic research database'
ILOSTAT_ROOT = REMOTE_ROOT + '/atlas/ilostat-bulk-v1'
CANONICAL_SHA256 = '8f6ae9a3674045851c0726aa6415014389082a6309e784c77753542d7147072f'
ILOSTAT_WORKFLOW = 'atlas-ilostat-bulk-resumable-ingestion.yml'
BATCH_WORKFLOW = 'expand-economic-sources-batch.yml'
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
    if workflow not in (ILOSTAT_WORKFLOW, BATCH_WORKFLOW):
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


def make_ledger(ilostat: dict, wid: dict, joined: dict, canonical: dict, batch: dict) -> list[dict]:
    return [
        {'source': 'canonical_warehouse', 'stage': 'validated_real_numeric_sqlite_previous_run', 'physical_values': canonical['canonical_observations_previously_verified'], 'numeric_rows_this_run_verified': 'no', 'additive_to_other_rows': 'no'},
        {'source': 'WID_23country', 'stage': 'actual_numeric_sqlite_sha_and_schema_verified', 'physical_values': wid['observations'], 'numeric_rows_this_run_verified': 'yes', 'additive_to_other_rows': 'no'},
        {'source': 'WID_WDI_join', 'stage': 'actual_joined_numeric_csv_values_verified', 'physical_values': joined['observed_numeric_cells'], 'numeric_rows_this_run_verified': 'yes', 'additive_to_other_rows': 'no'},
        {'source': 'ILOSTAT_bulk', 'stage': 'raw_official_csv_gzip_counted_not_harmonized', 'physical_values': ilostat['validated_raw_csv_rows_NOT_all_numeric'], 'numeric_rows_this_run_verified': 'no', 'additive_to_other_rows': 'no'},
        {'source': 'monthly_source_batch', 'stage': batch['status'], 'physical_values': '', 'numeric_rows_this_run_verified': 'no', 'additive_to_other_rows': 'no'},
    ]


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--repo', default='diegoduque16/Global-economic-policy-tool')
    p.add_argument('--skip-external', action='store_true', help='Offline audit only, no gh or rclone execution')
    p.add_argument('--auto-repair', action='store_true', help='Dispatch existing ILOSTAT ingestion only when stale')
    a = p.parse_args(argv)
    d = a.input
    a.out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    state = load_json(d / 'ilostat_state.json')
    ilostat = verify_ilostat(state, now)
    wid = verify_wid(d / 'wid.sqlite', load_json(d / 'wid_sha.json'), load_json(d / 'wid_validation.json'))
    joined = verify_joined(d / 'joined_numeric.csv', load_json(d / 'joined_sha.json'), load_json(d / 'joined_report.json'))
    canonical = verify_canonical_source_marker(d / 'canonical_archive.sha256')
    if not a.skip_external:
        ilostat['sampled_source_integrity'] = sample_ilostat_remote(state)
        runs = github_runs(a.repo, ILOSTAT_WORKFLOW)
        repair = choose_ingestion_repair(runs, now)
        if a.auto_repair and repair['action'].startswith('dispatch_'):
            dispatch_ilostat(a.repo)
            repair['action'] = 'dispatched_existing_bounded_20_file_ingestion'
        batch = backup_recent_batch(a.repo, now, a.out)
    else:
        repair = {'action': 'offline_checks_only'}
        batch = {'status': 'NOT_CHECKED_OFFLINE'}
    ledger = make_ledger(ilostat, wid, joined, canonical, batch)
    report = {
        'timestamp_utc': now.isoformat(),
        'status': 'AUDITED_SOURCE_COVERAGE_NO_NEW_HARMONIZATION_OR_CAUSAL_INFERENCE',
        'canonical': canonical,
        'wid': wid,
        'wid_worldbank_join': joined,
        'ilostat_bulk': ilostat,
        'source_batch_backup': batch,
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
    print(json.dumps({'status': report['status'], 'wid_values': wid['observations'], 'joined_numeric_cells': joined['observed_numeric_cells'], 'ilostat_verified_files': ilostat['verified_compressed_files'], 'ilostat_pending': ilostat['remaining_bulk_files'], 'repair': repair['action'], 'batch_backup': batch['status']}))


if __name__ == '__main__':
    main()
