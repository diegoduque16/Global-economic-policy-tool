"""Evidence-gated Romer & Romer 2010 source variable/quarter mapping audit.

Downloads the same author archive already fingerprinted by the source audit. Writes
ONLY metadata, original variable *names*, dates if stored as string labels,
counts of populated numeric cells, and quality flags. Never publishes numeric tax
shock values or declares them eligible for causal estimation. No silent mapping.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

from policy_shocks.audit_sources import audit_zip, retrieve
from policy_shocks.inspect_romer import SOURCE_SHA256, WORKBOOK, WORKBOOK_SHA256

MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
RELS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'
NS = {'m': MAIN}

# Documented workbook layout, observed in successful Oct 25 2026 schema audit.
BLOCKS = [
    {'variant': 'nonretroactive_liability_change', 'start': 'B', 'description_col': 'B',
     'raw_label_col': 'E', 'ratio_label_col': 'G', 'definition_col': 'I',
     'header_col': 'B', 'header_phrase': 'excluding retroactive'},
    {'variant': 'retroactive_liability_change', 'start': 'P', 'description_col': 'P',
     'raw_label_col': 'S', 'ratio_label_col': 'U', 'definition_col': 'X',
     'header_col': 'P', 'header_phrase': 'including retroactive'},
    {'variant': 'present_value_change', 'start': 'AD', 'description_col': 'AD',
     'raw_label_col': 'AG', 'ratio_label_col': 'AI', 'definition_col': 'AK',
     'header_col': 'AD', 'header_phrase': 'present value'},
]
CATEGORIES = {
    4: ('spending_driven','endogenous_component'),
    5: ('countercyclical','endogenous_component'),
    6: ('deficit_driven','exogenous_component_as_classified_by_authors'),
    7: ('long_run_growth','exogenous_component_as_classified_by_authors'),
    8: ('endogenous_combined','endogenous'),
    9: ('exogenous_combined','narrative_exogenous_candidate'),
    10: ('all_legislated','not_exogenous'),
}
MAX_BLOB=3_000_000
MAX_CELLS=40_000


def _colnum(label: str) -> int:
    n=0
    for c in label:
        if not 'A'<=c<='Z': raise ValueError('Invalid Excel column')
        n=n*26+ord(c)-64
    return n


def _index_parts(ref: str):
    m=re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]{0,4})',ref)
    if not m:raise ValueError('Unrecognized cell reference')
    return _colnum(m[1]),int(m[2])


def _text(cell: ET.Element, shared: list[str]) -> str:
    k=cell.get('t',''); v=cell.find('m:v', NS)
    if k=='s':
        if v is None or v.text is None: return ''
        i=int(v.text)
        if i<0 or i>=len(shared):raise ValueError('Invalid shared-string index')
        return shared[i]
    if k in ('inlineStr','str'):
        text=''.join(t.text or '' for t in cell.findall('.//m:t',NS))
        return text or (v.text or '' if v is not None else '')
    return ''


def workbook_cells(workbook: bytes, target_sheet='Tax Measures'):
    """Parse OOXML without external dependencies, no source values leave memory."""
    if len(workbook)>MAX_BLOB:raise ValueError('Unexpectedly large original workbook')
    with zipfile.ZipFile(io.BytesIO(workbook)) as z:
        names=set(z.namelist())
        for required in ('xl/workbook.xml','xl/_rels/workbook.xml.rels'):
            if required not in names: raise ValueError('Missing required workbook metadata')
        strings=[]
        if 'xl/sharedStrings.xml' in names:
            root=ET.fromstring(z.read('xl/sharedStrings.xml'))
            strings=[''.join(t.text or '' for t in v.findall('.//m:t',NS)) for v in root.findall('m:si',NS)]
            if len(strings)>50_000:raise ValueError('Unreasonable shared string count')
        root=ET.fromstring(z.read('xl/workbook.xml'))
        links=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        rel={e.get('Id'):e.get('Target') for e in links.findall(f'{{{PKG}}}Relationship')}
        target=None
        for sheet in root.findall('m:sheets/m:sheet',NS):
            if sheet.get('name') == target_sheet:
                rid=sheet.get(f'{{{RELS}}}id'); path=rel.get(rid,'')
                target=path.lstrip('/') if path.startswith('/') else posixpath.normpath(posixpath.join('xl',path))
        if not target or not target.startswith('xl/worksheets/') or target not in names:
            raise ValueError(f'Missing or unsafe expected worksheet {target_sheet}')
        sheet=ET.fromstring(z.read(target))
        values={}; type_count=0
        for cell in sheet.findall('.//m:sheetData/m:row/m:c',NS):
            type_count+=1
            if type_count>MAX_CELLS:raise ValueError('Unexpected workbook cell count')
            ref=cell.get('r','')
            col,row=_index_parts(ref)
            if col>100 or row>2000:raise ValueError('Unexpected workbook dimensions')
            text=_text(cell, strings).strip()
            formula=cell.find('m:f',NS) is not None
            v=cell.find('m:v',NS)
            # Numeric state only: never return original numeric cell values.
            numeric=cell.get('t','') not in ('s','str','inlineStr','e','b') and bool(v is not None and v.text and v.text.strip())
            if text or numeric or formula:
                values[ref]={'col':col,'row':row,'text':text[:320], 'numeric':numeric,'formula':formula,
                             'type':cell.get('t','n')}
        dimension=sheet.find('m:dimension',NS)
        return values,dimension.get('ref') if dimension is not None else ''


def audit_mapping_from_workbook(workbook: bytes, *, source_checksum: str, allow_synthetic=False):
    """List candidate variable labels, counts and unresolved timing; don't export data."""
    if not allow_synthetic and hashlib.sha256(workbook).hexdigest()!=WORKBOOK_SHA256:
        raise ValueError('Workbook SHA changed; manual source review required')
    cells, dimension=workbook_cells(workbook)
    if not allow_synthetic and dimension!='A1:AT264':
        raise ValueError(f'Unexpected worksheet dimensions: {dimension}')
    results=[]
    errors=[]
    for block in BLOCKS:
        header=cells.get(block['header_col']+'1',{}).get('text','')
        if block['header_phrase'] not in header.lower():
            errors.append(f"Header mismatch for {block['variant']}: {header[:80]}")
        for row,(category,identification) in CATEGORIES.items():
            description=cells.get(block['description_col']+str(row),{}).get('text','')
            if not description:
                errors.append(f'Missing category label {block["variant"]} row {row}')
            if row == 9 and 'exogenous' not in description.lower():
                errors.append(f'Exogenous row mislabeled for {block["variant"]}')
            if row == 8 and 'endogenous' not in description.lower():
                errors.append(f'Endogenous row mislabeled for {block["variant"]}')
            labels={kind: {'source_cell':block[colkey]+str(row),
                            'label':cells.get(block[colkey]+str(row),{}).get('text','')}
                    for kind,colkey in [('raw_data','raw_label_col'),('ratio_to_nominal_gdp','ratio_label_col'),('description','definition_col')]}
            # Row 9 label is the candidate semantic definition, NOT the numeric time-series position.
            results.append({'variant':block['variant'],'category':category,'identification':identification,
                            'description':description,'source_cell':block['description_col']+str(row),
                            'series_labels':labels,'status':'mapping_and_timing_review_required'})
    if errors:raise ValueError('; '.join(errors))
    # Counts include numeric non-time-series metadata; deliberately no values are extracted.
    cols=defaultdict(lambda:{'numeric_rows':[],'formula_rows':[],'text_rows':[]})
    for ref,rec in cells.items():
        m=re.match(r'([A-Z]+)',ref)
        label=m.group(1)
        if rec['numeric']:cols[label]['numeric_rows'].append(rec['row'])
        if rec['formula']:cols[label]['formula_rows'].append(rec['row'])
        if rec['text']:cols[label]['text_rows'].append(rec['row'])
    summary={}
    for col in sorted(cols,key=_colnum):
        item=cols[col]
        numeric=[r for r in item['numeric_rows'] if r>=11]
        summary[col]={'numeric_cells_row11_onward':len(numeric),
                      'first_numeric_row':min(numeric) if numeric else None,
                      'last_numeric_row':max(numeric) if numeric else None,
                      'formula_cells':len(item['formula_rows']),
                      'text_cells':len(item['text_rows'])}
    # Source labels only, never raw numeric values or embedded workbook data.
    header_text=[{'cell':ref,'text':rec['text']} for ref,rec in cells.items()
                 if rec['row']<=12 and rec['text']]
    header_text.sort(key=lambda p:(_index_parts(p['cell'])[1],_index_parts(p['cell'])[0]))
    # Any non-numeric period strings can help identify the date anchor, but only by cell reference.
    date_markers=[{'cell':ref,'text':rec['text'][:45]} for ref,rec in cells.items()
                  if 11<=rec['row']<=22 and rec['text'] and re.search(r'(?:19\d\d|20\d\d|[Qq][1-4])',rec['text'])]
    date_markers.sort(key=lambda p:(_index_parts(p['cell'])[1],_index_parts(p['cell'])[0]))
    return {'schema_version':2,'source_id':'romer_romer_2010',
            'source_archive_sha256':source_checksum,
            'workbook_sha256':hashlib.sha256(workbook).hexdigest(),
            'sheet':'Tax Measures','reported_dimension':dimension,
            'definition_layout':results,'header_text_cells':header_text[:220],
            'numeric_column_occupancy':summary,
            'potential_date_text_markers':date_markers[:35],
            'imported_numeric_shocks':0,
            'source_numeric_values_redistributed':False,
            'mapping_certified':False,
            'quarterly_alignment_certified':False,
            'cautions':['The three variants must never be spliced or pooled as identical treatments.',
                        'The authors’ narrative exogeneity labels depend on their identification assumptions.',
                        'The workbook row 9 labels are not proof of which cells hold quarterly observations.',
                        'Original tax-policy shocks are measured relative to nominal GDP, but the stored ratio scale is not yet verified.',
                        'No causal effects, attribution to political parties, or AI impact is computed.']}


