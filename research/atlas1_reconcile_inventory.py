#!/usr/bin/env python3
"""Atlas 1: reconcile stale catalog claims against pinned successful source artifacts.

Records partial evidence, not blanket ingestion claims. Standard library only.
No private dataset values downloaded or placed into public GitHub artifacts.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

EVIDENCE = {
    # Only explicitly scoped family overlap; never imply all series in a provider.
    'World Bank World Development Indicators (32 selected)': ('wdi', 'worldbank_wdi_expanded'),
    'IMF World Economic Outlook (WEO)': ('imf_oecd', 'imf_extension'),
    'IMF Government Finance Statistics (GFS)': ('imf_gfs',),
    'FRED 13 selected U.S. series': ('us_europe',),
    'BLS Current Employment Statistics (CES)': ('labor',),
    'BLS Current Population Survey (CPS)': ('labor',),
    'BLS Quarterly Census of Employment and Wages (QCEW)':
        ('us_state_qcew', 'us_state_qcew_historical', 'bls_qcew_us_national'),
    'BLS Job Openings and Labor Turnover Survey (JOLTS)': ('bls_jolts',),
    'BLS Business Employment Dynamics (BED)': ('bls_bed',),
    'Census County Business Patterns (CBP)': ('census_cbp_states',),
    'Census Business Dynamics Statistics': ('census_bds',),
    'Census Business Formation Statistics': ('census_bfs_monthly',),
    'ILOSTAT employment/working time/earnings': ('labor',),
    'Eurostat selected annual GDP and unemployment': ('us_europe',),
    'Eurostat EU Labour Force Survey aggregates': ('labor',),
    'OECD Revenue Statistics': ('imf_oecd',),
    'OECD Corporate Tax Statistics': ('oecd_cit',),
    'US state and local tax sources (Census STC)': ('us_state_tax',),
    'U.S. Treasury FiscalData': ('us_treasury_mts',),
    'Romer & Romer US exogenous tax changes': ('private_romer_tax_source',),
    'Eurostat enterprise AI adoption isoc_eb_ai': ('eurostat_ai',),
    'Maddison Project via Our World in Data': ('historical',),
    'Den Besten et al 2026 narrative US tariff shocks': ('tariff_shocks',),
    'California AI Exposure and UI Claims Tracker': ('cait_ai_claims',),
}

PROVENANCE = {
    'baseline_run': 40439631125,
    'wave1_audit_run': 40449156776,
    'native_materialize_run': 40617910011,
    'native_quality_run': 40619605467,
    'descriptive_run': 40623397720,
    'romer_private_run': 40626561967,
    'alignment_private_run': 40627969382,
}
SOURCE_BASELINE_HASH = 'c74fa28dabcf58c55afa513bd271a3b465eb121dad61f58c40c7250c2b4c2378'
FIELDS = ('theme', 'dataset', 'measures', 'scope', 'priority', 'access_mode',
          'current_status', 'official_source_url', 'research_rationale_or_limit')


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def confirm_sha(folder: Path, name: str, manifest: str):
    d=load(folder / manifest)
    v=d[name]
    expected=v if isinstance(v,str) else v['sha256']
    blob=(folder / name).read_bytes()
    if hashlib.sha256(blob).hexdigest()!=expected:
        raise ValueError('Artifact checksum disagrees: '+name)


def read_catalog(path):
    with Path(path).open(newline='', encoding='utf-8-sig') as f:
        reader=csv.DictReader(f)
        if tuple(reader.fieldnames or ())!=FIELDS:
            raise ValueError('Source catalog schema changed: '+str(reader.fieldnames))
        entries=list(reader)
    if len(entries)<125:raise ValueError('Source catalog unexpectedly lost entries')
    names=set()
    for r in entries:
        if None in r or any(x is None for x in r.values()):
            raise ValueError('Malformed source catalog row')
        name=r['dataset'].strip()
        if not name or name in names:raise ValueError('Missing/duplicate catalog dataset: '+name)
        if r['priority'] not in ('P0','P1','P2'):
            raise ValueError('Unrecognized catalog priority')
        names.add(name)
    return entries


def reconcile(catalog, baseline, native, descriptive, romer, alignment):
    if baseline.get('source_run_id')!='40439631125':
        raise ValueError('Wrong original baseline run')
    if baseline.get('manifest_sha256')!=SOURCE_BASELINE_HASH:
        raise ValueError('Wrong baseline manifest fingerprint')
    families=baseline.get('families')
    if not isinstance(families,dict) or len(families)!=24 or any(type(v)!=int or v<=0 for v in families.values()):
        raise ValueError('Baseline family dictionary invalid')
    if sum(families.values())!=2_780_117 or baseline.get('observations')!=2_780_117:
        raise ValueError('Baseline family totals have changed')
    if baseline.get('source_family_count')!=len(families):
        raise ValueError('Baseline family count disagreement')
    if native.get('base_analytical_observations_unchanged')!=2_780_117 or native.get('base_source_families_unchanged')!=24:
        raise ValueError('Native quality audit refers to another baseline')
    if native.get('source_snapshot_id')!='canonical-native-20261028-40617910011':
        raise ValueError('Native audit points to unexpected snapshot')
    native_rows={k:v['audited_rows'] for k,v in native['source_reports'].items()}
    if len(native_rows)!=5 or any(type(n)!=int or n<1 for n in native_rows.values()):
        raise ValueError('Native source dictionary invalid')
    if sum(native_rows.values())!=2_104_609 or native.get('queryable_source_native_rows_audited')!=2_104_609:
        raise ValueError('Native source totals mismatch')
    if native.get('ready_for_cross_provider_harmonization') is not False or native.get('ready_for_causal_inference') is not False:
        raise ValueError('Native quality status changed unexpectedly')
    if descriptive.get('descriptive_panel_measurements')!=67_842 or descriptive.get('treasury_quarantined_measurements')!=7_767:
        raise ValueError('Unexpected descriptive panel values')
    if descriptive.get('cross_source_harmonized') is not False or descriptive.get('eligible_for_causal_inference') is not False:
        raise ValueError('Unexpected causal or harmonization classification')
    if romer.get('private_quarterly_rows')!=756 or romer.get('private_component_measurements')!=2_268 or romer.get('numeric_source_data_in_public_artifact') is not False:
        raise ValueError('Private Romer extraction not verified')
    if romer.get('causal_inference_ready') is not False:
        raise ValueError('Romer is not a verified independent causal replication')
    if alignment.get('original_policy_rows')!=756 or alignment.get('joined_policy_outcome_rows')!=756:
        raise ValueError('Historical quarter matching not verified')
    if alignment.get('outcome_measurements_present')!=2_940 or sum(alignment.get('outcome_missing_cells',{}).values())!=84:
        raise ValueError('Historical outcome coverage mismatch')
    if alignment.get('causal_inference_ready') is not False or alignment.get('numeric_source_values_in_public_report') is not False:
        raise ValueError('Historical alignment research-readiness classification changed')

    source_records=[]
    for f,n in sorted(families.items()):
        source_records.append(dict(source_family=f, layer='CANONICAL_SOURCE_OBSERVATIONS', records=n,
            provenance_run=PROVENANCE['baseline_run'],research_ready='false',
            caveat='Native provider definitions preserved; no broad cross-provider harmonization'))
    for f,n in sorted(native_rows.items()):
        source_records.append(dict(source_family=f, layer='SOURCE_NATIVE_UNHARMONIZED', records=n,
            provenance_run=PROVENANCE['native_quality_run'],research_ready='false',
            caveat='Searchable original source records, possibly overlapping baseline source values'))
    catalog_names={r['dataset'] for r in catalog}
    if not set(EVIDENCE).issubset(catalog_names):
        raise ValueError('Catalog coverage changed: some explicit mappings need review')
    available=set(families)|set(native_rows)|{'private_romer_tax_source'}
    bad={k:[x for x in v if x not in available] for k,v in EVIDENCE.items() if not set(v).issubset(available)}
    if bad:raise ValueError('Catalog mapping references unknown source family: '+str(bad))
    mapped=[];legacy_changed=[];unmapped=[];catalog_rows=[]
    for r in catalog:
        groups=EVIDENCE.get(r['dataset'],())
        layer=[]
        for g in groups:
            if g in families:layer.append('CANONICAL_PARTIAL_FAMILY_EVIDENCE')
            if g in native_rows:layer.append('SOURCE_NATIVE_UNHARMONIZED_EVIDENCE')
            if g=='private_romer_tax_source':layer.append('PRIVATE_ORIGINAL_STUDY_VALUES_EVIDENCE')
        status='PARTIAL_EVIDENCE_PRESENT' if groups else 'NO_EXPLICIT_FAMILY_MAPPING_YET'
        if groups: mapped.append(r['dataset'])
        else: unmapped.append(r['dataset'])
        if groups and r['current_status'].strip().upper() in ('NOT INGESTED','CATALOGUED','IMPORT CODE ONLY'):
            legacy_changed.append(r['dataset'])
        row={
            'theme':r['theme'],'dataset':r['dataset'],'priority':r['priority'],
            'catalog_claim_as_written':r['current_status'],
            'verified_evidence_status':status,
            'evidence_layers':';'.join(sorted(set(layer))),
            'source_family_ids':';'.join(groups),
            'observed_source_records_by_family':';'.join(f'{g}:{families[g] if g in families else native_rows[g] if g in native_rows else 756}' for g in groups),
            'full_dataset_scope_verified':'false',
            'cross_provider_harmonized':'false',
            'redistribution_license_verified':'false',
            'research_ready':'false',
            'interpretation':('Matched source family or private study only; does NOT establish full catalog dataset coverage, independent value counts, legal redistribution, or causal validity'
                              if groups else 'No manual match against verified evidence in this Atlas 1 pilot; NOT proof the dataset is absent'),
        }
        catalog_rows.append(row)

    unlinked=sorted((set(families)|set(native_rows))-{g for v in EVIDENCE.values() for g in v})
    report={
      'schema_version':'atlas1-inventory-v1','generated_utc':datetime.now(timezone.utc).isoformat(),
      'input_verified_runs':PROVENANCE,
      'catalog_entries':len(catalog),'catalog_priority_p0':sum(r['priority']=='P0' for r in catalog),
      'catalog_entries_with_explicit_partial_evidence':len(mapped),
      'catalog_entries_without_manual_evidence_mapping':len(unmapped),
      'catalog_previous_not_ingested_or_catalogued_now_partial_evidence':len(legacy_changed),
      'catalog_reconciliation_names':legacy_changed,
      'canonical_source_families':len(families),'canonical_observations':sum(families.values()),
      'additional_native_sources':len(native_rows),'additional_native_records':sum(native_rows.values()),
      'source_records_across_separate_layers_not_deduplicated':sum(families.values())+sum(native_rows.values()),
      'descriptive_panel_measurements_derived_not_additive':67_842,
      'quarantined_treasury_measurements':7_767,
      'private_romer_quarterly_source_rows_not_additive':756,
      'private_aligned_source_rows_not_additive':756,
      'private_aligned_outcome_measurements_not_additive':2_940,
      'unmapped_verified_source_families':unlinked,
      'all_source_data_integrated_to_common_gold_panel':False,
      'source_value_reverification_performed_by_atlas1':False,
      'causal_research_ready':False,
      'limitations':[
        'Partial overlaps: multiple catalog entries may map to the same family; do not sum counts across catalog rows.',
        'Verified source counts are derived from pinned successful artifact reports, not a newly executed row-by-row source audit.',
        'The original wave1 baseline manifest audit verifies counts and member existence, not each decompressed hash independently.',
        'Missing explicit catalog evidence is not proof of absence in another unassessed dataset.',
        'Stored rows across canonical and native layers can overlap; no unique economic measurements total is claimed.',
        'Private study values remain private; this public output contains only counts, labels and provenance.',
        'Neither a successful extraction nor provider narrative classifications independently establishes causal identification.'
      ]}
    return source_records,catalog_rows,report


def write_csv(path,rows):
    if not rows:raise ValueError('Refusing to create empty CSV')
    with Path(path).open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def build(catalog_path,inputs_dir,out):
    inputs_dir=Path(inputs_dir);out=Path(out)
    if out.exists() and any(out.iterdir()):raise FileExistsError('Atlas 1 output directory must be empty')
    out.mkdir(parents=True,exist_ok=True)
    baseline=load(inputs_dir/'wave1/source_wave1_results/baseline_manifest_audit.json')
    quality_dir=inputs_dir/'quality'
    confirm_sha(quality_dir,'native_quality_report.json','sha256_manifest.json')
    native=load(quality_dir/'native_quality_report.json')
    panels_dir=inputs_dir/'descriptive'
    confirm_sha(panels_dir,'descriptive_panel_report.json','sha256_manifest.json')
    descriptive=load(panels_dir/'descriptive_panel_report.json')
    romer=load(inputs_dir/'romer/romer_private_extraction_audit.json')
    aligned=load(inputs_dir/'alignment/historical_outcome_alignment_audit.json')
    source_rows,catalog_rows,report=reconcile(read_catalog(catalog_path),baseline,native,descriptive,romer,aligned)
    write_csv(out/'verified_source_family_inventory.csv',source_rows)
    write_csv(out/'catalog_evidence_reconciliation.csv',catalog_rows)
    (out/'atlas1_inventory_summary.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    checks={}
    for path in sorted(out.iterdir()):checks[path.name]={'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    (out/'sha256_manifest.json').write_text(json.dumps(checks,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('catalog_entries','catalog_entries_with_explicit_partial_evidence','catalog_entries_without_manual_evidence_mapping','canonical_source_families','canonical_observations','additional_native_sources','additional_native_records')},indent=2))
    return report


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--catalog',default='open_data/expanded_source_catalog.csv')
    ap.add_argument('--inputs-dir',default='atlas1_inputs')
    ap.add_argument('--out',default='atlas1_output')
    args=ap.parse_args()
    build(args.catalog,args.inputs_dir,args.out)

if __name__=='__main__':main()
