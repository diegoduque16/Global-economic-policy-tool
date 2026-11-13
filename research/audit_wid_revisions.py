#!/usr/bin/env python3
"""Read-only WID observation revision comparison against verified private SQLite.

Called by the EXISTING Atlas evidence watchdog on opt-in manual runs only.
No fetch, write to OneDrive, data publication, or warehouse mutation.
Counts and provenance only; never prints raw private numeric measurements.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

REQUIRED = {'wid_area', 'year', 'percentile', 'concept', 'source_variable',
            'share_fraction', 'source_file_sha256', 'research_status'}
PERCENTILES = {'p0p50', 'p50p90', 'p90p100', 'p99p100'}
CONCEPTS = {'pretax_national_income_share', 'posttax_national_income_share',
            'posttax_disposable_income_share', 'personal_wealth_share'}


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def staged_rows(path: Path, area: str, source_sha: str, expected: int) -> dict:
    """Load numeric, source-native CSV without silently losing missing/duplicate keys."""
    if not path.is_file() or path.is_symlink():
        raise ValueError('WID refreshed input must be a regular local file')
    # Use the EXISTING WID variable-code validator so identical year/pct keys
    # with incompatible pretax/posttax concepts cannot be compared as equals.
    from research.build_wid_numeric_panel import parse_variable
    values = {}
    with path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        names = reader.fieldnames or []
        if len(names) != len(set(names)) or not REQUIRED.issubset(names):
            raise ValueError('WID refreshed staged source has unexpected columns')
        for i, row in enumerate(reader, 2):
            if None in row or any(x is None for x in row.values()):
                raise ValueError('Malformed staged WID record')
            if row['wid_area'] != area or row['source_file_sha256'] != source_sha:
                raise ValueError('WID row source identity/parent raw SHA differs')
            if row['research_status'] != 'source_native_descriptive_not_causal':
                raise ValueError('WID input incorrectly asserts research readiness')
            year, pct, concept = row['year'], row['percentile'], row['concept']
            if (not re.fullmatch(r'\d{4}', year) or not 1700 <= int(year) <= 2200
                    or pct not in PERCENTILES or concept not in CONCEPTS):
                raise ValueError('Invalid WID observation key')
            if parse_variable(row['source_variable']) != concept:
                raise ValueError('WID source variable does not match concept definition')
            if not row['share_fraction'].strip():
                raise ValueError('Missing WID numeric value (not zero)')
            try:
                value = float(row['share_fraction'])
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError('Non-numeric WID source observation') from exc
            if not math.isfinite(value) or not -5 <= value <= 5:
                raise ValueError('WID source value nonfinite or outside source guards')
            key = (int(year), pct, concept)
            if key in values:
                raise ValueError('Duplicate WID numeric observation key')
            values[key] = value
    if len(values) != expected:
        raise ValueError('WID refreshed source row count differs from pinned evidence')
    return values


def compare(db_path: Path, refresh_dir: Path, manifest_path: Path,
            fingerprints_path: Path) -> dict:
    """Compare by exact WID area, year, percentile, concept and SQLite REAL value."""
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    evidence = json.loads(fingerprints_path.read_text(encoding='utf-8'))
    if evidence.get('schema_version') != 1 or evidence.get('source_run_id') != 41588514160:
        raise ValueError('Unsupported WID source fingerprint evidence version')
    meta = evidence.get('source_fingerprints')
    expected = evidence.get('expected_refreshed_country_count')
    if not isinstance(meta, list) or type(expected) is not int or len(meta) != expected:
        raise ValueError('Incomplete WID refresh fingerprint evidence')
    if not refresh_dir.is_dir():
        raise ValueError('Refreshed WID source directory not available')
    baseline_sources = {row['wid_area']: row for row in manifest['countries']}
    if len(baseline_sources) != manifest.get('expected_country_count'):
        raise ValueError('WID baseline source manifest unexpectedly changed')
    records = []
    totals = dict(unchanged=0, changed=0, added=0, removed=0, baseline=0, refreshed=0)
    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('WID original SQLite integrity check failed')
        for x in meta:
            code = x['wid_area']
            if code not in baseline_sources or code in [r['wid_area'] for r in records]:
                raise ValueError('Unexpected/duplicate WID country evidence')
            src = baseline_sources[code]
            raw_sha = x.get('source_sha256')
            if not isinstance(raw_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', raw_sha):
                raise ValueError('Invalid refreshed WID SHA256 fingerprint')
            if raw_sha == src['source_sha256']:
                # Same raw source version is okay, but not proof of numeric identity.
                pass
            expected_count = x.get('retained_source_shares')
            if type(expected_count) is not int or expected_count < 1:
                raise ValueError('Invalid WID refreshed expected count')
            baseline = list(db.execute(
                '''SELECT year,percentile,concept,value_fraction,original_source_sha256
                   FROM wid_numeric_observations WHERE wid_area=?''', (code,)))
            if len(baseline) != src['source_native_rows']:
                raise ValueError('Original WID country row count differs from source manifest')
            prior = {}
            for year, percentile, concept, value, old_sha in baseline:
                if old_sha != src['source_sha256'] or not isinstance(value, float) or not math.isfinite(value):
                    raise ValueError('WID original value/parent SHA validation failed')
                key = (year, percentile, concept)
                if key in prior:
                    raise ValueError('Duplicate baseline WID observation key')
                prior[key] = value
            fresh_file = refresh_dir / (code + '.csv')
            refreshed = staged_rows(fresh_file, code, raw_sha, expected_count)
            prior_keys, fresh_keys = set(prior), set(refreshed)
            common = prior_keys & fresh_keys
            unchanged = sum(prior[k] == refreshed[k] for k in common)
            differences = len(common) - unchanged
            added = len(fresh_keys - prior_keys)
            removed = len(prior_keys - fresh_keys)
            rec = {'wid_area': code,
                   'baseline_numeric_records': len(prior),
                   'refreshed_numeric_records': len(refreshed),
                   'shared_observation_keys': len(common),
                   'unchanged_numeric_values_at_sqlite_real_precision': unchanged,
                   'revised_numeric_values_at_sqlite_real_precision': differences,
                   'new_observation_keys': added, 'removed_observation_keys': removed,
                   'source_raw_sha256_changed': src['source_sha256'] != raw_sha,
                   'retrieved_staged_csv_sha256': file_sha(fresh_file)}
            records.append(rec)
            for name, number in [('unchanged', unchanged), ('changed', differences),
                                 ('added', added), ('removed', removed),
                                 ('baseline', len(prior)), ('refreshed', len(refreshed))]:
                totals[name] += number
    if len(records) != expected or totals['refreshed'] != evidence['expected_refreshed_numeric_source_rows']:
        raise ValueError('Incomplete WID numeric country/value coverage')
    return {'status': 'PRIVATE_WID_EXACT_KEY_AND_SQLITE_REAL_VALUE_AUDIT',
            'research_status': 'DESCRIPTIVE_SOURCE_COMPARISON_NOT_CAUSAL',
            'comparison_precision': 'IEEE754_SQLITE_REAL_NUMERIC_EQUALITY',
            'revisions_are_not_additive_to_canonical': True,
            'raw_source_files_reverified_this_run': False,
            'refreshed_staged_csv_bytes_sha256_recorded': True,
            'source_run_id': evidence['source_run_id'],
            'country_count': len(records),
            'totals': totals,
            'by_country': sorted(records, key=lambda r: r['wid_area']),
            'note': 'Counts compare 20 source-native WID panels against the separately SHA-verified baseline SQLite. A revision is not a new observation or causal effect; no raw values are published.'}
