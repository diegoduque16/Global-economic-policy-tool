"""Synthetic tests for fail-closed public BLS JOLTS Atlas exporter."""
from pathlib import Path
import csv
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from warehouse.native_materialize import schema
from warehouse.export_public_bls import export


class BLSPublicReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "source.sqlite"
        self.pointer = self.root / "pointer.json"
        self.pointer.write_text("{}")
        with sqlite3.connect(self.db) as conn:
            conn.executescript(schema())
            conn.execute("INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)",
                ("bls_jolts_JTU000000000000000", "a"*64, 2, 2,
                 "original", "Job openings (source original)", "SOURCE_NATIVE_UNHARMONIZED", 0, "[]"))
            conn.execute("INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)",
                ("worldbank_wdi", "b"*64, 1, 1, "original", "Private excluded source",
                 "SOURCE_NATIVE_UNHARMONIZED", 0, "[]"))
            conn.executemany("INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)", [
                ("bls_jolts_JTU000000000000000",1,"2024-01","2024-01","US","JTU000000000000000","", "42",json.dumps({"private_raw":"NOT_FOR_PUBLIC_RELEASE"})),
                ("bls_jolts_JTU000000000000000",2,"2024-02","2024-02","US","JTU000000000000000","", "43",json.dumps({"private_raw":"NOT_FOR_PUBLIC_RELEASE"})),
                ("worldbank_wdi",1,"2024","2024","UG","WDI","", "999",json.dumps({"private_raw":"NOT_FOR_PUBLIC_RELEASE"}))
            ])
        self.pointer_data={"edition_id":"atlas-native-ilostat-next-20261122-44986267530",
                           "archive_sha256":"a"*64, "native_records":3, "native_sources":2}

    def run_export(self):
        with patch("warehouse.export_public_bls.EXPECTED_BLS_SERIES", 1), \
             patch("warehouse.export_public_bls.EXPECTED_BLS_ROWS", 2), \
             patch("warehouse.export_public_bls.load_pointer",return_value=self.pointer_data), \
             patch("warehouse.export_public_bls.verify_parent_db",return_value=frozenset({"EXAMPLE"})):
            return export(self.db, self.pointer, self.root/"out")

    def test_excludes_raw_private_fields_and_other_provider_records(self):
        receipt=self.run_export()
        self.assertEqual(receipt["source_native_records"],2)
        self.assertEqual(receipt["finite_numeric_values"],2)
        self.assertTrue(receipt["not_harmonized"])
        with zipfile.ZipFile(self.root/"out"/receipt["csv_zip"]) as archive:
            csv_text=archive.read("bls_jolts_source_native.csv").decode()
            rows=list(csv.DictReader(io.StringIO(csv_text)))
            self.assertEqual(len(rows),2)
            self.assertNotIn("999",csv_text)
            self.assertNotIn("NOT_FOR_PUBLIC_RELEASE",csv_text)
            self.assertEqual(rows[0]["original_value_text"],"42")
            self.assertEqual(rows[0]["original_month_key"],"2024-01")
            cite=archive.read("CITATION_AND_USAGE.txt").decode()
            self.assertIn("Bureau of Labor Statistics",cite)
            self.assertIn("https://www.bls.gov/jlt/",cite)
            sources=json.loads(archive.read("ORIGINAL_SOURCE_CATALOG.json"))
            self.assertEqual(len(sources),1)
            self.assertNotIn("worldbank",json.dumps(sources))

    def test_tampered_original_receipt_fails_closed(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE native_sources SET original_records=3 WHERE source_id LIKE 'bls_jolts_%'")
        with self.assertRaisesRegex(ValueError,"Invalid original BLS"):
            self.run_export()


if __name__=="__main__":
    unittest.main()
