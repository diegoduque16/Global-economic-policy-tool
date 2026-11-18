"""Synthetic offline source-completion cases, no private provider source values."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

from scripts.ilostat_gap_report import build_gap_report, write_gap_report


def toc():
    return {
        "EMP_MISSING_NB_A": {"last.update": "2026-11-17", "n.records": "10", "freq": "A"},
        "EMP_NEW_NB_A": {"last.update": "2026-11-17", "n.records": "11", "freq": "A"},
        "EMP_OLD_NB_A": {"last.update": "2026-11-17", "n.records": "12", "freq": "A"},
        "EMP_LARGE_NB_A": {"last.update": "2026-11-17", "n.records": "13", "freq": "A"},
        "EMP_FAILURE_NB_A": {"last.update": "2026-11-17", "n.records": "14", "freq": "A"},
    }


def state():
    return {
        "schema_version": 1,
        "downloaded": {
            "EMP_NEW_NB_A": {
                "state": "NATIVE_NOT_HARMONIZED", "records_validated": 11,
                "last_update": "2026-11-17", "toc_records": "11",
                "remote": "onedrive:PRIVATE_DO_NOT_EXPOSE",
                "sha256": "a" * 64
            },
            "EMP_OLD_NB_A": {
                "state": "NATIVE_NOT_HARMONIZED", "records_validated": 12,
                "last_update": "2026-11-11", "toc_records": "12",
                "remote": "onedrive:PRIVATE_DO_NOT_EXPOSE",
                "sha256": "b" * 64
            },
        },
        "deferred": {
            "EMP_LARGE_NB_A": {
                "last_update": "2026-11-17", "max_file_mib": 512,
                "private_reason": "NO_COPY"
            }
        },
        "failures": {
            "EMP_FAILURE_NB_A": {
                "date": "2026-11-17", "last_update": "2026-11-17",
                "last_error": "PRIVATE_FAILURE_DO_NOT_EXPOSE",
            }
        },
    }


class GapReportTests(unittest.TestCase):
    def test_missing_deferred_failed_and_stale_keep_distinct(self):
        rows, report = build_gap_report(toc(), state(), now_day="2026-11-17")
        self.assertEqual(report["archived_toc_table_ids_any_vintage"], 2)
        self.assertEqual(report["official_table_ids_at_current_vintage"], 1)
        self.assertEqual(report["official_table_ids_not_archived"], 3)
        self.assertEqual(report["current_vintage_attention_required"], 4)
        self.assertEqual(report["attention_by_category"], {
            "not_yet_downloaded": 1, "failed_this_day": 1,
            "deferred_oversize_at_cap": 1, "verified_older_vintage": 1,
        })
        byid = {r["table_id"]: r["stage"] for r in rows}
        self.assertEqual(byid["EMP_MISSING_NB_A"], "not_yet_downloaded")
        self.assertEqual(byid["EMP_OLD_NB_A"], "verified_older_vintage")
        self.assertEqual(byid["EMP_LARGE_NB_A"], "deferred_oversize_at_cap")
        self.assertEqual(byid["EMP_FAILURE_NB_A"], "failed_this_day")
        self.assertFalse(report["fully_archived_official_toc"])

    def test_complete_requires_all_current_official_ids(self):
        data = state()
        for code, meta in toc().items():
            data["downloaded"][code] = {
                "state": "NATIVE_NOT_HARMONIZED", "records_validated": 2,
                "last_update": meta["last.update"], "toc_records": meta["n.records"]
            }
        rows, report = build_gap_report(toc(), data, now_day="2026-11-17")
        self.assertEqual(rows, [])
        self.assertTrue(report["fully_archived_official_toc"])
        self.assertTrue(report["all_originals_at_current_toc_vintage"])

    def test_old_vintage_does_not_disappear_from_completion_gaps(self):
        data = state()
        data["downloaded"]["EMP_MISSING_NB_A"] = {
            "state": "NATIVE_NOT_HARMONIZED", "records_validated": 1,
            "last_update": "2026-11-01", "toc_records": "10"
        }
        rows, report = build_gap_report(toc(), data, now_day="2026-11-17")
        self.assertEqual(report["archived_toc_table_ids_any_vintage"], 3)
        self.assertEqual(report["current_vintage_attention_required"], 4)
        self.assertIn("EMP_MISSING_NB_A", {r["table_id"] for r in rows})

    def test_report_writes_only_official_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            d=Path(root)
            summary = write_gap_report(d, toc(), state(), now_day="2026-11-17")
            self.assertEqual(summary["current_vintage_attention_required"], 4)
            raw = (d / "remaining_official_source_ids.csv").read_text()
            saved = json.loads((d / "official_source_completion.json").read_text())
            self.assertEqual(len(list(csv.DictReader(raw.splitlines()))), 4)
            self.assertEqual(saved, summary)
            self.assertNotIn("onedrive:", raw)
            self.assertNotIn("PRIVATE_", raw)
            self.assertNotIn("sha256", raw.lower())
            self.assertNotIn("PRIVATE_", json.dumps(saved))

    def test_invalid_identity_and_unverified_record_fail_closed(self):
        t=toc()
        t["../secret"]={"last.update":"2026-11-17","n.records":"3"}
        with self.assertRaisesRegex(ValueError, "Untrusted"):
            build_gap_report(t, state(), now_day="2026-11-17")
        data=state()
        data["downloaded"]["EMP_NEW_NB_A"]["records_validated"]=0
        with self.assertRaisesRegex(ValueError, "Unverified archived"):
            build_gap_report(toc(), data, now_day="2026-11-17")

    def test_deferred_cap_and_failure_day_are_exact(self):
        data=state()
        data["deferred"]["EMP_LARGE_NB_A"]["max_file_mib"]=256
        data["failures"]["EMP_FAILURE_NB_A"]["date"]="2026-11-16"
        rows,_=build_gap_report(toc(), data, now_day="2026-11-17")
        byid={r["table_id"]:r["stage"] for r in rows}
        self.assertEqual(byid["EMP_LARGE_NB_A"],"not_yet_downloaded")
        self.assertEqual(byid["EMP_FAILURE_NB_A"],"not_yet_downloaded")


if __name__ == "__main__":
    unittest.main()
