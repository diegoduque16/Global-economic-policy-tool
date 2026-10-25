"""Offline schema inspection tests; synthetic workbooks only, no source data included."""
import io
import json
import tempfile
import unittest
import zipfile
from policy_shocks import inspect_romer as m

WORKBOOK='''<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="CandidateTaxData" sheetId="1" r:id="rId1"/></sheets></workbook>'''
RELS='''<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'''
SHEET='''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:C4"/><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row><row r="2"><c r="A2"><v>1950</v></c><c r="B2"><v>0.25</v></c><c r="C2"><v>7</v></c></row><row r="3"><c r="A3" t="inlineStr"><is><t>Review exogenous classification</t></is></c></row></sheetData></worksheet>'''
SHARED='''<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Year</t></si><si><t>Exogenous tax shock</t></si><si><t>GDP</t></si></sst>'''

def workbook(sheet=SHEET,relation=RELS):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        z.writestr('xl/workbook.xml',WORKBOOK)
        z.writestr('xl/_rels/workbook.xml.rels',relation)
        z.writestr('xl/worksheets/sheet1.xml',sheet)
        z.writestr('xl/sharedStrings.xml',SHARED)
    return b.getvalue()


def source_zip(book=None):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        z.writestr(m.WORKBOOK,book or workbook())
        z.writestr(m.README,b'%PDF-1.4 fake fixture not a real PDF')
        z.writestr('DataSet/RATS Programs and Databanks/EXOGER.RAT',b'OPEN TAXNR.DED\nEXOGER TAXNR VARIOUS')
    return b.getvalue()

class InspectTests(unittest.TestCase):
    def test_keyword_labels_located_without_values(self):
        s=m.sheet_structure(workbook())
        self.assertEqual(s['worksheet_count'],1)
        self.assertEqual(s['worksheets'][0]['reported_dimension'],'A1:C4')
        self.assertIn('B1',[x['cell'] for x in s['worksheets'][0]['keyword_label_candidates']])
        serialized=json.dumps(s)
        self.assertNotIn('0.25',serialized)
    def test_numeric_source_values_not_exported(self):
        s=m.sheet_structure(workbook())
        self.assertEqual(s['worksheets'][0]['numeric_cells'],3)
        self.assertFalse(s['numeric_values_exported'])
    def test_bad_excel_relationship_rejected(self):
        bad=RELS.replace('worksheets/sheet1.xml','../../danger.xml')
        with self.assertRaises(ValueError):m.sheet_structure(workbook(relation=bad))
    def test_invalid_shared_string_rejected(self):
        with self.assertRaises(ValueError):m.sheet_structure(workbook(sheet=SHEET.replace('<v>2</v>','<v>77</v>')))
    def test_original_archive_hash_pin(self):
        with self.assertRaisesRegex(ValueError,'changed'):
            m.inspect(source_zip(),enforce_hash=True)
    def test_fixture_report_is_uncertified(self):
        out=m.inspect(source_zip(),enforce_hash=False)
        self.assertEqual(out['imported_numeric_shocks'],0)
        self.assertFalse(out['source_data_redistributed'])
        self.assertEqual(out['workbook_structure']['worksheet_count'],1)
        self.assertEqual(len(out['rats_program_identifier_clues']),1)
    def test_fingerprint_strings(self):
        self.assertEqual(len(m.SOURCE_SHA256),64)
        self.assertEqual(len(m.WORKBOOK_SHA256),64)
    def test_non_excel_rejected(self):
        with self.assertRaises(zipfile.BadZipFile):m.sheet_structure(b'bad')

if __name__=='__main__':unittest.main()
