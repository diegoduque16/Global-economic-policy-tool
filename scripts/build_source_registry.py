"""Build a deterministic source registry from the repo's existing catalog.

Usage: python scripts/build_source_registry.py
No internet or third-party Python packages needed. Fails closed on schema errors.
"""
from __future__ import annotations
import csv, hashlib, json, re, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

# Reuse Atlas 1's historical partial crosswalk; do not create a competing registry.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research.atlas1_reconcile_inventory import EVIDENCE as ATLAS1_FAMILY_MAP

HISTORICAL_MAP_URL = 'https://github.com/diegoduque16/Global-economic-policy-tool/actions/runs/40631309627'
SEPARATE_SOURCE_RUNS = {
    'World Inequality Database WID': ('SEPARATE_WID_NUMERIC_SNAPSHOT', 'https://github.com/diegoduque16/Global-economic-policy-tool/actions/runs/41015652979'),
    'ILOSTAT employment/working time/earnings': ('PARTIAL_RAW_BULK_ARCHIVE', 'https://github.com/diegoduque16/Global-economic-policy-tool/actions/runs/43207368259'),
}
# A candidate overlap is NOT a demonstrated duplicate observation.
OVERLAP_REVIEW_REQUIRED = {
    'World Bank World Development Indicators (32 selected)',
    'World Inequality Database WID',
    'ILOSTAT employment/working time/earnings',
}

INPUT = Path('open_data/expanded_source_catalog.csv')
OUTPUT = Path('research/source_registry')
WID_MANIFEST = Path('research/wid_23_source_manifest.json')
WID_REFRESH_WORKFLOW = Path('.github/workflows/atlas-wid-expand-20-countries.yml')
WID_REFRESH_SUCCESS_RUN = 'https://github.com/diegoduque16/Global-economic-policy-tool/actions/runs/41588514160'
REQUIRED = ['theme','dataset','measures','scope','priority','access_mode','current_status','official_source_url','research_rationale_or_limit']


def normalize_status(raw):
    s=raw.strip().upper()
    if s.startswith('LIVE'): return 'reported_live_unverified'
    if s == 'NOT INGESTED': return 'not_ingested'
    if s == 'CATALOGUED': return 'catalogued_only'
    if s == 'IMPORT CODE ONLY': return 'importer_only'
    raise ValueError(f'Unknown catalog status: {raw!r}')


