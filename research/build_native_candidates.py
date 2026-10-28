#!/usr/bin/env python3
"""Phase 4: create source-separated, definition-aware candidate files, never a merged panel.

The verified source warehouse is read-only and remains unchanged. Each CSV row has
source lineage and remains 'candidate_requires_metadata_review', NOT statistically
harmonized, cross-provider equivalent, or suitable for causal inference.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research.audit_native_quality import extract_verified_db, iso_codes

BASELINE = 2_780_117
NATIVE = 2_104_609
FAMILIES = 24
EXPECTED = {
    'census_bfs_monthly': 36_883,
    'us_treasury_mts': 3_145,
    'worldbank_wdi_expanded': 210_672,
    'bls_qcew_us_national': 108_408,
    'census_cbp_states': 1_745_501,
}
MONTHS = 'jan feb mar apr may jun jul aug sep oct nov dec'.split()
STATES = set('AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split())
REGIONS = {'NO','MW','SO','WE'}
BFS_SERIES = {'BA_BA','BA_CBA','BA_HBA','BA_WBA','BF_BF4Q','BF_BF8Q','BF_PBF4Q','BF_PBF8Q','BF_SBF4Q','BF_SBF8Q','BF_DUR4Q','BF_DUR8Q'}
TREASURY_MEASURES = {'current_month_gross_rcpt_amt': 'gross_receipts',
                     'current_month_gross_outly_amt': 'gross_outlays',
                     'current_month_dfct_sur_amt': 'deficit_surplus'}

FIELDS = ['source_id','source_ordinal','period','geography','geography_level','indicator','subgroup',
          'adjustment_or_disclosure','value','source_measure','source_report_date','review_flags',
          'research_status','provenance']
STATUS = 'candidate_requires_metadata_review_not_causal_ready'


def nfloat(x):
    if x is None or str(x).strip().lower() in ('','null','none','na','n','s','d','-'):
        return None
    try: number=float(str(x).replace(',','').strip())
    except (ValueError,TypeError,OverflowError):return None
    return number if math.isfinite(number) else None


def write_hash(f):
    h=hashlib.sha256()
    with open(f,'rb') as stream:
        for part in iter(lambda:stream.read(1024*1024),b''):h.update(part)
    return h.hexdigest()


def ensure_date(x):
    try:return date.fromisoformat(x)
    except (ValueError,TypeError):return None


def candidate(ordinal,source,period,geo,level,indicator,subgroup,flags,value,measure,
              report_date='',extra=''):
    return {'source_id':source,'source_ordinal':ordinal,'period':period,'geography':geo,
            'geography_level':level,'indicator':indicator,'subgroup':subgroup,
            'adjustment_or_disclosure':extra,'value':format(value,'.15g'),
            'source_measure':measure,'source_report_date':report_date,
            'review_flags':flags,'research_status':STATUS,'provenance':'source_native.original_row_json; no imputation or rescaling'}


def expand_bfs(ordinal,raw):
    """The Census file is WIDE: year plus Jan-Dec cells, not one value per row."""
    try:y=int(raw['year'])
    except (ValueError,TypeError,KeyError):return []
    geo=raw.get('geo','').strip().upper()
    sa=raw.get('sa','').strip()
    series=raw.get('series','').strip()
    sector=raw.get('naics_sector','').strip()
    if not (2004<=y<=date.today().year+1 and sa in ('A','U') and
            series in BFS_SERIES and sector and (geo in STATES or geo in REGIONS or geo=='US')):
        return []
    level='national' if geo=='US' else ('region' if geo in REGIONS else 'state_usps')
    result=[]
    for m,key in enumerate(MONTHS,1):
        value=nfloat(raw.get(key))
        if value is None or value<0:continue
        period=f'{y:04d}-{m:02d}'
        if ensure_date(period+'-01')>date.today():continue
        flags=['bfs_release_vintage_unverified','seasonal_adjustment_specific','series_definition_specific']
        if series.startswith('BF_PBF'):flags.append('projected_business_formation_not_observed')
        if series.startswith('BF_SBF'):flags.append('spliced_actual_and_projected')
        if sector!='TOTAL':flags.append('sector_overlap_do_not_sum_with_TOTAL')
        if level=='region':flags.append('region_overlap_do_not_sum_with_national')
        result.append(candidate(ordinal,'census_bfs_monthly',period,geo,level,series,sector,
                                 ';'.join(flags),value,'source_bfs_measure',extra='sa='+sa))
    return result


def expand_treasury(ordinal,raw):
    """Separate 3 Treasury amount columns; record_date is a REPORT DATE, not month of outlay.

    Monthly Treasury Statement Table 1 has FY headings, months and YTD columns. Without
    grouping headings by actual fiscal year we will not assign a monthly observation date.
    """
    reported=raw.get('record_date','').strip()
    if not ensure_date(reported) or raw.get('data_type_cd','').strip()!='D':return []
    result=[]
    typ=raw.get('record_type_cd','').strip()
    label=raw.get('classification_desc','').strip()
    fiscal_year=raw.get('record_fiscal_year','').strip()
    for field,measure in TREASURY_MEASURES.items():
        value=nfloat(raw.get(field))
        if value is None:continue
        result.append(candidate(ordinal,'us_treasury_mts','', 'US','national',measure,
                     f'report_row={label}|record_type={typ}|fiscal_year={fiscal_year}',
                     'reported_snapshot_date_not_economic_period;fiscal_year_heading_unresolved;source_unit_not_verified',
                     value,field,report_date=reported,extra='data_type=D'))
    return result


def expand_wdi(ordinal,raw,existing,iso):
    code=raw.get('country_id','').strip();series=raw.get('indicator','').strip()
    try:y=int(raw.get('year',''))
    except (ValueError,TypeError):return [],'invalid_year'
    value=nfloat(raw.get('value'))
    if code not in iso:return [],'aggregate_or_non_iso_geography'
    if value is None:return [],'missing_value'
    if not 1960<=y<=date.today().year+1:return [],'invalid_year'
    key=(series,code,y)
    if key in existing:return [],'key_exists_in_baseline'
    flags='source_vintage_review;indicator_unit_definition_review;not_automatically_cross_provider_comparable'
    if raw.get('obs_status','').strip():flags+=';nonempty_source_observation_status'
    return [candidate(ordinal,'worldbank_wdi_expanded',str(y),code,'iso3_country_or_territory',
            series,'',flags,value,'wdi_indicator_value',extra='world_bank_native')], 'new_country_indicator_year_key'


def expand_qcew(ordinal,raw):
    """Select only national all-industries all-ownership all-size quarterly aggregates.
    Other NAICS/ownership levels remain available in native_records, not in this flat file.
    """
    if not all(raw.get(k,'').strip()==v for k,v in [('area_fips','US000'),('own_code','0'),
        ('industry_code','10'),('size_code','0'),('agglvl_code','10')]):return []
    if raw.get('disclosure_code','').strip():return []
    year=raw.get('year','').strip();q=raw.get('qtr','').strip()
    if not (year.isdigit() and 2000<=int(year)<=date.today().year+1 and q in '1234'):return []
    if (raw.get('requested_year','').strip(),raw.get('requested_quarter','').strip())!=(year,q):return []
    m=nfloat(raw.get('month1_emplvl'))
    if m is None or m<0:return []
    return [candidate(ordinal,'bls_qcew_us_national',f'{year}-Q{q}','US','national',
            'qcew_month1_employment','own_code=0|industry=10|size=0|agglvl=10',
            'quarter_first_month_only;not_annual_average;benchmark_and_revision_review',
            m,'month1_emplvl',extra='non_suppressed')]


def expand_cbp(ordinal,raw,year):
    """Only state total industry + all legal forms; preserve noise flags and source value."""
    if raw.get('naics','').strip()!='------' or raw.get('lfo','').strip()!='-':return []
    state=raw.get('fipstate','').strip()
    if not (len(state)==2 and state.isdigit() and 1<=int(state)<=56):return []
    v=nfloat(raw.get('emp'))
    if v is None or v<0:return []
    flag=raw.get('emp_nf','').strip()
    return [candidate(ordinal,'census_cbp_states',str(year),state,'state_fips',
            'cbp_employment','all_industries|all_legal_forms',
            'cbp_noise_or_disclosure_flag_requires_review;nonadditive_with_industry_or_legal_form_rows',
            v,'emp',extra='emp_nf='+flag)]


def validate_input(db, expected):
    assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    base=db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
    families=db.execute('SELECT COUNT(*) FROM source_files').fetchone()[0]
    if expected:
        if base!=BASELINE or families!=FAMILIES:raise ValueError('Baseline changed')
    rows=db.execute('SELECT source_id,original_records,indexed_records,status,is_harmonized FROM native_sources').fetchall()
    sources={r[0]:int(r[1]) for r in rows}
    if set(sources)!=(set(EXPECTED) if expected else set(sources)):
        raise ValueError('Native source set changed')
    for key,original,indexed,status,flag in rows:
        if original!=indexed or status!='SOURCE_NATIVE_UNHARMONIZED' or flag!=0:
            raise ValueError('Source provenance/status invalid: '+key)
        if expected and original!=EXPECTED[key]:raise ValueError('Source row count changed: '+key)
    if expected and db.execute('SELECT COUNT(*) FROM native_records').fetchone()[0]!=NATIVE:
        raise ValueError('Native source count changed')
    return base,sources


def build(db_path,out,iso,quality_report=None,*,expected=True):
    """Build source-specific research candidates from audited, immutable raw source fields."""
    out=Path(out)
    if out.exists() and any(out.iterdir()):raise FileExistsError('Output directory already has files')
    out.mkdir(parents=True,exist_ok=True)
    iso=set(iso)
    with sqlite3.connect(f'file:{Path(db_path).resolve()}?mode=ro',uri=True) as db:
        base,sources=validate_input(db,expected)
        if quality_report:
            audit=json.loads(Path(quality_report).read_text())
            if audit['base_analytical_observations_unchanged']!=base or set(audit['source_reports'])!=set(sources):
                raise ValueError('Phase 3 report is not for this source set')
            for key,count in sources.items():
                if audit['source_reports'][key]['audited_rows']!=count:
                    raise ValueError('Phase 3 report and source counts disagree: '+key)
        # Names and definitions, not numeric values, govern WDI cross-source de-duplication.
        existing={(a,b,int(c)) for a,b,c in db.execute("SELECT DISTINCT series,country_or_polity_code,year FROM observations WHERE family='wdi'")}
        counters={};outputs={};csv_writers={}
        for source in sorted(sources):
            p=out/(source+'_candidates.csv')
            outputs[source]=p
            f=p.open('w',newline='',encoding='utf-8');csv_writers[source]=(f,csv.DictWriter(f,fieldnames=FIELDS))
            csv_writers[source][1].writeheader()
        try:
            for source in sorted(sources):
                sc=Counter()
                cur=db.execute('SELECT ordinal,year_text,raw_row_json FROM native_records WHERE source_id=? ORDER BY ordinal',(source,))
                for ordinal,year_text,blob in cur:
                    sc['native_rows_read']+=1
                    raw=json.loads(blob)
                    if not isinstance(raw,dict):raise ValueError('Invalid native JSON')
                    if source=='census_bfs_monthly':
                        rows=expand_bfs(ordinal,raw)
                    elif source=='us_treasury_mts':
                        rows=expand_treasury(ordinal,raw)
                    elif source=='worldbank_wdi_expanded':
                        rows,reason=expand_wdi(ordinal,raw,existing,iso)
                        sc['wdi_'+reason]+=1
                    elif source=='bls_qcew_us_national':
                        rows=expand_qcew(ordinal,raw)
                    elif source=='census_cbp_states':
                        rows=expand_cbp(ordinal,raw,year_text)
                    else:raise ValueError('Unexpected source')
                    if rows:sc['native_rows_with_one_or_more_candidates']+=1
                    for rec in rows:
                        csv_writers[source][1].writerow(rec)
                        sc['candidate_measurements']+=1
                if sc['native_rows_read']!=sources[source]:raise ValueError('Incomplete input row scan '+source)
                counters[source]=dict(sc)
        finally:
            for f,w in csv_writers.values():f.close()
    report={
        'schema_version':1,'generated_utc':datetime.now(timezone.utc).isoformat(),
        'baseline_analytical_observations_unchanged':base,
        'total_native_records_read':sum(x['native_rows_read'] for x in counters.values()),
        'output_candidate_measurements':sum(x.get('candidate_measurements',0) for x in counters.values()),
        'sources':counters,
        'source_specific_candidates_only':True,
        'seasonal_adjustments_are_separate':True,
        'cross_provider_harmonized':False,
        'causal_inference_ready':False,
        'policy_treatment_ready':False,
        'warnings':[
            'BFS: Jan-Dec numeric cells are unpivoted; original wide record is preserved in the native warehouse.',
            'Treasury: report_date is not an economic observation month; candidate period deliberately blank pending fiscal-heading resolution.',
            'World Bank: new numeric ISO3 year-indicator keys are only candidates. Codes may include dependent territories; units/vintage need review.',
            'QCEW: narrow all-industry all-ownership filter; quarter-first-month employment is not a quarterly or annual average.',
            'CBP: narrow state all-industry all-legal-form filter; noise/suppression metadata retained for review.',
            'Output CSVs represent source-specific candidate measurements, not unique comparable observations or causal evidence.'
        ]}
    (out/'candidate_quality_report.json').write_text(json.dumps(report,indent=2)+'\n')
    checksums={f.name:{'sha256':write_hash(f),'bytes':f.stat().st_size} for f in out.iterdir() if f.is_file()}
    (out/'sha256_manifest.json').write_text(json.dumps(checksums,indent=2)+'\n')
    return report


def selftest():
    # Offline, deterministic: tests the actual source-column patterns and no source writes.
    assert nfloat('null') is None and nfloat('NaN') is None and nfloat('1,250')==1250
    bfs={'year':'2024','geo':'CT','series':'BA_BA','sa':'U','naics_sector':'TOTAL',
         'jan':'15','feb':'','mar':'20',**{x:'' for x in MONTHS[3:]}}
    results=expand_bfs(3,bfs)
    assert [r['period'] for r in results]==['2024-01','2024-03']
    assert [float(r['value']) for r in results]==[15,20]
    assert not expand_bfs(3,dict(bfs,sa='unknown'))
    treasury={'record_date':'2025-03-31','classification_desc':'October','data_type_cd':'D',
              'record_type_cd':'MTH','record_fiscal_year':'2015',
              'current_month_gross_outly_amt':'500','current_month_gross_rcpt_amt':'600',
              'current_month_dfct_sur_amt':'-100'}
    result=expand_treasury(7,treasury)
    assert len(result)==3 and all(x['period']=='' for x in result)
    assert all(x['source_report_date']=='2025-03-31' for x in result)
    assert expand_wdi(2,{'country_id':'USA','indicator':'GDP','year':'2022','value':'5'},set(),{'USA'})[1]=='new_country_indicator_year_key'
    assert expand_wdi(2,{'country_id':'USA','indicator':'GDP','year':'2022','value':'5'},{('GDP','USA',2022)},{'USA'})[1]=='key_exists_in_baseline'
    assert expand_wdi(2,{'country_id':'WLD','indicator':'GDP','year':'2022','value':'5'},set(),{'USA'})[1]=='aggregate_or_non_iso_geography'
    q={'area_fips':'US000','own_code':'0','industry_code':'10','size_code':'0','agglvl_code':'10',
       'disclosure_code':'','year':'2023','qtr':'1','requested_year':'2023','requested_quarter':'1','month1_emplvl':'100'}
    assert len(expand_qcew(4,q))==1 and not expand_qcew(4,dict(q,own_code='1'))
    cb={'naics':'------','lfo':'-','fipstate':'01','emp':'123','emp_nf':'G'}
    assert len(expand_cbp(5,cb,'2019'))==1 and not expand_cbp(5,dict(cb,lfo='C'),'2019')
    with tempfile.TemporaryDirectory() as td:
        root=Path(td)
        dbpath=root/'test.sqlite'
        with sqlite3.connect(dbpath) as db:
            db.executescript('CREATE TABLE observations(family TEXT,series TEXT,country_or_polity_code TEXT,year INTEGER);'
                             'CREATE TABLE source_files(family TEXT);'
                             'CREATE TABLE native_sources(source_id TEXT,original_records INTEGER,indexed_records INTEGER,status TEXT,is_harmonized INTEGER);'
                             'CREATE TABLE native_records(source_id TEXT,ordinal INTEGER,year_text TEXT,raw_row_json TEXT);')
            fixtures={'census_bfs_monthly':bfs,'us_treasury_mts':treasury,
                      'worldbank_wdi_expanded':{'country_id':'USA','indicator':'GDP','year':'2022','value':'5'},
                      'bls_qcew_us_national':q,'census_cbp_states':cb}
            for key,fixture in fixtures.items():
                db.execute('INSERT INTO native_sources VALUES (?,?,?,?,0)',(key,1,1,'SOURCE_NATIVE_UNHARMONIZED'))
                db.execute('INSERT INTO native_records VALUES (?,?,?,?)',(key,1,'2019' if key=='census_cbp_states' else '2024',json.dumps(fixture)))
        before=write_hash(dbpath)
        report=build(dbpath,root/'out',{'USA'},expected=False)
        assert report['total_native_records_read']==5
        assert report['output_candidate_measurements']==2+3+1+1+1
        assert before==write_hash(dbpath),'Source DB changed in read-only operation'
        assert not report['causal_inference_ready']
    print('PASS: BFS wide-month unpivot, Treasury date safety, WDI keys, narrow QCEW/CBP filters, read-only provenance')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',type=Path)
    p.add_argument('--quality-report',type=Path)
    p.add_argument('--iso-codes',type=Path,default=Path('research/iso3_country_codes.csv'))
    p.add_argument('--out',type=Path,default=Path('candidate_output'))
    p.add_argument('--self-test',action='store_true')
    args=p.parse_args()
    if args.self_test:return selftest()
    if not args.archive or not args.quality_report:p.error('--archive and --quality-report are required')
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'warehouse.sqlite'
        inv,manifest=extract_verified_db(args.archive,path)
        report=build(path,args.out,iso_codes(args.iso_codes),args.quality_report)
        report['source_snapshot_id']=inv['snapshot_id']
        report['parent_phase3_quality_report']=args.quality_report.name
        (args.out/'candidate_quality_report.json').write_text(json.dumps(report,indent=2)+'\n')
        # Refresh manifest after adding source snapshot metadata.
        checksums={f.name:{'sha256':write_hash(f),'bytes':f.stat().st_size} for f in args.out.iterdir() if f.is_file() and f.name!='sha256_manifest.json'}
        (args.out/'sha256_manifest.json').write_text(json.dumps(checksums,indent=2)+'\n')
        print(json.dumps({'source':inv['snapshot_id'],'input_rows':report['total_native_records_read'],
                          'derived_candidate_measurements':report['output_candidate_measurements'],
                          'source_counts':report['sources']},indent=2))

if __name__=='__main__':main()
