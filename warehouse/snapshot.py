"""Produce audited, source-separated SQLite snapshots from five existing collectors.

Requires only the Python standard library. No cross-provider interpolation, recoding,
statistical identification, or definition harmonization is performed here.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile

FAMILIES = {
    'wdi': ('wdi', 'wdi_panel.csv', 100_000),
    'us_europe': ('us_europe', 'us_europe_panel.csv', 5_000),
    'historical': ('historical', 'historical_panel.csv', 10_000),
    'imf_oecd': ('imf_oecd', 'imf_oecd_panel.csv', 10_000),
    'labor': ('labor', 'labor_panel.csv', 10_000),
}

OPTIONAL_FAMILIES = {
    'census_bds': ('census_bds', 'census_bds_panel.csv', 100000),
    'tariff_shocks': ('tariff', 'tariff_shocks_panel.csv', 185),
    'cait_ai_claims': ('cait', 'cait_panel.csv', 1872),
    'eurostat_ai': ('eurostat_ai', 'eurostat_ai_panel.csv', 50),
    'imf_gfs': ('imf_gfs', 'imf_gfs_panel.csv', 100),
    'bls_bed': ('bls_bed', 'bls_bed_panel.csv', 200),
    'oecd_cit': ('oecd_cit', 'oecd_cit_panel.csv', 100),
    'bls_jolts': ('bls_jolts', 'bls_jolts_panel.csv', 5000),
    'historical_turnover': ('historical_turnover', 'historical_turnover_panel.csv', 2800),
    'bocconi_fiscal': ('bocconi_fiscal', 'bocconi_fiscal_panel.csv', 1000),
    'imf_extension': ('imf_extension', 'imf_extension_panel.csv', 10000),
    'us_state_qcew': ('us_state_qcew', 'us_state_qcew_panel.csv', 5000),
    'us_state_qcew_historical': ('us_state_qcew_historical', 'us_state_qcew_historical_panel.csv', 9000),
    'us_state_bea': ('us_state_bea', 'us_state_bea_panel.csv', 5000),
    'us_state_tax': ('us_state_tax', 'us_state_tax_panel.csv', 5000),
    'bjk_state_rates': ('bjk_state_rates', 'bjk_state_rates_panel.csv', 10000),
    'bjk_county_rates': ('bjk_county_rates', 'bjk_county_rates_panel.csv', 100000),
}

SCHEMA = '''
CREATE TABLE snapshots (
    snapshot_id TEXT PRIMARY KEY, created_utc TEXT NOT NULL,
    git_sha TEXT, data_harmonized INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE source_files (
    family TEXT PRIMARY KEY, csv_sha256 TEXT NOT NULL,
    csv_rows INTEGER NOT NULL, source_report_observations INTEGER NOT NULL,
    first_year INTEGER, last_year INTEGER, country_codes INTEGER, series_count INTEGER,
    source_report_json TEXT NOT NULL
);
CREATE TABLE observations (
    observation_id INTEGER PRIMARY KEY,
    family TEXT NOT NULL REFERENCES source_files(family),
    provider TEXT NOT NULL, country_or_polity_code TEXT NOT NULL,
    year INTEGER NOT NULL, period TEXT NOT NULL, series TEXT NOT NULL,
    value REAL NOT NULL, unit TEXT NOT NULL, frequency TEXT NOT NULL,
    disaggregation_json TEXT NOT NULL, source_status TEXT NOT NULL,
    source_url TEXT NOT NULL, original_row_json TEXT NOT NULL
);
CREATE INDEX idx_family_country_year ON observations(family,country_or_polity_code,year);
CREATE INDEX idx_family_series_year ON observations(family,series,year);
CREATE INDEX idx_provider_period ON observations(provider,period);
'''


def digest(path: Path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def normalized(family, row):
    """Preserve native source units, labels, periods and classifications."""
    country_key = 'country_or_polity_code' if family == 'historical' else ('country_code' if family == 'imf_oecd' else 'country_iso3')
    series_key = 'indicator' if family in ('wdi', 'historical', 'imf_oecd') else 'series'
    country = (row.get(country_key) or '').strip()
    metric = (row.get(series_key) or '').strip()
    year = int(row['year'])
    value = float(row['value'])
    if not country or not metric or not math.isfinite(value) or not -1000 <= year <= 2200:
        raise ValueError(f'Bad {family} source row: country/metric/number/year')
    provider = row.get('provider') or row.get('source') or family
    period = row.get('period') or str(year)
    unit = row.get('unit') or 'source_defined'
    frequency = row.get('frequency') or ('irregular_historical' if family == 'historical' else 'annual')
    details = row.get('disaggregation') or '{}'
    if isinstance(details, str):
        try: parsed = json.loads(details)
        except json.JSONDecodeError: parsed = {'source_field': details}
    else: parsed = details
    extra = {k: row.get(k) for k in ('obs_status','status_flag','data_status','observation_kind') if row.get(k)}
    return (family,provider,country,year,str(period),metric,value,unit,frequency,
            json.dumps(parsed,sort_keys=True,ensure_ascii=False),
            json.dumps(extra,sort_keys=True,ensure_ascii=False),
            row.get('source_url') or '',json.dumps(row,sort_keys=True,ensure_ascii=False))


def _read_json(path):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc: raise ValueError(f'Missing or invalid source report {path}: {exc}') from exc


def build_snapshot(data_dir, output_dir, *, snapshot_id, git_sha='', include_raw=False, enforce_full=True):
    data_dir=Path(data_dir); output_dir=Path(output_dir)
    if not snapshot_id or any(x not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for x in snapshot_id):
        raise ValueError('Snapshot ID must contain only letters, digits, hyphens, underscores')
    snapshot_dir=output_dir/snapshot_id
    if snapshot_dir.exists(): raise FileExistsError(f'Will not overwrite existing snapshot: {snapshot_dir}')
    snapshot_dir.mkdir(parents=True)
    conn=None
    try:
        database=snapshot_dir/'warehouse.sqlite'
        conn=sqlite3.connect(database)
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript(SCHEMA)
        timestamp=dt.datetime.now(dt.timezone.utc).isoformat()
        conn.execute('INSERT INTO snapshots VALUES (?,?,?,0)',(snapshot_id,timestamp,git_sha))
        report_by_family={}; inventories=[]; grand_total=0
        enabled_families={**FAMILIES,**{k:v for k,v in OPTIONAL_FAMILIES.items() if (data_dir/v[0]/'run_report.json').is_file()}}
        for family, (subfolder, filename, minimum) in enabled_families.items():
            root=data_dir/subfolder
            csvfile=root/'processed'/filename
            reportfile=root/'run_report.json'
            report=_read_json(reportfile)
            if report.get('errors') or report.get('conflicts'):
                raise ValueError(f'{family} collector reported errors/conflicts: {report.get("errors")}, {report.get("conflicts")}')
            try: expected=int(report['observations'])
            except Exception as exc: raise ValueError(f'{family} missing source observation count') from exc
            if not csvfile.is_file(): raise FileNotFoundError(csvfile)
            # Register the source first; foreign keys prevent orphan observations.
            # Fill dimensions only after validating the entire source file.
            conn.execute('INSERT INTO source_files VALUES (?,?,?,?,?,?,?,?,?)',
                         (family,digest(csvfile),0,expected,None,None,None,None,json.dumps(report,sort_keys=True)))
            with csvfile.open('r',encoding='utf-8-sig',newline='') as f:
                reader=csv.DictReader(f)
                needed={'year','value'}
                if not needed.issubset(reader.fieldnames or []): raise ValueError(f'{family} missing essential columns')
                n=0; years=[]; countries=set(); metrics=set(); batch=[]
                for row in reader:
                    try: canonical=normalized(family,row)
                    except Exception as exc: raise ValueError(f'{family} row {n+2} invalid: {exc}') from exc
                    batch.append(canonical); n+=1
                    years.append(canonical[3]); countries.add(canonical[2]);metrics.add(canonical[5])
                    if len(batch)>=10_000:
                        conn.executemany('''INSERT INTO observations
                            (family,provider,country_or_polity_code,year,period,series,value,unit,frequency,disaggregation_json,source_status,source_url,original_row_json)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',batch)
                        batch.clear()
                if batch:
                    conn.executemany('''INSERT INTO observations
                        (family,provider,country_or_polity_code,year,period,series,value,unit,frequency,disaggregation_json,source_status,source_url,original_row_json)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',batch)
            if n != expected:
                raise ValueError(f'{family} source count mismatch: CSV={n}, report={expected}')
            if n == 0 or (enforce_full and n < minimum):
                raise ValueError(f'{family}: {n} rows, expected full collector >= {minimum}')
            grand_total+=n
            conn.execute('''UPDATE source_files SET csv_rows=?, first_year=?, last_year=?,
                         country_codes=?, series_count=? WHERE family=?''',
                         (n,min(years),max(years),len(countries),len(metrics),family))
            target=snapshot_dir/'sources'/family
            (target/'processed').mkdir(parents=True,exist_ok=True)
            shutil.copy2(csvfile,target/'processed'/filename)
            shutil.copy2(reportfile,target/'run_report.json')
            # Raw downloads may have independent redistribution restrictions.
            if include_raw and (root/'raw').is_dir():
                shutil.copytree(root/'raw',target/'raw')
            if include_raw and (root/'manifest.json').is_file():
                shutil.copy2(root/'manifest.json',target/'manifest.json')
            report_by_family[family]={'rows':n,'country_or_polity_codes':len(countries),'series':len(metrics),
                                      'earliest_year':min(years),'latest_year':max(years)}
        conn.commit()
        integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity!='ok':raise ValueError(f'SQLite integrity failure: {integrity}')
        actual=conn.execute('SELECT count(*) FROM observations').fetchone()[0]
        if actual!=grand_total: raise ValueError(f'Warehouse row mismatch: {actual} vs {grand_total}')
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        conn.close();conn=None
        for path in sorted(snapshot_dir.rglob('*')):
            if path.is_file() and path.name!='manifest.json':
                inventories.append({'path':str(path.relative_to(snapshot_dir)),'sha256':digest(path),'bytes':path.stat().st_size})
        collection_warnings=[]
        warning_dir=data_dir/'collection_warnings'
        if warning_dir.is_dir():
            for warning_file in sorted(warning_dir.glob('*.json')):
                warning=_read_json(warning_file)
                if warning.get('family')!='historical_turnover' or warning.get('status')!='SOURCE_UNAVAILABLE_HTTP_403':
                    raise ValueError('Unexpected optional source warning: '+str(warning_file))
                if warning['family'] in report_by_family:
                    raise ValueError('Optional source marked both included and unavailable')
                collection_warnings.append(warning)
        manifest={
          'schema_version':1,'snapshot_id':snapshot_id,'created_utc':timestamp,'git_sha':git_sha,
          'source_families':report_by_family,'total_source_observations':grand_total,
          'omitted_optional_families':sorted(set(OPTIONAL_FAMILIES)-set(report_by_family)),
          'collection_warnings':collection_warnings,
          'files':inventories,'contains_raw_source_payloads':include_raw,
          'is_definition_harmonized':False,'is_causal_inference_ready':False,
          'notes':['Observations from different providers may overlap; no cross-source deduplication or harmonization.',
            'Historical polity codes are not assured to be modern ISO country identifiers.',
            'ILOSTAT disaggregations remain distinct; never sum or compare without provider codebook.',
            'IMF may include projections; do not treat future values as realized outcomes.',
            'Source CSVs, JSON reports and unmodified original row fields are preserved in this snapshot.',
            'GitHub Releases on a public repository are PUBLIC; review provider redistribution terms before publishing.',
            'The snapshot is permanent only while the GitHub Release and its assets are retained; keep a separate backup.']}
        (snapshot_dir/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        return manifest, snapshot_dir
    except Exception:
        if conn:conn.close()
        shutil.rmtree(snapshot_dir,ignore_errors=True)
        raise


def package_snapshot(snapshot_dir, output_tar):
    snapshot_dir=Path(snapshot_dir);output_tar=Path(output_tar)
    output_tar.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(output_tar,'w:gz') as tar:
        tar.add(snapshot_dir,arcname=snapshot_dir.name)
    return digest(output_tar)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',default='data')
    parser.add_argument('--out',default='warehouse_output')
    parser.add_argument('--snapshot-id',required=True)
    parser.add_argument('--git-sha',default=os.environ.get('GITHUB_SHA',''))
    parser.add_argument('--include-raw',action='store_true')
    parser.add_argument('--allow-partial',action='store_true',help='Local testing only; do not use for public production snapshots')
    args=parser.parse_args()
    manifest,folder=build_snapshot(args.data,args.out,snapshot_id=args.snapshot_id,git_sha=args.git_sha,
                                   include_raw=args.include_raw,enforce_full=not args.allow_partial)
    filename=Path(args.out)/(args.snapshot_id+'.tar.gz')
    archive_hash=package_snapshot(folder,filename)
    print(json.dumps({'snapshot_id':manifest['snapshot_id'],
                      'observations':manifest['total_source_observations'],
                      'families':manifest['source_families'], 'archive':str(filename),
                      'archive_sha256':archive_hash},indent=2))

if __name__=='__main__': main()
