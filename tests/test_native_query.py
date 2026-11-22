"""Synthetic tests for the private read-only Atlas native query interface."""
import json
import csv
import io
from contextlib import redirect_stdout
from unittest.mock import patch
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

    def test_original_facets_and_conditional_counts(self):
        with connect_read_only(self.path) as db:
            facets=browse(db,command="facets",source_id="ilostat_TEST_abcdef123456")
            filtered=browse(db,command="facets",source_id="ilostat_TEST_abcdef123456",geo="UG")
        self.assertEqual(facets["facets"]["geographies"],
                         [{"code":"KE","rows":1},{"code":"UG","rows":1}])
        self.assertEqual(facets["facets"]["years"],
                         [{"code":"2023","rows":1},{"code":"2024","rows":1}])
        self.assertEqual(filtered["facets"]["years"], [{"code":"2023","rows":1}])
        self.assertTrue(facets["source_native_not_harmonized"])

    def test_source_catalog_search_pagination_and_literal_prefix(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)",
                ("ilostat_EXTRA_abc123abc123", "b"*64, 1, 1,
                 "original", "Employment estimate", "SOURCE_NATIVE_UNHARMONIZED", 0, '[]'))
            db.execute("INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)",
                ("wdi_GDP", "c"*64, 1, 1,
                 "original", "Gross Domestic Product", "SOURCE_NATIVE_UNHARMONIZED", 0, '[]'))
        with connect_read_only(self.path) as db:
            first = browse(db, command="sources", source_prefix="ilostat_", limit=1)
            second = browse(db, command="sources", source_prefix="ilostat_", limit=1, offset=1)
            by_name = browse(db, command="sources", match="employment")
            by_code = browse(db, command="sources", match="GDP")
            literal_wildcard = browse(db, command="sources", match="%")
            with self.assertRaises(ValueError):
                browse(db, command="sources", offset=-1)
            with self.assertRaises(ValueError):
                browse(db, command="sources", match=" ")
            with self.assertRaises(ValueError):
                browse(db, command="records", source_id="ilostat_TEST_abcdef123456",
                       match="employment")
        self.assertEqual(first["total_matching_sources"], 2)
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])
        self.assertNotEqual(first["rows"][0]["source_id"], second["rows"][0]["source_id"])
        self.assertEqual(by_name["rows"][0]["source_id"], "ilostat_EXTRA_abc123abc123")
        self.assertEqual(by_code["rows"][0]["source_id"], "wdi_GDP")
        self.assertEqual(literal_wildcard["total_matching_sources"], 0)

    def test_csv_export_cli_no_raw_fields_by_default(self):
        from warehouse.native_query import main
        stdout=io.StringIO()
        with patch("sys.argv", ["native_query","--db",str(self.path),
                                "records","--source-id","ilostat_TEST_abcdef123456",
                                "--geo","UG","--format","csv"]), redirect_stdout(stdout):
            main()
        rows=list(csv.DictReader(io.StringIO(stdout.getvalue())))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["geo_code"],"UG")
        self.assertEqual(rows[0]["value_text"],"10")
        self.assertNotIn("raw_row",rows[0])

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
            with self.assertRaises(ValueError):
                browse(db,command="facets",source_id="ilostat_TEST_abcdef123456",include_raw=True)
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM native_records")
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM native_records").fetchone()[0],2)


if __name__=="__main__":
    unittest.main()
