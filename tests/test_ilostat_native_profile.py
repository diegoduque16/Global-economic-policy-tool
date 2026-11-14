"""Offline tests for actual ILOSTAT gzip profiling and source-table coverage.

Synthetic data only; never downloads private datasets, credentials or OneDrive.
"""
import csv
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from scripts.ilostat_native_profile import (
    PROFILE_STAGE, build_source_inventory, profile_native_file, write_inventory,
)


def native(path, values, *, status=True):
    with gzip.open(path, "wt", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["ref_area", "time", "obs_value", "obs_status"])
        for geo, year, value, marker in values:
            writer.writerow([geo, year, value, marker if status else ""])


def state_from_profile(profile=None):
    entry = {
        "sha256": "a" * 64, "state": "NATIVE_NOT_HARMONIZED",
        "records_validated": 4, "last_update": "2026-11-14",
        "toc_records": "4", "verified_utc": "2026-11-14T12:00:00Z",
    }
    if profile is not None:
        entry["native_numeric_profile"] = profile
    return {"schema_version": 1, "downloaded": {"EMP_TEST_NB_A": entry}}


def toc():
    return {"EMP_TEST_NB_A": {"freq": "A", "last.update": "2026-11-14",
                              "n.records": "4"}}


class NativeILOSTATNumericProfileTests(unittest.TestCase):
    def test_full_gzip_numeric_missing_markers_and_source_geography(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "native.csv.gz"
            native(p, [
                ("GHA", "2020", "0", ""),
                ("NGA", "2021", "1.5", "E"),
                ("NGA", "2022", "", "M"),
                ("WLD", "2023", "not available", ""),
            ])
            result = profile_native_file(p, "4")
            self.assertEqual(result["records_validated"], 4)
            self.assertEqual(result["numeric_obs_value_rows"], 2)
            self.assertEqual(result["blank_obs_value_rows"], 1)
            self.assertEqual(result["other_nonnumeric_or_nonfinite_rows"], 1)
            self.assertEqual(result["source_ref_area_distinct_codes"], 3)
            self.assertEqual(result["obs_status_nonempty_rows"], 2)
            self.assertEqual((result["first_annual_time_if_present"],
                              result["last_annual_time_if_present"]), (2020, 2023))
            self.assertFalse(result["complete_native_observation_key_audited"])
            self.assertFalse(result["canonical_warehouse_integrated"])
            self.assertFalse(result["distinct_economic_observations_proven"])

    def test_nonfinite_and_invalid_value_never_certified_as_finite_numeric(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "test.csv.gz"
            native(p, [
                ("USA", "2020", "NaN", ""),
                ("USA", "2021", "Infinity", ""),
                ("USA", "2022", "-2.5", ""),
                ("USA", "2023", "1e5", ""),
            ])
            result = profile_native_file(p, 4)
            self.assertEqual(result["numeric_obs_value_rows"], 2)
            self.assertEqual(result["other_nonnumeric_or_nonfinite_rows"], 2)
            self.assertFalse(result["all_numeric_values_finite"])

    def test_wrong_toc_count_or_missing_required_columns_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "native.csv.gz"
            native(p, [("USA", "2020", "1", "")])
            with self.assertRaisesRegex(ValueError, "TOC row count"):
                profile_native_file(p, "2")
            with gzip.open(p, "wt", newline="") as file:
                file.write("ref_area,time,not_observation\nGHA,2020,1\n")
            with self.assertRaisesRegex(ValueError, "required CSV fields"):
                profile_native_file(p, "1")

    def test_ragged_or_empty_files_are_not_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "native.csv.gz"
            with gzip.open(p, "wt", newline="") as file:
                file.write("ref_area,time,obs_value\nGHA,2020,1,UNEXPECTED\n")
            with self.assertRaisesRegex(ValueError, "Ragged"):
                profile_native_file(p)
            with gzip.open(p, "wt", newline="") as file:
                file.write("ref_area,time,obs_value\n")
            with self.assertRaisesRegex(ValueError, "zero physical"):
                profile_native_file(p)

    def test_inventory_does_not_invent_numeric_rows_for_unprofiled_tables(self):
        state = state_from_profile()
        rows, summary = build_source_inventory(state, toc(), checkpoint_sha256="b" * 64)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["profile_state"], "NOT_PROFILED_HISTORICAL_SOURCE")
        self.assertEqual(rows[0]["source_native_numeric_rows_profiled"], "")
        self.assertEqual(summary["verified_source_table_files"], 1)
        self.assertEqual(summary["historical_source_tables_not_yet_profiled"], 1)
        self.assertEqual(summary["finite_numeric_obs_value_rows_in_profiled_tables_ONLY"], 0)
        self.assertEqual(summary["numeric_rows_across_unprofiled_tables"], "UNKNOWN_NOT_ZERO")
        self.assertFalse(summary["warehouse_integrated"])

    def test_inventory_profiles_exact_count_not_all_historical_tables(self):
        profile = {
            "profile_stage": PROFILE_STAGE, "records_validated": 4,
            "numeric_obs_value_rows": 2, "blank_obs_value_rows": 1,
            "other_nonnumeric_or_nonfinite_rows": 1,
            "obs_status_nonempty_rows": 2, "source_ref_area_distinct_codes": 3,
            "first_annual_time_if_present": 2020, "last_annual_time_if_present": 2023,
            "canonical_warehouse_integrated": False,
        }
        state = state_from_profile(profile)
        state["downloaded"]["EMP_SECOND_NB_A"] = {
            "sha256": "c" * 64, "state": "NATIVE_NOT_HARMONIZED",
            "records_validated": 100, "last_update": "2026-10-01",
            "toc_records": "100", "verified_utc": "2026-10-02T00:00:00Z",
        }
        native_toc = {**toc(), "EMP_SECOND_NB_A": {
            "freq": "A", "last.update": "2026-10-01", "n.records": "100"}}
        with tempfile.TemporaryDirectory() as folder:
            report = write_inventory(Path(folder), state, native_toc,
                                     checkpoint_sha256="b" * 64)
            self.assertEqual(report["physical_source_native_csv_rows_all_verified_files_NOT_unique_observations"], 104)
            self.assertEqual(report["source_tables_with_numeric_value_profile"], 1)
            self.assertEqual(report["historical_source_tables_not_yet_profiled"], 1)
            self.assertEqual(report["finite_numeric_obs_value_rows_in_profiled_tables_ONLY"], 2)
            with (Path(folder) / "source_native_table_inventory.csv").open(newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 2)
            self.assertEqual(sum(r["profile_state"] == PROFILE_STAGE for r in rows), 1)
            self.assertTrue(all("onedrive:" not in str(r) for r in rows))
            self.assertEqual(json.loads((Path(folder) / "source_native_numeric_coverage.json").read_text()),
                             report)

    def test_mismatched_profile_inflation_hard_fails(self):
        p = {
            "profile_stage": PROFILE_STAGE, "records_validated": 4,
            "numeric_obs_value_rows": 10, "blank_obs_value_rows": 0,
            "other_nonnumeric_or_nonfinite_rows": 0,
            "obs_status_nonempty_rows": 0, "source_ref_area_distinct_codes": 1,
            "first_annual_time_if_present": 2020, "last_annual_time_if_present": 2021,
            "canonical_warehouse_integrated": False,
        }
        with self.assertRaisesRegex(ValueError, "does not reconcile"):
            build_source_inventory(state_from_profile(p), toc(), checkpoint_sha256="b" * 64)

    def test_public_inventory_cannot_claim_canonical_integration(self):
        p = {
            "profile_stage": PROFILE_STAGE, "records_validated": 4,
            "numeric_obs_value_rows": 4, "blank_obs_value_rows": 0,
            "other_nonnumeric_or_nonfinite_rows": 0,
            "obs_status_nonempty_rows": 0, "source_ref_area_distinct_codes": 1,
            "first_annual_time_if_present": 2020, "last_annual_time_if_present": 2021,
            "canonical_warehouse_integrated": True,
        }
        with self.assertRaisesRegex(ValueError, "canonical integration"):
            build_source_inventory(state_from_profile(p), toc(), checkpoint_sha256="b" * 64)


if __name__ == "__main__":
    unittest.main()
