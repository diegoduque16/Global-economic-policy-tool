"""Deterministic offline tests of the canonical warehouse gate."""
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from warehouse import build_canonical as target


def make_base(root):
    snap = root / 'economic-consolidated-test'
    snap.mkdir()
    dbpath = snap / 'warehouse.sqlite'
    with sqlite3.connect(dbpath) as db:
        db.executescript('''CREATE TABLE observations (id INTEGER PRIMARY KEY);
            CREATE TABLE source_files (family TEXT PRIMARY KEY);''')
        db.executemany('INSERT INTO observations (id) VALUES (?)', [(1,), (2,), (3,)])
        db.executemany('INSERT INTO source_files (family) VALUES (?)', [('alpha',), ('beta',)])
    source = snap / 'sources' / 'alpha'
    source.mkdir(parents=True)
    (source / 'source.csv').write_text('year,value\n2020,1\n')
    files = []
    for p in (dbpath, source / 'source.csv'):
        files.append({'path': str(p.relative_to(snap)), 'sha256': target.sha256(p)})
    (snap / 'manifest.json').write_text(json.dumps({
        'snapshot_id': 'economic-consolidated-test',
        'total_source_observations': 3,
        'source_families': {'alpha': {'rows': 2}, 'beta': {'rows': 1}},
        'files': files,
    }))
    archive = root / 'baseline.tar.gz'
    with tarfile.open(archive, 'w:gz') as f:
        f.add(snap, arcname=snap.name)
    return archive


def make_batch(root):
    directory = root / 'batch'
    native = directory / 'source_native'
    native.mkdir(parents=True)
    requests = []
    census = native / target.REQUIRED_COLLECTORS['census_cbp_states']
    with zipfile.ZipFile(census, 'w', compression=zipfile.ZIP_STORED) as outer:
        for year in range(2019, 2024):
            z = io.BytesIO()
            with zipfile.ZipFile(z, 'w') as inner:
                inner.writestr(f'cbp{year%100:02d}st.txt', 'fipstate,naics,emp\n01,00,42\n')
            name = f'cbp{year%100:02d}st.zip'
            outer.writestr(name, z.getvalue())
            requests.append({'year': year, 'member': name,
                             'rows': 1, 'raw_zip_sha256': hashlib.sha256(z.getvalue()).hexdigest()})
    reports = []
    for name, filename in target.REQUIRED_COLLECTORS.items():
        p = native / filename
        if name != 'census_cbp_states':
            with p.open('w', newline='') as f:
                w = csv.writer(f)
                w.writerows([['country', 'year', 'value'], ['US', '2020', '1']])
            count = 1
        else:
            count = 5
        reports.append({'collector': name, 'source': name,
                        'file': f'source_native/{filename}', 'sha256': target.sha256(p),
                        'rows': count, 'requests': requests if name == 'census_cbp_states' else []})
    audit = {'selected': list(target.REQUIRED_COLLECTORS), 'successful': reports,
             'failures': [], 'warehouse_modified': False,
             'catalog_entries': 125, 'catalog_p0': 71}
    (directory / 'audit.json').write_text(json.dumps(audit))
    return directory


def make_wave1(root):
    folder = root / 'wave1' / 'source_wave1_results'
    native = folder / 'source_native'
    native.mkdir(parents=True)
    reports = []
    for key, filename in target.WAVE1_FILES.items():
        path = native / filename
        path.write_text('period,geo,value\n2020,US,1\n2021,US,2\n')
        reports.append({'id': key, 'source': key, 'data_file': filename,
                        'rows': 2, 'sha256': target.sha256(path),
                        'status': 'STAGED_SOURCE_NATIVE_NOT_WAREHOUSE_INTEGRATED'})
    (folder / 'source_native_fetch_audit.json').write_text(json.dumps({'retrievals': reports, 'failures': []}))
    (folder / 'source_coverage_summary.json').write_text(json.dumps({
        'warehouse_observations_unchanged': 3,
        'families_in_verified_warehouse': 2,
    }))
    return folder.parent


class CanonicalTests(unittest.TestCase):
    def test_build_preserves_counts_and_sources(self):
        with tempfile.TemporaryDirectory() as d, patch.object(target, 'MIN_BASELINE_ROWS', 3), \
                patch.object(target, 'MIN_BASELINE_FAMILIES', 2):
            root = Path(d)
            baseline = make_base(root)
            batch = make_batch(root)
            inventory, tarpath = target.build(baseline, batch, root / 'out', snapshot_id='test-1')
            self.assertEqual(inventory['canonical_observations'], 3)
            self.assertEqual(inventory['canonical_source_families'], 2)
            self.assertEqual(inventory['staged_batch_records_not_integrated'], 7)
            self.assertFalse(inventory['warehouse_updated_with_staged_rows'])
            self.assertTrue(tarpath.is_file())
            self.assertEqual(set(inventory['staged_sources']), set(target.REQUIRED_COLLECTORS))
            with sqlite3.connect(root / 'out' / 'test-1' / 'canonical_warehouse' / 'warehouse.sqlite') as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0], 3)

    def test_wave1_and_latest_batch_are_both_staged(self):
        with tempfile.TemporaryDirectory() as d, patch.object(target, 'MIN_BASELINE_ROWS', 3), \
                patch.object(target, 'MIN_BASELINE_FAMILIES', 2):
            root = Path(d)
            baseline, batch, wave1 = make_base(root), make_batch(root), make_wave1(root)
            inv, tarpath = target.build(baseline, batch, root / 'out', snapshot_id='test-wave1', wave1_root=wave1)
            self.assertEqual(inv['canonical_observations'], 3)
            self.assertEqual(inv['staged_batch_records_not_integrated'], 7)
            self.assertEqual(inv['staged_wave1_records_not_integrated'], 4)
            self.assertEqual(inv['all_staged_records_not_integrated'], 11)
            self.assertEqual(len(inv['staged_sources']), 5)
            self.assertTrue(tarpath.exists())

    def test_rejects_tampered_batch_without_writing_final_snapshot(self):
        with tempfile.TemporaryDirectory() as d, patch.object(target, 'MIN_BASELINE_ROWS', 3), \
                patch.object(target, 'MIN_BASELINE_FAMILIES', 2):
            root = Path(d)
            baseline = make_base(root)
            batch = make_batch(root)
            with (batch / 'source_native' / target.REQUIRED_COLLECTORS['worldbank_wdi_expanded']).open('a') as f:
                f.write('US,2021,2\n')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                target.build(baseline, batch, root / 'out', snapshot_id='test-tamper')
            self.assertFalse((root / 'out' / 'test-tamper').exists())

    def test_rejects_unsafe_archive_member(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tarpath = root / 'bad.tar.gz'
            with tarfile.open(tarpath, 'w:gz') as tar:
                payload = b'bad'
                info = tarfile.TarInfo('root/../../escape')
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            with self.assertRaisesRegex(ValueError, 'Unsafe baseline archive path'):
                target.extract_verified_baseline(tarpath, root / 'output')


if __name__ == '__main__':
    unittest.main()
