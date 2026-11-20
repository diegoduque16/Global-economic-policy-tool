"""Synthetic, offline tests for source-backed next ILOSTAT batch imports."""
import csv
import gzip
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.ilostat_native_profile import profile_native_file
from warehouse.native_materialize import sha256
from warehouse.ilostat_native_extension import SOURCE_BASE
from warehouse.ilostat_next_batch import (
    append_next_batch, verify_selection, select_verified_tables, ALREADY_INTEGRATED,
    PARENT_ROWS, PARENT_SOURCES,
)

TABLE = "EMP_TEST_SEX_AGE_RT_A"
HEADER = ["ref_area", "indicator", "source", "sex", "classif1",
          "time", "obs_value", "obs_status"]


class NextILOSTATBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parent = self.root / "parent.sqlite"
        with sqlite3.connect(self.parent) as db:
            db.executescript("""
                CREATE TABLE native_sources (
                    source_id TEXT PRIMARY KEY, raw_sha256 TEXT NOT NULL,
                    original_records INTEGER NOT NULL, indexed_records INTEGER NOT NULL,
                    original_scope TEXT NOT NULL, source_name TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status='SOURCE_NATIVE_UNHARMONIZED'),
                    is_harmonized INTEGER NOT NULL CHECK(is_harmonized=0),
                    fields_json TEXT NOT NULL
                );
                CREATE TABLE native_records (
                    source_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    year_text TEXT, period_text TEXT, geo_code TEXT,
                    series_code TEXT, industry_code TEXT, value_text TEXT,
                    raw_row_json TEXT NOT NULL,
                    PRIMARY KEY(source_id,ordinal)
                ) WITHOUT ROWID;
            """)
            db.execute("INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)",
                       ("parent", "a"*64, 1, 1, "{}", "parent",
                        "SOURCE_NATIVE_UNHARMONIZED", 0, "[]"))
            db.execute("INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)",
                       ("parent", 1, "2023", "2023", "USA", "PARENT", "", "1", "{}"))
        self.file = self.root / (TABLE + ".csv.gz")
        with gzip.open(self.file, "wt", newline="", encoding="utf-8") as fp:
            writer = csv.writer(fp)
            writer.writerow(HEADER)
            writer.writerows([
                ["USA", "EMP_TEST", "ILOEST", "SEX_M", "AGE_Y15-24", "2023", "4.2", ""],
                ["USA", "EMP_TEST", "ILOEST", "SEX_F", "AGE_Y15-24", "2024", "", "U"],
            ])
        fingerprint = sha256(self.file)
        self.entry = {
            "state": "NATIVE_NOT_HARMONIZED", "sha256": fingerprint,
            "records_validated": 2, "csv_header": HEADER,
            "native_numeric_profile": profile_native_file(self.file, 2),
            "remote": SOURCE_BASE + "/indicator-data/" + TABLE + "/" +
                      TABLE + "-20261119-" + fingerprint[:16] + ".csv.gz",
        }
        self.state = {"schema_version": 1, "downloaded": {TABLE: self.entry}}
        self.checkpoint = self.root / "checkpoint.json"
        self.save_state()
        self.output = self.root / "next.sqlite"

    def save_state(self):
        self.checkpoint.write_text(json.dumps(self.state), encoding="utf-8")

    def test_appends_source_rows_without_editing_parent(self):
        before = sha256(self.parent)
        with patch("warehouse.ilostat_next_batch.PARENT_ROWS", 1), patch(
            "warehouse.ilostat_next_batch.PARENT_SOURCES", 1
        ):
            report = append_next_batch(
                self.parent, before, self.checkpoint,
                self.root, [TABLE], self.output,
            )
        self.assertEqual(report["original_source_rows_added"], 2)
        self.assertEqual(report["finite_numeric_values_added"], 1)
        self.assertEqual(report["missing_source_values_retained"], 1)
        self.assertEqual(report["new_attachment_source_native_rows"], 3)
        self.assertEqual(
            report["queryable_new_original_tables"][TABLE]["original_source_rows"], 2)
        self.assertEqual(
            report["queryable_new_original_tables"][TABLE]["observed_source_geo_codes"], 1)
        self.assertEqual(
            report["queryable_new_original_tables"][TABLE]["observed_years"], 2)
        self.assertEqual(
            report["queryable_new_original_tables"][TABLE]["original_dimension_keys_not_harmonized_series"], 2)
        self.assertFalse(report["public_release"])
        self.assertEqual(sha256(self.parent), before)
        with sqlite3.connect(self.output) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM native_records").fetchone()[0], 3)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute(
                "SELECT obs_status FROM (SELECT json_extract(raw_row_json, '$.obs_status') AS obs_status FROM native_records) WHERE obs_status = 'U'"
            ).fetchone()[0], "U")

    def test_selection_uses_actual_checkpoint_ids_and_profiled_rows(self):
        chosen = select_verified_tables(self.state)
        self.assertEqual(chosen, [TABLE])
        # The initial three already-indexed SDGs cannot be selected again.
        self.state["downloaded"]["SDG_0852_SEX_AGE_RT_A"] = dict(self.entry)
        self.assertEqual(select_verified_tables(self.state), [TABLE])

    def test_selection_excludes_unprofiled_or_nonannual_source_files(self):
        without_profile = dict(self.entry, native_numeric_profile=None)
        self.state["downloaded"][TABLE] = without_profile
        with self.assertRaisesRegex(ValueError, "No eligible"):
            select_verified_tables(self.state)
        self.state["downloaded"][TABLE] = self.entry
        self.state["downloaded"]["EMP_TEST_SEX_AGE_RT_M"] = dict(self.entry)
        self.assertEqual(select_verified_tables(self.state), [TABLE])

    def test_selection_skips_sha_invalid_and_inconsistent_source_receipts(self):
        original = dict(self.entry)
        for bad in (
            dict(original, sha256="0" * 64),
            dict(original, remote="onedrive:unverified-source.csv.gz"),
            dict(original, native_numeric_profile={
                **original["native_numeric_profile"],
                "numeric_obs_value_rows": 100,
            }),
        ):
            self.state["downloaded"][TABLE] = bad
            with self.assertRaisesRegex(ValueError, "No eligible"):
                select_verified_tables(self.state)
        self.state["downloaded"][TABLE] = original
        self.assertEqual(select_verified_tables(self.state), [TABLE])

    def test_sequential_parent_is_certified_successor(self):
        self.assertEqual(PARENT_ROWS, 1_134_051)
        self.assertEqual(PARENT_SOURCES, 985)
        self.assertEqual(len(ALREADY_INTEGRATED), 9)
        self.assertTrue({
            "EMP_STEM_SEX_OCU_NB_A",
            "HOW_TEMP_SEX_OCU_DSB_NB_A",
            "HOW_XEES_SEX_OCU_DSB_NB_A",
            "EAR_GGAP_OCU_RT_A",
            "EMP_5EMP_SEX_OCU_NB_A",
            "EMP_TOUR_SEX_OCU_NB_A",
        }.issubset(ALREADY_INTEGRATED))

    def test_all_nine_integrated_originals_are_never_selected_again(self):
        for table in ALREADY_INTEGRATED:
            with self.assertRaisesRegex(ValueError, "already indexed"):
                verify_selection(self.state, [table], self.root)
            self.state["downloaded"][table] = dict(self.entry)
        self.assertEqual(select_verified_tables(self.state), [TABLE])

    def test_rejects_reusing_existing_three_sdg_tables(self):
        with self.assertRaisesRegex(ValueError, "already indexed"):
            verify_selection(self.state, ["SDG_0852_SEX_AGE_RT_A"], self.root)

    def test_rejects_unverified_source_bytes(self):
        self.file.write_bytes(self.file.read_bytes() + b"modified")
        with self.assertRaisesRegex(ValueError, "SHA-invalid"):
            verify_selection(self.state, [TABLE], self.root)

    def test_rejects_changed_original_row_count(self):
        self.entry["records_validated"] = 3
        self.save_state()
        with patch("warehouse.ilostat_next_batch.PARENT_ROWS", 1), patch(
            "warehouse.ilostat_next_batch.PARENT_SOURCES", 1
        ):
            with self.assertRaisesRegex(ValueError, "numeric counts changed|record count|row count|rows"):
                append_next_batch(self.parent, sha256(self.parent), self.checkpoint,
                                  self.root, [TABLE], self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_wrong_parent_sha(self):
        with self.assertRaisesRegex(ValueError, "SHA not verified"):
            append_next_batch(self.parent, "0"*64, self.checkpoint,
                              self.root, [TABLE], self.output)

    def test_rejects_nonannual_and_duplicate_selection(self):
        with self.assertRaises(ValueError):
            verify_selection(self.state, [TABLE, TABLE], self.root)
        with self.assertRaises(ValueError):
            verify_selection(self.state, ["EMP_TEST_SEX_AGE_RT_M"], self.root)

    def test_prevents_overwriting_an_existing_snapshot(self):
        self.output.write_bytes(b"already exists")
        with self.assertRaises(FileExistsError):
            append_next_batch(self.parent, sha256(self.parent), self.checkpoint,
                              self.root, [TABLE], self.output)


if __name__ == "__main__":
    unittest.main()
