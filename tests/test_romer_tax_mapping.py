"""Offline unit tests of metadata-only tax definition mapping (synthetic workbook)."""
import io
import json
import unittest
import zipfile
from policy_shocks import romer_tax_mapping as m


def fake_book(*,broken_header=False,broken_row=False,contains_numeric=True):
    main=m.MAIN
    workbook=f'''<?xml version="1.0"?><workbook xmlns="{main}" xmlns:r="{m.RELS}"><sheets><sheet name="Tax Measures" sheetId="1" r:id="rId1"/></sheets></workbook>'''
    rels=f'''<?xml version="1.0"?><Relationships xmlns="{m.PKG}"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>'''
    layout=[('B','excluding retroactive'),('P','including retroactive'),('AD','present value')]
    strings=[]
    for bl in m.BLOCKS:
        label='WRONG' if broken_header and bl['variant']=='retroactive_liability_change' else 'Tax variables '+bl['header_phrase']+' tax changes'
        strings.append((bl['header_col']+'1',label))
        for r,(name,_) in m.CATEGORIES.items():
            title=name.replace('_',' ').title()
            if r==9:title='Exogenous Tax Changes'
            if r==8:title='Endogenous Tax Changes'
            if broken_row and r==9 and bl['variant']=='nonretroactive_liability_change':title='Wrong classification'
            strings.append((bl['description_col']+str(r),title))
            strings.append((bl['raw_label_col']+str(r),'RAW_CODE_'+str(r)))
            strings.append((bl['ratio_label_col']+str(r),'RATIO_CODE_'+str(r)))
            strings.append((bl['definition_col']+str(r),'Unit and definition narrative'))
    strings.append(('A11','1947Q1'))
    cells=''.join(f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>' for ref,value in strings)
    if contains_numeric:cells+='<c r="G12"><v>555.12345</v></c><c r="G13"><v>6.5</v></c>'
    body=f'''<?xml version="1.0"?><worksheet xmlns="{main}"><dimension ref="A1:AT264"/><sheetData><row r="1">{cells}</row></sheetData></worksheet>'''
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('xl/workbook.xml',workbook)
        z.writestr('xl/_rels/workbook.xml.rels',rels)
        z.writestr('xl/worksheets/sheet1.xml',body)
    return b.getvalue()

class MapTest(unittest.TestCase):
    def test_candidate_definitions_all_variants(self):
        data=m.audit_mapping_from_workbook(fake_book(),source_checksum='fixture',allow_synthetic=True)
        self.assertEqual(len(data['definition_layout']),21)
        matches=[r for r in data['definition_layout'] if r['category']=='exogenous_combined']
        self.assertEqual(len(matches),3)
        self.assertEqual({x['variant'] for x in matches},{x['variant'] for x in m.BLOCKS})
        self.assertTrue(all(x['status']=='mapping_and_timing_review_required' for x in matches))

    def test_no_numeric_values_leave_report(self):
        report=m.audit_mapping_from_workbook(fake_book(),source_checksum='fixture',allow_synthetic=True)
        encoded=json.dumps(report)
        self.assertNotIn('555.12345',encoded)
        self.assertNotIn('6.5',encoded)
        self.assertFalse(report['mapping_certified'])
        self.assertEqual(report['imported_numeric_shocks'],0)

    def test_numeric_occupancy_only(self):
        report=m.audit_mapping_from_workbook(fake_book(),source_checksum='fixture',allow_synthetic=True)
        self.assertEqual(report['numeric_column_occupancy']['G']['numeric_cells_row11_onward'],2)
        self.assertEqual(report['numeric_column_occupancy']['G']['first_numeric_row'],12)
        self.assertEqual(report['numeric_column_occupancy']['G']['last_numeric_row'],13)

    def test_textual_quarter_marker_is_not_a_certified_date(self):
        report=m.audit_mapping_from_workbook(fake_book(),source_checksum='fixture',allow_synthetic=True)
        self.assertIn({'cell':'A11','text':'1947Q1'},report['potential_date_text_markers'])
        self.assertFalse(report['quarterly_alignment_certified'])

    def test_bad_variant_header_rejected(self):
        with self.assertRaisesRegex(ValueError,'Header mismatch'):
            m.audit_mapping_from_workbook(fake_book(broken_header=True),source_checksum='fixture',allow_synthetic=True)

    def test_bad_exogenous_label_rejected(self):
        with self.assertRaisesRegex(ValueError,'Exogenous row mislabeled'):
            m.audit_mapping_from_workbook(fake_book(broken_row=True),source_checksum='fixture',allow_synthetic=True)

    def test_nonoriginal_workbook_requires_explicit_fixture_mode(self):
        with self.assertRaisesRegex(ValueError,'SHA changed'):
            m.audit_mapping_from_workbook(fake_book(),source_checksum='fixture')

    def test_bad_workbook_fails(self):
        with self.assertRaises((ValueError,zipfile.BadZipFile)):
            m.workbook_cells(b'invalid')

    def test_missing_sheet_rejected(self):
        with self.assertRaisesRegex(ValueError,'expected worksheet'):
            m.workbook_cells(fake_book(),target_sheet='Different')

    def test_column_index(self):
        self.assertEqual(m._colnum('AI'),35)
        self.assertEqual(m._index_parts('AT264'),(46,264))

    def test_confirm_source_pins_are_present(self):
        self.assertEqual(len(m.SOURCE_SHA256),64)
        self.assertEqual(len(m.WORKBOOK_SHA256),64)

    def test_categories_do_not_infer_causality(self):
        report=m.audit_mapping_from_workbook(fake_book(),source_checksum='fixture',allow_synthetic=True)
        self.assertTrue(any('assumptions' in x for x in report['cautions']))
        self.assertTrue(all(x['identification']!='verified_causal_effect' for x in report['definition_layout']))

if __name__=='__main__':unittest.main()
