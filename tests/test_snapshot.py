"""Offline smoke/regression tests for the versioned warehouse publisher."""
import csv
import json
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path
from warehouse import snapshot

ROWS={
 'wdi': [{'country_iso3':'USA','year':'2020','indicator':'NY.GDP.MKTP.KD.ZG','value':'2.1','unit':'source_defined','source':'World Bank','source_url':'https://example.com'}],
 'us_europe': [{'country_iso3':'USA','year':'2020','period':'2020-01-01','series':'PAYEMS','value':'152000','unit':'FRED_series_defined_verify_metadata','provider':'FRED','frequency':'monthly'}],
 'historical': [{'country_or_polity_code':'XYZ','entity_name':'Historical polity','year':'1700','indicator':'gdp','value':'320','unit':'source_defined_see_metadata','source':'Maddison'}],
 'imf_oecd': [{'country_code':'USA','year':'2020','indicator':'NGDP_RPCH','value':'-2.5','unit':'percent','provider':'IMF DataMapper','data_status':'historical_or_current'}],
 'labor': [{'country_iso3':'USA','year':'2020','period':'2020','series':'UNE_2UNE_SEX_AGE_NB_A','value':'72','unit':'source_defined_confirm_ILOSTAT_metadata','provider':'ILOSTAT','disaggregation':'{"sex":"SEX_T","age":"Y15-24"}'}],
}


def fixture(base, *, break_report=None, with_error=False):
    for family,(folder,filename,minimum) in snapshot.FAMILIES.items():
        directory=base/folder
        (directory/'processed').mkdir(parents=True)
        with (directory/'processed'/filename).open('w',newline='',encoding='utf-8') as handle:
            writer=csv.DictWriter(handle,fieldnames=list(ROWS[family][0]));writer.writeheader();writer.writerows(ROWS[family])
        (directory/'run_report.json').write_text(json.dumps({'observations':4 if family==break_report else 1,
            'errors':[{'issue':'bad'}] if family=='labor' and with_error else [],'conflicts':[]}))


class SnapshotTest(unittest.TestCase):
    def test_all_five_source_families_preserved(self):
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t);fixture(folder/'data')
            report,out=snapshot.build_snapshot(folder/'data',folder/'output',snapshot_id='test-123',enforce_full=False)
            self.assertEqual(report['total_source_observations'],5)
            self.assertEqual(len(report['source_families']),5)
            self.assertFalse(report['is_definition_harmonized'])
            with sqlite3.connect(out/'warehouse.sqlite') as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0],5)
                hist=db.execute("SELECT country_or_polity_code FROM observations WHERE family='historical'").fetchone()[0]
                self.assertEqual(hist,'XYZ')
                d=db.execute("SELECT disaggregation_json FROM observations WHERE family='labor'").fetchone()[0]
                self.assertIn('Y15-24',d)
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            for entry in report['files']:
                self.assertEqual(snapshot.digest(out/entry['path']),entry['sha256'])
            compressed=folder/'snapshot.tar.gz'
            snapshot.package_snapshot(out,compressed)
            with tarfile.open(compressed) as archive:
                names=archive.getnames()
                self.assertIn('test-123/manifest.json',names)
                self.assertIn('test-123/warehouse.sqlite',names)

    def test_source_report_mismatch_prevents_publication(self):
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t);fixture(folder/'data',break_report='labor')
            with self.assertRaisesRegex(ValueError,'count mismatch'):
                snapshot.build_snapshot(folder/'data',folder/'out',snapshot_id='test-123',enforce_full=False)
            self.assertFalse((folder/'out'/'test-123').exists())

    def test_collector_error_prevents_publication(self):
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t);fixture(folder/'data',with_error=True)
            with self.assertRaisesRegex(ValueError,'errors/conflicts'):
                snapshot.build_snapshot(folder/'data',folder/'out',snapshot_id='test-123',enforce_full=False)

    def test_full_mode_rejects_smoke_size(self):
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t);fixture(folder/'data')
            with self.assertRaisesRegex(ValueError,'expected full collector'):
                snapshot.build_snapshot(folder/'data',folder/'out',snapshot_id='test-123')

    def test_bad_value_and_invalid_id(self):
        with self.assertRaisesRegex(ValueError,'Snapshot ID'):
            snapshot.build_snapshot('unused','unused',snapshot_id='../danger')
        with self.assertRaises(ValueError):
            snapshot.normalized('wdi',{'country_iso3':'USA','year':'2024','indicator':'a','value':'nan'})

    def test_overlap_not_silently_deduplicated(self):
        # Different provider metrics may share a code, year, and value; both retained.
        wdi=snapshot.normalized('wdi',ROWS['wdi'][0]);labor=snapshot.normalized('labor',ROWS['labor'][0]);
        self.assertNotEqual(wdi[0],labor[0]);self.assertEqual(wdi[2],labor[2])

if __name__=='__main__':unittest.main()
