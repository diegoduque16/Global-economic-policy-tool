#!/usr/bin/env python3
"""Phase 6: fail-closed policy-identification readiness audit of Phase 5 panels.

Does not create, infer, or join policy shocks and never claims causal readiness.
Only source-authored registry metadata, independently verified Phase 5 records,
and explicit audit observations enter outputs. Source archive and registry read only.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import zipfile

PANELS = (
    'bfs_business_applications',
    'bfs_high_propensity_applications',
    'wdi_current_account_pct_gdp_new_keys',
    'cbp_state_mid_march_employment',
    'qcew_national_quarter_first_month_employment',
)
REQUIRED_COLUMNS = ('source_id','period','geography','geography_level','indicator',
                    'subgroup','adjustment_or_disclosure','value','unit','period_type',
                    'review_flags','research_status','provenance')
EVENT_FIELDS = (
    'event_id','policy_title','policy_family','source_study_id_or_new_study',
    'country_iso3','jurisdiction_level','jurisdiction_code',
    'policy_announcement_date','policy_enactment_date','policy_effective_date',
    'first_implementation_date','treatment_intensity','treatment_intensity_unit',
    'primary_law_or_source_url','primary_source_pinpoint','secondary_verification_url',
    'proposed_outcome_panel','proposed_treated_group','proposed_control_group',
    'anticipation_concern','spillover_concern','contemporaneous_confounders',
    'pre_period_coverage_checked','post_period_coverage_checked',
    'data_vintage','independent_reviewer','reviewed_on','evidence_status',
)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def json_bytes(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)+'\n').encode('utf-8')


def period_valid(p: str, period_type: str) -> bool:
    patterns = {'month':r'(?:18|19|20)\d{2}-(?:0[1-9]|1[0-2])',
                'quarter':r'(?:18|19|20)\d{2}-Q[1-4]',
                'year':r'(?:18|19|20)\d{2}'}
    return bool(re.fullmatch(patterns.get(period_type, r'(?!)'), p))


def profile_panel(z: zipfile.ZipFile, name: str, n_expected: int):
    info = zipfile.ZipInfo  # stdlib only; streamed read below
    counts = Counter(); geos = set(); levels=set(); indicators=set(); units=set(); types=set()
    seasons=set(); periods=set(); keys=set(); bad=0; first=None; last=None
    with z.open(name+'.csv') as f, io.TextIOWrapper(f,encoding='utf-8-sig',newline='') as textfile:
        reader = csv.DictReader(textfile)
        if not set(REQUIRED_COLUMNS).issubset(reader.fieldnames or []):
            raise ValueError(f'{name}: required columns missing')
        for row in reader:
            bad += 1
            period=row['period']; typ=row['period_type']
            if not period_valid(period,typ):
                raise ValueError(f'{name}: invalid {typ} period {period!r}')
            if row['unit'].strip() == '' or row['research_status'].strip() == '':
                raise ValueError(f'{name}: missing unit or review status')
            # Repeated observations are never resolved by averaging or deduplication.
            key=(row['period'],row['geography_level'],row['geography'],row['indicator'],
                 row['subgroup'],row['adjustment_or_disclosure'])
            if key in keys: raise ValueError(f'{name}: duplicate panel key {key}')
            keys.add(key)
            counts[row['geography_level']]+=1
            geos.add(row['geography']);levels.add(row['geography_level'])
            indicators.add(row['indicator']);units.add(row['unit']);types.add(typ)
            seasons.add(row['adjustment_or_disclosure']);periods.add(period)
            first = min(first,period) if first else period
            last = max(last,period) if last else period
    if bad != n_expected: raise ValueError(f'{name}: expected {n_expected}, found {bad}')
    if len(units)!=1 or len(types)!=1 or len(indicators)!=1:
        raise ValueError(f'{name}: heterogeneous indicator, unit or period type')
    if not bad: raise ValueError(f'{name}: panel empty')
    return {
        'panel': name, 'measurements':bad, 'period_type':next(iter(types)),
        'earliest_period':first,'latest_period':last,
        'unique_periods':len(periods),'unique_geographies':len(geos),
        'geography_levels':sorted(levels),'observations_by_geography_level':dict(sorted(counts.items())),
        'indicator':next(iter(indicators)),'unit':next(iter(units)),
        'adjustment_codes':sorted(seasons),'geography_codes':geos,
    }


def evidence_match(study, prof):
    country=study['country'];levels=set(prof['geography_levels']);codes=prof['geography_codes']
    # Registry country is a source jurisdiction, not evidence of actual shock timing.
    if country == 'multiple':
        return ('unknown_multicountry_coverage',
                'Registry lacks verified study-country treatment observations; cannot assess match.')
    if country == 'USA':
        if 'iso3_country_or_territory' in levels:
            if 'USA' in codes: return ('country_present','USA appears in partial annual WDI panel; not a complete outcome panel.')
        if 'national' in levels and 'US' in codes:
            return ('national_outcome_present','U.S. national outcome exists; no verified overlapping numeric policy-shock period.')
        if 'state_fips' in levels or 'state_usps' in levels:
            return ('us_subnational_outcomes_only','U.S. state outcomes exist, but national shocks do not supply untreated U.S. state controls.')
        return ('not_demonstrated','No compatible United States geography in this panel.')
    if country == 'GBR':
        if 'GBR' in codes and 'iso3_country_or_territory' in levels:
            return ('country_present','GBR appears in partial annual WDI panel; not evidence of sufficient outcome history.')
        return ('not_demonstrated','No United Kingdom geography for this study in the panel.')
    return ('not_demonstrated','No verified geographic mapping supplied.')


def write_csv(path:Path, fields, rows):
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='raise');w.writeheader();w.writerows(rows)


def build(panel_zip:Path, registry_path:Path, output:Path):
    if output.exists() and any(output.iterdir()):raise FileExistsError('Output folder must be empty')
    output.mkdir(parents=True,exist_ok=True)
    source_registry_bytes=registry_path.read_bytes()
    registry=json.loads(source_registry_bytes)
    studies=registry.get('sources')
    if not isinstance(studies,list) or not studies:
        raise ValueError('No registered studies')
    ids=set()
    for s in studies:
        for field in ('id','country','design','replication_url','study_url'):
            if not str(s.get(field,'')).strip():raise ValueError('Incomplete study registry')
        if s['id'] in ids:raise ValueError('Duplicate study ID')
        ids.add(s['id'])
    with zipfile.ZipFile(panel_zip) as z:
        names=set(z.namelist())
        if len(names)!=len(z.namelist()) or any('/' in n or '\\' in n for n in names):
            raise ValueError('Unexpected ZIP member layout')
        expected_names={p+'.csv' for p in PANELS}|{'descriptive_panel_report.json','sha256_manifest.json','series_coverage.csv','treasury_unresolved_fiscal_periods.csv'}
        if names!=expected_names:raise ValueError('Phase 5 artifact members do not match documented snapshot')
        manifest=json.loads(z.read('sha256_manifest.json'))
        if set(manifest)!=names-{'sha256_manifest.json'}:
            raise ValueError('Phase 5 checksum manifest incomplete')
        for f,meta in manifest.items():
            raw=z.read(f)
            if sha(raw)!=meta['sha256'] or len(raw)!=meta['bytes']:
                raise ValueError(f'Phase 5 source byte verification failed: {f}')
        previous=json.loads(z.read('descriptive_panel_report.json'))
        if previous['descriptive_panel_measurements']!=67842 or previous['treasury_quarantined_measurements']!=7767:
            raise ValueError('Unknown Phase 5 lineage/counts')
        if previous['eligible_for_causal_inference'] is not False or previous['cross_source_harmonized'] is not False:
            raise ValueError('Phase 5 status not conservatively flagged')
        profiles=[profile_panel(z,name,previous['panels'][name]['observations']) for name in PANELS]
        if sum(p['measurements'] for p in profiles)!=67842:
            raise ValueError('Panel accounting mismatch')
        out_matrix=[]
        for study in studies:
            for p in profiles:
                geo, note=evidence_match(study,p)
                out_matrix.append({
                    'registered_study':study['id'], 'study_country_scope':study['country'],
                    'study_design_as_registered':study['design'],
                    'outcome_panel':p['panel'], 'outcome_period_type':p['period_type'],
                    'first_outcome_period':p['earliest_period'], 'last_outcome_period':p['latest_period'],
                    'geography_evidence':geo, 'unverified_treatment_observations':0,
                    'identification_ready':'no',
                    'limitation':note+' Treatment dates, magnitudes, and control strategy not verified.'
                })
        prof_csv=[]
        for p in profiles:
            prof_csv.append({k:(','.join(v) if isinstance(v,list) else v)
                for k,v in p.items() if k!='geography_codes' and k!='observations_by_geography_level'})
        write_csv(output/'panel_readiness_profiles.csv',list(prof_csv[0]),prof_csv)
        matrix_fields=['registered_study','study_country_scope','study_design_as_registered',
                       'outcome_panel','outcome_period_type','first_outcome_period','last_outcome_period',
                       'geography_evidence','unverified_treatment_observations','identification_ready','limitation']
        write_csv(output/'source_outcome_readiness_matrix.csv',matrix_fields,out_matrix)
        write_csv(output/'policy_event_evidence_TEMPLATE.csv',EVENT_FIELDS,[])
        summary={
            'schema_version':1, 'phase5_artifact_sha256':sha(panel_zip.read_bytes()),
            'phase5_verified_report_sha256':sha(z.read('descriptive_panel_report.json')),
            'study_registry_sha256':sha(source_registry_bytes),
            'phase5_input_measurements':67842,'panels_checked':len(profiles),
            'registered_policy_studies':len(studies),
            'registered_study_outcome_pairs_screened':len(out_matrix),
            'validated_policy_event_observations':0,
            'verified_policy_treatment_dates':0,
            'valid_policy_counterfactual_designs':0,
            'causal_inference_ready':False,
            'event_template_contains_no_invented_events':True,
            'phase5_source_data_modified':False,
            'treasury_7767_measurements_remain_quarantined':True,
            'panel_summaries':[ {k:v for k,v in p.items() if k!='geography_codes'} for p in profiles],
            'conclusions':[
                'Observed outcomes alone never establish exogenous policy treatment, causal attribution, or jobs created.',
                'Registered studies are metadata only; no source-extracted, independently verified event dates or magnitudes were supplied.',
                'National tax-shock studies do not by themselves identify untreated U.S. states for state differences-in-differences.',
                'BFS measures applications, not jobs or completed business formations; seasonal adjustments must not be pooled.',
                'WDI values are new-to-baseline keys only and cannot be treated as a complete cross-country panel.',
                'CBP noise flags, QCEW quarter-first-month definitions, and Treasury fiscal-period uncertainty remain material.',
                'Historical policy shocks cannot automatically identify modern generative-AI interaction effects.'
            ],
            'recommended_next_evidence':[
                'Verified state-level policy enactment and effective dates, amounts, coverage and untreated/control jurisdictions.',
                'Official historical U.S. payroll, earnings, hours, prices and real output series covering original narrative shock years.',
                'Actual published fiscal-shock numeric series with motivation codes, units, sign conventions and replication.',
                'Independent outcome-vintage verification, geographic boundary comparability, pretrend tests and spillover assessments.',
                'AI exposure measurements that actually cover the policy/outcome years; keep scenario forecasts distinct from causal evidence.'
            ],
        }
        (output/'identification_readiness_report.json').write_bytes(json_bytes(summary))
        readme = '''# Phase 6 — Research identification readiness (audit only)\n\nThis output verifies the Phase 5 artifact and inventories what can be matched to the project\'s registered fiscal-policy study metadata. **Zero treatment observations have been extracted or validated.**\nThe blank policy-event template is a schema for manually verified original sources; it is NOT a dataset and contains no synthetic or assumed events.\n\nThe 5 outcomes are kept separate. The 7,767 Treasury measurements remain quarantined.\nNo cross-provider merge, shock scaling, party attribution, regressions, or causal estimates were performed.\n\nRecommended first real research track: verified U.S. state policy actions paired with Census BFS state series (2004-07 through 2026-09). A credible design would need pretrend windows, unaffected comparison states, anticipation and interstate spillover assessments, policy intensity and timing verification, and a longer-run employment outcome such as state QCEW/LAUS.\n\nThe registry also identifies Romer/Romer (USA), Cloyne (UK) and IMF consolidation sources. Retrieve their actual numerical treatment histories and independently verify original definitions before attempting a historical macroeconomic identification design. Data vintage and country coverage must be inspected, rather than inferred from paper publication years.\n'''
        (output/'README.md').write_text(readme,encoding='utf-8')
    file_meta={}
    for f in sorted(output.iterdir()):
        file_meta[f.name]={'sha256':sha(f.read_bytes()),'bytes':f.stat().st_size}
    (output/'sha256_manifest.json').write_bytes(json_bytes(file_meta))
    return summary


def self_test():
    assert period_valid('2004-07','month') and not period_valid('2004-13','month')
    assert period_valid('2026-Q4','quarter') and not period_valid('2026Q4','quarter')
    assert period_valid('2019','year') and not period_valid('2019-01','year')
    assert evidence_match({'country':'USA'},{'geography_levels':['national'], 'geography_codes':{'US'}})[0]=='national_outcome_present'
    assert evidence_match({'country':'USA'},{'geography_levels':['state_fips'], 'geography_codes':{'01'}})[0]=='us_subnational_outcomes_only'
    assert evidence_match({'country':'GBR'},{'geography_levels':['national'], 'geography_codes':{'US'}})[0]=='not_demonstrated'
    assert evidence_match({'country':'multiple'},{'geography_levels':['national'], 'geography_codes':{'US'}})[0]=='unknown_multicountry_coverage'
    print('PASS: fail-closed period, country and comparison design checks')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--self-test',action='store_true')
    ap.add_argument('--phase5-zip',type=Path)
    ap.add_argument('--registry',type=Path,default=Path('policy_shocks/sources.json'))
    ap.add_argument('--out',type=Path,default=Path('readiness_output'))
    args=ap.parse_args()
    if args.self_test:self_test();return
    if not args.phase5_zip:ap.error('--phase5-zip required unless --self-test')
    r=build(args.phase5_zip,args.registry,args.out)
    print(json.dumps({'phase5_measurements':r['phase5_input_measurements'],
        'panels':r['panels_checked'],'registered_studies':r['registered_policy_studies'],
        'study_outcome_pairs':r['registered_study_outcome_pairs_screened'],
        'verified_treatments':r['validated_policy_event_observations'],
        'causal_ready':r['causal_inference_ready']},indent=2))

if __name__=='__main__':main()
