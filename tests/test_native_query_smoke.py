"""Safe offline synthetic tests for Atlas actual-private-database smoke contract."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from warehouse.native_materialize import schema
from warehouse.native_query_smoke import check_private_query


class SmokeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "source.sqlite"
        self.pointer = self.root / "pointer.json"
        self.pointer.write_text("{}")
        with sqlite3.connect(self.db) as conn:
            conn.executescript(schema())
            for source in ("ilostat_EMP_EXAMPLE_abcdef123456", "worldbank_wdi"):
                conn.execute(
                    "INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)",
                    (source, "a"*64, 1, 1, "test", "source", "SOURCE_NATIVE_UNHARMONIZED", 0, '[]')
                )
                conn.execute(
                    "INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)",
                    (source, 1, "2022", "2022", "UG", "SERIES", "", "13",
                     json.dumps({"ref_area": "UG", "time": "2022", "obs_value": "13"}))
                )
        self.metadata = {"native_records": 2, "native_sources": 2}

    def test_valid_private_original_filtering(self):
        with patch("warehouse.native_query_smoke.load_pointer", return_value=self.metadata):
            with patch("warehouse.native_query_smoke.verify_parent_db",
                       return_value=frozenset({"EMP_EXAMPLE"})):
                receipt = check_private_query(self.db, self.pointer)
        self.assertEqual(receipt["private_source_native_query_smoke"], "PASS")
        self.assertEqual(receipt["original_source_families_sampled"], 2)
        self.assertFalse(receipt["public_release"])
        self.assertNotIn("raw_row", receipt)

    def test_invalid_original_value_is_not_treated_as_valid_data(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE native_sources SET indexed_records=0 WHERE source_id='worldbank_wdi'")
        with patch("warehouse.native_query_smoke.load_pointer", return_value=self.metadata):
            with patch("warehouse.native_query_smoke.verify_parent_db",
                       return_value=frozenset({"EMP_EXAMPLE"})):
                with self.assertRaisesRegex(ValueError, "receipt counts"):
                    check_private_query(self.db, self.pointer)


if __name__ == "__main__":
    unittest.main()
