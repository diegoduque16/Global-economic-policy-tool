"""Offline schema, annual series, and missingness tests for Census historical STC."""
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from open_data.us_state_tax import parse, collect, xlsx_rows, parse_number

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'

def sample_book(duplicates=False,bad_schema=False):
    headers=['Year','State','Name','FY Ending Date','C105','T01','T09','T40','T41']
    if bad_schema:headers[4]='WRONG'
    headers+=['T'+str(x).zfill(2) for x in range(10,30)]
    labels=['','','','','Total Taxes','Property tax','Sales tax','Individual income tax','Corporate income tax']+[f'Tax {x}' for x in range(10,30)]
    rows=[headers,labels]
    for y in (2023,2024,2025):
        rows.append([str(y),'0','US STATE GOVTS','N/A','99999','100','200','300','400']+['90']*20)
        for i,name in enumerate(('CA','CT','FL','NY','TX')):
            vals=[str(y),str(i+1),name+' STATE GOVT','63025',str(1000+i),'0','X',str(100+i),str(20+i)]+[str(i+x) for x in range(20)]
            rows.append(vals)
    if duplicates:
        rows.append(['2025','1','CA STATE GOVT','63025','1','2','3','4','5']+['6']*20)
    def xml_cell(col,val,ri):
        label=''
        n=col+1
        while n: n,rem=divmod(n-1,26);label=chr(65+rem)+label
        return f'<c r="{label}{ri}" t="inlineStr"><is><t>{val}</t></is></c>'
    xml=f'<worksheet xmlns="{NS}"><sheetData>'+''.join(
        f'<row r="{i}">'+''.join(xml_cell(j,v,i) for j,v in enumerate(row))+'</row>'
        for i,row in enumerate(rows,1))+'</sheetData></worksheet>'
    workbook=f'<workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="STC_Historical_DB (2025)" sheetId="1" r:id="rId1"/></sheets></workbook>'
    rel='<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
    payload=io.BytesIO()
    with zipfile.ZipFile(payload,'w') as z:
        z.writestr('xl/workbook.xml',workbook)
        z.writestr('xl/_rels/workbook.xml.rels',rel)
        z.writestr('xl/worksheets/sheet1.xml',xml)
    return payload.getvalue()

class TaxTests(unittest.TestCase):
    def test_row_stream(self):
        rows=list(xlsx_rows(sample_book()))
        self.assertEqual(rows[0]['A'],'Year')
        self.assertEqual(rows[2]['C'],'US STATE GOVTS')

    def test_missing_distinct_from_explicit_zero(self):
        self.assertIsNone(parse_number('X'))
        self.assertEqual(parse_number('0'),0)
        self.assertIsNone(parse_number('(NA)'))
        with self.assertRaises(ValueError):parse_number('unknown')

    def test_state_only_and_tax_code_preservation(self):
        rows,stats=parse(sample_book(),states={'CA','CT','FL','NY','TX'},start=2023,end=2025)
        self.assertEqual(stats['state_count'],5)
        self.assertEqual(stats['year_count'],3)
        self.assertEqual(stats['states_2025'],5)
        self.assertGreaterEqual(stats['explicit_zero_cells'],15)
        self.assertGreaterEqual(stats['source_missing_cells']['X'],15)
        self.assertTrue(all(json.loads(x['disaggregation'])['state_abbr'] in {'CA','CT','FL','NY','TX'} for x in rows))
        self.assertTrue(any(x['series']=='CENSUS.STC.C105' for x in rows))
        self.assertFalse(any(x['value'] is None for x in rows))

    def test_duplicate_state_raises_after_schema_validated(self):
        with self.assertRaisesRegex(ValueError,'Duplicate state-year-tax code'):
            parse(sample_book(duplicates=True))

    def test_five_state_pilot_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            rc=collect(mode='pilot',out=d,get=sample_book)
            self.assertEqual(rc,0)
            report=json.loads((Path(d)/'run_report.json').read_text())
            self.assertEqual(report['state_count'],5)
            file=Path(d)/'processed'/'us_state_tax_panel.csv'
            with file.open() as f:
                self.assertEqual(sum(1 for x in csv.DictReader(f)),report['observations'])

    def test_source_validation_stops_bad_codebook(self):
        with tempfile.TemporaryDirectory() as d:
            rc=collect(mode='pilot',out=d,get=lambda:sample_book(bad_schema=True))
            self.assertEqual(rc,1)
            report=json.loads((Path(d)/'run_report.json').read_text())
            self.assertEqual(report['status'],'VALIDATION_FAILED')

if __name__=='__main__':unittest.main()
