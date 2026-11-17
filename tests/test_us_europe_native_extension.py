"""Offline database integrity tests; no source downloads or private credentials."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from warehouse.us_europe_native_extension import extend_sqlite


class EurostatFredNativeSQLiteTest(unittest.TestCase):
    def make_db(self, path):
        with sqlite3.connect(path) as c:
            c.execute("CREATE TABLE native_sources (source_id TEXT PRIMARY KEY, source_sha TEXT, original_records INTEGER, indexed_records INTEGER, details TEXT, origin TEXT, status TEXT, flag INTEGER, schema TEXT)")
            c.execute("CREATE TABLE native_records (source_id TEXT, ordinal INTEGER, period TEXT, original_period TEXT, country TEXT, series_code TEXT, status_flag TEXT, value TEXT, payload TEXT, PRIMARY KEY(source_id, ordinal))")
            c.execute("INSERT INTO native_sources VALUES ('legacy','x',1,1,'{}','legacy','native',0,'[]')")
            c.execute("INSERT INTO native_records VALUES ('legacy',1,'2000','2000','USA','OLD','','1','{}')")

    def test_append_keeps_parent_and_native_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp)/'atlas.sqlite'
            self.make_db(db)
            row={'series':'PAYEMS','period':'2026-01-01','country_iso3':'USA','status_flag':'',
                 'value':'158.0','unit':'thousands','frequency':'monthly',
                 'source_url':'https://fred.stlouisfed.org/graph/fredgraph.csv?id=PAYEMS'}
            report=extend_sqlite(db,[('us',row)],{('us','PAYEMS'):'a'*64},expected_parent_rows=1)
            self.assertEqual(report['native_records_in_new_attachment'],2)
            with sqlite3.connect(db) as c:
                self.assertEqual(c.execute('SELECT value FROM native_records WHERE source_id=\'legacy\'').fetchone()[0],'1')
                self.assertEqual(c.execute('SELECT COUNT(*) FROM native_sources').fetchone()[0],2)
                self.assertEqual(c.execute('SELECT status_flag FROM native_records WHERE series_code=\'PAYEMS\'').fetchone()[0],'')

    def test_parent_count_mismatch_fails_without_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp)/'atlas.sqlite'
            self.make_db(db)
            with self.assertRaisesRegex(ValueError,'Parent'):
                extend_sqlite(db,[],{},expected_parent_rows=2)
            with sqlite3.connect(db) as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM native_records').fetchone()[0],1)

    def test_duplicate_replay_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp)/'atlas.sqlite'
            self.make_db(db)
            row={'series':'PAYEMS','period':'2026-01-01','country_iso3':'USA','status_flag':'',
                 'value':'158','unit':'thousands','frequency':'monthly','source_url':'x'}
            with self.assertRaisesRegex(ValueError,'Duplicate provider/geography/series/period'):
                extend_sqlite(db,[('us',row),('us',row)],{('us','PAYEMS'):'a'*64},expected_parent_rows=1)
            with sqlite3.connect(db) as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM native_records').fetchone()[0],1)
                self.assertEqual(c.execute('SELECT COUNT(*) FROM native_sources').fetchone()[0],1)


if __name__ == '__main__':
    unittest.main()
