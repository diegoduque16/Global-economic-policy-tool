#!/usr/bin/env python3
"""Add verified FRED/Eurostat source records to the existing private Atlas ATTACH layer.

Offline only: consumes already-backed-up original provider responses; never downloads
sources, rewrites the >10GB canonical warehouse, or publishes raw observations.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tarfile
import tempfile

from open_data.us_europe import FRED, EUROSTAT, parse_fred, parse_jsonstat
from warehouse.native_materialize import sha256, dump

PARENT_SHA = "2037962a29cd3587303a93bf542b7ff7ce521ce6f6294b6ad03041b978f05284"
SOURCES_SHA = "94d1f84eb450ebe0f0f47677d333a1948aa9e80a83bc1709acf1546b8537251a"
PARENT_ROWS = 613087
FRED_ROWS = 13885
EUROSTAT_ROWS = 3436
ADDED_ROWS = FRED_ROWS + EUROSTAT_ROWS
FIELDS = ['country_iso3','region','year','period','series','value','unit','frequency',
          'provider','observation_kind','status_flag','source_url','retrieved_utc']


def read_members(archive, prefix, allowed=None, max_bytes=500_000_000):
    """Read only bounded ordinary files beneath an exact archive prefix."""
    files = {}
    total = 0
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            p = PurePosixPath(member.name)
            if p.is_absolute() or '..' in p.parts or member.issym() or member.islnk():
                raise ValueError("Unsafe Atlas archive member")
            if not member.isfile():
                continue
            if not str(p).startswith(prefix + '/'):
                raise ValueError("Unexpected private archive member")
            rel = str(p)[len(prefix) + 1:]
            if not rel or rel in files or (allowed is not None and rel not in allowed):
                raise ValueError("Unexpected/duplicate native archive member")
            total += member.size
            if total > max_bytes:
                raise ValueError("Source archive size limit exceeded")
            fh = tar.extractfile(member)
            if fh is None:
                raise ValueError("Unreadable archive member")
            files[rel] = fh.read()
    return files


def read_source_release(archive, *, check_pinned=True):
    if check_pinned and sha256(archive) != SOURCES_SHA:
        raise ValueError("Original private FRED/Eurostat source archive hash changed")
    # Private archive was created with 'tar -C data us_europe' (see economic-expansion.yml).
    members = read_members(archive, 'us_europe')
    report = json.loads(members['run_report.json'])
    manifest = report['manifest']
    if (report['scope'], report['sources_requested'], report['tasks_requested'],
        report['tasks_passed'], report['errors'], report['conflicts']) != (
        'full', 'all', 27, 27, [], []):
        raise ValueError("Incomplete original US/Europe source run")
    if len(manifest) != 27 or report['observations'] != ADDED_ROWS:
        raise ValueError("Original FRED/Eurostat source release count changed")
    expected = {('us', code) for code in FRED} | {('europe', code) for code in EUROSTAT}
    if {(m['source'], m['series']) for m in manifest} != expected:
        raise ValueError("FRED/Eurostat original series identity changed")

    originals = {}
    sha_by_series = {}
    for m in manifest:
        source, code = m['source'], m['series']
        raw = members['raw/' + m['filename']]
        digest = hashlib.sha256(raw).hexdigest()
        if digest != m['sha256'] or m['filename'] != f"{source}_{code}_{digest[:12]}.raw":
            raise ValueError("Original provider response digest changed")
        if source == 'us':
            if m['url'] != 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=' + code:
                raise ValueError("Original FRED endpoint changed")
            rows, skipped = parse_fred(raw, code, 1900, 2026)
        else:
            if not m['url'].startswith('https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/'):
                raise ValueError("Original Eurostat endpoint changed")
            rows, skipped = parse_jsonstat(raw, code, 1990, 2026)
        if len(rows) != m['observations'] or skipped != m['skipped_or_missing']:
            raise ValueError("Provider original numeric replay count differs")
        sha_by_series[(source, code)] = digest
        for row in rows:
            key = (row['provider'],row['country_iso3'],row['period'],row['series'])
            if key in originals:
                raise ValueError("Duplicate source-native provider key")
            row.update(source_url=m['url'],retrieved_utc=report['retrieved_utc'])
            row.setdefault('status_flag','')
            originals[key] = row

    panel = members['processed/us_europe_panel.csv'].decode('utf-8')
    reader = csv.DictReader(io.StringIO(panel))
    if reader.fieldnames != FIELDS:
        raise ValueError("Original source panel columns changed")
    output = []
    seen = set()
    counts = {'us':0, 'europe':0}
    for row in reader:
        key = (row['provider'],row['country_iso3'],row['period'],row['series'])
        native = originals.get(key)
        if native is None or key in seen:
            raise ValueError("Processed data differs from original provider bytes")
        seen.add(key)
        if not math.isfinite(float(row['value'])) or float(row['value']) != native['value']:
            raise ValueError("Provider original numerical value mismatch")
        for name in FIELDS:
            if name == 'value':
                continue
            if str(row[name]) != str(native[name]):
                raise ValueError("Provider metadata/flags/frequency/vintage mismatch: " + name)
        provider = 'us' if row['provider'].startswith('FRED (') else 'europe'
        if provider == 'us' and (row['country_iso3'] != 'USA' or row['series'] not in FRED):
            raise ValueError("Incorrect FRED geographic/series scope")
        if provider == 'europe' and row['series'] not in EUROSTAT:
            raise ValueError("Incorrect Eurostat series")
        counts[provider] += 1
        output.append((provider,row))
    if seen != set(originals) or counts != {'us':FRED_ROWS,'europe':EUROSTAT_ROWS}:
        raise ValueError("Missing original numeric keys or count mismatch")
    return output, sha_by_series, report


def extend_sqlite(dbpath, observations, hashes, *, expected_parent_rows=PARENT_ROWS):
    with sqlite3.connect(dbpath) as db:
        db.execute('PRAGMA foreign_keys=ON')
        before = db.execute('SELECT COUNT(*) FROM native_records').fetchone()[0]
        if before != expected_parent_rows:
            raise ValueError("Parent WID/WDI native SQLite row count mismatch")
        before_sources = db.execute('SELECT COUNT(*) FROM native_sources').fetchone()[0]
        db.execute('BEGIN IMMEDIATE')
        try:
            ordinal = {}
            for provider, row in observations:
                series = row['series']
                digest = hashes[(provider,series)]
                sid = 'fred_' + series + '_' + digest[:12] if provider == 'us' else 'eurostat_' + series + '_' + digest[:12]
                if sid not in ordinal:
                    ordinal[sid] = 0
                    details = json.dumps({
                        'original_source':'FRED' if provider=='us' else 'Eurostat',
                        'native_indicator_code':series,'raw_sha256':digest,
                        'unit':row['unit'],'frequency':row['frequency'],
                        'source_url':row['source_url'],
                        'source_release_stage':'UNHARMONIZED_SOURCE_NATIVE_PRIVATE',
                        'cross_provider_overlap':'NOT_DEDUPLICATED'
                    },sort_keys=True)
                    db.execute("INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)",
                               (sid,digest,0,0,details,'FRED original CSV' if provider=='us' else 'Eurostat original JSON-stat',
                                'SOURCE_NATIVE_UNHARMONIZED',0,json.dumps(FIELDS)))
                ordinal[sid] += 1
                db.execute("INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)",
                           (sid,ordinal[sid],row['period'],row['period'],row['country_iso3'],
                            series,row['status_flag'],row['value'],json.dumps(row,sort_keys=True)))
            for sid, n in ordinal.items():
                db.execute('UPDATE native_sources SET original_records=?,indexed_records=? WHERE source_id=?',(n,n,sid))
            if (db.execute('SELECT COUNT(*) FROM native_records').fetchone()[0] != before+len(observations)
                or db.execute('SELECT COUNT(*) FROM native_sources').fetchone()[0] != before_sources+len(hashes)
                or db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'):
                raise ValueError("New Atlas SQLite source-native row integrity failed")
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return {'prior_wid_wdi_rows_preserved':before,'fred_eurostat_rows_added':len(observations),
            'native_records_in_new_attachment':before+len(observations),
            'added_native_source_series':len(hashes)}


def build(parent_archive, source_archive, out, snapshot):
    if not snapshot.startswith('atlas-native-') or not all(c.isalnum() or c in '-_' for c in snapshot):
        raise ValueError("Invalid immutable edition identifier")
    if sha256(parent_archive) != PARENT_SHA:
        raise ValueError("Original certified WID/WDI private attachment digest changed")
    rows, hashes, report = read_source_release(source_archive)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / (snapshot+'.tar.gz')
    receipt = out / (snapshot+'.integration_report.json')
    if target.exists() or receipt.exists():
        raise FileExistsError("Atlas private editions must never be overwritten")
    with tempfile.TemporaryDirectory(dir=out) as td:
        work = Path(td)
        members = read_members(parent_archive, 'atlas-native-wid-wdi-20261116-43780249697',
                               {'native_source_extension.sqlite','source_native_release_manifest.json'})
        manifest = json.loads(members['source_native_release_manifest.json'])
        if (manifest['native_records_queryable_with_attachment'] != PARENT_ROWS or
            manifest['public_release'] is not False):
            raise ValueError("Uncertified native WDI/WID attachment parent")
        root = work / snapshot
        root.mkdir()
        dest = root / 'native_source_extension.sqlite'
        dest.write_bytes(members['native_source_extension.sqlite'])
        details = extend_sqlite(dest, rows, hashes)
        dump(root/'source_native_release_manifest.json',{
            'parent_attachment_sha256':PARENT_SHA,
            'original_economic_source_release_sha256':SOURCES_SHA,
            'original_run_observations':report['observations'],
            **details,'public_release':False,'research_ready_causal':False,
            'harmonized_units':False,'historical_canonical_sqlite_modified':False})
        with tarfile.open(target,'w:gz') as tar:
            tar.add(root,arcname=snapshot)
    details.update({'snapshot_id':snapshot,'archive_sha256':sha256(target),
                    'parent_attachment_sha256':PARENT_SHA,
                    'source_archive_sha256':SOURCES_SHA,'public_release':False})
    dump(receipt,details)
    return details


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parent-archive',required=True,type=Path)
    p.add_argument('--source-archive',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--snapshot-id',required=True)
    a=p.parse_args()
    print(json.dumps(build(a.parent_archive,a.source_archive,a.out,a.snapshot_id),indent=2))


if __name__=='__main__':
    main()
