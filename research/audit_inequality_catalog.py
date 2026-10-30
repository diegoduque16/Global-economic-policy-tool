#!/usr/bin/env python3
"""Read-only audit: catalog claims are NOT evidence of stored numeric observations.

A snapshot-based data acquisition audit remains a separate step. Do not interpret
`LIVE` as verified, or a row in the catalog as downloaded or research-ready.
"""
from __future__ import annotations
import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

REQUIRED = {'dataset', 'theme', 'current_status', 'official_source_url'}
FOCUS = {
    'WID': ('World Inequality Database WID',),
    'OECD_IDD': ('OECD Income Distribution Database',),
    'WORLD_BANK_PIP': ('World Bank Poverty & Inequality Platform',),
    'SCF': ('Federal Reserve Survey of Consumer Finances',),
}


def audit(catalog: Path) -> dict:
    with catalog.open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if not REQUIRED.issubset(reader.fieldnames or []):
            raise ValueError('Unexpected catalog schema')
        rows = list(reader)
    if not rows:
        raise ValueError('Empty catalog')
    names = defaultdict(list)
    states = Counter()
    for pos, row in enumerate(rows, start=2):
        if not row['dataset'].strip():
            raise ValueError(f'Empty catalog entry on line {pos}')
        names[' '.join(row['dataset'].lower().split())].append(pos)
        states[row['current_status'].strip()] += 1
    duplicates = {name: indexes for name, indexes in names.items() if len(indexes) > 1}
    focused = {}
    for code, options in FOCUS.items():
        matches = [r for r in rows if r['dataset'].strip() in options]
        focused[code] = [dict(dataset=r['dataset'], reported_status=r['current_status'],
                              official_source_url=r['official_source_url'],
                              actual_numeric_observations='not_verified_by_catalog') for r in matches]
    return dict(registry_entries=len(rows), distinct_dataset_names=len(names),
                duplicate_dataset_names=duplicates,
                reported_catalog_statuses=dict(sorted(states.items())),
                inequality_focus=focused,
                limitation='Cannot determine actual downloads/OneDrive archive/quality/causal readiness from the catalog')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog', type=Path, default=Path('open_data/expanded_source_catalog.csv'))
    p.add_argument('--out', type=Path, default=Path('research/inequality_catalog_audit.json'))
    args = p.parse_args()
    result = audit(args.catalog)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    main()
