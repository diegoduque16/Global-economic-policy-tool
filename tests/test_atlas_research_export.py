"""Source-preserving WDI research exports: synthetic failures and real published release."""
import csv
import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path
import tempfile
import unittest

from atlas_public.research_export import build_research_exports
from research.query_atlas_wdi import subset


class AtlasResearchExports(unittest.TestCase):
    def small_release(self, root):
        data = root / "data"
        (data / "series").mkdir(parents=True)
        geographies = {
            "USA": {"name": "United States", "kind": "country_or_territory", "region": "North America"},
            "GHA": {"name": "Ghana", "kind": "country_or_territory", "region": "Sub-Saharan Africa"},
            "WLD": {"name": "World", "kind": "aggregate", "region": "Aggregates"},
        }
        series = [
            {"code": "GDP.TEST", "title": "GDP growth test", "topic": "growth",
             "rows": 4, "first_year": 2020, "last_year": 2021, "geographies": 3},
            {"code": "TAX.TEST", "title": "Tax test", "topic": "fiscal",
             "rows": 2, "first_year": 2020, "last_year": 2021, "geographies": 1},
        ]
        rows = {
            "GDP.TEST": [["USA", 2020, 0], ["USA", 2021, 2.5],
                         ["GHA", 2021, 1.5], ["WLD", 2020, 1.8]],
            "TAX.TEST": [["USA", 2020, 18.1], ["USA", 2021, 18.2]],
        }
        for definition in series:
            code = definition["code"]
            payload = {"code": code, "title": definition["title"], "topic": definition["topic"],
                       "unit": "source_defined", "rows": rows[code]}
            (data / "series" / (code + ".json")).write_text(json.dumps(payload))
        manifest = {
            "public_edition": True,
            "source_run_id": 123,
            "source_downloaded_at_utc": "2026-11-09T00:00:00Z",
            "input_panel_sha256": "a" * 64,
            "summary": {
                "source_separated_numeric_checks_passed": True,
                "cross_provider_harmonized": False,
                "geographies": 3, "observations": 6,
                "country_or_territory_codes": 2, "aggregate_codes": 1,
            },
            "series": series, "geographies": geographies,
        }
        (data / "catalog.json").write_text(json.dumps(manifest))
        return data

    def test_preserve_zero_missing_and_exclude_aggregates_from_wide(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = self.small_release(root)
            report = build_research_exports(data, root / "export")
            self.assertEqual(report["numeric_observations"], 6)
            self.assertEqual(report["country_numeric_observations"], 5)
            self.assertEqual(report["aggregate_numeric_observations"], 1)
            self.assertEqual(report["country_year_records"], 3)
            with (root / "export/wdi_country_year_wide.csv").open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual({(x["geography_code"], x["year"]) for x in rows},
                             {("USA", "2020"), ("USA", "2021"), ("GHA", "2021")})
            self.assertEqual(next(x for x in rows if x["geography_code"] == "USA" and
                                  x["year"] == "2020")["GDP.TEST"], "0")
            self.assertEqual(next(x for x in rows if x["geography_code"] == "GHA")["TAX.TEST"], "")
            with (root / "export/wdi_aggregate_year_long.csv").open(newline="") as stream:
                aggregate = list(csv.DictReader(stream))
            self.assertEqual(len(aggregate), 1)
            self.assertEqual(aggregate[0]["geography_code"], "WLD")
            self.assertFalse(report["causal_inference_ready"])
            for name, meta in report["output_files"].items():
                blob = (root / "export" / name).read_bytes()
                self.assertEqual(len(blob), meta["bytes"])
                self.assertEqual(hashlib.sha256(blob).hexdigest(), meta["sha256"])

    def test_sqlite_research_download_preserves_real_keys_and_separate_aggregates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = self.small_release(root)
            report = build_research_exports(data, root / "export")
            info = report["sqlite_research_edition"]
            self.assertEqual(info["all_observations"], 6)
            self.assertEqual(info["country_observations"], 5)
            self.assertEqual(info["aggregate_observations"], 1)
            self.assertFalse(info["cross_provider_harmonized"])
            bundle = root / "export" / "atlas_wdi_research.sqlite.zip"
            self.assertTrue(bundle.is_file())
            self.assertFalse((root/"export"/"atlas_wdi_research.sqlite").exists())
            with zipfile.ZipFile(bundle) as archive:
                self.assertEqual(archive.namelist(), ["atlas_wdi_research.sqlite"])
                self.assertIsNone(archive.testzip())
                database = root / "recovered.sqlite"
                database.write_bytes(archive.read("atlas_wdi_research.sqlite"))
            with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(db.execute("SELECT COUNT(*) FROM country_observations").fetchone()[0], 5)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM aggregate_observations").fetchone()[0], 1)
                self.assertEqual(db.execute(
                    "SELECT value FROM country_observations WHERE geography_code='USA' "
                    "AND year=2020 AND indicator_code='GDP.TEST'").fetchone()[0], 0)
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM country_observations WHERE geography_code='GHA' "
                    "AND indicator_code='TAX.TEST'").fetchone()[0], 0)
                self.assertEqual(db.execute(
                    "SELECT value FROM aggregate_observations WHERE geography_code='WLD'").fetchone()[0], 1.8)
                self.assertEqual(db.execute(
                    "SELECT value FROM source_edition WHERE property='source_run_id'").fetchone()[0], "123")
                self.assertEqual(db.execute(
                    "SELECT value FROM source_edition WHERE property='causal_inference_ready'").fetchone()[0], "false")

    def test_duplicate_numeric_or_geography_integrity_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = self.small_release(root)
            file = data / "series/GDP.TEST.json"
            p = json.loads(file.read_text())
            p["rows"].append(p["rows"][0])
            p["rows"] = p["rows"][:-1] + [p["rows"][0]]
            file.write_text(json.dumps(p))
            with self.assertRaisesRegex(ValueError, "Duplicate WDI"):
                build_research_exports(data, root / "invalid")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = self.small_release(root)
            metadata = json.loads((data / "catalog.json").read_text())
            metadata["geographies"]["USA"]["kind"] = "unresolved"
            (data / "catalog.json").write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, "Unknown World Bank"):
                build_research_exports(data, root / "invalid")

    def test_research_query_is_reproducible_and_rejects_tampered_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = self.small_release(root)
            build_research_exports(data, root / "export")
            audit = subset(root / "export", root / "selected.csv", {"USA"},
                           {"GDP.TEST", "TAX.TEST"}, 2020, 2021)
            self.assertEqual(audit["actual_numeric_observations"], 4)
            self.assertTrue((root / "selected.audit.json").is_file())
            with (root / "export/wdi_country_year_long.csv").open("a") as f:
                f.write("USA,2020,GDP.TEST,999\\n")
            with self.assertRaisesRegex(ValueError, "SHA256/size"):
                subset(root / "export", root / "tampered.csv", {"USA"},
                       {"GDP.TEST"}, 2020, 2021)

    def test_actual_public_data_build_has_complete_provenance(self):
        data = Path(__file__).resolve().parents[1] / "atlas_public" / "data"
        with tempfile.TemporaryDirectory() as tmp:
            report = build_research_exports(data, Path(tmp))
            self.assertEqual(report["numeric_observations"], 300240)
            self.assertEqual(report["indicators"], 32)
            self.assertEqual(report["country_or_territory_codes_with_data"], 217)
            self.assertEqual(report["aggregate_codes_with_data"], 44)
            self.assertEqual(report["source_run_id"], 42581151428)
            self.assertGreater(report["country_year_records"], 10000)
            self.assertTrue(report["country_aggregates_never_mixed"])
            self.assertEqual(report["sqlite_research_edition"]["all_observations"], 300240)
            self.assertEqual(report["sqlite_research_edition"]["indicators"], 32)
            self.assertLess(report["sqlite_research_edition"]["archive_bytes"], 25_000_000)
            self.assertIn("atlas_wdi_research.sqlite.zip", report["output_files"])
            self.assertFalse((Path(tmp)/"atlas_wdi_research.sqlite").exists())


if __name__ == "__main__":
    unittest.main()
