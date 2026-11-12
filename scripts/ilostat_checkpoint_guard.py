#!/usr/bin/env python3
"""Fail-closed, separate-history guard for the existing Atlas ILOSTAT workflow.

Does not ingest, harmonize, delete, or rewrite indicator-data files.
Raw state and immutable backups remain on private OneDrive only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

SOURCE = 'onedrive:Diego/economic research database/atlas/ilostat-bulk-v1/checkpoint/state.json'
ROOT = 'onedrive:Diego/economic research database/atlas/ilostat-checkpoint-audit-v1'
LATEST = ROOT + '/latest.json'
VALID_ID = re.compile(r'[A-Za-z0-9_]{4,100}\Z')
VALID_SHA = re.compile(r'[0-9a-f]{64}\Z')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_checkpoint(raw: bytes) -> dict:
    obj = json.loads(raw)
    if not isinstance(obj, dict) or obj.get('schema_version') != 1:
        raise ValueError('Unexpected ILOSTAT checkpoint schema')
    d = obj.get('downloaded')
    if not isinstance(d, dict):
        raise ValueError('Missing downloaded source manifest')
    for key, val in d.items():
        if not isinstance(key, str) or not VALID_ID.fullmatch(key):
            raise ValueError('Malformed table ID in source checkpoint')
        if not isinstance(val, dict) or val.get('state') != 'NATIVE_NOT_HARMONIZED':
            raise ValueError('Invalid source table verification record')
        if not isinstance(val.get('records_validated'), int) or val['records_validated'] < 1:
            raise ValueError('Source table lacks verified CSV rows')
        if not isinstance(val.get('sha256'), str) or not VALID_SHA.fullmatch(val['sha256']):
            raise ValueError('Malformed source SHA256')
    return obj


def compare_states(older: dict, newer: dict) -> dict:
    previous = set(older['downloaded'])
    current = set(newer['downloaded'])
    removed = sorted(previous - current)
    if removed:
        raise ValueError('CHECKPOINT REGRESSION: %d previously verified tables disappeared; first %s' %
                         (len(removed), ','.join(removed[:5])))
    added = sorted(current - previous)
    refreshed = sorted(k for k in previous & current
                       if older['downloaded'][k].get('sha256') != newer['downloaded'][k].get('sha256'))
    return {'previous_verified': len(previous), 'current_verified': len(current),
            'new_distinct_tables': len(added), 'refreshed_table_files': len(refreshed),
            'lost_table_files': 0}


def run_rclone(args: list[str], *, input_bytes: bytes | None = None) -> bytes:
    p = subprocess.run(['rclone', *args], input=input_bytes, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=300, check=False)
    if p.returncode:
        raise RuntimeError('rclone command failed (%d): %s' %
                           (p.returncode, p.stderr.decode(errors='replace')[-400:]))
    return p.stdout


def read_remote(remote: str) -> bytes:
    return run_rclone(['cat', remote])


def read_json_with_retry(command: list[str], *, label: str) -> tuple[bytes, object]:
    """Retry transient empty, malformed, or failed OneDrive JSON reads only.

    Never replace a missing/corrupt response with an empty checkpoint or
    silently reset the audit baseline. Exhausted retries abort the workflow.
    """
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            raw = run_rclone(command)
            if not raw.strip():
                raise ValueError('Empty remote JSON response')
            return raw, json.loads(raw)
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            last_error = exc
            if attempt == 3:
                break
            time.sleep(2 ** attempt)
    raise RuntimeError(f'{label} unreadable after 4 attempts; failing closed '
                       f'({type(last_error).__name__})') from last_error


def restore_verified_history(manifest: dict) -> dict:
    if manifest.get('schema_version') != 1:
        raise ValueError('Unknown ILOSTAT audit history schema')
    path, sha = manifest.get('snapshot_remote'), manifest.get('snapshot_sha256')
    if not isinstance(path, str) or not path.startswith(ROOT + '/snapshots/run-'):
        raise ValueError('Unexpected private snapshot path')
    if not isinstance(sha, str) or not VALID_SHA.fullmatch(sha):
        raise ValueError('Invalid saved checkpoint SHA256')
    data, _ = read_json_with_retry(['cat', path], label='immutable audit snapshot')
    if digest(data) != sha:
        raise ValueError('Historical checkpoint snapshot SHA256 mismatch')
    parsed = parse_checkpoint(data)
    if len(parsed['downloaded']) != manifest.get('verified_file_count'):
        raise ValueError('Saved checkpoint count does not agree with the immutable snapshot')
    return parsed


def load_latest_manifest() -> dict | None:
    # Create the private audit directory only; never touch the source checkpoint path.
    run_rclone(['mkdir', ROOT])
    _, listing = read_json_with_retry(['lsjson', ROOT, '--files-only'], label='audit directory listing')
    if not isinstance(listing, list):
        raise ValueError('Unexpected private audit listing')
    if not any(entry.get('Name') == 'latest.json' for entry in listing):
        return None
    _, manifest = read_json_with_retry(['cat', LATEST], label='audit latest pointer')
    if not isinstance(manifest, dict):
        raise ValueError('Malformed checkpoint audit manifest')
    return manifest


def assert_current_against_history(current: dict, latest: dict | None) -> dict:
    if latest is None:
        return {'audit_baseline': 'not_yet_established', 'current_verified': len(current['downloaded'])}
    prior = restore_verified_history(latest)
    return {'audit_baseline': 'verified_private_snapshot', **compare_states(prior, current)}


def save_history(data: bytes, *, run_id: str, attempt: str) -> dict:
    # GitHub run_id and run_attempt are numeric. Never overwrite a previous snapshot.
    if not re.fullmatch(r'[0-9]{1,25}', run_id) or not re.fullmatch(r'[0-9]{1,4}', attempt):
        raise ValueError('Invalid run identity')
    remote = f'{ROOT}/snapshots/run-{run_id}-attempt-{attempt}.json'
    run_rclone(['mkdir', ROOT + '/snapshots'])
    _, listing = read_json_with_retry(['lsjson', ROOT + '/snapshots', '--files-only'], label='immutable snapshot listing')
    if any(entry.get('Name') == f'run-{run_id}-attempt-{attempt}.json' for entry in listing):
        raise FileExistsError('Refusing to overwrite immutable private checkpoint snapshot')
    # rclone copyto requires a local file; do not log its contents or expose an artifact.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='atlas-checkpoint-guard-') as folder:
        p = Path(folder) / 'state.json'
        p.write_bytes(data)
        run_rclone(['copyto', str(p), remote, '--retries', '3'])
        restored, _ = read_json_with_retry(['cat', remote], label='new audit snapshot verification')
        if digest(restored) != digest(data):
            raise ValueError('OneDrive restored checkpoint backup SHA256 mismatch')
        manifest = {'schema_version': 1, 'snapshot_remote': remote,
                    'snapshot_sha256': digest(data),
                    'verified_file_count': len(parse_checkpoint(data)['downloaded']),
                    'run_id': run_id, 'run_attempt': attempt,
                    'verified_at_utc': datetime.now(timezone.utc).isoformat()}
        b = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
        p.write_bytes(b)
        run_rclone(['copyto', str(p), LATEST, '--retries', '3'])
        restored_latest, _ = read_json_with_retry(['cat', LATEST], label='latest pointer verification')
        if restored_latest != b:
            raise ValueError('Private checkpoint audit latest-pointer restore mismatch')
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', required=True, choices=['pre', 'post'])
    args = parser.parse_args()
    tempdir = Path(os.environ.get('RUNNER_TEMP', '/tmp'))
    prefile = tempdir / 'atlas-ilostat-checkpoint-before.json'
    source_data, _ = read_json_with_retry(['cat', SOURCE], label='source checkpoint')
    current = parse_checkpoint(source_data)
    latest = load_latest_manifest()
    report = assert_current_against_history(current, latest)
    if args.mode == 'pre':
        prefile.write_bytes(source_data)
        print('ILOSTAT_CHECKPOINT_PRE', json.dumps(report, sort_keys=True), flush=True)
        return 0
    if not prefile.exists():
        raise FileNotFoundError('Missing same-job checkpoint baseline; refusing unguarded post-run')
    same_run = compare_states(parse_checkpoint(prefile.read_bytes()), current)
    manifest = save_history(source_data, run_id=os.environ['GITHUB_RUN_ID'],
                            attempt=os.environ.get('GITHUB_RUN_ATTEMPT', '1'))
    print('ILOSTAT_CHECKPOINT_POST', json.dumps({**report, 'within_run': same_run,
        'checkpoint_backed_up_privately': True, 'snapshot_sha256': manifest['snapshot_sha256']},
        sort_keys=True), flush=True)
    prefile.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('ILOSTAT_CHECKPOINT_GUARD_FAILED:', type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        sys.exit(1)
