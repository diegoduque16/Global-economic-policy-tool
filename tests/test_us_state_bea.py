import csv
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from open_data.us_state_bea import (STATES, STATE_BY_FIPS, SOURCES, choose_member,
                                    collect, number, parse_table)

PILOT=('CA','CT','FL','NY','TX')


def fake_archive(source, *, bad=False):
    tab=SOURCES[source]
    output=io.StringIO()
    yrs=[str(x) for x in range(2021,2026)]
    names=['GeoFIPS','GeoName','Region','TableName','LineCode','IndustryClassification','Description','Unit']+yrs
    w=csv.DictWriter(output,fieldnames=names,lineterminator=chr(10))
    w.writeheader()
    for abbr in PILOT:
        codes=(1,3) if source=='SAGDP' else (1,2,3)
        for code in codes:
            row={k:'' for k in names}
            row.update({'GeoFIPS':STATES[abbr]+'000','GeoName':abbr,
                        'TableName':tab,'LineCode':str(code),'Description':'Published BEA measure',
                        'Unit':'Millions of chained 2017 dollars' if source=='SAGDP' and code==1 else 'dollars'})
            for year in yrs:
                row[year]=str(10000+(int(year)-2000)*100+code)
            if bad and abbr=='CT' and code==1:
                row['2022']='(NA)'
            w.writerow(row)
    row={k:'' for k in names}
    row.update({'GeoFIPS':'00000','GeoName':'United States','TableName':tab,
                'LineCode':'1','Description':'National','Unit':'millions'})
    row.update({y:999999 for y in yrs});w.writerow(row)
    memory=io.BytesIO()
    with zipfile.ZipFile(memory,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(f'{tab}__ALL_AREAS_1997_2025.csv',output.getvalue())
        z.writestr('OTHER.csv','ignore')
    return memory.getvalue(), f'https://apps.bea.gov/regional/zip/{source}.zip'


class BEAStateTests(unittest.TestCase):
    def test_all_states_fips(self):
        self.assertEqual(len(STATES),51)
        self.assertEqual(len(STATE_BY_FIPS),51)
        self.assertEqual(STATE_BY_FIPS['09000'],'CT')
        self.assertEqual(STATE_BY_FIPS['11000'],'DC')

    def test_strict_table_id_ignores_sagdp11(self):
        raw,_=fake_archive('SAGDP')
        data,name=choose_member(raw,'SAGDP1')
        mem=io.BytesIO()
        with zipfile.ZipFile(mem,'w') as archive:
            archive.writestr(name,data)
            archive.writestr('SAGDP11__ALL_AREAS_1998_2025.csv',data)
        selected,label=choose_member(mem.getvalue(),'SAGDP1')
        self.assertEqual(label,name)
        self.assertEqual(selected,data)

    def test_zip_member_selected_by_table(self):
        raw,_=fake_archive('SAGDP')
        data,name=choose_member(raw,'SAGDP1')
        self.assertTrue(name.startswith('SAGDP1__ALL_AREAS'))
        self.assertIn(b'GeoFIPS',data)

    def test_reject_multiple_matching_members(self):
        raw,_=fake_archive('SAGDP')
        mem=io.BytesIO()
        with zipfile.ZipFile(mem,'w') as z:
            with zipfile.ZipFile(io.BytesIO(raw)) as old:
                selected=old.read('SAGDP1__ALL_AREAS_1997_2025.csv')
            z.writestr('SAGDP1__ALL_AREAS_1997_2025.csv',selected)
            z.writestr('SAGDP1__ALL_AREAS_1997_2024.csv',selected)
        with self.assertRaises(ValueError):
            choose_member(mem.getvalue(),'SAGDP1')

    def test_missing_not_zero_and_states_only(self):
        raw,url=fake_archive('SAINC',bad=True)
        body,member=choose_member(raw,'SAINC1')
        rows,stat=parse_table(body,'SAINC1',url,member,'test',2021,2025,set(PILOT))
        self.assertEqual(len(rows),74)
        self.assertEqual(stat['missing_or_suppressed_cells'],1)
        self.assertTrue(all(row['country_iso3']=='USA' for row in rows))
        self.assertNotIn('00000',{json.loads(row['disaggregation'])['state_bea_geo_fips'] for row in rows})
        self.assertFalse(any(r['value']==0 for r in rows))

    def test_blank_and_zero_distinct(self):
        self.assertIsNone(number('(NA)'))
        self.assertEqual(number('0'),0)
        self.assertEqual(number('1,500.0'),1500.0)

    def test_five_state_mock_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=collect(tmp,'pilot',get=fake_archive)
            self.assertEqual(r['state_count'],5)
            self.assertEqual(r['observations'],125)
            self.assertEqual(r['series_count'],5)
            self.assertEqual(r['status'],'VALIDATED_DESCRIPTIVE_STATE_MACRO_DATA_NOT_CAUSAL_SHOCKS')
            p=Path(tmp)/'processed'/'us_state_bea_panel.csv'
            self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),r['panel_sha256'])
            self.assertFalse(r['research_ready'])

    def test_missing_table_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            def malformed(source):
                if source=='SAINC':return b'not_a_zip', 'https://apps.bea.gov/regional/zip/SAINC.zip'
                return fake_archive(source)
            r=collect(tmp,'pilot',get=malformed)
            self.assertEqual(r['status'],'VALIDATION_FAILED')
            self.assertTrue(r['errors'])
            self.assertFalse((Path(tmp)/'processed'/'us_state_bea_panel.csv').exists())

if __name__=='__main__': unittest.main()
