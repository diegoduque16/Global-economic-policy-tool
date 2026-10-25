"""Fail-closed conversion of HUMAN-REVIEWED numeric source observations.

Does not extract ambiguous ZIP columns or assign causal identification automatically.
Only original reported series values with documented units, sign and provenance are
accepted. No joins to GDP, employment, party affiliation, or AI series.
"""
from __future__ import annotations
import argparse
import csv
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re

FIELDS=['study_id','source_member','source_member_sha256','source_row_ref',
        'country_iso3','period','frequency','shock_original_value','shock_unit',
        'source_column','sign_convention','policy_family','identification_class',
        'motivation_codebook_quote_or_reference','codebook_url','reviewed_by','review_date',
        'review_status']
ALLOWED_FAMILIES={'tax','spending','combined_consolidation'}
ALLOWED_IDENTIFICATION={'narrative_exogenous_tax','narrative_deficit_consolidation'}
VALID_FREQ={'annual','quarterly'}


def load_inventory(path):
    inv=json.loads(Path(path).read_text(encoding='utf-8'))
    if inv.get('source_id')!='romer_romer_2010' or inv.get('download_is_local_fixture') is True:
        raise ValueError('Requires a genuine auto-audited primary-source archive; fixtures not admissible')
    members={x['member']:x['sha256'] for x in inv['members']}
    if not members: raise ValueError('Empty inventory')
    return inv, members


def reviewed_to_rows(path, inventory_path, registry_path):
    inv,members=load_inventory(inventory_path)
    registry=json.loads(Path(registry_path).read_text(encoding='utf-8'))
    recognized={x['id']:x for x in registry['sources']}
    accepted=[]; seen=set()
    with Path(path).open(newline='',encoding='utf-8-sig') as file:
        reader=csv.DictReader(file)
        if set(reader.fieldnames or [])!=set(FIELDS):
            raise ValueError('Reviewed CSV columns must match reviewed_shocks_TEMPLATE.csv exactly')
        for n,row in enumerate(reader,start=2):
            if not any((v or '').strip() for v in row.values()):continue
            if any(not (v or '').strip() for v in row.values()):
                raise ValueError(f'Row {n}: blank required value')
            if row['study_id'] != inv['source_id'] or row['study_id'] not in recognized:
                raise ValueError(f'Row {n}: source does not match audited archive')
            if row['review_status']!='accepted':
                raise ValueError(f'Row {n}: must be explicitly accepted by a reviewer')
            if row['source_member'] not in members or row['source_member_sha256']!=members[row['source_member']]:
                raise ValueError(f'Row {n}: source member digest mismatch')
            if not re.fullmatch(r'[A-Z]{3}',row['country_iso3']):
                raise ValueError(f'Row {n}: invalid ISO3 format')
            if row['country_iso3'] != 'USA':
                raise ValueError(f'Row {n}: Romer & Romer source must be USA')
            if row['policy_family'] not in ALLOWED_FAMILIES or row['policy_family'] != 'tax':
                raise ValueError(f'Row {n}: source-policy family not verified')
            if row['identification_class'] != 'narrative_exogenous_tax':
                raise ValueError(f'Row {n}: cannot reclassify narrative design')
            if row['frequency'] not in VALID_FREQ:
                raise ValueError(f'Row {n}: unsupported frequency')
            pattern=r'\d{4}' if row['frequency']=='annual' else r'\d{4}Q[1-4]'
            if not re.fullmatch(pattern,row['period']):
                raise ValueError(f'Row {n}: invalid period')
            year=int(row['period'][:4])
            if not 1945 <= year <= 2010:
                raise ValueError(f'Row {n}: period outside conservative research scope')
            try: value=float(row['shock_original_value'])
            except ValueError as exc: raise ValueError(f'Row {n}: nonnumeric shock') from exc
            if not math.isfinite(value):raise ValueError(f'Row {n}: nonfinite shock')
            if not row['codebook_url'].startswith('https://'):
                raise ValueError(f'Row {n}: codebook HTTPS reference required')
            try: reviewed_date=date.fromisoformat(row['review_date'])
            except ValueError as exc: raise ValueError(f'Row {n}: review_date must be YYYY-MM-DD') from exc
            if reviewed_date>date.today(): raise ValueError(f'Row {n}: future review date')
            key=(row['study_id'],row['source_member'],row['source_row_ref'],row['source_column'])
            if key in seen:raise ValueError(f'Row {n}: duplicate source observation')
            seen.add(key)
            # Store literal source value; NEVER change its sign or assume pct GDP.
            accepted.append(dict(row))
    if not accepted: raise ValueError('No reviewed numeric shock observations supplied')
    return accepted, inv


def build(input_file,inventory,registry,out):
    records,inv=reviewed_to_rows(input_file,inventory,registry)
    root=Path(out)
    root.mkdir(parents=True,exist_ok=True)
    if any(root.iterdir()):raise FileExistsError('Output directory must be empty')
    dest=root/'reviewed_policy_shock_observations.csv'
    with dest.open('w',newline='',encoding='utf-8') as file:
        writer=csv.DictWriter(file,fieldnames=FIELDS);writer.writeheader();writer.writerows(records)
    report={'status':'reviewed_source_values_not_causal_estimates',
      'source_study':inv['source_id'],'source_archive_sha256':inv['source_archive_sha256'],
      'observations':len(records),'periods':len(set(x['period'] for x in records)),
      'signed_values_unmodified':True,'cross_source_comparison_ready':False,
      'causal_estimation_ready':False,'no_zero_filling_or_interpolation':True,
      'reviewed_csv_sha256':hashlib.sha256(dest.read_bytes()).hexdigest(),
      'warnings':['Reviewer acceptance is necessary but does not prove successful scientific replication.',
          'The importer does not check that the quoted values match source bytes; independent extraction verification is required.',
          'No estimates of jobs caused by policies are generated.']}
    (root/'reviewed_policy_shock_report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input',required=True);ap.add_argument('--inventory',required=True)
    ap.add_argument('--registry',default='policy_shocks/sources.json')
    ap.add_argument('--out',default='reviewed_shock_output');args=ap.parse_args()
    build(args.input,args.inventory,args.registry,args.out)

if __name__=='__main__':main()
