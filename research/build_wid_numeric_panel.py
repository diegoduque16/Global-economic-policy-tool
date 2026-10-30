#!/usr/bin/env python3
"""Build a private, REAL-NUMERIC 23-country WID SQLite staging panel.

Input: only SHA-pinned source-native WID data CSV files already verified in
private OneDrive, plus repository manifest. No metadata-only rows count as data.
No imputation, causal effect estimates, canonical warehouse mutation, or
unreviewed cross-provider harmonization. Python stdlib only.
"""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

SERIES = {
    'ptinc': 'pretax_national_income_share',
    'diinc': 'posttax_national_income_share',
    'cainc': 'posttax_disposable_income_share',
    'hweal': 'personal_wealth_share',
}
PERCENTILES = ('p0p50', 'p50p90', 'p90p100', 'p99p100')
CONCEPTS = tuple(SERIES.values())
REQUIRED_FIELDS = ('wid_area','year','percentile','concept','source_variable',
                   'share_fraction','source_file_sha256','research_status')
COUNTRY_FIELDS = ('wid_area','iso3','source_sha256','source_native_rows',
                  'pretax_posttax_pairs','source_run')
DB_SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE source_files (
    wid_area TEXT PRIMARY KEY, iso3 TEXT NOT NULL UNIQUE,
    original_source_sha256 TEXT NOT NULL, staged_csv_sha256 TEXT NOT NULL,
    source_rows INTEGER NOT NULL CHECK(source_rows > 0),
    matched_pretax_posttax_pairs INTEGER NOT NULL,
    original_github_run_id TEXT NOT NULL,
    data_status TEXT NOT NULL CHECK (data_status='VERIFIED_NUMERIC_STAGING_NOT_CANONICAL')
);
CREATE TABLE wid_numeric_observations (
    wid_area TEXT NOT NULL REFERENCES source_files(wid_area),
    iso3 TEXT NOT NULL, year INTEGER NOT NULL,
    percentile TEXT NOT NULL, concept TEXT NOT NULL,
    value_fraction REAL NOT NULL,
    source_variable TEXT NOT NULL, original_source_sha256 TEXT NOT NULL,
    quality_grade TEXT NOT NULL,
    research_status TEXT NOT NULL,
    PRIMARY KEY (wid_area,year,percentile,concept),
    CHECK (year BETWEEN 1700 AND 2200),
    CHECK (value_fraction >= -5 AND value_fraction <= 5),
    CHECK (percentile IN ('p0p50','p50p90','p90p100','p99p100'))
) WITHOUT ROWID;
CREATE INDEX wid_values_by_iso3_year ON wid_numeric_observations (iso3,year,concept);
CREATE TABLE wid_distribution_panel (
    wid_area TEXT NOT NULL REFERENCES source_files(wid_area),
    iso3 TEXT NOT NULL, year INTEGER NOT NULL, percentile TEXT NOT NULL,
    pretax_national_income_share REAL,
    posttax_national_income_share REAL,
    posttax_disposable_income_share REAL,
    personal_wealth_share REAL,
    posttax_minus_pretax_percentage_points REAL,
    research_status TEXT NOT NULL CHECK (research_status='DESCRIPTIVE_NOT_CAUSAL'),
    PRIMARY KEY (wid_area,year,percentile)
) WITHOUT ROWID;
'''


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def parse_variable(v):
    for part, name in SERIES.items():
        if v in ('s'+part+'992j','s'+part+'j992'):
            return name
    return None


def verified_manifest(path):
    manifest=json.loads(Path(path).read_text(encoding='utf-8'))
    required=('countries','expected_country_count','expected_numeric_values',
              'expected_pretax_posttax_pairs','source_remote_prefix')
    if any(k not in manifest for k in required):
        raise ValueError('Incomplete source manifest')
    countries=manifest['countries']
    if not countries or len(countries)!=int(manifest['expected_country_count']):
        raise ValueError('Country-count mismatch in source manifest')
    codes=set(); iso_codes=set()
    for entry in countries:
        if not set(COUNTRY_FIELDS).issubset(entry):
            raise ValueError('Manifest missing country source-evidence fields')
        code=entry['wid_area']; iso=entry['iso3']
        if not re.fullmatch('[A-Z]{2}', code) or not re.fullmatch('[A-Z]{3}',iso):
            raise ValueError('Invalid WID or reviewed ISO3 country code')
        if code in codes or iso in iso_codes:
            raise ValueError('Duplicate manifest country/ISO3')
        codes.add(code);iso_codes.add(iso)
        if not re.fullmatch('[a-f0-9]{64}',entry['source_sha256']):
            raise ValueError('Invalid pinned original source SHA256')
        if int(entry['source_native_rows'])<1 or int(entry['pretax_posttax_pairs'])<0:
            raise ValueError('Invalid pinned source row/pair count')
    if sum(int(e['source_native_rows']) for e in countries)!=int(manifest['expected_numeric_values']):
        raise ValueError('Manifest source-native numeric row totals disagree')
    if sum(int(e['pretax_posttax_pairs']) for e in countries)!=int(manifest['expected_pretax_posttax_pairs']):
        raise ValueError('Manifest pre/post matched pair totals disagree')
    return manifest


def read_values(path, entry):
    # Strictly require a physical numeric field, not just dataset metadata.
    with path.open(newline='',encoding='utf-8-sig') as f:
        reader=csv.DictReader(f)
        fields=reader.fieldnames or []
        if len(fields)!=len(set(fields)) or not set(REQUIRED_FIELDS).issubset(fields):
            raise ValueError(f'{entry["wid_area"]}: missing/duplicated *numeric* CSV columns')
        seen=set(); rows=[]
        counts=Counter();years=set();warnings=[]
        for line,row in enumerate(reader,2):
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f'{entry["wid_area"]}:{line}: malformed row')
            if row['wid_area']!=entry['wid_area'] or row['source_file_sha256']!=entry['source_sha256']:
                raise ValueError(f'{entry["wid_area"]}:{line}: source country/hash mismatch')
            if row['research_status']!='source_native_descriptive_not_causal':
                raise ValueError('Source-native/noncausal status changed unexpectedly')
            name=parse_variable(row['source_variable'])
            if name is None or name!=row['concept']:
                raise ValueError(f'{entry["wid_area"]}:{line}: WID definition code disagreement')
            if row['percentile'] not in PERCENTILES:
                raise ValueError('Unsupported percentile; cannot mix population definitions')
            if not re.fullmatch(r'\d{4}',row['year']) or not (1700<=int(row['year'])<=dt.date.today().year+1):
                raise ValueError('Invalid/unsupported observation year')
            if not row['share_fraction'].strip():
                raise ValueError('Missing *data value*; metadata rows do not count')
            try:value=float(row['share_fraction'])
            except (ValueError,OverflowError) as exc:
                raise ValueError('Non-numeric observation; metadata is not an imported value') from exc
            if not math.isfinite(value) or not (-5<=value<=5):
                raise ValueError('Non-finite or out-of-range source-native share')
            k=(int(row['year']),row['percentile'],row['concept'])
            if k in seen:
                raise ValueError('Duplicate WID numeric key')
            seen.add(k)
            rows.append((entry['wid_area'],entry['iso3'],k[0],k[1],k[2],value,
                         row['source_variable'],entry['source_sha256'],
                         row.get('quality_grade',''),row['research_status']))
            counts[name]+=1;years.add(k[0])
            if abs(value)>1.05:
                warnings.append(f'{entry["wid_area"]} {k}: unusually large share {value}')
    if len(rows)!=int(entry['source_native_rows']):
        raise ValueError(f'{entry["wid_area"]}: REAL numeric rows {len(rows)} != pinned {entry["source_native_rows"]}')
    return rows,counts,years,warnings


def build(manifest_path, source_root, output_dir):
    m=verified_manifest(manifest_path)
    root=Path(source_root); out=Path(output_dir)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError('Refusing to overwrite a prior numeric panel')
    out.mkdir(parents=True,exist_ok=True)
    dbpath=out/'wid_23country_values.sqlite'
    country_report=[]
    risk=[];row_count=0;matched_pairs=0
    try:
        with sqlite3.connect(dbpath) as db:
            db.executescript(DB_SCHEMA)
            for entry in m['countries']:
                area=entry['wid_area']
                csvfile=root/area/'wid_source_native_shares.csv'
                if not csvfile.is_file() or csvfile.stat().st_size==0:
                    raise FileNotFoundError(f'NO NUMERIC SOURCE FILE for {area}')
                rows,counts,years,warnings=read_values(csvfile,entry)
                filehash=digest(csvfile)
                db.execute('INSERT INTO source_files VALUES (?,?,?,?,?,?,?,?)',
                    (area,entry['iso3'],entry['source_sha256'],filehash,len(rows),
                     entry['pretax_posttax_pairs'],entry['source_run'],
                     'VERIFIED_NUMERIC_STAGING_NOT_CANONICAL'))
                db.executemany('''INSERT INTO wid_numeric_observations
                  (wid_area,iso3,year,percentile,concept,value_fraction,source_variable,
                   original_source_sha256,quality_grade,research_status)
                  VALUES (?,?,?,?,?,?,?,?,?,?)''',rows)
                matched=defaultdict(dict)
                for row in rows:
                    matched[(row[2],row[3])][row[4]]=row[5]
                pairs=sum(int(CONCEPTS[0] in values and CONCEPTS[1] in values)
                          for values in matched.values())
                if pairs!=int(entry['pretax_posttax_pairs']):
                    raise ValueError(f'{area}: computed matched income pairs {pairs} != source report {entry["pretax_posttax_pairs"]}')
                derived=[]
                for (year,pct),values in sorted(matched.items()):
                    before=values.get('pretax_national_income_share')
                    after=values.get('posttax_national_income_share')
                    diff=100*(after-before) if before is not None and after is not None else None
                    derived.append((area,entry['iso3'],year,pct,*[values.get(k) for k in CONCEPTS],diff,'DESCRIPTIVE_NOT_CAUSAL'))
                db.executemany('INSERT INTO wid_distribution_panel VALUES (?,?,?,?,?,?,?,?,?,?)',derived)
                row_count+=len(rows);matched_pairs+=pairs
                country_report.append({'wid_area':area,'iso3':entry['iso3'],
                    'validated_numeric_values':len(rows), 'distinct_years':len(years),
                    'earliest_year':min(years),'latest_year':max(years),
                    'matched_income_pairs':pairs,'counts_by_concept':dict(counts),
                    'original_source_sha256':entry['source_sha256'],
                    'staged_numeric_csv_sha256':filehash})
                risk.extend(warnings[:10])
            db_count=db.execute('SELECT COUNT(*) FROM wid_numeric_observations').fetchone()[0]
            groups=db.execute('SELECT COUNT(*) FROM wid_distribution_panel').fetchone()[0]
            if db_count!=row_count or row_count!=m['expected_numeric_values']:
                raise ValueError('SQLITE HAS FEWER ACTUAL NUMERIC OBSERVATIONS THAN VERIFIED SOURCE COUNTS')
            if matched_pairs!=m['expected_pretax_posttax_pairs']:
                raise ValueError('SQLITE source-matched pairs differ from expected')
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('SQLite integrity check failed')
            # Show users actual native measures for 2024, not metadata counts.
            preview=list(db.execute('''SELECT wid_area,iso3,percentile,
                     pretax_national_income_share,posttax_national_income_share,
                     posttax_disposable_income_share,personal_wealth_share,
                     posttax_minus_pretax_percentage_points
                     FROM wid_distribution_panel WHERE year=2024 ORDER BY wid_area,percentile'''))
            db.commit()
        with (out/'value_evidence_2024.csv').open('w',newline='',encoding='utf-8') as f:
            wr=csv.writer(f)
            wr.writerow(['wid_area','iso3','percentile','pretax_share','posttax_share','disposable_income_share','wealth_share','descriptive_gap_pp'])
            wr.writerows(preview)
        report={'status':'REAL_NUMERIC_VALUES_IMPORTED_INTO_PRIVATE_RESEARCH_SQLITE_NOT_CANONICAL',
            'created_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
            'country_count':len(country_report),'actual_numeric_observation_rows_in_sqlite':db_count,
            'actual_derived_country_year_percentile_rows':groups,
            'actual_matched_pretax_posttax_pairs':matched_pairs,
            'real_value_preview_rows_2024':len(preview),
            'source_dataset':'WID source-native numeric share CSV files',
            'source_manifest_sha256':digest(manifest_path),
            'country_evidence':country_report,
            'large_share_warnings_count':len(risk),
            'large_share_warnings':risk[:30],
            'canonical_warehouse_modified':False,
            'data_counting_policy':'Only finite REAL numbers inserted in SQLite count; metadata-only source catalog does NOT count',
            'research_status':'SOURCE_NATIVE_DESCRIPTIVE_NOT_CAUSAL_NOT_CROSS_PROVIDER_HARMONIZED',
            'notes':['WID original area codes and explicit ISO3 crosswalk are stored separately.',
                     'Source values are WID share fractions; do not interpret as currency or person counts.',
                     'Income group top 1% is nested in top 10%: never add those together.',
                     'Posttax-minus-pretax share is descriptive, NOT a causal effect of any tax law.',
                     'Any tax policy/event and GDP/employment merge must pass separate source and definition audits.']}
        (out/'numeric_value_validation.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        (out/'sha256_manifest.json').write_text(json.dumps({name:digest(out/name) for name in
            ['wid_23country_values.sqlite','value_evidence_2024.csv','numeric_value_validation.json']},indent=2)+'\n')
        return report
    except Exception:
        # Fail closed: do not leave a misleading partial-looking panel.
        if dbpath.exists():dbpath.unlink()
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',required=True,type=Path)
    p.add_argument('--input-dir',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    a=p.parse_args()
    res=build(a.manifest,a.input_dir,a.out)
    print(json.dumps({k:res[k] for k in ('status','country_count','actual_numeric_observation_rows_in_sqlite',
        'actual_derived_country_year_percentile_rows','actual_matched_pretax_posttax_pairs')},indent=2))

if __name__=='__main__':main()
