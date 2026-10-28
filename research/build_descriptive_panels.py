#!/usr/bin/env python3
"""Build narrowly scoped, source-separated descriptive research panels from Phase 4.

Input: archived Phase-4 candidate GitHub Actions artifact (ZIP). No network
access, imputation, annualization, cross-provider joins or causal estimation.
The original SQLite warehouse is not read or modified. Python stdlib only.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

SOURCE_IDS = (
    'census_bfs_monthly','worldbank_wdi_expanded',
    'census_cbp_states','bls_qcew_us_national','us_treasury_mts'
)
CANDIDATE_COLUMNS = (
    'source_id','source_ordinal','period','geography','geography_level','indicator',
    'subgroup','adjustment_or_disclosure','value','source_measure',
    'source_report_date','review_flags','research_status','provenance'
)
PANEL_FIELDS = list(CANDIDATE_COLUMNS) + [
    'panel','unit','period_type','definition','descriptive_status','official_definition_url'
]
SPECS = {
 'bfs_business_applications': {
     'source':'census_bfs_monthly', 'code':'BA_BA', 'period_type':'month',
     'definition':'Census BFS Business Applications (not completed business formations)',
     'unit':'business_application_count',
     'url':'https://www.census.gov/econ/bfs/definitions.html',
     'status':'candidate_compare_within_same_adjustment_geography_and_vintage',
 },
 'bfs_high_propensity_applications': {
     'source':'census_bfs_monthly', 'code':'BA_HBA', 'period_type':'month',
     'definition':'Census BFS High-Propensity Business Applications (not observed employment)',
     'unit':'high_propensity_business_application_count',
     'url':'https://www.census.gov/econ/bfs/definitions.html',
     'status':'candidate_compare_within_same_adjustment_geography_and_vintage',
 },
 'wdi_current_account_pct_gdp_new_keys': {
     'source':'worldbank_wdi_expanded', 'code':'BN.CAB.XOKA.GD.ZS', 'period_type':'year',
     'definition':'World Bank current account balance as percent of GDP; new-to-baseline keys only',
     'unit':'percent_of_gdp',
     'url':'https://databank.worldbank.org/metadataglossary/world-development-indicators/series/BN.CAB.XOKA.GD.ZS',
     'status':'new_keys_only_not_a_complete_cross_indicator_wdi_panel',
 },
 'cbp_state_mid_march_employment': {
     'source':'census_cbp_states', 'code':'cbp_employment', 'period_type':'year',
     'definition':'Census CBP payroll employment in period including March 12; noise G under 2 percent',
     'unit':'paid_employment_mid_march',
     'url':'https://www.census.gov/programs-surveys/cbp/about/glossary.html',
     'status':'candidate_noise_infused_not_comparable_without_metadata_review',
 },
 'qcew_national_quarter_first_month_employment': {
     'source':'bls_qcew_us_national', 'code':'qcew_month1_employment', 'period_type':'quarter',
     'definition':'BLS QCEW employment in FIRST month of quarter, not a quarter average',
     'unit':'employment_in_first_month_of_quarter',
     'url':'https://www.bls.gov/cew/',
     'status':'candidate_not_quarterly_average_and_not_cbp_equivalent',
 },
}
QUARANTINE_FIELDS=list(CANDIDATE_COLUMNS)+['quarantine_reason']


def digest_bytes(b):
    return hashlib.sha256(b).hexdigest()


def verify_zip(artifact):
    """Verify the exact data declared by Phase 4 against Phase 4's manifest."""
    archive=zipfile.ZipFile(artifact)
    try:
        infos=archive.infolist(); names=[x.filename for x in infos]
        if any(x.is_dir() or '/' in x.filename or '\\' in x.filename or x.filename.startswith('.')
               for x in infos) or len(names)!=len(set(names)):
            raise ValueError('Unexpected, duplicate, or unsafe artifact path')
        required={'candidate_quality_report.json','sha256_manifest.json'}|{
            source+'_candidates.csv' for source in SOURCE_IDS}
        if set(names)!=required:raise ValueError('Unexpected missing or extra artifact files')
        if sum(x.file_size for x in infos)>400_000_000 or any(x.file_size>300_000_000 for x in infos):
            raise ValueError('Artifact uncompressed size outside safe range')
        report=json.loads(archive.read('candidate_quality_report.json'))
        manifest=json.loads(archive.read('sha256_manifest.json'))
        if set(manifest)!=required-{'sha256_manifest.json'}:
            raise ValueError('Phase 4 manifest missing or unexpected files')
        for name,meta in manifest.items():
            h=hashlib.sha256();count=0
            with archive.open(name) as source:
                for block in iter(lambda:source.read(1024*1024),b''):
                    h.update(block);count+=len(block)
            if h.hexdigest()!=meta['sha256'] or count!=int(meta['bytes']):
                raise ValueError('Phase 4 artifact checksum mismatch: '+name)
        if report.get('total_native_records_read')!=2104609 or \
           report.get('baseline_analytical_observations_unchanged')!=2780117 or \
           report.get('cross_provider_harmonized') is not False or \
           report.get('causal_inference_ready') is not False:
            raise ValueError('Wrong or unexpectedly reclassified Phase 4 archive')
        if set(report['sources'])!=set(SOURCE_IDS):
            raise ValueError('Unexpected Phase 4 source inventory')
        if sum(v['candidate_measurements'] for v in report['sources'].values())!=report['output_candidate_measurements']:
            raise ValueError('Phase 4 candidate accounting disagreement')
        return archive,report
    except Exception:
        archive.close()
        raise


