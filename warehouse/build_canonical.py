#!/usr/bin/env python3
"""Build a single audited snapshot from a validated warehouse and a raw-data batch.

DOES NOT insert source-native rows into observations. They remain explicitly
STAGED until a source-specific harmonization/validation step is approved.
Requires standard library only. Existing snapshot is never modified in place.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone

REQUIRED_COLLECTORS = {
    'worldbank_wdi_expanded': 'world_bank_wdi_expanded_native.csv',
    'bls_qcew_us_national': 'bls_qcew_us_national_2020_2025_native.csv',
    'census_cbp_states': 'census_cbp_state_raw_2019_2023.zip',
}
MIN_BASELINE_ROWS = 2_780_117
MIN_BASELINE_FAMILIES = 24
WAVE1_FILES = {'us_treasury_mts': 'treasury_mts_table1_native.csv',
               'census_bfs_monthly': 'census_bfs_monthly_native.csv'}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def only_one(root, pattern):
    options = sorted(Path(root).rglob(pattern))
    if len(options) != 1:
        raise ValueError(f'Expected exactly one {pattern} inside {root}, found {len(options)}')
    return options[0]


def extract_verified_baseline(archive, dest):
    """Extract regular files only; reject archive traversal and verify manifest."""
    dest = Path(dest)
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        files = [m for m in members if m.isfile()]
        if not files or len(files) > 5000:
            raise ValueError('Baseline archive missing files or unreasonably large file count')
        if any(not (m.isfile() or m.isdir()) for m in members):
            raise ValueError('Baseline archive contains symlink or special file')
        parts = [PurePosixPath(m.name).parts for m in members]
        if any(not p or p[0] in ('', '.', '..') or '..' in p or m.name.startswith('/')
               for p, m in zip(parts, members)):
            raise ValueError('Unsafe baseline archive path')
        roots = {p[0] for p in parts}
        if len(roots) != 1:
            raise ValueError('Expected exactly one baseline snapshot root')
        root = next(iter(roots))
        manifest_name = f'{root}/manifest.json'
        if sum(m.name == manifest_name for m in files) != 1:
            raise ValueError('Missing or duplicated baseline manifest')
        for member in files:
            rel = PurePosixPath(member.name).relative_to(root)
            dst = dest.joinpath(*rel.parts)
            dst.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(member) as source_file, dst.open('wb') as target:
                shutil.copyfileobj(source_file, target)
    manifest = read_json(dest / 'manifest.json')
    if not isinstance(manifest.get('files'), list):
        raise ValueError('Baseline manifest lacks per-file SHA256 inventory')
    expected = {}
    for item in manifest['files']:
        p = item['path']
        parts = PurePosixPath(p).parts
        if not parts or '..' in parts or p.startswith('/') or p in expected:
            raise ValueError('Invalid/duplicate manifest inventory path')
        expected[p] = item['sha256']
    for relative, digest in expected.items():
        f = dest.joinpath(*PurePosixPath(relative).parts)
        if not f.is_file() or sha256(f) != digest:
            raise ValueError(f'Baseline file hash mismatch: {relative}')
    # No undocumented files can be silently imported.
    extracted = {str(p.relative_to(dest)) for p in dest.rglob('*') if p.is_file()}
    if extracted != set(expected) | {'manifest.json'}:
        raise ValueError('Baseline archive contains untracked files or missing inventory entries')
    return manifest


def validate_baseline(dest, manifest):
    n = int(manifest['total_source_observations'])
    families = manifest['source_families']
    if n < MIN_BASELINE_ROWS or len(families) < MIN_BASELINE_FAMILIES:
        raise ValueError('Baseline warehouse regressed below validated floor')
    if sum(int(f['rows']) for f in families.values()) != n:
        raise ValueError('Baseline family counts do not match total')
    dbfile = Path(dest) / 'warehouse.sqlite'
    with sqlite3.connect(f'file:{dbfile}?mode=ro', uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Baseline SQLite integrity check failed')
        actual = db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
        family_count = db.execute('SELECT COUNT(*) FROM source_files').fetchone()[0]
    if (actual, family_count) != (n, len(families)):
        raise ValueError('Baseline SQLite counts do not match snapshot manifest')
    return n, len(families)


def validate_batch(batchdir):
    batchdir = Path(batchdir)
    audit = read_json(batchdir / 'audit.json')
    if audit.get('failures') or audit.get('warehouse_modified') is not False:
        raise ValueError('Batch reports failures or unverified warehouse mutation')
    successful = audit.get('successful', [])
    selected = audit.get('selected', [])
    if set(selected) != set(REQUIRED_COLLECTORS) or len(selected) != len(REQUIRED_COLLECTORS):
        raise ValueError('Batch selection is not precisely the three expected collectors')
    if len(successful) != 3 or {s.get('collector') for s in successful} != set(REQUIRED_COLLECTORS):
        raise ValueError('Missing/duplicate successful batch collectors')
    if int(audit.get('catalog_entries', 0)) < 125 or int(audit.get('catalog_p0', 0)) < 71:
        raise ValueError('Source catalog coverage regressed')
    index = {}
    for s in successful:
        name = s['collector']
        rel = s['file']
        expected_rel = f'source_native/{REQUIRED_COLLECTORS[name]}'
        if rel != expected_rel or not rel.startswith('source_native/'):
            raise ValueError(f'Unexpected file for {name}: {rel}')
        target = batchdir / rel
        if not target.is_file() or sha256(target) != s['sha256']:
            raise ValueError(f'Staged artifact hash mismatch for {name}')
        count = int(s['rows'])
        if count <= 0:
            raise ValueError('Batch source has no rows')
        if name != 'census_cbp_states':
            with target.open(encoding='utf-8-sig', newline='') as f:
                actual = sum(1 for _ in csv.DictReader(f))
            if actual != count:
                raise ValueError(f'CSV count disagreement in {name}: {actual} vs {count}')
        else:
            # Audited CBP source files are preserved byte-for-byte. Inner ZIP checks
            # protect the original five Census releases as well as the outer bundle.
            req = s.get('requests', [])
            if [r.get('year') for r in req] != list(range(2019, 2024)):
                raise ValueError('Expected CBP annual releases 2019–2023')
            with zipfile.ZipFile(target) as archive:
                if set(archive.namelist()) != {r['member'] for r in req}:
                    raise ValueError('CBP bundle members changed')
                for record in req:
                    if hashlib.sha256(archive.read(record['member'])).hexdigest() != record['raw_zip_sha256']:
                        raise ValueError(f"Original Census ZIP hash mismatch: {record['year']}")
            if sum(int(r['rows']) for r in req) != count:
                raise ValueError('CBP annual counts do not add to audit total')
        index[name] = {'status': 'STAGED_NOT_IN_OBSERVATIONS', 'rows': count,
                       'file': rel, 'sha256': s['sha256'],
                       'scope': s.get('scope', ''), 'source': s['source']}
    return audit, index


def validate_wave1(root):
    folder = only_one(root, 'source_native_fetch_audit.json').parent
    report = read_json(folder / 'source_native_fetch_audit.json')
    coverage = read_json(folder / 'source_coverage_summary.json')
    if report.get('failures') or len(report.get('retrievals', [])) != 2:
        raise ValueError('Wave 1 has incomplete or failed retrievals')
    if int(coverage.get('warehouse_observations_unchanged', 0)) != MIN_BASELINE_ROWS or \
            int(coverage.get('families_in_verified_warehouse', 0)) != MIN_BASELINE_FAMILIES:
        raise ValueError('Wave 1 references an unexpected warehouse baseline')
    seen = set()
    index = {}
    for row in report['retrievals']:
        name = row['id']
        if name in seen or name not in WAVE1_FILES or row.get('data_file') != WAVE1_FILES[name]:
            raise ValueError('Unexpected or duplicate wave 1 source')
        seen.add(name)
        if row.get('status') != 'STAGED_SOURCE_NATIVE_NOT_WAREHOUSE_INTEGRATED':
            raise ValueError('Wave 1 source falsely reported as research ready')
        file = folder / 'source_native' / row['data_file']
        if not file.is_file() or sha256(file) != row['sha256']:
            raise ValueError('Wave 1 source hash mismatch')
        with file.open(encoding='utf-8-sig', newline='') as f:
            count = sum(1 for _ in csv.DictReader(f))
        if count != int(row['rows']):
            raise ValueError('Wave 1 reported incorrect CSV row count')
        index[name] = {'status': 'STAGED_NOT_IN_OBSERVATIONS', 'rows': count,
                       'file': f'source_native/{row["data_file"]}',
                       'sha256': row['sha256'], 'source': row['source']}
    if set(index) != set(WAVE1_FILES):
        raise ValueError('Wave 1 missing required sources')
    return folder, report, coverage, index


def build(baseline_archive, batch_root, output_root, *, snapshot_id, catalog_path=None, wave1_root=None):
    if not snapshot_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in snapshot_id):
        raise ValueError('Snapshot ID must be alphanumeric, hyphen or underscore')
    baseline_archive = Path(baseline_archive)
    batch_root = Path(batch_root)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    final = output_root / snapshot_id
    if final.exists():
        raise FileExistsError('Refusing to overwrite existing snapshot')
    with tempfile.TemporaryDirectory(dir=output_root) as tmp:
        staging = Path(tmp) / snapshot_id
        base_dest = staging / 'canonical_warehouse'
        base_dest.mkdir(parents=True)
        manifest = extract_verified_baseline(baseline_archive, base_dest)
        count, families = validate_baseline(base_dest, manifest)
        batch_dir = only_one(batch_root, 'audit.json').parent
        audit, index = validate_batch(batch_dir)
        for name, obj in index.items():
            dest = staging / 'staged_source_native' / REQUIRED_COLLECTORS[name]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(batch_dir / obj['file'], dest)
            if sha256(dest) != obj['sha256']:
                raise ValueError('Copied source hash mismatch')
        (staging / 'batch_audit.json').write_text(json.dumps(audit, indent=2) + '\n', encoding='utf-8')
        wave1_index = {}
        if wave1_root is not None:
            wave1_folder, wave1_audit, wave1_coverage, wave1_index = validate_wave1(wave1_root)
            for name, obj in wave1_index.items():
                dest = staging / 'staged_source_native' / WAVE1_FILES[name]
                shutil.copyfile(wave1_folder / obj['file'], dest)
                if sha256(dest) != obj['sha256']:
                    raise ValueError('Copied wave 1 hash mismatch')
            (staging / 'wave1_fetch_audit.json').write_text(json.dumps(wave1_audit, indent=2) + '\n')
            (staging / 'wave1_coverage_summary.json').write_text(json.dumps(wave1_coverage, indent=2) + '\n')
        catalog_hash = None
        if catalog_path is not None:
            catalog_path = Path(catalog_path)
            with catalog_path.open(newline='', encoding='utf-8-sig') as source:
                entries = list(csv.DictReader(source))
            if len(entries) != int(audit['catalog_entries']) or sum(
                x.get('priority') == 'P0' for x in entries
            ) != int(audit['catalog_p0']):
                raise ValueError('Catalog file disagrees with staged audit')
            catalog_file = staging / 'expanded_source_catalog.csv'
            shutil.copyfile(catalog_path, catalog_file)
            catalog_hash = sha256(catalog_file)
        summary = {
            'schema_version': 1,
            'snapshot_id': snapshot_id,
            'created_utc': datetime.now(timezone.utc).isoformat(),
            'canonical_snapshot_id': manifest['snapshot_id'],
            'canonical_observations': count,
            'canonical_source_families': families,
            'staged_batch_records_not_integrated': sum(x['rows'] for x in index.values()),
            'staged_wave1_records_not_integrated': sum(x['rows'] for x in wave1_index.values()),
            'all_staged_records_not_integrated': sum(x['rows'] for x in [*index.values(), *wave1_index.values()]),
            'staged_sources': {**index, **wave1_index},
            'catalog_entries': audit['catalog_entries'],
            'catalog_p0': audit['catalog_p0'],
            'catalog_sha256': catalog_hash,
            'warehouse_updated_with_staged_rows': False,
            'is_definition_harmonized': False,
            'is_causal_inference_ready': False,
            'warning': ('The canonical observation count EXCLUDES staged extracts. '
                        'Do not add these counts: WDI overlaps existing series, '
                        'QCEW has multiple NAICS categories, and CBP categories are non-additive.'),
            'source_files': [],
        }
        for p in sorted(staging.rglob('*')):
            if p.is_file():
                summary['source_files'].append({'path': str(p.relative_to(staging)),
                                                'sha256': sha256(p), 'bytes': p.stat().st_size})
        (staging / 'unified_inventory.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
        shutil.move(str(staging), str(final))
    tarpath = output_root / (snapshot_id + '.tar.gz')
    if tarpath.exists():
        raise FileExistsError('Refusing to overwrite existing archive')
    with tarfile.open(tarpath, 'w:gz') as archive:
        archive.add(final, arcname=final.name)
    return summary, tarpath


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--baseline-dir', required=True)
    ap.add_argument('--batch-dir', required=True)
    ap.add_argument('--wave1-dir', required=True)
    ap.add_argument('--output', default='canonical_output')
    ap.add_argument('--snapshot-id', required=True)
    ap.add_argument('--catalog', default='open_data/expanded_source_catalog.csv')
    args = ap.parse_args()
    baseline = only_one(args.baseline_dir, '*.tar.gz')
    inventory, archive = build(baseline, args.batch_dir, args.output, snapshot_id=args.snapshot_id, catalog_path=args.catalog, wave1_root=args.wave1_dir)
    print(json.dumps({'archive': str(archive), 'sha256': sha256(archive),
                      'canonical_observations': inventory['canonical_observations'],
                      'canonical_source_families': inventory['canonical_source_families'],
                      'staged_not_integrated': inventory['all_staged_records_not_integrated']}, indent=2))


if __name__ == '__main__':
    main()
