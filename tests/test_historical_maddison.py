"""Offline tests for the historical data collector."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from open_data.historical_maddison import parse_series, run

SAMPLE = ("Entity,Code,Year,GDP per capita\n"
          "United States,USA,1820,1234.5\n"
          "United States,USA,1950,5000\n"
          "United Kingdom,GBR,1870,2500\n"
          "World,OWID_WRL,1820,999\n"
          "France,FRA,1870,\n"
          "United States,USA,1960,7000\n").encode()


class TestHistoricalMaddison(unittest.TestCase):
    def test_early_dates_and_zero_imputation_disabled(self):
        rows, info = parse_series(SAMPLE, "gdp_per_capita", start=1, end=1959)
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["year"] for r in rows], [1820, 1950, 1870])
        self.assertEqual(info["missing"], 1)
        self.assertNotIn("OWID_WRL", [r["country_or_polity_code"] for r in rows])

    def test_unexpected_schema_fails(self):
        with self.assertRaises(ValueError):
            parse_series(b"country,year,value\nUSA,1820,1\n", "gdp_per_capita", start=1, end=1959)

    def test_bounds_validation(self):
        with self.assertRaises(ValueError):
            run("not_used", start=1960, end=1965, fetch=lambda x: SAMPLE)

    def test_run_and_audit_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fake_fetch(url):
                return b'{"attribution": "mock metadata"}' if ".json" in url else SAMPLE
            report = run(tmp, start=1, end=1959, series=("gdp_per_capita",), fetch=fake_fetch)
            self.assertEqual(report["observations"], 3)
            self.assertEqual(report["earliest_observed_year"], 1820)
            self.assertEqual(len(report["manifest"]), 1)
            self.assertTrue((Path(tmp) / "processed/historical_panel.csv").exists())
            self.assertTrue((Path(tmp) / "raw/gdp-per-capita-maddison-project-database.csv").exists())
            self.assertEqual(json.loads((Path(tmp) / "run_report.json").read_text())["observations"], 3)

    def test_no_data_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fake_fetch(url):
                return b'{}' if ".json" in url else b"Entity,Code,Year,value\nUSA,2000,5\n"
            with self.assertRaises(ValueError):
                run(tmp, series=("gdp_per_capita",), fetch=fake_fetch)


if __name__ == "__main__":
    unittest.main()