def period_number(period,period_type):
    if period_type=='month':
        m=re.fullmatch(r'(\d{4})-(0[1-9]|1[0-2])',period)
        if not m:raise ValueError('Invalid Census BFS month: '+period)
        return int(m[1])*12+int(m[2])-1
    if period_type=='quarter':
        m=re.fullmatch(r'(\d{4})-Q([1-4])',period)
        if not m:raise ValueError('Invalid BLS quarter: '+period)
        return int(m[1])*4+int(m[2])-1
    if period_type=='year':
        if not re.fullmatch(r'\d{4}',period):raise ValueError('Invalid annual period: '+period)
        return int(period)
    raise ValueError('Unknown period type')


def panel_for(row):
    sid,indicator=row['source_id'],row['indicator']
    if sid=='census_bfs_monthly':
        if row['subgroup']!='TOTAL' or row['adjustment_or_disclosure'] not in {'sa=A','sa=U'}:
            return None
        if row['geography_level'] not in {'national','state_usps','region'}:
            return None
        if indicator=='BA_BA':return 'bfs_business_applications'
        if indicator=='BA_HBA':return 'bfs_high_propensity_applications'
    if sid=='worldbank_wdi_expanded' and indicator=='BN.CAB.XOKA.GD.ZS' and \
       row['geography_level']=='iso3_country_or_territory':
        return 'wdi_current_account_pct_gdp_new_keys'
    if sid=='census_cbp_states' and indicator=='cbp_employment' and \
       row['subgroup']=='all_industries|all_legal_forms' and \
       row['geography_level']=='state_fips' and row['adjustment_or_disclosure']=='emp_nf=G':
        return 'cbp_state_mid_march_employment'
    if sid=='bls_qcew_us_national' and indicator=='qcew_month1_employment' and \
       row['geography_level']=='national' and row['subgroup']=='own_code=0|industry=10|size=0|agglvl=10':
        return 'qcew_national_quarter_first_month_employment'
    return None


def valid_row(row,source):
    if set(row)!=set(CANDIDATE_COLUMNS):raise ValueError('Candidate CSV columns changed: '+source)
    if row['source_id']!=source:raise ValueError('Wrong source ID in '+source)
    if not row['source_ordinal'].isdigit() or int(row['source_ordinal'])<1:
        raise ValueError('Invalid original ordinal: '+source)
    if not row['geography'] or not row['indicator'] or not row['value']:
        raise ValueError('Candidate without essential geography, indicator or value: '+source)
    try:v=float(row['value'])
    except (TypeError,ValueError):raise ValueError('Non-numeric candidate: '+source)
    if not math.isfinite(v):raise ValueError('Non-finite candidate: '+source)
    if row['research_status']!='candidate_requires_metadata_review_not_causal_ready':
        raise ValueError('Unreviewed change in research status')