def audit_original(source_zip:bytes):
    audited=audit_zip(source_zip)
    if hashlib.sha256(source_zip).hexdigest()!=SOURCE_SHA256:
        raise ValueError('Original source archive SHA does not match previously reviewed fingerprint')
    with zipfile.ZipFile(io.BytesIO(source_zip)) as z:
        if WORKBOOK not in z.namelist():raise ValueError('Missing expected workbook')
        workbook=z.read(WORKBOOK)
    return audit_mapping_from_workbook(workbook,source_checksum=audited['source_archive_sha256'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',default='romer_mapping_output')
    args=p.parse_args()
    source=retrieve()
    review=audit_original(source)
    review['created_utc']=datetime.now(timezone.utc).isoformat()
    out=Path(args.out)
    out.mkdir(parents=True,exist_ok=True)
    if any(out.iterdir()):raise FileExistsError('Output directory must be empty')
    (out/'romer_variable_mapping_review.json').write_text(json.dumps(review,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'source_sha_verified':True,
                     'definition_rows':len(review['definition_layout']),
                     'candidate_exogenous_definitions':sum(i['category']=='exogenous_combined' for i in review['definition_layout']),
                     'mapped_numeric_shocks':0,
                     'review_needed':True,
                     'output':str(out/'romer_variable_mapping_review.json')},indent=2))

if __name__=='__main__':main()
