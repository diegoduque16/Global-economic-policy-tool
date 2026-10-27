"""Reconstruct an audited union of two validated warehouse snapshots.

Copies processed CSVs and their collector reports without fabricating observations.
Requires validated per-file hashes and a no-regression floor for each family.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
from contextlib import ExitStack

from warehouse.snapshot import FAMILIES, OPTIONAL_FAMILIES

# Source-family coverage of the Oct 26 baseline and Oct 27 validated build.
BASELINE_FLOORS = {
    'wdi': 300240, 'us_europe': 13448, 'historical': 16395,
    'imf_oecd': 51243, 'labor': 123233, 'eurostat_ai': 139,
    'imf_gfs': 2579, 'bls_bed': 3444, 'oecd_cit': 3929,
    'bls_jolts': 252390, 'historical_turnover': 2868,
    'bocconi_fiscal': 7672, 'imf_extension': 94524,
    'us_state_qcew': 25625, 'us_state_bea': 13056,
    'us_state_tax': 118155, 'bjk_state_rates': 22124,
    'bjk_county_rates': 173789,
}
RECENT_FLOORS = {
    'wdi': 300240, 'us_europe': 13448, 'historical': 16395,
    'imf_oecd': 51243, 'labor': 123233, 'eurostat_ai': 139,
    'tariff_shocks': 185, 'cait_ai_claims': 1872,
    'us_state_qcew_historical': 11700,
    'bjk_state_rates': 22124, 'bjk_county_rates': 173789,
}
REQUIRED_FAMILIES = set(BASELINE_FLOORS) | set(RECENT_FLOORS)
MIN_OBSERVATIONS = 1_238_610


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class SnapshotArchive:
    def __init__(self, path: Path):
        self.path = path
        self.archive = tarfile.open(path, 'r:gz')
        entries = {entry.name: entry for entry in self.archive.getmembers() if entry.isfile()}
        candidates = [name for name in entries if name.count('/') == 1 and name.endswith('/manifest.json')]
        if len(candidates) != 1:
            raise ValueError(f'{path}: expected one root manifest, found {candidates}')
        self.root = candidates[0].split('/')[0]
        self.entries = entries
        self.manifest = json.loads(self._read(candidates[0]))
        self.inventory = {record['path']: record['sha256'] for record in self.manifest['files']}
        self.families = self.manifest['source_families']
        if sum(int(x['rows']) for x in self.families.values()) != int(self.manifest['total_source_observations']):
            raise ValueError(f'{path}: manifest totals are inconsistent')

    def _read(self, name: str) -> bytes:
        obj = self.archive.extractfile(self.entries[name])
        if obj is None:
            raise ValueError(f'{self.path}: cannot read {name}')
        return obj.read()

    def copy_verified(self, relative_name: str, target: Path) -> None:
        expected_hash = self.inventory.get(relative_name)
        if expected_hash is None:
            raise ValueError(f'{self.path}: inventory missing {relative_name}')
        member = f'{self.root}/{relative_name}'
        if member not in self.entries:
            raise ValueError(f'{self.path}: archive missing {member}')
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.archive.extractfile(self.entries[member]) as src, target.open('wb') as out:
            shutil.copyfileobj(src, out)
        if sha256(target) != expected_hash:
            target.unlink(missing_ok=True)
            raise ValueError(f'{self.path}: checksum mismatch for {member}')

    def close(self):
        self.archive.close()


def exactly_one_archive(directory: Path) -> Path:
    found = list(directory.rglob('*.tar.gz'))
    if len(found) != 1:
        raise ValueError(f'Expected one warehouse .tar.gz under {directory}, found {len(found)}')
    return found[0]


def consolidate(legacy_archive: Path, recent_archive: Path, output: Path, report_path: Path):
    registry = {**FAMILIES, **OPTIONAL_FAMILIES}
    output = Path(output)
    report_path = Path(report_path)
    if output.exists() and any(output.iterdir()):
        raise ValueError(f'Output directory must be empty: {output}')
    if not REQUIRED_FAMILIES.issubset(registry):
        raise ValueError('warehouse.snapshot registry is missing required families')

    with ExitStack() as stack:
        legacy = SnapshotArchive(Path(legacy_archive))
        recent = SnapshotArchive(Path(recent_archive))
        stack.callback(legacy.close)
        stack.callback(recent.close)
        for label, snap, required in [('legacy', legacy, BASELINE_FLOORS), ('recent', recent, RECENT_FLOORS)]:
            missing = set(required) - set(snap.families)
            if missing:
                raise ValueError(f'{label} snapshot is missing {sorted(missing)}')
            regressions = {fam: [int(snap.families[fam]['rows']), floor]
                           for fam, floor in required.items() if int(snap.families[fam]['rows']) < floor}
            if regressions:
                raise ValueError(f'{label} source coverage regressed: {regressions}')

        families = sorted(set(legacy.families) | set(recent.families))
        if not REQUIRED_FAMILIES.issubset(families):
            raise ValueError('Union is missing required source families')
        unfamiliar = sorted(set(families) - set(registry))
        if unfamiliar:
            raise ValueError(f'Unregistered family would be lost: {unfamiliar}')

        selection = {}
        grand_total = 0
        for family in families:
            selected = recent if family in recent.families else legacy
            origin = 'recent' if selected is recent else 'legacy'
            directory, filename, _ = registry[family]
            stored_report = output / directory / 'run_report.json'
            stored_csv = output / directory / 'processed' / filename
            selected.copy_verified(f'sources/{family}/run_report.json', stored_report)
            selected.copy_verified(f'sources/{family}/processed/{filename}', stored_csv)
            source_report = json.loads(stored_report.read_text(encoding='utf-8'))
            if source_report.get('errors') or source_report.get('conflicts'):
                raise ValueError(f'{family}: source report contains collection errors/conflicts')
            expected = int(selected.families[family]['rows'])
            if expected != int(source_report['observations']):
                raise ValueError(f'{family}: source report and snapshot counts differ')
            with stored_csv.open(newline='', encoding='utf-8-sig') as file:
                actual = sum(1 for _ in csv.DictReader(file))
            if actual != expected:
                raise ValueError(f'{family}: CSV has {actual} rows, manifest expects {expected}')
            floor = max(BASELINE_FLOORS.get(family, 0), RECENT_FLOORS.get(family, 0))
            if actual < floor:
                raise ValueError(f'{family}: regression {actual} below required {floor}')
            grand_total += actual
            selection[family] = {'source_snapshot': origin,
                                 'rows': actual,
                                 'sha256': sha256(stored_csv)}
        if grand_total < MIN_OBSERVATIONS:
            raise ValueError(f'Merged count {grand_total} is below the {MIN_OBSERVATIONS} regression floor')
        result = {
            'legacy_snapshot': legacy.manifest['snapshot_id'],
            'recent_snapshot': recent.manifest['snapshot_id'],
            'total_observations': grand_total,
            'source_family_count': len(families),
            'source_selection': selection,
            'source_snapshots_verified_from_sha256_inventory': True,
            'is_definition_harmonized': False,
            'is_causal_inference_ready': False,
            'notes': [
                'Newer sources are preferred when a family occurs in both snapshots.',
                'Missing historical source families are restored from the original validated snapshot.',
                'Historical-only sources have NOT been refreshed; their original retrieval dates are preserved.',
                'Source units, definitions, and overlapping providers have not been harmonized.',
            ],
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'observations': grand_total, 'families': len(families),
                          'restored_from_legacy': sorted(set(legacy.families) - set(recent.families)),
                          'added_from_recent': sorted(set(recent.families) - set(legacy.families))}, indent=2))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-dir', type=Path, required=True)
    parser.add_argument('--recent-dir', type=Path, required=True)
    parser.add_argument('--out-data', type=Path, default=Path('data'))
    parser.add_argument('--report', type=Path, default=Path('consolidation_report.json'))
    args = parser.parse_args()
    consolidate(exactly_one_archive(args.legacy_dir), exactly_one_archive(args.recent_dir),
                args.out_data, args.report)


if __name__ == '__main__':
    main()
