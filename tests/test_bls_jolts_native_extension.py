"""Transactional source-native BLS JOLTS tests. Synthetic rows only, no downloads."""
import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from open_data.bls_jolts import COLUMNS, BASE
from warehouse.bls_jolts_native_extension import insert_jolts


class BlsJoltsNativeExtensionTests(unittest.TestCase):
    def make_parent(self, path):
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE native_sources (source_id TEXT PRIMARY KEY, source_sha TEXT, original_records INTEGER, indexed_records INTEGER, details TEXT, origin TEXT, status TEXT, flag INTEGER, schema TEXT)')
            db.execute('CREATE TABLE native_records (source_id TEXT, ordinal INTEGER, period TEXT, original_period TEXT, country TEXT, series_code TEXT, status_flag TEXT, value TEXT, payload TEXT, PRIMARY KEY(source_id, ordinal))')
            db.execute("INSERT INTO native_sources VALUES ('legacy','x',1,1,'{}','legacy','native',0,'[]')")
            db.execute("INSERT INTO native_records VALUES ('legacy',1,'2000','2000','USA','OLD','','1','{}')")

    def make_row(self, *, period='2025-06', value='125.3'):
        sid='JTS000000000000000JOL'
        dims={'series_id':sid,'seasonal':'S','industry_code':'000000',
              'state_code':'00','area_code':'00000','sizeclass_code':'00',
              'dataelement_code':'JO','ratelevel_code':'L',
              'geography_level':'national_industry'}
        return dict(provider='US Bureau of Labor Statistics JOLTS',
                    country_iso3='USA',year=period[:4],period=period,
                    series='JOLTS.job_openings.L.SA',value=value,
                    unit='thousands_of_vacancies',frequency='monthly',
                    disaggregation=json.dumps(dims,sort_keys=True),
                    status_flag='',source_url=BASE+'jt.data.1.AllItems',
                    retrieved_utc='2026-11-17T20:30:00+00:00')

    def make_panel(self,path,rows):
        with Path(path).open('w',newline='',encoding='utf-8') as f:
            writer=csv.DictWriter(f,fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def manifests():
        return {'jt.data.1.AllItems':{'sha256':'a'*64},
                'jt.series':{'sha256':'b'*64}}

    def test_valid_append_preserves_parent_and_dimensions(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'atlas.sqlite';p=Path(td)/'jolts.csv'
            self.make_parent(db)
            self.make_panel(p,[self.make_row(),self.make_row(period='2025-07')])
            result=insert_jolts(db,p,self.manifests(),
                               expected_parent_rows=1,expected_parent_sources=1,
                               expected_rows=2,expected_series=1)
            self.assertEqual(result['new_attachment_records'],3)
            with sqlite3.connect(db) as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM native_records WHERE source_id='legacy'").fetchone()[0],1)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM native_records WHERE source_id LIKE 'bls_jolts_%'").fetchone()[0],2)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM native_sources").fetchone()[0],2)
                self.assertEqual(c.execute("SELECT series_code FROM native_records WHERE period='2025-07'").fetchone()[0],
                                 'JTS000000000000000JOL')

    def test_duplicate_month_rolls_back_atomically(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'atlas.sqlite';p=Path(td)/'jolts.csv'
            self.make_parent(db)
            row=self.make_row()
            self.make_panel(p,[row,row])
            with self.assertRaisesRegex(ValueError,'Duplicate BLS original series / month'):
                insert_jolts(db,p,self.manifests(),expected_parent_rows=1,
                             expected_parent_sources=1,expected_rows=2,expected_series=1)
            with sqlite3.connect(db) as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM native_records').fetchone()[0],1)
                self.assertEqual(c.execute('SELECT COUNT(*) FROM native_sources').fetchone()[0],1)

    def test_wrong_unit_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'atlas.sqlite';p=Path(td)/'jolts.csv'
            self.make_parent(db)
            row=self.make_row();row['unit']='percent'
            self.make_panel(p,[row])
            with self.assertRaisesRegex(ValueError,'metadata/units inconsistent'):
                insert_jolts(db,p,self.manifests(),expected_parent_rows=1,
                             expected_parent_sources=1,expected_rows=1,expected_series=1)
            with sqlite3.connect(db) as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM native_records').fetchone()[0],1)

    def test_parent_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'atlas.sqlite';p=Path(td)/'jolts.csv'
            self.make_parent(db);self.make_panel(p,[self.make_row()])
            with self.assertRaisesRegex(ValueError,'Parent source-native SQLite edition changed'):
                insert_jolts(db,p,self.manifests(),expected_parent_rows=2,expected_parent_sources=1,
                             expected_rows=1,expected_series=1)


if __name__=='__main__':
    unittest.main()
