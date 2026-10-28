#!/usr/bin/env python3
"""Extract pinned author-source quarterly Romer/Romer 2010 tax measures to PRIVATE storage.

The original ZIP and workbook hashes, date labels, exact series names and component
identity are checked independently. No causal effect is estimated. The original
numeric values are written ONLY to a local private output folder; GitHub artifacts
must contain ONLY the separate, nonnumeric metadata output.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import statistics
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from policy_shocks.audit_sources import retrieve
from policy_shocks.inspect_romer import SOURCE_SHA256, WORKBOOK_SHA256, WORKBOOK
from policy_shocks.romer_tax_mapping import workbook_cells, MAIN, NS, RELS, PKG

BLOCKS = (
    dict(name='nonretroactive', deficit='K', growth='L', exogenous='N',
         labels=('DEFICNRRATIO','LONGRNRRATIO','EXOGENRRATIO'), expected_nonzero=45),
    dict(name='retroactive', deficit='Y', growth='Z', exogenous='AB',
         labels=('DEFICRETRORATIO','LONGRRETRORATIO','EXOGERETRORATIO'), expected_nonzero=53),
    dict(name='present_value', deficit='AM', growth='AN', exogenous='AP',
         labels=('DEFICPDVRATIO','LONGRPDVRATIO','EXOGEPDVRATIO'), expected_nonzero=29),
)
EXPECTED = [f'{y}Q{q}' for y in range(1945,2008) for q in range(1,5)]
ROMANS = {'I':'1','II':'2','III':'3','IV':'4'}
FIELDS = ('period','source_variant','deficit_motivation_original_value',
          'long_run_growth_motivation_original_value','exogenous_combined_original_value',
          'source_sheet','source_excel_row','deficit_source_cell','growth_source_cell',
          'exogenous_source_cell','source_unit','source_workbook_sha256')


def require_empty(folder: Path) -> None:
    if folder.exists() and any(folder.iterdir()):
        raise FileExistsError('Output folder must be empty: '+str(folder))
    folder.mkdir(parents=True,exist_ok=True)


def source_quarters(labels: dict[str,dict]) -> list[str]:
    q=[]
    for r in range(13,265):
        label=labels.get(f'A{r}',{}).get('text','')
        m=re.fullmatch(r'(19\d{2}|20\d{2})-(I|II|III|IV)',label)
        if not m:
            raise ValueError(f'Invalid or missing original worksheet date in A{r}: {label!r}')
        q.append(f'{m.group(1)}Q{ROMANS[m.group(2)]}')
    if q!=EXPECTED:raise ValueError('Quarterly dates are missing, duplicated or shifted')
    return q


def source_numeric_cells(workbook: bytes, wanted: set[str]) -> dict[str, float]:
    """Direct OOXML numeric source values; no formula evaluations or missing-to-zero."""
    with zipfile.ZipFile(io.BytesIO(workbook)) as archive:
        names=set(archive.namelist())
        root=ET.fromstring(archive.read('xl/workbook.xml'))
        rel=ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        ids={x.get('Id'):x.get('Target') for x in rel.findall('{%s}Relationship'%PKG)}
        sheetpath=None
        for sh in root.findall('m:sheets/m:sheet',NS):
            if sh.get('name')=='Tax Measures':
                t=ids.get(sh.get('{%s}id'%RELS),'')
                sheetpath=t.lstrip('/') if t.startswith('/') else 'xl/'+t.lstrip('/')
        if not sheetpath or '..' in sheetpath or sheetpath not in names or not sheetpath.startswith('xl/worksheets/'):
            raise ValueError('Missing or unsafe Tax Measures worksheet')
        xml=archive.read(sheetpath)
        if len(xml)>7_000_000:raise ValueError('Unexpected source worksheet size')
        tree=ET.fromstring(xml)
        out={}
        for cell in tree.findall('.//m:sheetData/m:row/m:c',NS):
            ref=cell.get('r','')
            if ref not in wanted:continue
            if ref in out:raise ValueError('Duplicate source cell '+ref)
            if cell.find('m:f',NS) is not None:raise ValueError('Formula in source cell '+ref)
            if cell.get('t','') in ('e','s','str','inlineStr','b'):
                raise ValueError('Non-numeric original source cell '+ref)
            v=cell.find('m:v',NS)
            if v is None or not (v.text or '').strip():raise ValueError('Missing source value '+ref)
            try:value=float(v.text.strip())
            except ValueError as e:raise ValueError('Invalid source number '+ref) from e
            if not math.isfinite(value):raise ValueError('Nonfinite source number '+ref)
            out[ref]=value
        missing=wanted-out.keys()
        if missing:raise ValueError(f'Missing {len(missing)} required source numeric cells, e.g. {sorted(missing)[:5]}')
        return out


def assemble(workbook: bytes, *, enforce_source_hash=True):
    if enforce_source_hash and hashlib.sha256(workbook).hexdigest()!=WORKBOOK_SHA256:
        raise ValueError('Source workbook fingerprint differs from verified vintage')
    labels,dimension=workbook_cells(workbook)
    if dimension!='A1:AT264':raise ValueError('Source worksheet dimensions changed')
    quarters=source_quarters(labels)
    wanted={f'{b[k]}{r}' for b in BLOCKS for k in ('deficit','growth','exogenous') for r in range(13,265)}
    numbers=source_numeric_cells(workbook,wanted)
    panels={};metadata={}
    for b in BLOCKS:
        for k,expected_label in zip(('deficit','growth','exogenous'),b['labels']):
            actual=labels.get(f'{b[k]}12',{}).get('text','')
            if actual!=expected_label:raise ValueError(f'Original variable label mismatch in {b[k]}12')
        values={k:[numbers[f'{b[k]}{r}'] for r in range(13,265)] for k in ('deficit','growth','exogenous')}
        err=max(abs(e-d-g) for e,d,g in zip(values['exogenous'],values['deficit'],values['growth']))
        if err>0.001:raise ValueError(f'Exogenous components do not sum for {b["name"]}')
        nonzero=sum(x!=0 for x in values['exogenous'])
        if nonzero!=b['expected_nonzero']:raise ValueError(f'Exogenous source nonzero count changed for {b["name"]}')
        sample=values['exogenous'][8:]
        mean,sd=statistics.mean(sample),statistics.pstdev(sample)
        if b['name']=='nonretroactive':
            if not(abs(mean+0.03)<0.02 and abs(sd-0.24)<0.065):
                raise ValueError('Baseline statistics no longer consistent with verified source audit')
            if values['exogenous'][EXPECTED.index('1948Q2')]>=0:
                raise ValueError('Original negative 1948Q2 tax change not observed')
        rows=[]
        for i,period in enumerate(quarters):
            r=i+13
            rows.append({'period':period,'source_variant':b['name'],
                'deficit_motivation_original_value':format(values['deficit'][i],'.17g'),
                'long_run_growth_motivation_original_value':format(values['growth'][i],'.17g'),
                'exogenous_combined_original_value':format(values['exogenous'][i],'.17g'),
                'source_sheet':'Tax Measures','source_excel_row':r,
                'deficit_source_cell':f'{b["deficit"]}{r}',
                'growth_source_cell':f'{b["growth"]}{r}',
                'exogenous_source_cell':f'{b["exogenous"]}{r}',
                'source_unit':'as_stored_ratio_to_nominal_GDP; paper appears to report percentage points; conversion_not_performed',
                'source_workbook_sha256':WORKBOOK_SHA256})
        panels[b['name']]=rows
        metadata[b['name']]={'quarterly_rows':len(rows),'period_start':quarters[0],
            'period_end':quarters[-1],'exogenous_source_column':b['exogenous'],
            'exogenous_source_variable':b['labels'][2],
            'nonzero_exogenous_quarters':nonzero,'zero_exogenous_quarters':252-nonzero,
            'max_component_identity_residual':round(err,9),
            'mean_1947_2007_as_stored':round(mean,9),
            'population_stddev_1947_2007_as_stored':round(sd,9)}
    return panels,metadata


def export(*,private: Path,public: Path,source_zip: bytes):
    require_empty(private);require_empty(public)
    if hashlib.sha256(source_zip).hexdigest()!=SOURCE_SHA256:
        raise ValueError('Original author ZIP hash changed; stop for review')
    with zipfile.ZipFile(io.BytesIO(source_zip)) as archive:
        workbook=archive.read(WORKBOOK)
    panels,metadata=assemble(workbook)
    manifest={}
    for name,rows in panels.items():
        file=private/f'romer_tax_{name}_quarterly.csv'
        with file.open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=FIELDS)
            writer.writeheader();writer.writerows(rows)
        manifest[file.name]={'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),
                             'bytes':file.stat().st_size,'rows':len(rows)}
    private_meta={'study_id':'romer_romer_2010','archive_sha256':SOURCE_SHA256,
        'workbook_sha256':WORKBOOK_SHA256,'created_utc':datetime.now(timezone.utc).isoformat(),
        'files':manifest,'source_measurements':sum(x['rows']*3 for x in manifest.values()),
        'source_narrative_classification_only':True,'independent_causal_replication_ready':False,
        'redistribution_permission_verified':False}
    (private/'private_file_manifest.json').write_text(json.dumps(private_meta,indent=2)+'\n')
    report={'status':'PINNED_ORIGINAL_NUMERIC_SERIES_EXTRACTED_PRIVATELY_NOT_CAUSALLY_VALIDATED',
        'source_archive_sha256':SOURCE_SHA256,'source_workbook_sha256':WORKBOOK_SHA256,
        'original_source_url':'https://eml.berkeley.edu/~dromer/papers/DataSet.zip',
        'paper_doi':'10.1257/aer.100.3.763',
        'source_period':'1945Q1-2007Q4','variants':metadata,
        'private_quarterly_rows':sum(len(x) for x in panels.values()),
        'private_component_measurements':sum(len(x)*3 for x in panels.values()),
        'private_file_manifest_sha256':hashlib.sha256((private/'private_file_manifest.json').read_bytes()).hexdigest(),
        'numeric_source_data_in_public_artifact':False,
        'public_source_data_license_verification_complete':False,
        'cross_provider_merged':False,'causal_inference_ready':False,
        'ai_economy_coverage':False,
        'remaining_gates':['Review original source license before any public redistribution',
         'Independently reproduce source narrative selection and timing using paper and codebook',
         'Match historical US macroeconomic outcomes at quarterly frequency',
         'Specify identification, anticipation windows, inference and robustness']}
    (public/'romer_private_extraction_audit.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'status':report['status'],
        'private_quarterly_rows':report['private_quarterly_rows'],
        'private_component_measurements':report['private_component_measurements'],
        'public_contains_numeric_source':False,'independent_causal_replication_ready':False},indent=2))
    return report


def self_test():
    # Dates are taken from literal original source labels; never inferred from row number.
    labels={f'A{i+13}':{'text':f'{p[:4]}-{["I","II","III","IV"][int(p[-1])-1]}'} for i,p in enumerate(EXPECTED)}
    assert source_quarters(labels)==EXPECTED
    altered={k:dict(v) for k,v in labels.items()};altered['A13']['text']='1945-II'
    try:source_quarters(altered)
    except ValueError:pass
    else:raise AssertionError('Shifted periods must fail')
    with tempfile.TemporaryDirectory() as tmp:
        folder=Path(tmp)/'existing'
        folder.mkdir();(folder/'flag').write_text('x')
        try:require_empty(folder)
        except FileExistsError:pass
        else:raise AssertionError('Nonempty output should fail')
    assert len(EXPECTED)==252 and sum(b['expected_nonzero'] for b in BLOCKS)==127
    print('PASS: source period shift, duplicate destination and expected layout guards')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--self-test',action='store_true')
    ap.add_argument('--private-out',default='private_romer_output')
    ap.add_argument('--public-out',default='public_romer_audit')
    ap.add_argument('--local-zip',default=None,help='Optional local source archive for offline validation; checksum still enforced')
    args=ap.parse_args()
    if args.self_test:self_test();return
    data=Path(args.local_zip).read_bytes() if args.local_zip else retrieve()
    export(private=Path(args.private_out),public=Path(args.public_out),source_zip=data)

if __name__=='__main__':main()
