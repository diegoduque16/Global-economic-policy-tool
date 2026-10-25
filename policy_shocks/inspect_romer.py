"""Inspect the Romer & Romer (2010) author archive for documentation and Excel schema.

Never exports original source data, tax-shock values, or labels observations exogenous.
Reports sheet metadata, text labels with source cell coordinates, source fingerprints,
and short excerpts from the authors' README for manual codebook review.
Standard library except optional pypdf for README extraction.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
import posixpath
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from policy_shocks.audit_sources import retrieve, audit_zip

SOURCE_SHA256 = 'c6c3b4888f3de596abf56bc136e2175ec5a4aa0dab137e79e261984a0a84701c'
WORKBOOK = 'DataSet/Romer&RomerFiscalData.xlsx'
WORKBOOK_SHA256 = 'e0803df2b6a541041ef80c9dee050fc65bd75ea5ac57926f67483d1ee94dadd3'
README = 'DataSet/ReadMe.pdf'
README_SHA256 = 'ce9fba22a5c5ff1bb0b0675f65154f8c5e45592277b73814157d9fdc285fa1b7'
MAIN_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG_REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
NS = {'m': MAIN_NS}
TERMS = re.compile(r'\b(?:exogen(?:ous)?|endogen(?:ous)?|tax(?:es|ation)?|shock(?:s)?|quarter(?:ly)?|year|revenue|deficit|long[ -]?run|anticipat(?:ed|ion)|narrative|discretionary|liabilit(?:y|ies))\b', re.I)
MAX_CANDIDATES_PER_SHEET = 35
MAX_PREVIEW_PER_SHEET = 18
MAX_TEXT_CELLS = 180000


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decoded_label(text: str, length=115) -> str:
    return re.sub(r'\s+', ' ', text).strip()[:length]


def sheet_structure(body: bytes) -> dict:
    """Parse Excel XML metadata, never expose numeric cell values."""
    with zipfile.ZipFile(io.BytesIO(body)) as book:
        names = set(book.namelist())
        if 'xl/workbook.xml' not in names or 'xl/_rels/workbook.xml.rels' not in names:
            raise ValueError('Not a supported OOXML .xlsx workbook')
        shared = []
        if 'xl/sharedStrings.xml' in names:
            root = ET.fromstring(book.read('xl/sharedStrings.xml'))
            shared = [''.join(t.text or '' for t in si.findall('.//m:t', NS)) for si in root.findall('m:si', NS)]
            if len(shared) > 500000:
                raise ValueError('Unexpectedly large shared-string table')
        wbroot = ET.fromstring(book.read('xl/workbook.xml'))
        relroot = ET.fromstring(book.read('xl/_rels/workbook.xml.rels'))
        rels = {e.attrib['Id']:e.attrib['Target'] for e in relroot.findall(f'{{{PKG_REL_NS}}}Relationship')}
        sheets=[]
        for item in wbroot.findall('m:sheets/m:sheet', NS):
            rid = item.attrib.get(f'{{{REL_NS}}}id')
            target = rels.get(rid, '')
            target = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl',target))
            if not target.startswith('xl/worksheets/') or target not in names:
                raise ValueError('Unsafe or missing worksheet path')
            tree = ET.fromstring(book.read(target))
            candidates=[];preview=[];numeric_count=0;formulas=0;strings=0
            for cell in tree.findall('.//m:sheetData/m:row/m:c', NS):
                if numeric_count + strings > MAX_TEXT_CELLS:
                    raise ValueError('Workbook exceeds safe inspection cell cap')
                pos = cell.attrib.get('r','?')
                kind = cell.attrib.get('t','')
                if cell.find('m:f', NS) is not None:
                    formulas+=1
                value = cell.find('m:v',NS)
                t=''
                if kind == 's' and value is not None:
                    idx=int(value.text or '-1')
                    if 0<=idx<len(shared):t=shared[idx]
                    else:raise ValueError('Shared string index out of range')
                elif kind in ('inlineStr','str'):
                    t=''.join(x.text or '' for x in cell.findall('.//m:t',NS))
                    if not t and value is not None:t=value.text or ''
                elif kind=='e':
                    continue
                else:
                    if value is not None and value.text is not None:
                        numeric_count+=1
                if t.strip():
                    strings+=1
                    label=decoded_label(t)
                    if len(preview)<MAX_PREVIEW_PER_SHEET and re.search(r'\d+',pos) and int(re.search(r'\d+',pos).group())<=35:
                        preview.append({'cell':pos,'text':label})
                    if TERMS.search(t) and len(candidates)<MAX_CANDIDATES_PER_SHEET:
                        candidates.append({'cell':pos,'label':label})
            dim=tree.find('m:dimension',NS)
            sheets.append({'sheet':item.attrib.get('name'), 'sheet_xml_path':target,
                           'reported_dimension':dim.attrib.get('ref') if dim is not None else None,
                           'text_cells':strings,'numeric_cells':numeric_count,'formula_cells':formulas,
                           'top_text_labels':preview,'keyword_label_candidates':candidates,
                           'candidate_limit_reached':len(candidates)==MAX_CANDIDATES_PER_SHEET})
        names_meta=[]
        for node in wbroot.findall('m:definedNames/m:definedName',NS):
            name=node.attrib.get('name','')
            if name and not name.startswith('_xlnm'):
                names_meta.append({'defined_name':decoded_label(name,100),'reference':decoded_label(node.text or '',120)})
        return {'worksheets':sheets,'defined_names':names_meta[:80],
                'worksheet_count':len(sheets), 'only_schema_and_text_labels_exported':True,
                'numeric_values_exported':False}


def rats_identifiers(z: zipfile.ZipFile):
    """RATS program token inventory is a navigational clue, not variable verification."""
    out=[]
    for name in z.namelist():
        if not name.upper().endswith('.RAT'):
            continue
        data=z.read(name)
        if len(data)>250000: raise ValueError('RATS program unexpectedly large')
        content=data.decode('latin-1',errors='replace').upper()
        tokens=Counter(re.findall(r'\b[A-Z][A-Z0-9_]{3,}\b',content))
        selected={k:n for k,n in tokens.items() if re.search('EXOG|ENDOG|TAX|GDP|DEFICIT|SHOCK|PDV|REVENUE',k)}
        if selected:
            out.append({'member':name, 'possible_variable_tokens':dict(sorted(selected.items(),key=lambda x:(-x[1],x[0]))[:22])})
    return out


def readme_clues(data: bytes):
    """Only brief matched line fragments; not a copy of the copyrighted README."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return {'status':'pypdf_not_installed','notes':'No PDF text was extracted; manual review required.'}
    try:
        reader=PdfReader(io.BytesIO(data))
        hits=[]
        for page_no, page in enumerate(reader.pages,1):
            tx=page.extract_text() or ''
            for line in tx.splitlines():
                line=decoded_label(line,105)
                if TERMS.search(line) and len(hits)<10:
                    hits.append({'page':page_no, 'short_excerpt':line})
        return {'status':'short_clues_only','page_count':len(reader.pages),'keyword_line_clues':hits,
                'full_readme_not_redistributed':True}
    except Exception as exc:
        return {'status':'requires_manual_pdf_review','error_type':type(exc).__name__}


