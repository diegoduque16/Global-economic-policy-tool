"""Build a deterministic source registry from the repo's existing catalog.

Usage: python scripts/build_source_registry.py
No internet or third-party Python packages needed. Fails closed on schema errors.
"""
from __future__ import annotations
import csv, hashlib, json, re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

INPUT = Path('open_data/expanded_source_catalog.csv')
OUTPUT = Path('research/source_registry')
REQUIRED = ['theme','dataset','measures','scope','priority','access_mode','current_status','official_source_url','research_rationale_or_limit']


def normalize_status(raw):
    s=raw.strip().upper()
    if s.startswith('LIVE'): return 'reported_live_unverified'
    if s == 'NOT INGESTED': return 'not_ingested'
    if s == 'CATALOGUED': return 'catalogued_only'
    if s == 'IMPORT CODE ONLY': return 'importer_only'
    raise ValueError(f'Unknown catalog status: {raw!r}')


def build(source: Path = INPUT):
    blob=source.read_bytes()
    with source.open(newline='', encoding='utf-8-sig') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames != REQUIRED:
            raise ValueError(f'Unexpected source catalog schema: {reader.fieldnames}')
        records=list(reader)
    if not records: raise ValueError('Empty catalog')
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
           'record_count_verified':None,'research_ready':False
        })
    return blob,registry


def main():
    blob,registry=build()
    OUTPUT.mkdir(parents=True,exist_ok=True)
    status=Counter(r['normalized_status'] for r in registry)
    domains=Counter(r['domain'] for r in registry)
    priority=Counter(r['priority'] for r in registry)
    report={'registry_version':'0.1.0','source_catalog_sha256':hashlib.sha256(blob).hexdigest(),
            'generated_utc':datetime.now(timezone.utc).isoformat(),
            'source_count':len(registry),'by_status':dict(status),
            'by_priority':dict(priority),'by_domain':dict(domains),
            'verified_research_ready_count':0,
            'interpretation':'Catalog status only; labels LIVE are not independently validated by this build',
            'next_actions':['Independently audit live workflows and artifacts',
                            'Resolve license conditions before redistribution',
                            'Persist validated raw/processed snapshots beyond GitHub artifact retention',
                            'Implement connector-by-connector validation gates']}
    (OUTPUT/'registry.json').write_text(json.dumps(registry,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    (OUTPUT/'registry_summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__ == '__main__':main()
