"""Synthetic tests for the private read-only Atlas native query interface."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from warehouse.native_materialize import schema
from warehouse.native_query import browse, connect_read_only


class NativeQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "atlas.sqlite"
        with sqlite3.connect(self.path) as db:
            db.executescript(schema())
            db.execute("INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)",
                       ("ilostat_TEST_abcdef123456", "a"*64, 2, 2, "global",
                        "Original test source", "SOURCE_NATIVE_UNHARMONIZED", 0, '["time","ref_area"]'))
            db.executemany("INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)", [
                ("ilostat_TEST_abcdef123456",1,"2023","2023","UG","TEST","", "10", json.dumps({"ref_area":"UG","time":"2023","obs_value":"10"})),
                ("ilostat_TEST_abcdef123456",2,"2024","2024","KE","TEST","", "12", json.dumps({"ref_area":"KE","time":"2024","obs_value":"12"})),
            ])

    def test_source_inventory_contains_original_provenance(self):
        with connect_read_only(self.path) as db:
            result=browse(db,command="sources")
        self.assertEqual(result["rows"][0]["source_sha256"],"a"*64)
        self.assertEqual(result["rows"][0]["indexed_records"],2)

    def test_exact_filters_and_raw_opt_in(self):
        with connect_read_only(self.path) as db:
            result=browse(db,command="records",source_id="ilostat_TEST_abcdef123456",
                          geo="UG",year="2023",series="TEST")
            raw=browse(db,command="records",source_id="ilostat_TEST_abcdef123456",
                       geo="UG",include_raw=True)
        self.assertEqual(len(result["rows"]),1)
        self.assertEqual(result["rows"][0]["value_text"],"10")
        self.assertNotIn("raw_row",result["rows"][0])
        self.assertEqual(raw["rows"][0]["raw_row"]["ref_area"],"UG")

    def test_reject_missing_source_and_unbounded_requests(self):
        with connect_read_only(self.path) as db:
            with self.assertRaises(ValueError):
                browse(db,command="records")
            with self.assertRaises(ValueError):
                browse(db,command="records",source_id="not-present")
            with self.assertRaises(ValueError):
                browse(db,command="sources",limit=501)
            with self.assertRaises(ValueError):
                browse(db,command="sources",geo="UG")
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM native_records")
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM native_records").fetchone()[0],2)


if __name__=="__main__":
    unittest.main()