def inspect(payload: bytes, *, enforce_hash=True):
    audit=audit_zip(payload)
    if enforce_hash and sha(payload)!=SOURCE_SHA256:
        raise ValueError('Original research archive changed from audited reference; review manually before continuing')
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        for name, expected in [(WORKBOOK,WORKBOOK_SHA256),(README,README_SHA256)]:
            if name not in z.namelist():raise ValueError(f'Missing expected source member {name}')
            if enforce_hash and sha(z.read(name))!=expected:raise ValueError(f'Source member changed: {name}')
        workbook_blob=z.read(WORKBOOK)
        structure=sheet_structure(workbook_blob)
        readme=readme_clues(z.read(README))
        rats=rats_identifiers(z)
    meta={'source_id': 'romer_romer_2010','source_archive_sha256':audit['source_archive_sha256'],
          'workbook_sha256':sha(workbook_blob),
          'archive_members_audited':audit['member_count'],'created_utc':datetime.now(timezone.utc).isoformat(),
          'imported_numeric_shocks':0,'source_data_redistributed':False,
          'workbook_structure':structure,'readme_document_clues':readme,'rats_program_identifier_clues':rats,
          'review_required':['Confirm workbook sheet and source column for exogenous tax changes using original ReadMe and paper.',
                             'Verify shock units, denominator, sign convention, data frequency, exact timing and exogeneity motivation.',
                             'Distinguish identified narrative shocks from endogenous tax changes and from controls.',
                             'Check announcement-versus-implementation timing and any revisions before causal modeling.']}
    return meta


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',default='romer_schema_output')
    p.add_argument('--local-zip',help='Optional offline fixture (no network)')
    p.add_argument('--fixture-mode',action='store_true',help='Disable known SHA pin for unit tests only')
    a=p.parse_args()
    data=Path(a.local_zip).read_bytes() if a.local_zip else retrieve()
    result=inspect(data,enforce_hash=not a.fixture_mode)
    path=Path(a.out);path.mkdir(parents=True,exist_ok=True)
    dest=path/'romer_workbook_schema_review.json'
    if dest.exists():raise FileExistsError(dest)
    dest.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    sheets=[{'sheet':s['sheet'],'dimension':s['reported_dimension'],
             'possible_labels':len(s['keyword_label_candidates'])} for s in result['workbook_structure']['worksheets']]
    print(json.dumps({'archive_matches_prior_audit':not a.fixture_mode,
                      'sheets':sheets,'readme':result['readme_document_clues']['status'],
                      'rats_programs_with_relevant_identifiers':len(result['rats_program_identifier_clues']),
                      'imported_numeric_shocks':0,'report':str(dest)},indent=2))

if __name__=='__main__':main()