def write_outputs(archive, report, out):
    out=Path(out)
    if out.exists() and any(out.iterdir()):raise FileExistsError('Refusing to overwrite existing Phase 5 output')
    out.mkdir(parents=True,exist_ok=True)
    handles={};writers={}
    seen={name:set() for name in SPECS}
    continuity=defaultdict(set)
    periods=defaultdict(set)
    geography=defaultdict(set)
    counts=Counter();exclusions=Counter();source_counts=Counter();quarantine=Counter()
    try:
        for name in SPECS:
            f=(out/(name+'.csv')).open('w',newline='',encoding='utf-8')
            handles[name]=f
            w=csv.DictWriter(f,fieldnames=PANEL_FIELDS);w.writeheader();writers[name]=w
        f=(out/'treasury_unresolved_fiscal_periods.csv').open('w',newline='',encoding='utf-8')
        handles['treasury']=f;qw=csv.DictWriter(f,fieldnames=QUARANTINE_FIELDS);qw.writeheader()
        for sid in SOURCE_IDS:
            filename=sid+'_candidates.csv'
            with archive.open(filename) as binary:
                with io.TextIOWrapper(binary,encoding='utf-8-sig',newline='') as text:
                    reader=csv.DictReader(text)
                    if set(reader.fieldnames or [])!=set(CANDIDATE_COLUMNS):
                        raise ValueError('Phase 4 CSV header changed: '+sid)
                    for row in reader:
                        source_counts[sid]+=1;valid_row(row,sid)
                        if sid=='us_treasury_mts':
                            if row['period']:
                                raise ValueError('Treasury fiscal period unexpectedly populated; review required')
                            qw.writerow({**row,'quarantine_reason':'fiscal_observation_period_and_unit_not_resolved'})
                            quarantine['treasury_missing_validated_period']+=1
                            continue
                        name=panel_for(row)
                        if name is None:
                            exclusions[sid]+=1;continue
                        spec=SPECS[name]
                        if sid=='census_bfs_monthly' and row['subgroup']!='TOTAL':
                            raise ValueError('Overlapping sector subgroup in core BFS')
                        if sid=='census_cbp_states' and row['adjustment_or_disclosure']!='emp_nf=G':
                            raise ValueError('CBP disclosure flag unexpectedly changed')
                        num=period_number(row['period'],spec['period_type'])
                        k=(row['period'],row['geography_level'],row['geography'],
                           row['indicator'],row['subgroup'],row['adjustment_or_disclosure'])
                        if k in seen[name]:raise ValueError(f'Duplicate economic panel key: {name}, {k}')
                        seen[name].add(k)
                        series=(name,row['geography_level'],row['geography'],
                                row['subgroup'],row['adjustment_or_disclosure'])
                        continuity[series].add(num)
                        periods[name].add(row['period']);geography[name].add((row['geography_level'],row['geography']))
                        writers[name].writerow({**row,'panel':name,'unit':spec['unit'],
                            'period_type':spec['period_type'],'definition':spec['definition'],
                            'descriptive_status':spec['status'], 'official_definition_url':spec['url']})
                        counts[name]+=1
            if source_counts[sid]!=report['sources'][sid]['candidate_measurements']:
                raise ValueError(f'{sid} candidate input row count mismatch')
        if sum(counts.values())+sum(exclusions.values())+sum(quarantine.values())!=sum(source_counts.values()):
            raise ValueError('Failed to account for every Phase 4 candidate measurement')
        coverage=[]
        for k,numbers in sorted(continuity.items()):
            missing=max(numbers)-min(numbers)+1-len(numbers)
            coverage.append({'panel':k[0],'geography_level':k[1],'geography':k[2],
                'subgroup':k[3],'adjustment_or_disclosure':k[4],
                'start_numeric_period':min(numbers),'end_numeric_period':max(numbers),
                'observed_periods':len(numbers),'internal_gaps':missing})
        coverage_file=out/'series_coverage.csv'
        with coverage_file.open('w',encoding='utf-8',newline='') as f:
            columns=['panel','geography_level','geography','subgroup','adjustment_or_disclosure',
                     'start_numeric_period','end_numeric_period','observed_periods','internal_gaps']
            w=csv.DictWriter(f,fieldnames=columns);w.writeheader();w.writerows(coverage)
        panel_report={}
        for name,spec in SPECS.items():
            matching=[r for r in coverage if r['panel']==name]
            panel_report[name]={'observations':counts[name],'distinct_geographies':len(geography[name]),
                'distinct_series':len(matching),'earliest_period':min(periods[name],default=None),
                'latest_period':max(periods[name],default=None),
                'series_with_internal_gaps':sum(x['internal_gaps']>0 for x in matching),
                'total_internal_missing_periods':sum(x['internal_gaps'] for x in matching),
                'unit':spec['unit'],'official_definition_url':spec['url'],
                'descriptive_status':spec['status']}
        summary={'schema_version':1,'generated_utc':datetime.now(timezone.utc).isoformat(),
            'input_phase4_source_snapshot':report['source_snapshot_id'],
            'phase4_candidate_measurements_read':sum(source_counts.values()),
            'source_input_counts':dict(source_counts),
            'descriptive_panel_measurements':sum(counts.values()),
            'excluded_but_preserved_in_phase4_candidates':dict(exclusions),
            'treasury_quarantined_measurements':sum(quarantine.values()),
            'quarantine_reasons':dict(quarantine),
            'panels':panel_report,
            'original_warehouse_read_only':True,'cross_source_harmonized':False,
            'eligible_for_causal_inference':False,
            'limitations':[
                'Panels cover ONLY Phase 4 candidate measurements, not the full 2.78m-row base warehouse.',
                'World Bank panel includes new-to-baseline keys, not the entire WDI indicator registry.',
                'BFS seasonal adjustments and geographic/sector series must never be added together.',
                'BA_HBA is an applications proxy, not observed employer formations or jobs.',
                'CBP employment (mid-March and noise-infused) and QCEW first-month employment are different concepts.',
                'No employment effect, policy-treatment identification, deflation, per-capita scaling, or causal estimate.',
                'All Treasury entries quarantined until source-period and units are independently validated.',
            ]}
        (out/'descriptive_panel_report.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
        # Flush every open CSV before calculating file sizes and SHA-256 digests.
        for stream in handles.values():stream.flush()
        manifest={}
        for p in sorted(out.iterdir()):
            if p.is_file():manifest[p.name]={'sha256':digest_bytes(p.read_bytes()),'bytes':p.stat().st_size}
        (out/'sha256_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
        return summary
    except Exception:
        for f in handles.values():f.close()
        shutil.rmtree(out,ignore_errors=True)
        raise
    finally:
        for f in handles.values():f.close()


def run(archive_path,output):
    z,report=verify_zip(archive_path)
    try:return write_outputs(z,report,output)
    finally:z.close()


def self_test():
    assert period_number('2026-09','month')==2026*12+8
    assert period_number('2025-Q4','quarter')==2025*4+3
    assert period_number('2023','year')==2023
    for bad,kind in [('2026-13','month'),('2025-Q0','quarter'),('2023-Q1','year')]:
        try:period_number(bad,kind)
        except ValueError:pass
        else:raise AssertionError('Invalid period accepted')
    sample={c:'' for c in CANDIDATE_COLUMNS}
    sample.update(source_id='census_bfs_monthly',source_ordinal='1',period='2025-02',geography='US',
       geography_level='national',indicator='BA_BA',subgroup='TOTAL',
       adjustment_or_disclosure='sa=A',value='1234',research_status='candidate_requires_metadata_review_not_causal_ready')
    valid_row(sample,'census_bfs_monthly');assert panel_for(sample)=='bfs_business_applications'
    sample['subgroup']='NAICS52';assert panel_for(sample) is None
    sample['subgroup']='TOTAL';sample['value']='NaN'
    try:valid_row(sample,'census_bfs_monthly')
    except ValueError:pass
    else:raise AssertionError('Accepted non-finite value')
    print('PASS: period guards, source/classification filters, finite values and source isolation')


def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--artifact-zip',type=Path)
    a.add_argument('--out',type=Path,default=Path('descriptive_output'))
    a.add_argument('--self-test',action='store_true')
    v=a.parse_args()
    if v.self_test:return self_test()
    if not v.artifact_zip:a.error('--artifact-zip is required')
    summary=run(v.artifact_zip,v.out)
    print(json.dumps({'source_input_measurements':summary['phase4_candidate_measurements_read'],
        'descriptive_panel_measurements':summary['descriptive_panel_measurements'],
        'quarantined_treasury':summary['treasury_quarantined_measurements'],
        'panels':{k:v['observations'] for k,v in summary['panels'].items()}},indent=2))

if __name__=='__main__':main()