def wid_country_refresh_scope(manifest_path: Path = WID_MANIFEST,
                              workflow_path: Path = WID_REFRESH_WORKFLOW) -> dict:
    """Compare original SHA-pinned WID country scope with existing refresh plan.

    Does NOT read numeric values, assert refreshed files are identical, or
    count a new WID value from a successful country refresh.
    """
    original = json.loads(manifest_path.read_text(encoding='utf-8'))
    countries = original.get('countries')
    if not isinstance(countries, list) or len(countries) != original.get('expected_country_count'):
        raise ValueError('WID manifest country count inconsistent')
    codes = [x['wid_area'] for x in countries]
    if any(not re.fullmatch(r'[A-Z]{2}', c) for c in codes) or len(codes) != len(set(codes)):
        raise ValueError('WID original country codes invalid/duplicated')
    workflow = workflow_path.read_text(encoding='utf-8')
    # Deliberately restricted to this exact checked-in workflow format.
    # Fail closed rather than guess when the matrix changes.
    batches = re.findall(r'^\s+countries:\s*([A-Z]{2}(?:,[A-Z]{2})+)\s*
    """Conservative historical linkage, not a fresh value-level verification."""
    families = list(ATLAS1_FAMILY_MAP.get(dataset, ()))
    separate = SEPARATE_SOURCE_RUNS.get(dataset)
    if families:
        basis = 'HISTORICAL_PARTIAL_FAMILY_MAP_NOT_SERIES_VALIDATED'
    elif separate:
        basis = 'HISTORICAL_SEPARATE_SOURCE_RUN_NOT_CANONICAL'
    else:
        basis = 'NO_EXPLICIT_EVIDENCE_MAPPING_NOT_PROOF_OF_ABSENCE'
    return {
        'evidence_basis': basis,
        'historical_family_ids': families,
        'historical_family_crosswalk_audit': HISTORICAL_MAP_URL if families else None,
        'additional_source_stage': separate[0] if separate else None,
        'additional_source_run': separate[1] if separate else None,
        'exact_series_keys_verified': False,
        'complete_catalog_source_coverage_verified': False,
        'last_collected_at_utc': None,
        'last_warehouse_integrated_at_utc': None,
        'last_research_panel_integrated_at_utc': None,
        'source_snapshot_hash_verified_by_registry': False,
        'overlap_review': ('POTENTIAL_OVERLAP_NEEDS_KEY_AUDIT' if dataset in OVERLAP_REVIEW_REQUIRED else 'NOT_ASSESSED'),
        'verified_duplicate_observation_count': None,
        'interpretation': 'Historical partial evidence is not a fresh source-value audit, proof of complete source coverage, or a certified warehouse refresh.',
    }


def priority_evidence_rows(registry: list[dict]) -> list[dict]:
    """An evidence view of existing P0 catalog entries, not another inventory."""
    rows = []
    for item in registry:
        if item['priority'] != 'P0':
            continue
        ev = item['evidence_linkage']
        rows.append({
            'source_id': item['source_id'],
            'domain': item['domain'],
            'dataset': item['dataset'],
            'catalog_status_original': item['catalog_status_original'],
            'evidence_basis': ev['evidence_basis'],
            'historical_family_ids': ';'.join(ev['historical_family_ids']),
            'historical_family_crosswalk_audit': ev['historical_family_crosswalk_audit'] or '',
            'additional_source_stage': ev['additional_source_stage'] or '',
            'additional_source_run': ev['additional_source_run'] or '',
            'overlap_review': ev['overlap_review'],
            'wid_scheduled_country_overlap_count': (str(ev['wid_refresh_country_scope']['previously_in_original_country_count'])
                if 'wid_refresh_country_scope' in ev else ''),
            'wid_scheduled_new_country_count': (str(len(ev['wid_refresh_country_scope']['new_country_codes_not_in_original']))
                if 'wid_refresh_country_scope' in ev else ''),
            'wid_value_level_revision_audit_complete': 'false',
            'exact_series_keys_verified': 'false',
            'complete_source_coverage_verified': 'false',
            'last_collected_at_utc': '',
            'last_warehouse_integrated_at_utc': '',
            'research_ready': 'false',
            'next_gate': ('COMPARE_SOURCE_SERIES_PERIOD_GEO_UNIT_VINTAGE'
                          if ev['overlap_review'].startswith('POTENTIAL')
                          else 'VERIFY_PROVIDER_NATIVE_SERIES_AND_PROVENANCE'),
        })
    return rows


def build(source: Path = INPUT):
    blob=source.read_bytes()
    with source.open(newline='', encoding='utf-8-sig') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames != REQUIRED:
            raise ValueError(f'Unexpected source catalog schema: {reader.fieldnames}')
        records=list(reader)
    if not records: raise ValueError('Empty catalog')
    catalog_names = {x['dataset'].strip() for x in records}
    if not set(ATLAS1_FAMILY_MAP).issubset(catalog_names):
        raise ValueError('Historical Atlas 1 evidence crosswalk no longer matches source catalog')
    if not set(SEPARATE_SOURCE_RUNS).issubset(catalog_names):
        raise ValueError('Separate source evidence no longer matches source catalog')
    wid_scope = wid_country_refresh_scope()
    seen=set(); registry=[]
    for i,row in enumerate(records, start=2):
        if any(v is None for v in row.values()) or None in row: raise ValueError(f'Malformed CSV row {i}')
        if not row['dataset'].strip() or not row['theme'].strip(): raise ValueError(f'Missing required name at row {i}')
        if row['priority'] not in ('P0','P1','P2'): raise ValueError(f'Bad priority at row {i}')
        url=row['official_source_url'].strip()
        p=urlsplit(url)
        if p.scheme != 'https' or not p.hostname: raise ValueError(f'Non-HTTPS source URL at row {i}')
        key=(row['theme'].casefold().strip(), row['dataset'].casefold().strip())
        if key in seen:raise ValueError(f'Duplicate source: {key}')
        seen.add(key)
        slug=re.sub(r'[^a-z0-9]+','-',row['dataset'].lower()).strip('-')[:72]
        linked = evidence_for(row['dataset'].strip())
        if row['dataset'].strip() == 'World Inequality Database WID':
            linked['wid_refresh_country_scope'] = wid_scope
        registry.append({
           'source_id':f'src-{i-1:03d}-{slug}',
           'domain':row['theme'].strip(), 'dataset':row['dataset'].strip(),
           'measures':row['measures'].strip(),'coverage_as_catalogued':row['scope'].strip(),
           'priority':row['priority'],'access_method_as_catalogued':row['access_mode'].strip(),
           'source_url':url,'catalog_status_original':row['current_status'].strip(),
           'normalized_status':normalize_status(row['current_status']),
           'validation_state':'NOT_INDEPENDENTLY_VERIFIED',
           'fetch_attempted_by_registry_builder':False,
           'catalog_notes':row['research_rationale_or_limit'].strip(),
           'required_gates':['verify_official_definition','verify_terms_and_redistribution',
                             'document_download_method','record_version_and_retrieval_time',
                             'capture_checksum','validate_units_frequency_geography_and_missingness',
                             'preserve_original_raw_file','confirm_observation_count'],
           'connector_implementation':None,'last_successful_fetch_utc':None,
           'license_verified':False,'data_snapshot_sha256':None,
           'record_count_verified':None,'research_ready':False,
           'evidence_linkage':linked
        })
    return blob,registry


def main():
    blob,registry=build()
    OUTPUT.mkdir(parents=True,exist_ok=True)
    status=Counter(r['normalized_status'] for r in registry)
    domains=Counter(r['domain'] for r in registry)
    priority=Counter(r['priority'] for r in registry)
    p0 = priority_evidence_rows(registry)
    if sum(v['priority'] == 'P0' for v in registry) != len(p0):
        raise ValueError('P0 view does not match source registry')
    basis_counts = Counter(v['evidence_basis'] for v in p0)
    report={'registry_version':'0.2.0','source_catalog_sha256':hashlib.sha256(blob).hexdigest(),
            'generated_utc':datetime.now(timezone.utc).isoformat(),
            'source_count':len(registry),'by_status':dict(status),
            'lineage_evidence_map_sha256':hashlib.sha256(json.dumps({
                'wid_manifest_sha256':hashlib.sha256(WID_MANIFEST.read_bytes()).hexdigest(),
                'wid_refresh_workflow_sha256':hashlib.sha256(WID_REFRESH_WORKFLOW.read_bytes()).hexdigest(),
                'historical_family_map':ATLAS1_FAMILY_MAP,
                'separate_source_runs':SEPARATE_SOURCE_RUNS,
                'overlap_review_required':sorted(OVERLAP_REVIEW_REQUIRED),
            },sort_keys=True).encode('utf-8')).hexdigest(),
            'by_priority':dict(priority),'by_domain':dict(domains),
            'wid_country_refresh_scope_audit':next(x['evidence_linkage']['wid_refresh_country_scope']
                for x in registry if x['dataset']=='World Inequality Database WID'),
            'verified_research_ready_count':0,
            'priority_p0_evidence_summary': {
                'source_count':len(p0),
                'by_evidence_basis':dict(basis_counts),
                'potential_overlap_source_count':sum(x['overlap_review']=='POTENTIAL_OVERLAP_NEEDS_KEY_AUDIT' for x in p0),
                'exact_series_key_audits_completed':0,
                'fully_ingested_source_count_proven_by_this_builder':0,
                'observation_counts_are_additive':False,
                'basis':'Existing Atlas 1 crosswalk and pinned separate source runs; NOT fresh numerical verification',
            },
            'interpretation':'Catalog status only; labels LIVE are not independently validated by this build',
            'next_actions':['Independently audit live workflows and artifacts',
                            'Resolve license conditions before redistribution',
                            'Persist validated raw/processed snapshots beyond GitHub artifact retention',
                            'Implement connector-by-connector validation gates']}
    (OUTPUT/'registry.json').write_text(json.dumps(registry,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    (OUTPUT/'registry_summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    if not p0:
        raise ValueError('No priority sources to reconcile')
    with (OUTPUT/'priority_p0_evidence.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(p0[0]))
        writer.writeheader()
        writer.writerows(p0)
    print(json.dumps(report,indent=2))

if __name__ == '__main__':main()
,
                         workflow, flags=re.MULTILINE)
    if len(batches) != 5:
        raise ValueError('Unexpected WID country refresh matrix; manual review required')
    refreshed = [code for batch in batches for code in batch.split(',')]
    if len(refreshed) != 20 or len(set(refreshed)) != 20:
        raise ValueError('Duplicate/unexpected WID refresh country codes')
    existing, proposed = set(codes), set(refreshed)
    return {
        'evidence_type': 'COUNTRY_SCOPE_ONLY_NOT_OBSERVATION_DEDUPLICATION',
        'original_country_count': len(codes),
        'scheduled_refresh_country_count': len(refreshed),
        'previously_in_original_country_count': len(existing & proposed),
        'new_country_codes_not_in_original': sorted(proposed-existing),
        'original_country_codes_not_in_refresh': sorted(existing-proposed),
        'original_manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        'refresh_workflow_sha256': hashlib.sha256(workflow_path.read_bytes()).hexdigest(),
        'referenced_november_3_successful_run': WID_REFRESH_SUCCESS_RUN,
        'run_status_reverified_by_registry': False,
        'same_observation_keys_or_values_verified': False,
        'source_vintages_compared': False,
        'new_numeric_observations_verified': False,
        'next_gate': 'COMPARE_PRIVATE_WID_SOURCE_FILE_HASH_AND_COUNTRY_YEAR_PERCENTILE_CONCEPT_KEYS',
    }


def evidence_for(dataset: str) -> dict:
    """Conservative historical linkage, not a fresh value-level verification."""
    families = list(ATLAS1_FAMILY_MAP.get(dataset, ()))
    separate = SEPARATE_SOURCE_RUNS.get(dataset)
    if families:
        basis = 'HISTORICAL_PARTIAL_FAMILY_MAP_NOT_SERIES_VALIDATED'
    elif separate:
        basis = 'HISTORICAL_SEPARATE_SOURCE_RUN_NOT_CANONICAL'
    else:
        basis = 'NO_EXPLICIT_EVIDENCE_MAPPING_NOT_PROOF_OF_ABSENCE'
    return {
        'evidence_basis': basis,
        'historical_family_ids': families,
        'historical_family_crosswalk_audit': HISTORICAL_MAP_URL if families else None,
        'additional_source_stage': separate[0] if separate else None,
        'additional_source_run': separate[1] if separate else None,
        'exact_series_keys_verified': False,
        'complete_catalog_source_coverage_verified': False,
        'last_collected_at_utc': None,
        'last_warehouse_integrated_at_utc': None,
        'last_research_panel_integrated_at_utc': None,
        'source_snapshot_hash_verified_by_registry': False,
        'overlap_review': ('POTENTIAL_OVERLAP_NEEDS_KEY_AUDIT' if dataset in OVERLAP_REVIEW_REQUIRED else 'NOT_ASSESSED'),
        'verified_duplicate_observation_count': None,
        'interpretation': 'Historical partial evidence is not a fresh source-value audit, proof of complete source coverage, or a certified warehouse refresh.',
    }


def priority_evidence_rows(registry: list[dict]) -> list[dict]:
    """An evidence view of existing P0 catalog entries, not another inventory."""
    rows = []
    for item in registry:
        if item['priority'] != 'P0':
            continue
        ev = item['evidence_linkage']
        rows.append({
            'source_id': item['source_id'],
            'domain': item['domain'],
            'dataset': item['dataset'],
            'catalog_status_original': item['catalog_status_original'],
            'evidence_basis': ev['evidence_basis'],
            'historical_family_ids': ';'.join(ev['historical_family_ids']),
            'historical_family_crosswalk_audit': ev['historical_family_crosswalk_audit'] or '',
            'additional_source_stage': ev['additional_source_stage'] or '',
            'additional_source_run': ev['additional_source_run'] or '',
            'overlap_review': ev['overlap_review'],
            'exact_series_keys_verified': 'false',
            'complete_source_coverage_verified': 'false',
            'last_collected_at_utc': '',
            'last_warehouse_integrated_at_utc': '',
            'research_ready': 'false',
            'next_gate': ('COMPARE_SOURCE_SERIES_PERIOD_GEO_UNIT_VINTAGE'
                          if ev['overlap_review'].startswith('POTENTIAL')
                          else 'VERIFY_PROVIDER_NATIVE_SERIES_AND_PROVENANCE'),
        })
    return rows


def build(source: Path = INPUT):
    blob=source.read_bytes()
    with source.open(newline='', encoding='utf-8-sig') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames != REQUIRED:
            raise ValueError(f'Unexpected source catalog schema: {reader.fieldnames}')
        records=list(reader)
    if not records: raise ValueError('Empty catalog')
    catalog_names = {x['dataset'].strip() for x in records}
    if not set(ATLAS1_FAMILY_MAP).issubset(catalog_names):
        raise ValueError('Historical Atlas 1 evidence crosswalk no longer matches source catalog')
    if not set(SEPARATE_SOURCE_RUNS).issubset(catalog_names):
        raise ValueError('Separate source evidence no longer matches source catalog')
    seen=set(); registry=[]
    for i,row in enumerate(records, start=2):
        if any(v is None for v in row.values()) or None in row: raise ValueError(f'Malformed CSV row {i}')
        if not row['dataset'].strip() or not row['theme'].strip(): raise ValueError(f'Missing required name at row {i}')
        if row['priority'] not in ('P0','P1','P2'): raise ValueError(f'Bad priority at row {i}')
        url=row['official_source_url'].strip()
        p=urlsplit(url)
        if p.scheme != 'https' or not p.hostname: raise ValueError(f'Non-HTTPS source URL at row {i}')
        key=(row['theme'].casefold().strip(), row['dataset'].casefold().strip())
        if key in seen:raise ValueError(f'Duplicate source: {key}')
        seen.add(key)
        slug=re.sub(r'[^a-z0-9]+','-',row['dataset'].lower()).strip('-')[:72]
        registry.append({
           'source_id':f'src-{i-1:03d}-{slug}',
           'domain':row['theme'].strip(), 'dataset':row['dataset'].strip(),
           'measures':row['measures'].strip(),'coverage_as_catalogued':row['scope'].strip(),
           'priority':row['priority'],'access_method_as_catalogued':row['access_mode'].strip(),
           'source_url':url,'catalog_status_original':row['current_status'].strip(),
           'normalized_status':normalize_status(row['current_status']),
           'validation_state':'NOT_INDEPENDENTLY_VERIFIED',
           'fetch_attempted_by_registry_builder':False,
           'catalog_notes':row['research_rationale_or_limit'].strip(),
           'required_gates':['verify_official_definition','verify_terms_and_redistribution',
                             'document_download_method','record_version_and_retrieval_time',
                             'capture_checksum','validate_units_frequency_geography_and_missingness',
                             'preserve_original_raw_file','confirm_observation_count'],
           'connector_implementation':None,'last_successful_fetch_utc':None,
           'license_verified':False,'data_snapshot_sha256':None,
           'record_count_verified':None,'research_ready':False,
           'evidence_linkage':evidence_for(row['dataset'].strip())
        })
    return blob,registry


def main():
    blob,registry=build()
    OUTPUT.mkdir(parents=True,exist_ok=True)
    status=Counter(r['normalized_status'] for r in registry)
    domains=Counter(r['domain'] for r in registry)
    priority=Counter(r['priority'] for r in registry)
    p0 = priority_evidence_rows(registry)
    if sum(v['priority'] == 'P0' for v in registry) != len(p0):
        raise ValueError('P0 view does not match source registry')
    basis_counts = Counter(v['evidence_basis'] for v in p0)
    report={'registry_version':'0.2.0','source_catalog_sha256':hashlib.sha256(blob).hexdigest(),
            'generated_utc':datetime.now(timezone.utc).isoformat(),
            'source_count':len(registry),'by_status':dict(status),
            'lineage_evidence_map_sha256':hashlib.sha256(json.dumps({
                'historical_family_map':ATLAS1_FAMILY_MAP,
                'separate_source_runs':SEPARATE_SOURCE_RUNS,
                'overlap_review_required':sorted(OVERLAP_REVIEW_REQUIRED),
            },sort_keys=True).encode('utf-8')).hexdigest(),
            'by_priority':dict(priority),'by_domain':dict(domains),
            'verified_research_ready_count':0,
            'priority_p0_evidence_summary': {
                'source_count':len(p0),
                'by_evidence_basis':dict(basis_counts),
                'potential_overlap_source_count':sum(x['overlap_review']=='POTENTIAL_OVERLAP_NEEDS_KEY_AUDIT' for x in p0),
                'exact_series_key_audits_completed':0,
                'fully_ingested_source_count_proven_by_this_builder':0,
                'observation_counts_are_additive':False,
                'basis':'Existing Atlas 1 crosswalk and pinned separate source runs; NOT fresh numerical verification',
            },
            'interpretation':'Catalog status only; labels LIVE are not independently validated by this build',
            'next_actions':['Independently audit live workflows and artifacts',
                            'Resolve license conditions before redistribution',
                            'Persist validated raw/processed snapshots beyond GitHub artifact retention',
                            'Implement connector-by-connector validation gates']}
    (OUTPUT/'registry.json').write_text(json.dumps(registry,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    (OUTPUT/'registry_summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    if not p0:
        raise ValueError('No priority sources to reconcile')
    with (OUTPUT/'priority_p0_evidence.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(p0[0]))
        writer.writeheader()
        writer.writerows(p0)
    print(json.dumps(report,indent=2))

if __name__ == '__main__':main()
