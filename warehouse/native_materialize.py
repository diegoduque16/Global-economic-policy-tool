#!/usr/bin/env python3
"""Materialize five audited source-native datasets *inside* the existing SQLite warehouse.

Native records are NOT converted into harmonized/causal observations. Original
warehouse observations remain unchanged and source records retain every source
field in JSON, with search keys to enable controlled downstream harmonization.
Standard-library only. Never changes an input archive or an existing snapshot.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone

EXPECTED = {
    'worldbank_wdi_expanded': 'world_bank_wdi_expanded_native.csv',
    'bls_qcew_us_national': 'bls_qcew_us_national_2020_2025_native.csv',
    'census_cbp_states': 'census_cbp_state_raw_2019_2023.zip',
    'us_treasury_mts': 'treasury_mts_table1_native.csv',
    'census_bfs_monthly': 'census_bfs_monthly_native.csv',
}
BASELINE_MIN = 2_780_117


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def extract_safe(archive, dest):
    """Extract a canonical snapshot with no symlinks, traversal or surprising siblings."""
    with tarfile.open(archive, 'r:gz') as src:
        members = src.getmembers()
        files = [m for m in members if m.isfile()]
        if not files or len(files)>8000 or any(not(m.isfile() or m.isdir()) for m in members):
            raise ValueError('Unsafe or empty canonical tar archive')
        roots = set()
        total_unpacked = 0
        for m in members:
            parts = PurePosixPath(m.name).parts
            if not parts or m.name.startswith('/') or '..' in parts or m.name.startswith('./'):
                raise ValueError('Unsafe tar member path')
            roots.add(parts[0]);total_unpacked += m.size if m.isfile() else 0
        if len(roots)!=1 or total_unpacked>6_000_000_000:
            raise ValueError('Unexpected canonical archive root/size')
        root=next(iter(roots))
        target=Path(dest)/root
        if target.exists():raise FileExistsError('Archive destination already exists')
        for m in files:
            rel=PurePosixPath(m.name).relative_to(root)
            output=target.joinpath(*rel.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            with src.extractfile(m) as source, output.open('wb') as out:
                shutil.copyfileobj(source,out)
    return target


def verify_package(root):
    inv=read_json(root/'unified_inventory.json')
    if inv.get('native_rows_materialized'):
        raise ValueError('Refusing to re-materialize a derived native-data snapshot')
    if inv.get('canonical_observations',0)<BASELINE_MIN or inv.get('canonical_source_families',0)<24:
        raise ValueError('Canonical baseline count/family regression')
    sources=inv.get('staged_sources',{})
    if set(sources)!=set(EXPECTED):raise ValueError('Expected exactly five staged sources')
    if any(x.get('status')!='STAGED_NOT_IN_OBSERVATIONS' for x in sources.values()):
        raise ValueError('Staged source state unexpectedly changed')
    for f in inv.get('source_files',[]):
        rel=PurePosixPath(f['path'])
        if '..' in rel.parts or rel.is_absolute():raise ValueError('Unsafe inventory path')
        target=root.joinpath(*rel.parts)
        if not target.is_file() or sha256(target)!=f['sha256'] or target.stat().st_size!=int(f['bytes']):
            raise ValueError('Canonical snapshot inventory SHA256 mismatch: '+str(rel))
    for name,expected_filename in EXPECTED.items():
        source=sources[name]
        if source.get('file')!=f'source_native/{expected_filename}' or int(source.get('rows',0))<=0:
            raise ValueError('Unexpected staged source entry: '+name)
        data=root/'staged_source_native'/expected_filename
        if not data.is_file() or sha256(data)!=source['sha256']:
            raise ValueError('Staged file SHA256 mismatch: '+name)
    original_manifest=read_json(root/'canonical_warehouse'/'manifest.json')
    if original_manifest['total_source_observations']!=inv['canonical_observations']:
        raise ValueError('Baseline manifest count mismatch')
    dbfile=root/'canonical_warehouse'/'warehouse.sqlite'
    with sqlite3.connect(dbfile) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('Canonical SQLite integrity check failed')
        count=db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
        families=db.execute('SELECT COUNT(*) FROM source_files').fetchone()[0]
        if count!=inv['canonical_observations'] or families!=inv['canonical_source_families']:
            raise ValueError('Canonical SQLite observations/family totals differ')
    return inv,original_manifest


def schema():
    return '''
    CREATE TABLE native_sources (
        source_id TEXT PRIMARY KEY, raw_sha256 TEXT NOT NULL,
        original_records INTEGER NOT NULL, indexed_records INTEGER NOT NULL,
        original_scope TEXT NOT NULL, source_name TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status='SOURCE_NATIVE_UNHARMONIZED'),
        is_harmonized INTEGER NOT NULL CHECK(is_harmonized=0),
        fields_json TEXT NOT NULL
    );
    CREATE TABLE native_records (
        source_id TEXT NOT NULL REFERENCES native_sources(source_id),
        ordinal INTEGER NOT NULL, year_text TEXT, period_text TEXT,
        geo_code TEXT, series_code TEXT, industry_code TEXT,
        value_text TEXT, raw_row_json TEXT NOT NULL,
        PRIMARY KEY (source_id,ordinal)
    ) WITHOUT ROWID;
    CREATE INDEX idx_native_source_geo_year ON native_records(source_id,geo_code,year_text);
    CREATE INDEX idx_native_source_series_period ON native_records(source_id,series_code,period_text);
    '''


def get(row,*keys):
    lower={k.lower():v for k,v in row.items() if isinstance(k,str)}
    for key in keys:
        v=lower.get(key.lower())
        if v is not None and str(v).strip()!='':return str(v).strip()
    return ''


def source_keys(source,row,cbp_year=None):
    if source=='worldbank_wdi_expanded':
        return (get(row,'year'),get(row,'year'),get(row,'country_id'),
                get(row,'indicator'),'',get(row,'value'))
    if source=='bls_qcew_us_national':
        year=get(row,'year'); q=get(row,'qtr','requested_quarter')
        return (year,year+'-Q'+q if year and q else year,
                get(row,'area_fips','area_code'),get(row,'industry_code','industry'),
                get(row,'industry_code','industry'),get(row,'month1_emplvl','annual_avg_emplvl'))
    if source=='census_cbp_states':
        return (str(cbp_year),str(cbp_year),get(row,'fipstate','state'),
                get(row,'naics','naics2017','naics2022'),get(row,'naics','naics2017','naics2022'),
                get(row,'emp'))
    if source=='us_treasury_mts':
        date=get(row,'record_date');return (date[:4],date,'US',
                get(row,'classification_desc','classification'),'',
                get(row,'current_month_receipts_amt','current_month_outlays_amt'))
    if source=='census_bfs_monthly':
        date=get(row,'time','period','date','month');year=date[:4] if date else ''
        return (year,date,get(row,'geo','geography','region','state'),
                get(row,'series','measure','category','variable'),'',get(row,'value'))
    raise ValueError('Unrecognized source')


def csv_rows(stream):
    reader=csv.DictReader(stream)
    fields=reader.fieldnames
    if not fields or len(set(fields))!=len(fields) or len(fields)>400:
        raise ValueError('Missing, duplicate or excessive CSV fields')
    for row in reader:
        if None in row or any(v is None for v in row.values()):
            raise ValueError('Ragged source-native CSV record')
        yield fields,row


def enumerate_native(source,path,cbp_requests=None):
    """Yield (year_override, fields, row) with original source field names."""
    if source!='census_cbp_states':
        with Path(path).open(encoding='utf-8-sig',newline='') as stream:
            for fields,row in csv_rows(stream):
                yield None,fields,row
    else:
        if not cbp_requests or [int(x['year']) for x in cbp_requests]!=list(range(2019,2024)):
            raise ValueError('CBP 2019–2023 source release metadata missing')
        with zipfile.ZipFile(path) as outer:
            if set(outer.namelist())!={x['member'] for x in cbp_requests}:
                raise ValueError('CBP outer ZIP member mismatch')
            for release in cbp_requests:
                raw=outer.read(release['member'])
                if hashlib.sha256(raw).hexdigest()!=release['raw_zip_sha256']:
                    raise ValueError('CBP original Census ZIP checksum mismatch')
                with zipfile.ZipFile(io.BytesIO(raw)) as inner:
                    member=release['csv_member']
                    if member not in inner.namelist():raise ValueError('CBP audited CSV member missing')
                    original_count=0
                    with inner.open(member) as inp:
                        with io.TextIOWrapper(inp,encoding='utf-8-sig',newline='') as f:
                            for fields,row in csv_rows(f):
                                original_count+=1
                                yield int(release['year']),fields,row
                    if original_count!=int(release['rows']):
                        raise ValueError(f"CBP {release['year']} row-count mismatch")


def insert_native(db,root,inventory,batch_audit):
    if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='native_records'").fetchone():
        raise ValueError('Native table already exists; refuse duplicate integration')
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript(schema())
    counters={}
    reqs={x.get('collector'):x.get('requests',[]) for x in batch_audit['successful']}
    for name,source in inventory['staged_sources'].items():
        expected=int(source['rows'])
        path=root/'staged_source_native'/EXPECTED[name]
        rows_buffer=[];n=0;fields_seen=None
        # Reserve source row; on any failure the transaction rolls back.
        db.execute('INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)',
                   (name,source['sha256'],expected,0,source.get('scope',''),
                    source.get('source',name),'SOURCE_NATIVE_UNHARMONIZED',0,'[]'))
        for year_override,fields,row in enumerate_native(name,path,reqs.get(name)):
            if fields_seen is None:fields_seen=fields
            elif fields_seen!=fields and name!='census_cbp_states':
                raise ValueError('Unexpected change in native CSV columns: '+name)
            n+=1
            year,period,geo,series,industry,value=source_keys(name,row,year_override)
            rows_buffer.append((name,n,year,period,geo,series,industry,value,
                                json.dumps(row,ensure_ascii=False,separators=(',',':'))))
            if len(rows_buffer)>=5000:
                db.executemany('INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)',rows_buffer)
                rows_buffer=[]
        if rows_buffer:db.executemany('INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)',rows_buffer)
        if n!=expected:raise ValueError(f'{name}: audited {expected} vs actual {n} native rows')
        db.execute('UPDATE native_sources SET indexed_records=?,fields_json=? WHERE source_id=?',
                   (n,json.dumps(fields_seen or [],ensure_ascii=False),name))
        counters[name]=n
    if set(counters)!=set(EXPECTED):raise ValueError('Incomplete source-native integration')
    return counters


def materialize(input_archive,output_dir,snapshot_id):
    output_dir=Path(output_dir)
    if not snapshot_id or any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for ch in snapshot_id):
        raise ValueError('Unsafe new snapshot ID')
    output_dir.mkdir(parents=True,exist_ok=True)
    final=output_dir/snapshot_id
    out_tar=output_dir/(snapshot_id+'.tar.gz')
    if final.exists() or out_tar.exists():raise FileExistsError('Never overwrite a previous snapshot')
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        root=extract_safe(input_archive,tmp)
        original_root_name=root.name
        inventory,old_manifest=verify_package(root)
        original_inv=read_json(root/'unified_inventory.json')
        # Keep byte-for-byte copies of original manifests for reproducible provenance.
        shutil.copy2(root/'unified_inventory.json',root/'parent_unified_inventory.json')
        shutil.copy2(root/'canonical_warehouse'/'manifest.json',root/'parent_warehouse_manifest.json')
        baseline=int(inventory['canonical_observations'])
        dbpath=root/'canonical_warehouse'/'warehouse.sqlite'
        with sqlite3.connect(dbpath) as db:
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA foreign_keys=ON')
            batch_audit=read_json(root/'batch_audit.json')
            counters=insert_native(db,root,inventory,batch_audit)
            db.commit()
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('Materialized SQLite integrity check failed')
            if db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]!=baseline:
                raise ValueError('Original observation count changed')
            actual=db.execute('SELECT COUNT(*) FROM native_records').fetchone()[0]
            if actual!=sum(counters.values()):raise ValueError('Native row count does not match report')
            if db.execute('SELECT COUNT(*) FROM source_files').fetchone()[0]!=inventory['canonical_source_families']:
                raise ValueError('Original source family count changed')
            if db.execute('SELECT COUNT(*) FROM native_sources').fetchone()[0]!=5:
                raise ValueError('Native source count changed')
        if actual!=int(inventory['all_staged_records_not_integrated']):
            raise ValueError('Native row count does not match source inventory')
        # Derived manifest can describe modified SQLite hash without tampering with parent.
        old_manifest['native_source_tables_materialized']=True
        old_manifest['native_record_count_unharmonized']=actual
        old_manifest['derived_from_snapshot_id']=old_manifest['snapshot_id']
        old_manifest['snapshot_id']=snapshot_id
        for rec in old_manifest['files']:
            if rec['path']=='warehouse.sqlite':
                rec['sha256']=sha256(dbpath);rec['bytes']=dbpath.stat().st_size
        dump(root/'canonical_warehouse'/'manifest.json',old_manifest)
        inventory['parent_canonical_package_id']=original_root_name
        inventory['snapshot_id']=snapshot_id
        inventory['native_rows_materialized']=actual
        inventory['native_source_rows']=counters
        inventory['native_rows_are_harmonized']=False
        inventory['warehouse_updated_with_staged_rows']=False
        inventory['native_records_are_queryable_in_same_sqlite']=True
        inventory['created_utc']=datetime.now(timezone.utc).isoformat()
        inventory['warning']='Native sources indexed in native_records but NOT appended to harmonized observations. Do not sum across series/industry and ownership classifications.'
        inventory['source_files']=[{'path':str(p.relative_to(root)),'sha256':sha256(p),'bytes':p.stat().st_size}
                                   for p in sorted(root.rglob('*')) if p.is_file() and p.name!='unified_inventory.json']
        dump(root/'unified_inventory.json',inventory)
        shutil.move(str(root),str(final))
        final.rename(output_dir/snapshot_id) if final.name!=snapshot_id else None
        with tarfile.open(out_tar,'w:gz') as tar:
            tar.add(final,arcname=snapshot_id)
        report={'snapshot_id':snapshot_id,'parent_snapshot_id':original_inv['snapshot_id'],
                'canonical_observations':baseline,'canonical_source_families':inventory['canonical_source_families'],
                'source_native_unharmonized_rows':actual,'native_sources':counters,
                'warehouse_modified_observations':False,'native_indexing_only':True,
                'archive_sha256':sha256(out_tar)}
        dump(output_dir/(snapshot_id+'.integration_report.json'),report)
        return report


def selftest():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);dbpath=root/'test.sqlite'
        with sqlite3.connect(dbpath) as db:
            db.execute('CREATE TABLE observations (id INTEGER PRIMARY KEY)')
            db.execute('INSERT INTO observations VALUES(1)')
            db.execute('CREATE TABLE source_files (family TEXT PRIMARY KEY)')
            db.execute("INSERT INTO source_files VALUES ('baseline')")
            db.executescript(schema())
            db.execute('INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)',
                       ('x','abc',1,1,'','x','SOURCE_NATIVE_UNHARMONIZED',0,'[]'))
            db.execute('INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)',
                       ('x',1,'2022','2022','US','NAICS','00','','{"emp":"N","empflag":"D"}'))
            assert db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]==1
            assert db.execute('SELECT COUNT(*) FROM native_records').fetchone()[0]==1
            assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert source_keys('worldbank_wdi_expanded',{'year':'2025','country_id':'USA','indicator':'x'})[2]=='USA'
        assert source_keys('census_cbp_states',{'fipstate':'06','naics':'311','empflag':'D'},2022)[:3]==('2022','2022','06')
        assert source_keys('bls_qcew_us_national',{'year':'2024','qtr':'1'})[1]=='2024-Q1'
        assert source_keys('us_treasury_mts',{'record_date':'2024-01-01'})[0]=='2024'
        assert source_keys('census_bfs_monthly',{'time':'2024-01'})[0]=='2024'
        print('PASS: source-specific native keys, suppression preservation, baseline isolation and SQLite integrity')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--archive',type=Path)
    ap.add_argument('--output',type=Path,default=Path('native_output'))
    ap.add_argument('--snapshot-id',type=str)
    ap.add_argument('--self-test',action='store_true')
    args=ap.parse_args()
    if args.self_test: return selftest()
    if not args.archive or not args.snapshot_id:ap.error('--archive and --snapshot-id required')
    print(json.dumps(materialize(args.archive,args.output,args.snapshot_id),indent=2))

if __name__=='__main__':main()
