#!/usr/bin/env python3
"""Phase 3: read-only, source-aware audit of a canonical-native economic warehouse.

No imputation, aggregation, cross-source harmonization, or causal estimates.
The verified source database is opened read-only; source-native fields are preserved.
Use --self-test for dependency-free regression tests with synthetic data.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import sqlite3
import tarfile
import tempfile

SOURCES = {
    'worldbank_wdi_expanded', 'bls_qcew_us_national', 'census_cbp_states',
    'us_treasury_mts', 'census_bfs_monthly',
}
BASELINE_COUNT = 2_780_117
NATIVE_COUNT = 2_104_609
FAMILIES = 24


def jsonfile(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha256_file(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def extract_verified_db(archive, output):
    """Extract only the database, verifying it against the derived inventory SHA256.

    Archive input is checked for path traversal, duplicate entries and special files;
    the inventory (not merely a filename) must specify this database's hash and size.
    """
    output=Path(output)
    with tarfile.open(archive,'r:gz') as tar:
        members=tar.getmembers()
        if not members or len(members)>8000:
            raise ValueError('Unexpected archive member count')
        names=set();roots=set()
        for m in members:
            name=PurePosixPath(m.name)
            if (m.name.startswith('/') or '..' in name.parts or
                    not name.parts or m.name.startswith('./') or
                    not (m.isfile() or m.isdir())):
                raise ValueError('Unsafe archive entry')
            if m.name in names:raise ValueError('Duplicate archive entry')
            names.add(m.name);roots.add(name.parts[0])
        if len(roots)!=1:raise ValueError('Multiple archive roots')
        root=next(iter(roots))
        def read_regular(name,maximum):
            try:m=tar.getmember(name)
            except KeyError as e:raise ValueError('Missing '+name) from e
            if not m.isfile() or m.size>maximum:raise ValueError('Bad member '+name)
            with tar.extractfile(m) as f:return f.read()
        inventory=json.loads(read_regular(root+'/unified_inventory.json',10_000_000))
        manifest=json.loads(read_regular(root+'/canonical_warehouse/manifest.json',10_000_000))
        parent=json.loads(read_regular(root+'/parent_warehouse_manifest.json',10_000_000))
        assert manifest['snapshot_id']==inventory['snapshot_id']==root, 'Snapshot ID disagreement'
        if (int(manifest['total_source_observations'])!=BASELINE_COUNT or
                int(parent['total_source_observations'])!=BASELINE_COUNT or
                int(inventory['native_rows_materialized'])!=NATIVE_COUNT or
                int(inventory['canonical_source_families'])!=FAMILIES):
            raise ValueError('Snapshot counts differ from audited parent')
        if inventory.get('native_rows_are_harmonized') is not False:
            raise ValueError('Native harmonization status unexpected')
        target='canonical_warehouse/warehouse.sqlite'
        matches=[m for m in inventory['source_files'] if m.get('path')==target]
        if len(matches)!=1:raise ValueError('Missing/duplicated SQLite inventory entry')
        expected=matches[0]
        member=tar.getmember(root+'/'+target)
        if not member.isfile() or member.size!=int(expected['bytes']):
            raise ValueError('SQLite member size mismatch')
        output.parent.mkdir(parents=True,exist_ok=True)
        h=hashlib.sha256()
        with tar.extractfile(member) as f,output.open('wb') as dest:
            for b in iter(lambda:f.read(1024*1024),b''):
                h.update(b);dest.write(b)
        if h.hexdigest()!=expected['sha256']:
            output.unlink(missing_ok=True)
            raise ValueError('SQLite database checksum mismatch')
        return inventory,manifest


def parse_number(value):
    if value is None or str(value).strip()=='':return None
    try:v=float(str(value).replace(',','').strip())
    except (ValueError,TypeError,OverflowError):return None
    return v if math.isfinite(v) else None


def iso_codes(file):
    with Path(file).open(newline='',encoding='utf-8-sig') as f:
        result={r['iso3'] for r in csv.DictReader(f)
                if r.get('classification')=='ISO_3166_1_country_or_territory'}
    if len(result)<240:raise ValueError('ISO reference has too few country and territory codes')
    return result


def trimmed(counter, n=12):
    return [{'value':k,'rows':v} for k,v in counter.most_common(n)]


def summarize_source(db,source,iso,original_wdi):
    counters=Counter(); geos=Counter();series=Counter();years=Counter();status=Counter(); flags=Counter()
    seen_wdi=set(); overlaps=set(); overlap_same=0; overlap_diff=0
    cursor=db.execute('''SELECT year_text,period_text,geo_code,series_code,industry_code,
                        value_text,raw_row_json FROM native_records
                        WHERE source_id=? ORDER BY ordinal''',(source,))
    for year,period,geo,indicator,industry,value,raw in cursor:
        counters['rows']+=1
        row=json.loads(raw)
        if not isinstance(row,dict):raise ValueError('Non-object original native record')
        y=(year or '').strip(); p=(period or '').strip();g=(geo or '').strip();s=(indicator or '').strip()
        geos[g or '(missing)']+=1;series[s or '(missing)']+=1
        years[y or '(missing)']+=1
        if not y:counters['missing_search_year']+=1
        elif not (y.isdigit() and len(y)==4 and 1800<=int(y)<=2100):counters['unusual_search_year']+=1
        if not p:counters['missing_search_period']+=1
        if not g:counters['missing_search_geography']+=1
        if not s:counters['missing_search_series']+=1
        if parse_number(value) is None:counters['non_numeric_or_blank_search_value']+=1
        if source=='worldbank_wdi_expanded':
            if g not in iso:counters['non_iso_country_or_aggregate']+=1
            obs_status=(row.get('obs_status') or '').strip()
            if obs_status:status[obs_status]+=1
            key=(s,g,y)
            if key in seen_wdi:counters['repeated_wdi_indicator_geography_year']+=1
            seen_wdi.add(key)
            if key in original_wdi:
                overlaps.add(key);old_value=original_wdi[key]
                new_value=parse_number(value)
                if new_value is not None and old_value is not None:
                    if math.isclose(new_value,old_value,rel_tol=1e-10,abs_tol=1e-10):overlap_same+=1
                    else:overlap_diff+=1
        elif source=='census_cbp_states':
            f=(row.get('empflag') or '').strip()
            if f:flags[f]+=1
            if parse_number(row.get('emp')) is None:counters['missing_or_suppressed_emp']+=1
            if len(g)!=2 or not g.isdigit():counters['unexpected_state_fips']+=1
            # NAICS rows may repeat across legal forms and employment-size groups;
            # DO NOT aggregate those rows into state totals.
        elif source=='bls_qcew_us_national':
            if not p or '-Q' not in p:counters['unexpected_quarter_period']+=1
            for key in ('disclosure_code','disclosure_flag'):
                val=(row.get(key) or '').strip()
                if val:flags[val]+=1
            # QCEW varies by industry, ownership, and aggregation level.
            # The search value (month 1 employment) is not the annual average.
        elif source=='us_treasury_mts':
            if not str(row.get('record_date','')).strip():counters['missing_record_date']+=1
            # Treasury categories and monthly/fiscal-YTD amounts must not be summed blindly.
        elif source=='census_bfs_monthly':
            # Store an indexed-readiness audit, not a claim of known seasonal adjustment.
            for k in ('sa','seasonal_adjustment','seasonal_adj'):
                if (row.get(k) or '').strip():flags[f'{k}={row[k]}']+=1
    result={'source_id':source,'audited_rows':counters['rows'],
            'source_search_key_quality':dict(counters),
            'unique_geo_search_codes':len(geos),'unique_series_search_codes':len(series),
            'year_search_range':{'first':min((k for k in years if k.isdigit()),default=None),
                                 'last':max((k for k in years if k.isdigit()),default=None)},
            'top_search_geographies':trimmed(geos),
            'top_search_series':trimmed(series),
            'raw_flags':trimmed(flags),
            'source_status_values':trimmed(status),
            'data_use':'source_native_only_not_harmonized'}
    if source=='worldbank_wdi_expanded':
        result['warning']='WDI includes missing values, aggregates and vintage-specific revisions; overlapping country-year-indicator keys are not new observations.'
        result['wdi_overlap_audit']={
            'existing_wdi_indicator_geography_year_keys':len(original_wdi),
            'native_unique_keys':len(seen_wdi),
            'keys_already_in_existing_wdi':len(overlaps),
            'overlap_numeric_values_equal':overlap_same,
            'overlap_numeric_values_different_review_vintage':overlap_diff,
            'new_keys_not_in_existing_wdi':len(seen_wdi-overlaps),
            'warning':'Matching keys do not establish identical revision vintage, unit, or release date. Never blindly append.'}
    elif source=='census_cbp_states':
        result['warning']='CBP legal-form, industry and employment-size rows are nonadditive; suppression flags retained.'
    elif source=='bls_qcew_us_national':
        result['warning']='QCEW aggregation levels/industry/ownership can overlap; month1 employment is not annual average.'
    elif source=='us_treasury_mts':
        result['warning']='Treasury table fields mix classifications and monthly/YTD receipts and outlays; verify fiscal definitions.'
    else:
        result['warning']='BFS seasonality, geography and category definitions require review before comparisons.'
    return result


def audit(db_path,iso_file,output,*,reference_count=BASELINE_COUNT,native_count=NATIVE_COUNT,
          required_sources=SOURCES):
    """Read-only audit; never writes a source DB or modifies any analytical observations."""
    output=Path(output)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('Refusing to overwrite existing quality-report output')
    output.mkdir(parents=True,exist_ok=True)
    iso=iso_codes(iso_file)
    with sqlite3.connect(f'file:{Path(db_path).resolve()}?mode=ro',uri=True) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':
            raise ValueError('SQLite quick_check failed')
        original_count=db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
        family_count=db.execute('SELECT COUNT(*) FROM source_files').fetchone()[0]
        native_total=db.execute('SELECT COUNT(*) FROM native_records').fetchone()[0]
        if reference_count is not None and original_count!=reference_count:
            raise ValueError('Existing analytical observation count changed')
        if native_count is not None and native_total!=native_count:
            raise ValueError('Native source rows lost or duplicated')
        sources={r[0]:(r[1],r[2],r[3],r[4]) for r in db.execute(
            'SELECT source_id, original_records, indexed_records, status, is_harmonized FROM native_sources')}
        if set(sources)!=set(required_sources):raise ValueError('Native source inventory differs')
        for name,(original,indexed,status,flag) in sources.items():
            if original!=indexed or status!='SOURCE_NATIVE_UNHARMONIZED' or flag!=0:
                raise ValueError('Native provenance or status changed: '+name)
        # Compare exact World Bank code + year + indicator keys only. Similar indicator
        # labels from different providers must not be treated as equivalents.
        original_wdi={}
        for series,geo,year,value in db.execute(
                "SELECT series,country_or_polity_code,year,value FROM observations WHERE family='wdi'"):
            original_wdi[(str(series or ''),str(geo or ''),str(year))]=parse_number(value)
        reports={}
        for name in sorted(sources):
            result=summarize_source(db,name,iso,original_wdi)
            if result['audited_rows']!=sources[name][1]:
                raise ValueError('Per-source row accounting failure: '+name)
            reports[name]=result
        if sum(v['audited_rows'] for v in reports.values())!=native_total:
            raise ValueError('Total source audit accounting failure')
    report={'schema_version':1,'created_utc':datetime.now(timezone.utc).isoformat(),
            'base_analytical_observations_unchanged':original_count,
            'base_source_families_unchanged':family_count,
            'queryable_source_native_rows_audited':native_total,
            'sources_audited':len(sources),
            'source_reports':reports,
            'ready_for_cross_provider_harmonization':False,
            'ready_for_causal_inference':False,
            'notes':['This is a read-only source quality audit, not an integration of normalized measures.',
                     'Source native row counts and baseline observations overlap; do not sum them as independent datapoints.',
                     'Country ISO3 eligibility does not prove a source observation is a country rather than an aggregate.',
                     'WDI overlap counts depend on source vintages and native indexing; differing values require manual review.',
                     'Employment, business and industry detail often overlaps and cannot be summed without classification filters.',
                     'No inference, causal attribution, or country policy counterfactual was calculated.']}
    (output/'native_quality_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    fields=['source_id','audited_rows','missing_search_year','missing_search_geography',
            'missing_search_series','non_numeric_or_blank_search_value','special_review']
    with (output/'native_quality_summary.csv').open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for name,r in reports.items():
            c=r['source_search_key_quality']
            writer.writerow({'source_id':name,'audited_rows':r['audited_rows'],
                             **{k:c.get(k,0) for k in fields[2:-1]},
                             'special_review':r['warning']})
    checksums={p.name:sha256_file(p) for p in output.iterdir() if p.is_file()}
    (output/'sha256_manifest.json').write_text(json.dumps(checksums,indent=2)+'\n')
    return report


def selftest():
    with tempfile.TemporaryDirectory() as t:
        d=Path(t);source=d/'native.sqlite';ref=d/'iso.csv'
        with ref.open('w',newline='') as f:
            writer=csv.writer(f);writer.writerow(['iso3','classification'])
            for i in range(241):writer.writerow([f'{i:03d}','ISO_3166_1_country_or_territory'])
            writer.writerow(['USA','ISO_3166_1_country_or_territory'])
        from importlib.util import spec_from_file_location, module_from_spec
        module_path=Path(__file__).resolve().parents[1]/'warehouse'/'native_materialize.py'
        if module_path.exists():
            spec=spec_from_file_location('native_materialize',module_path)
            module=module_from_spec(spec);spec.loader.exec_module(module)
            schema=module.schema()
        else:
            schema='''CREATE TABLE native_sources (source_id TEXT,original_records INTEGER,indexed_records INTEGER,status TEXT,is_harmonized INTEGER);
            CREATE TABLE native_records (source_id TEXT,ordinal INTEGER,year_text TEXT,period_text TEXT,geo_code TEXT,series_code TEXT,industry_code TEXT,value_text TEXT,raw_row_json TEXT);'''
        with sqlite3.connect(source) as c:
            c.executescript('CREATE TABLE observations(family TEXT,series TEXT,country_or_polity_code TEXT,year INTEGER,value REAL);CREATE TABLE source_files(family TEXT);'+schema)
            c.execute("INSERT INTO observations VALUES('wdi','NY.GDP.MKTP.KD.ZG','USA',2022,2.5)")
            c.execute("INSERT INTO source_files VALUES('wdi')")
            for n in SOURCES:
                c.execute('INSERT INTO native_sources VALUES (?,?,?,?,?)' if not module_path.exists() else 'INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)',
                          (n,1,1,'SOURCE_NATIVE_UNHARMONIZED',0) if not module_path.exists() else (n,'x',1,1,'','x','SOURCE_NATIVE_UNHARMONIZED',0,'[]'))
                row={'year':'2022','value':'2.5'}
                if n=='worldbank_wdi_expanded':row.update({'indicator':'NY.GDP.MKTP.KD.ZG','country_id':'USA'})
                elif n=='census_cbp_states':row.update({'empflag':'D','emp':'N'})
                cols=(n,1,'2022','2022','USA','NY.GDP.MKTP.KD.ZG','', '2.5',json.dumps(row))
                c.execute('INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)',cols)
        result=audit(source,ref,d/'out',reference_count=1,native_count=5)
        assert result['queryable_source_native_rows_audited']==5
        assert result['source_reports']['worldbank_wdi_expanded']['wdi_overlap_audit']['keys_already_in_existing_wdi']==1
        assert result['source_reports']['census_cbp_states']['source_search_key_quality']['missing_or_suppressed_emp']==1
        assert sha256_file(source)
        print('PASS: 5-source audit, WDI key overlap, suppression flag, read-only baseline and SHA256 report')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--iso-codes',type=Path,default=Path('research/iso3_country_codes.csv'))
    parser.add_argument('--out',type=Path,default=Path('quality_output'))
    parser.add_argument('--self-test',action='store_true')
    args=parser.parse_args()
    if args.self_test:return selftest()
    if not args.archive:parser.error('--archive is required')
    with tempfile.TemporaryDirectory() as temp:
        path=Path(temp)/'warehouse.sqlite'
        inv,manifest=extract_verified_db(args.archive,path)
        report=audit(path,args.iso_codes,args.out)
        report['source_snapshot_id']=inv['snapshot_id']
        report['source_warehouse_manifest_snapshot_id']=manifest['snapshot_id']
        (args.out/'native_quality_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        checksums={p.name:sha256_file(p) for p in args.out.iterdir() if p.is_file() and p.name!='sha256_manifest.json'}
        (args.out/'sha256_manifest.json').write_text(json.dumps(checksums,indent=2)+'\n')
    print(json.dumps({'snapshot':report['source_snapshot_id'],
                      'warehouse_observations':report['base_analytical_observations_unchanged'],
                      'native_records_audited':report['queryable_source_native_rows_audited'],
                      'wdi_overlap':report['source_reports']['worldbank_wdi_expanded']['wdi_overlap_audit']},indent=2))

if __name__=='__main__':main()
