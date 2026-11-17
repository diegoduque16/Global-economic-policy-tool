#!/usr/bin/env python3
"""Append source-verified BLS JOLTS numeric records to the SAME private Atlas SQLite attachment.

Never alters the original historical canonical database or publishes raw data.
Each official BLS series_id is a separate native source identity; state, industry,
seasonal adjustment, unit and date remain source-defined.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import tarfile
import tempfile

from open_data.bls_jolts import BASE, COLUMNS, SERIES_REGEX, TARGET, verify_release
from warehouse.native_materialize import sha256, dump
from warehouse.us_europe_native_extension import read_members

PARENT_SHA = '6d4bbec96428a6981d548ec51c3d56160452de63fd4c363f5c9749878767ea85'
BLS_SOURCE_SHA = '668cb9857c7e3af856162d0c9ef693ca8706ad26efe589c84e2709c0f70824cf'
PARENT_PREFIX = 'atlas-native-wid-wdi-econ-20261117-44167631766'
PARENT_ROWS = 630408
BLS_ROWS = 252710
BLS_SERIES = 830
PARENT_SOURCES = 146
BLS_RAW_FILES = ('jt.series','jt.data.1.AllItems')


def source_release(archive, workspace, *, check_pinned=True):
    """Read only expected archive members; replay original source data without network."""
    if check_pinned and sha256(archive) != BLS_SOURCE_SHA:
        raise ValueError('BLS original private archive SHA256 mismatch')
    accepted={'run_report.json','processed/bls_jolts_panel.csv',
              'raw/jt.series','raw/jt.data.1.AllItems'}
    members=read_members(archive,'bls_jolts',accepted)
    if set(members)!=accepted:
        raise ValueError('Missing original BLS source or processed panel')
    root=Path(workspace)/'bls_jolts'
    for relative,payload in members.items():
        dest=root/relative
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(payload)
    validation=verify_release(root)
    report=json.loads((root/'run_report.json').read_text(encoding='utf-8'))
    if (validation['observations']!=BLS_ROWS or validation['series_ids']!=BLS_SERIES
        or report['first_year']!=2000 or report['last_year']!=2026
        or report['errors']!=[] or report['conflicts']!=[]
        or report['research_ready'] is not False):
        raise ValueError('Certified BLS JOLTS source release scope mismatch')
    manifest={m['name']:m for m in report['manifest']}
    if set(manifest)!=set(BLS_RAW_FILES):
        raise ValueError('BLS source manifest scope changed')
    return root/'processed/bls_jolts_panel.csv',manifest,report


def insert_jolts(dbpath, panel, manifest, *,
                 expected_parent_rows=PARENT_ROWS,
                 expected_parent_sources=PARENT_SOURCES,
                 expected_rows=BLS_ROWS,
                 expected_series=BLS_SERIES):
    """Preserve parent rows, append original series + period keys transactionally."""
    origin_hash=manifest['jt.data.1.AllItems']['sha256']
    metadata_hash=manifest['jt.series']['sha256']
    url=BASE+'jt.data.1.AllItems'
    with sqlite3.connect(dbpath) as db:
        prior=db.execute('SELECT COUNT(*) FROM native_records').fetchone()[0]
        parent_sources=db.execute('SELECT COUNT(*) FROM native_sources').fetchone()[0]
        if prior!=expected_parent_rows or parent_sources!=expected_parent_sources:
            raise ValueError('Parent source-native SQLite edition changed')
        db.execute('BEGIN IMMEDIATE')
        try:
            source_counts={}
            keys=set()
            batch=[]
            with Path(panel).open(newline='',encoding='utf-8') as inp:
                reader=csv.DictReader(inp)
                if tuple(reader.fieldnames or ())!=tuple(COLUMNS):
                    raise ValueError('BLS processed panel schema differs')
                for row in reader:
                    dims=json.loads(row['disaggregation'])
                    sid=dims.get('series_id','')
                    period=row['period']
                    if not isinstance(sid,str) or not SERIES_REGEX.fullmatch(sid):
                        raise ValueError('BLS source series code invalid')
                    if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])',period):
                        raise ValueError('BLS native monthly period invalid')
                    if period[:4]!=str(row['year']) or int(row['year'])>2026:
                        raise ValueError('BLS year / period inconsistent')
                    element=dims.get('dataelement_code')
                    rate=dims.get('ratelevel_code')
                    if (element not in TARGET or rate not in ('L','R')
                        or dims.get('seasonal')!='S'
                        or dims.get('area_code')!='00000'
                        or dims.get('sizeclass_code')!='00'
                        or sid[18:20]!=element or sid[-1]!=rate):
                        raise ValueError('BLS original series dimensions not preserved')
                    expected_unit='percent' if rate=='R' else (
                        'thousands_of_vacancies' if element=='JO' else 'thousands_of_workers')
                    if (row['country_iso3']!='USA'
                        or row['provider']!='US Bureau of Labor Statistics JOLTS'
                        or row['source_url']!=url
                        or row['frequency']!='monthly'
                        or row['unit']!=expected_unit
                        or row['series']!=f'JOLTS.{TARGET[element]}.{rate}.SA'):
                        raise ValueError('BLS source-native metadata/units inconsistent')
                    if (dims.get('industry_code')!=sid[3:9]
                        or dims.get('state_code')!=sid[9:11]
                        or dims.get('area_code')!=sid[11:16]
                        or dims.get('sizeclass_code')!=sid[16:18]):
                        raise ValueError('BLS series ID does not match source dimensions')
                    val=float(row['value'])
                    if not math.isfinite(val) or val<0:
                        raise ValueError('BLS source-native value invalid')
                    key=(sid,period)
                    if key in keys:
                        raise ValueError('Duplicate BLS original series / month')
                    keys.add(key)
                    source_id='bls_jolts_'+sid+'_'+origin_hash[:12]
                    if source_id not in source_counts:
                        source_counts[source_id]=0
                        details=json.dumps({
                            'provider':'BLS JOLTS',
                            'original_provider_series_id':sid,
                            'source_raw_sha256':origin_hash,
                            'source_metadata_sha256':metadata_hash,
                            'source_url':url,
                            'unit':expected_unit,'frequency':'monthly',
                            'geography_level':dims.get('geography_level'),
                            'industry_code':dims['industry_code'],
                            'state_code':dims['state_code'],
                            'seasonal_adjustment':'seasonally_adjusted',
                            'source_release_stage':'SOURCE_NATIVE_PRIVATE_UNHARMONIZED',
                            'overlap_with_fred_jolts':'POSSIBLE_NOT_DEDUPLICATED',
                        },sort_keys=True)
                        db.execute('INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)',
                            (source_id,origin_hash,0,0,details,
                             'US BLS JOLTS original bulk source file',
                             'SOURCE_NATIVE_UNHARMONIZED',0,json.dumps(list(COLUMNS))))
                    source_counts[source_id]+=1
                    batch.append((source_id,source_counts[source_id],period,period,'USA',
                                  sid,row['status_flag'],row['value'],
                                  json.dumps(row,sort_keys=True,separators=(',',':'))))
                    if len(batch)>=5000:
                        db.executemany('INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)',batch)
                        batch.clear()
            if batch:
                db.executemany('INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)',batch)
            if len(keys)!=expected_rows or len(source_counts)!=expected_series:
                raise ValueError('BLS original source numeric row / series count mismatch')
            db.executemany('UPDATE native_sources SET original_records=?,indexed_records=? WHERE source_id=?',
                           [(count,count,sid) for sid,count in source_counts.items()])
            total=db.execute('SELECT COUNT(*) FROM native_records').fetchone()[0]
            sources=db.execute('SELECT COUNT(*) FROM native_sources').fetchone()[0]
            if total!=prior+expected_rows or sources!=parent_sources+expected_series:
                raise ValueError('Native SQLite parent preservation/row counts failed')
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('BLS native SQLite failed integrity_check')
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return {'parent_records_preserved':prior,'bls_jolts_records_added':len(keys),
            'new_attachment_records':total,'bls_original_series_added':len(source_counts),
            'parent_source_series_preserved':parent_sources}


def build(parent_archive, bls_archive, output, snapshot):
    if not re.fullmatch(r'atlas-native-[a-zA-Z0-9_-]+',snapshot):
        raise ValueError('Invalid immutable Atlas snapshot identifier')
    if sha256(parent_archive)!=PARENT_SHA:
        raise ValueError('Original 630408-row Atlas parent digest changed')
    output=Path(output)
    output.mkdir(parents=True,exist_ok=True)
    archive=output/(snapshot+'.tar.gz')
    receipt=output/(snapshot+'.integration_report.json')
    if archive.exists() or receipt.exists():
        raise FileExistsError('Atlas private snapshots must never be overwritten')
    with tempfile.TemporaryDirectory() as temp:
        panel,source_manifest,report=source_release(bls_archive,temp)
        members=read_members(parent_archive,PARENT_PREFIX,
                             {'native_source_extension.sqlite','source_native_release_manifest.json'})
        if set(members)!={'native_source_extension.sqlite','source_native_release_manifest.json'}:
            raise ValueError('Incomplete certified native SQLite parent archive')
        parent_manifest=json.loads(members['source_native_release_manifest.json'])
        if (parent_manifest['native_records_in_new_attachment']!=PARENT_ROWS
            or parent_manifest['public_release'] is not False):
            raise ValueError('Uncertified WID/WDI/FRED/Eurostat parent edition')
        root=Path(temp)/snapshot
        root.mkdir()
        db=root/'native_source_extension.sqlite'
        db.write_bytes(members['native_source_extension.sqlite'])
        details=insert_jolts(db,panel,source_manifest)
        dump(root/'source_native_release_manifest.json',{
            'parent_archive_sha256':PARENT_SHA,
            'bls_original_source_archive_sha256':BLS_SOURCE_SHA,
            'bls_original_numeric_row_count':report['observations'],
            **details,'public_release':False,'harmonized_units':False,
            'research_ready_causal':False,'historical_canonical_sqlite_modified':False})
        with tarfile.open(archive,'w:gz') as tar:
            tar.add(root,arcname=snapshot)
    details.update({'snapshot_id':snapshot,'archive_sha256':sha256(archive),
                    'parent_archive_sha256':PARENT_SHA,
                    'bls_source_archive_sha256':BLS_SOURCE_SHA,'public_release':False})
    dump(receipt,details)
    return details


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parent-archive',required=True,type=Path)
    p.add_argument('--bls-archive',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--snapshot-id',required=True)
    args=p.parse_args()
    print(json.dumps(build(args.parent_archive,args.bls_archive,args.out,args.snapshot_id),indent=2))
