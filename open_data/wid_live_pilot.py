#!/usr/bin/env python3
"""Bounded official WID country-CSV ingestion and private OneDrive verification.

No country-series substitution, synthetic interpolation, or causal effect claims.
Only source-native WID country exports; source files never committed to GitHub.
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
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

OFFICIAL_TEMPLATE = 'https://wid.world/bulk_download/WID_data_{country}.csv'
STORE = 'onedrive:Diego/economic research database/atlas/wid-country-pilot-v1'
MAX_FILE = 190 * 1024 * 1024
MAX_TOTAL = 450 * 1024 * 1024
GROUPS = {'p0p50': 'bottom_50', 'p50p90': 'middle_40',
          'p90p100': 'top_10', 'p99p100': 'top_1'}
CONCEPTS = {'ptinc': 'pretax_national_income_share',
            'diinc': 'posttax_national_income_share',
            'cainc': 'posttax_disposable_income_share',
            'hweal': 'personal_wealth_share'}
FIELDS = ['wid_area', 'year', 'percentile', 'group', 'concept',
          'source_variable', 'share_fraction', 'quality_grade',
          'source_file_sha256', 'research_status']
MATCH_FIELDS = ['wid_area', 'year', 'percentile', 'group'] + list(CONCEPTS.values()) + [
    'posttax_minus_pretax_pp', 'research_status']


def sha(data):
    return hashlib.sha256(data).hexdigest()


def parse_code(raw):
    """Support both documented sptinc992j and live-bulk sptincj992 code layouts."""
    if not raw or raw[0] != 's':
        return None
    for concept in CONCEPTS:
        if raw == 's' + concept + '992j' or raw == 's' + concept + 'j992':
            return CONCEPTS[concept]
    return None


def validate_csv(raw, country, source_sha):
    try:
        txt = raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError('WID CSV not UTF-8') from exc
    head = txt.splitlines()[0] if txt.splitlines() else ''
    delim = '\t' if '\t' in head else (';' if ';' in head else ',')
    reader = csv.DictReader(io.StringIO(txt), delimiter=delim)
    cols = [k.strip().lower() for k in (reader.fieldnames or [])]
    if not {'variable', 'year', 'percentile', 'value'}.issubset(cols) or len(cols) != len(set(cols)):
        raise ValueError(f'Unexpected WID CSV headers: {cols[:20]}')
    if cols != (reader.fieldnames or []):
        raise ValueError('Noncanonical or ambiguous WID CSV header casing')
    rows = []
    counts = Counter()
    keys = set()
    year_min = None
    year_max = None
    for idx, rec in enumerate(reader, 2):
        if None in rec or any(v is None for v in rec.values()):
            raise ValueError(f'Malformed CSV at source line {idx}')
        concept = parse_code(rec['variable'].strip())
        pct = rec['percentile'].strip()
        if not concept or pct not in GROUPS:
            continue
        native_area = rec.get('country', '').strip().upper() or country
        if native_area != country:
            raise ValueError(f'Source area {native_area} differs from requested {country} at line {idx}')
        if rec.get('age', '').strip() not in ('', '992') or rec.get('pop', '').strip() not in ('', 'j'):
            raise ValueError('WID series code and explicit population/age dimensions disagree')
        yr = rec['year'].strip()
        if not re.fullmatch(r'\d{4}', yr) or not (1700 <= int(yr) <= dt.date.today().year + 1):
            raise ValueError(f'Invalid year at source line {idx}')
        raw_value = rec['value'].strip()
        if not raw_value:
            counts['missing_source_value_skipped'] += 1
            continue
        try:
            value = float(raw_value)
        except ValueError as exc:
            raise ValueError(f'Non-numeric value at source line {idx}') from exc
        if not math.isfinite(value) or not (-5 <= value <= 5):
            raise ValueError(f'Nonfinite or suspect source share at line {idx}')
        k = (native_area, yr, pct, concept)
        if k in keys:
            raise ValueError(f'Duplicate country/year/percentile/concept: {k}')
        keys.add(k)
        q = rec.get('quality', rec.get('quality_grade', rec.get('quality_score', ''))).strip()
        if q and q not in ('0','1','2','3','4','5'):
            q = 'unparsed_source_quality:' + q[:32]
        rows.append({'wid_area': native_area, 'year': yr, 'percentile': pct,
                     'group': GROUPS[pct], 'concept': concept,
                     'source_variable': rec['variable'].strip(),
                     'share_fraction': format(value, '.17g'),
                     'quality_grade': q or 'not_provided_in_source',
                     'source_file_sha256': source_sha,
                     'research_status': 'source_native_descriptive_not_causal'})
        counts[concept] += 1
        if abs(value) > 1.05:
            counts['fraction_magnitude_warning'] += 1
        year_min = min(year_min, int(yr)) if year_min is not None else int(yr)
        year_max = max(year_max, int(yr)) if year_max is not None else int(yr)
    if not rows:
        raise ValueError(f'{country}: no target WID shares found in official country CSV')
    return rows, dict(counts), year_min, year_max


def csv_bytes(fields, rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='raise')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8')


def summarize(rows):
    by_key = defaultdict(dict)
    for row in rows:
        by_key[(row['wid_area'], row['year'], row['percentile'])][row['concept']] = float(row['share_fraction'])
    out = []
    matched = 0
    for (area, yr, pct), vals in sorted(by_key.items()):
        item = {'wid_area': area, 'year': yr, 'percentile': pct, 'group': GROUPS[pct]}
        for concept in CONCEPTS.values():
            item[concept] = format(vals[concept], '.17g') if concept in vals else ''
        if 'pretax_national_income_share' in vals and 'posttax_national_income_share' in vals:
            item['posttax_minus_pretax_pp'] = format(100 * (vals['posttax_national_income_share'] - vals['pretax_national_income_share']), '.15g')
            matched += 1
        else:
            item['posttax_minus_pretax_pp'] = ''
        item['research_status'] = 'descriptive_gap_not_tax_reform_effect'
        out.append(item)
    return out, matched


def official_url(country):
    if not re.fullmatch(r'[A-Z]{2}', country):
        raise ValueError('Pilot requires two-letter uppercase WID country code')
    return OFFICIAL_TEMPLATE.format(country=country)


def fetch(country, attempts=3):
    url = official_url(country)
    last = None
    for n in range(attempts):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'GlobalEconomicPolicyTool/0.1 (WID official data research pilot)',
                'Accept': 'text/csv,*/*', 'Accept-Encoding': 'identity'})
            with urllib.request.urlopen(req, timeout=120) as response:
                final = urllib.parse.urlsplit(response.geturl())
                if final.scheme != 'https' or (final.hostname or '') not in ('wid.world', 'www.wid.world'):
                    raise ValueError(f'Unexpected WID download redirect: {final.hostname}')
                if response.status != 200:
                    raise ValueError(f'WID HTTP {response.status}')
                size = response.headers.get('Content-Length')
                if size and int(size) > MAX_FILE:
                    raise ValueError('Source CSV exceeds per-file cap')
                raw = response.read(MAX_FILE + 1)
                if len(raw) == 0 or len(raw) > MAX_FILE:
                    raise ValueError('Empty or oversized WID source file')
                return raw, url
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            last = exc
            if n < attempts - 1:
                time.sleep(min(2 ** n, 8))
        except Exception:
            raise
    raise RuntimeError(f'WID {country} unavailable: {last}')


def rclone_verify(local_path, remote_path):
    def cmd(args):
        ret = subprocess.run(args, text=True, capture_output=True, timeout=600)
        if ret.returncode:
            raise RuntimeError(f'rclone failure ({ret.returncode}): {ret.stderr[-500:]}')
    # Never overwrite an existing cloud object with mismatched data.
    check = subprocess.run(['rclone', 'lsf', remote_path], capture_output=True, text=True, timeout=90)
    if check.returncode == 0 and check.stdout.strip():
        existing = subprocess.run(['rclone', 'cat', remote_path], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=600)
        if existing.returncode or sha(existing.stdout) != sha(local_path.read_bytes()):
            raise RuntimeError(f'Cloud hash conflict: {remote_path}')
        return
    cmd(['rclone', 'copyto', str(local_path), remote_path, '--retries', '3'])
    copy = subprocess.run(['rclone', 'cat', remote_path], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=600)
    if copy.returncode or sha(copy.stdout) != sha(local_path.read_bytes()):
        raise RuntimeError(f'Cloud roundtrip checksum mismatch: {remote_path}')


def run(countries, out_dir, backup=True, downloader=fetch):
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError('Output directory must be new/empty')
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {'source': 'WID.world official country bulk CSV',
              'source_url_template': OFFICIAL_TEMPLATE,
              'run_timestamp_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'research_status': 'noncausal_source_staging_not_canonical_warehouse',
              'countries': [], 'numeric_values_retrieved': 0,
              'raw_published_to_github': False, 'onedrive_verified': False}
    total = 0
    try:
        for country in countries:
            raw, url = downloader(country)
            total += len(raw)
            if total > MAX_TOTAL:
                raise ValueError('Exceeded overall compressed/uncompressed download budget')
            h = sha(raw)
            rows, counts, lo, hi = validate_csv(raw, country, h)
            matched, pairs = summarize(rows)
            country_dir = out_dir / 'private' / country
            country_dir.mkdir(parents=True, exist_ok=True)
            raw_file = country_dir / f'WID_data_{country}.csv'
            raw_file.write_bytes(raw)
            clean_file = country_dir / 'wid_source_native_shares.csv'
            clean_file.write_bytes(csv_bytes(FIELDS, rows))
            joined_file = country_dir / 'wid_matched_distribution.csv'
            joined_file.write_bytes(csv_bytes(MATCH_FIELDS, matched))
            files = [raw_file, clean_file, joined_file]
            remote_base = f'{STORE}/{country}/{h[:24]}'
            if backup:
                for path in files:
                    rclone_verify(path, remote_base + '/' + path.name)
            report['countries'].append({'area': country, 'source_url': url,
              'source_sha256': h, 'source_bytes': len(raw),
              'retained_source_shares': len(rows), 'matched_pretax_posttax_pairs': pairs,
              'year_min': lo, 'year_max': hi, 'counts_by_concept': counts,
              'backed_up_to_private_onedrive': backup, 'snapshot_base': remote_base if backup else None})
            report['numeric_values_retrieved'] += len(rows)
        report['onedrive_verified'] = backup
        return report
    finally:
        # Public GitHub Actions artifact gets only metadata (no source rows).
        (out_dir / 'public_summary.json').write_text(json.dumps(report, indent=2)+'\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--countries', default='US,FR,CO')
    ap.add_argument('--out', type=Path, default=Path('wid_live_pilot_output'))
    ap.add_argument('--no-backup', action='store_true', help='Local test only; never used in CI')
    args = ap.parse_args()
    countries = [s.strip().upper() for s in args.countries.split(',')]
    if len(countries) != len(set(countries)) or not 1 <= len(countries) <= 5:
        ap.error('Choose 1 to 5 unique countries')
    for country in countries:
        official_url(country)
    summary = run(countries, args.out, not args.no_backup)
    print(json.dumps({'countries': len(summary['countries']), 'retained_share_rows': summary['numeric_values_retrieved'],
                      'onedrive_verified': summary['onedrive_verified']}, indent=2))

if __name__ == '__main__':
    main()
