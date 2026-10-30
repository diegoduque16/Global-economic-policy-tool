#!/usr/bin/env python3
"""Validate source-native WID income/wealth shares and build a NON-CAUSAL panel.

Input: official WID.world CSV/TSV (or ZIP containing source-native CSV/TSV files).
No external libraries, API key, network access, interpolation or inferred tax shocks.
Use `--input` to point to a source download explicitly obtained from WID.world.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
import zipfile

SOURCE = 'https://wid.world/data/'
METHODOLOGY = 'https://wid.world/codes-dictionary/'
# Adults age 20+, equal-split allocation. Do not pool unlike populations.
SERIES = {
    'sptinc992j': 'pretax_national_income_share',
    'sdiinc992j': 'posttax_national_income_share',
    'scainc992j': 'posttax_disposable_income_share',
    'shweal992j': 'personal_wealth_share',
}
PERCENTILES = {
    'p0p50': 'bottom_50', 'p50p90': 'middle_40',
    'p90p100': 'top_10', 'p99p100': 'top_1',
}
REQUIRED = ('country', 'variable', 'percentile', 'year', 'value')
FIELDS = ['wid_area_code', 'year', 'percentile', 'group_name', 'variable', 'concept',
          'value_fraction', 'age_code', 'population_unit', 'source_file',
          'source_sha256', 'observed_or_imputed', 'source_note', 'research_status']
JOIN_FIELDS = ['wid_area_code', 'year', 'percentile', 'group_name',
               'pretax_national_income_share', 'posttax_national_income_share',
               'posttax_disposable_income_share', 'personal_wealth_share',
               'posttax_minus_pretax_share_pp', 'income_redistribution_data_available',
               'research_status']
MAX_BYTES = 300 * 1024 * 1024
MAX_MEMBER_BYTES = 150 * 1024 * 1024


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def iter_sources(path: Path):
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('Input is not a file or exceeds 300 MB; stage in bounded batches')
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError('Duplicate ZIP member path')
            count = 0
            total_bytes = 0
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if (info.filename.startswith('/') or '\\' in info.filename or
                    '..' in Path(info.filename).parts):
                    raise ValueError('Unsafe ZIP member path')
                if Path(info.filename).suffix.lower() not in ('.csv', '.tsv', '.txt'):
                    continue  # Ignore WID metadata binaries and external documentation.
                if info.file_size > MAX_MEMBER_BYTES:
                    raise ValueError('ZIP member exceeds safe uncompressed size')
                total_bytes += info.file_size
                if total_bytes > MAX_BYTES:
                    raise ValueError('ZIP uncompressed total exceeds 300 MB; split the input')
                raw = archive.read(info)
                count += 1
                yield info.filename, raw
            if not count:
                raise ValueError('ZIP has no source-native CSV/TSV files')
    else:
        if path.suffix.lower() not in ('.csv', '.tsv', '.txt'):
            raise ValueError('Expected CSV, TSV, or ZIP')
        yield path.name, path.read_bytes()


def parse_rows(name: str, raw: bytes):
    try:
        contents = raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError(f'{name}: expected UTF-8 input; avoid silently changing encoding') from exc
    sample = contents[:8192]
    # WID exports can use commas, tabs or semicolons; do not guess decimal separators.
    first_line = sample.splitlines()[0] if sample.splitlines() else ''
    delim = '\t' if '\t' in first_line else ';' if ';' in first_line else ','
    reader = csv.DictReader(io.StringIO(contents), delimiter=delim)
    fields = [str(f or '').strip().lower() for f in (reader.fieldnames or [])]
    filename_area = ''
    official_file = re.fullmatch(r'WID_data_([A-Z]{2})\.(csv|tsv|txt)', Path(name).name, re.I)
    if official_file:
        filename_area = official_file.group(1).upper()
    has_core = set(REQUIRED).difference({'country'}).issubset(fields)
    if not has_core or ('country' not in fields and not filename_area):
        # WID metadata/dictionaries are not observations.
        return None
    if fields != [str(f).strip().lower() for f in (reader.fieldnames or [])]:
        raise ValueError(f'{name}: ambiguous column names')

    def records():
        for ordinal, row in enumerate(reader, 2):
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f'{name}:{ordinal}: malformed data row')
            rec = {str(k).strip().lower(): str(v).strip() for k, v in row.items()}
            if filename_area and 'country' not in rec:
                # Official country-file naming is a documented, explicit WID native code.
                rec['country'] = filename_area
            elif filename_area and rec['country'].strip().upper() != filename_area:
                raise ValueError(f'{name}:{ordinal}: country disagrees with WID filename')
            yield ordinal, rec
    return records()


def normalize(rec, source_file: str, checksum: str):
    variable = rec['variable']
    pct = rec['percentile']
    if variable not in SERIES or pct not in PERCENTILES:
        return None
    area = rec['country'].strip().upper()
    if not re.fullmatch(r'[A-Z]{2}(?:-[A-Z0-9]{1,8})?', area):
        raise ValueError(f'Invalid WID native area code {area!r}')
    year = rec['year']
    if not re.fullmatch(r'\d{4}', year) or not (1700 <= int(year) <= dt.date.today().year + 1):
        raise ValueError(f'Invalid year {year!r}')
    try:
        value = float(rec['value'])
    except ValueError as exc:
        raise ValueError(f'Non-numeric value for {area}/{variable}/{pct}/{year}') from exc
    if not math.isfinite(value):
        raise ValueError('NaN/Inf is not an observation')
    # Share series are SOURCE-UNIT FRACTIONS; allow negative wealth shares.
    if value < -5 or value > 5:
        raise ValueError('Suspect share value: source units must be confirmed')
    if rec.get('age') and rec['age'] != '992':
        raise ValueError('Age dimension disagrees with WID 992 code')
    if rec.get('pop') and rec['pop'].lower() != 'j':
        raise ValueError('Population dimension disagrees with WID j code')
    imputed = rec.get('imputation', '')
    return {
        'wid_area_code': area, 'year': year, 'percentile': pct,
        'group_name': PERCENTILES[pct], 'variable': variable,
        'concept': SERIES[variable], 'value_fraction': format(value, '.17g'),
        'age_code': '992', 'population_unit': 'j', 'source_file': source_file,
        'source_sha256': checksum, 'observed_or_imputed': imputed or 'unknown_no_imputation_metadata',
        'source_note': rec.get('source', ''),
        'research_status': 'source_native_descriptive_only_not_causal',
    }


def write_csv(path: Path, fields, records):
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='raise')
        writer.writeheader()
        writer.writerows(records)


def build(input_path: Path, output_dir: Path):
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError('Output directory must not contain prior results')
    output_dir.mkdir(parents=True, exist_ok=True)
    measures = {}
    skipped = []
    sources = []
    counts = Counter()
    bad_share_examples = []
    for name, raw in iter_sources(input_path):
        digest = sha256(raw)
        parsed = parse_rows(name, raw)
        if parsed is None:
            skipped.append({'file': name, 'reason': 'not_WID_observation_schema'})
            continue
        raw_rows = 0
        selected = 0
        for line, item in parsed:
            raw_rows += 1
            try:
                row = normalize(item, name, digest)
            except ValueError as exc:
                raise ValueError(f'{name}:{line}: {exc}') from exc
            if row is None:
                continue
            selected += 1
            key = (row['wid_area_code'], row['year'], row['percentile'], row['variable'])
            if key in measures:
                # Do not silently aggregate duplicate vintages or versions.
                raise ValueError(f'Duplicate WID observation key {key}: {name}')
            measures[key] = row
            counts[row['concept']] += 1
            if abs(float(row['value_fraction'])) > 1.5:
                bad_share_examples.append({'key': key, 'value': row['value_fraction']})
        sources.append({'name': name, 'sha256': digest, 'bytes': len(raw),
                        'raw_rows': raw_rows, 'selected_rows': selected})
    if not measures:
        raise ValueError('No required WID share variables were found; check native variable codes')
    ordered = [measures[k] for k in sorted(measures)]
    write_csv(output_dir / 'wid_source_native_shares.csv', FIELDS, ordered)
    matched = defaultdict(dict)
    for record in ordered:
        matched[(record['wid_area_code'], record['year'], record['percentile'])][record['concept']] = float(record['value_fraction'])
    joined_rows = []
    matched_income = 0
    for (area, year, percentile), values in sorted(matched.items()):
        pretax = values.get('pretax_national_income_share')
        posttax = values.get('posttax_national_income_share')
        full_pair = pretax is not None and posttax is not None
        if full_pair:
            matched_income += 1
        entry = dict(wid_area_code=area, year=year, percentile=percentile,
                     group_name=PERCENTILES[percentile])
        for concept in SERIES.values():
            entry[concept] = format(values[concept], '.17g') if concept in values else ''
        entry.update(posttax_minus_pretax_share_pp=(format(100 * (posttax - pretax), '.15g') if full_pair else ''),
                     income_redistribution_data_available=str(full_pair).lower(),
                     research_status='matched_descriptive_gap_not_tax_reform_effect')
        joined_rows.append(entry)
    write_csv(output_dir / 'wid_matched_distribution.csv', JOIN_FIELDS, joined_rows)
    # Non-overlapping group accounting, if all pieces exist; do not sum top 1% again.
    sum_warnings = []
    by_concept = defaultdict(dict)
    for row in ordered:
        by_concept[(row['wid_area_code'], row['year'], row['variable'])][row['percentile']] = float(row['value_fraction'])
    for (area, year, variable), portions in by_concept.items():
        if {'p0p50', 'p50p90', 'p90p100'}.issubset(portions):
            total = sum(portions[x] for x in ('p0p50', 'p50p90', 'p90p100'))
            if abs(total - 1) > .02:
                sum_warnings.append({'wid_area_code': area, 'year': year,
                                     'variable': variable, 'sum_of_nonoverlapping_shares': total})
    report = dict(source='WID.World native data', source_url=SOURCE, methodology_url=METHODOLOGY,
                  input_name=input_path.name, status='source_validated_not_warehouse_integrated',
                  source_files=sources, metadata_files_skipped=skipped,
                  retained_share_observations=len(ordered),
                  distinct_area_year_percentile_rows=len(joined_rows),
                  matched_pretax_posttax_pairs=matched_income,
                  counts_by_concept=dict(sorted(counts.items())),
                  unexpected_share_magnitude=bad_share_examples[:25],
                  nonoverlapping_share_sum_warnings=sum_warnings[:100],
                  notes=['WID regional and historical area codes remain native; not automatically ISO3 countries.',
                         'Posttax-minus-pretax is descriptive and is NOT the causal effect of a tax reform.',
                         'Post-tax national income includes cash and in-kind redistribution; do not relabel as disposable income.',
                         'WID revisions, imputation status and comparability require methodological review.',
                         'Data staged separately; no double count or overwrite of existing warehouse or OneDrive.'])
    (output_dir / 'wid_qa_report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    report['output_sha256'] = {p.name: sha256(p.read_bytes()) for p in sorted(output_dir.iterdir())}
    # Freeze manifest, including QA report, after all outputs were written.
    (output_dir / 'source_manifest.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', required=True, type=Path, help='Original downloaded WID CSV/TSV or ZIP')
    p.add_argument('--out', default='data/wid_staging', type=Path)
    args = p.parse_args()
    report = build(args.input, args.out)
    print(json.dumps({k:report[k] for k in ('status','retained_share_observations',
          'distinct_area_year_percentile_rows','matched_pretax_posttax_pairs')}, indent=2))


if __name__ == '__main__':
    main()
