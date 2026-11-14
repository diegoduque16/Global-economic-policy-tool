"""Actual-source ZIP WDI recovery tests (synthetic provider-format source only)."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from open_data.wdi_bulk import BULK_URL, collect_bulk, scan_bulk, validate_bulk_download
from scripts.atlas_data_watchdog import validate_wdi_download


YEARS = list(range(1960, 2027))
CODES = ["NY.GDP.MKTP.KD.ZG", "SL.UEM.TOTL.ZS"]


def make_source(path: Path, *, missing_indicator=False, malformed=False,
                duplicate=False, nonfinite=False, data_member="WDIData.csv",
                nested=False):
    series = io.StringIO()
    writer = csv.writer(series)
    writer.writerow(["Series Code", "Indicator Name", "License Type"])
    for code in (CODES[:1] if missing_indicator else CODES):
        writer.writerow([code, code, "CC BY 4.0"])
    data = io.StringIO()
    writer = csv.writer(data)
    writer.writerow(["Country Name", "Country Code", "Indicator Name", "Indicator Code"] +
                    [str(year) + " [YR" + str(year) + "]" for year in YEARS])
    def source_row(geo, indicator, observed):
        row = [geo, geo, indicator, indicator]
        row.extend(observed.get(y, "") for y in YEARS)
        writer.writerow(row)
    source_row("USA", CODES[0], {2020: "0", 2021: "3.2"})
    source_row("WLD", CODES[0], {2020: "1.8"})
    source_row("USA", CODES[1], {2020: "4.4"})
    if duplicate:
        source_row("USA", CODES[0], {2020: "0", 2021: "3.2"})
    if malformed:
        source_row("GHA", CODES[0], {2021: "not a number"})
    if nonfinite:
        source_row("GHA", CODES[0], {2021: "NaN"})
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        prefix = "WorldBankWDI/" if nested else ""
        bundle.writestr(prefix + data_member, data.getvalue())
        bundle.writestr(prefix + "WDISeries.csv", series.getvalue())
        bundle.writestr(prefix + "WDICountry.csv", "Country Code,Region\nUSA,North America\n")


class OfficialBulkWDIChecks(unittest.TestCase):
    def test_private_bulk_release_source_values_and_zero_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zip_path = root / "source.zip"
            make_source(zip_path)
            output = root / "release"
            report = collect_bulk(output, CODES, source_zip=zip_path)
            self.assertEqual(report["observations"], 4)
            self.assertEqual(report["indicator_count"], 2)
            self.assertEqual(report["source_url"], BULK_URL)
            self.assertEqual(report["errors"], [])
            quality = validate_bulk_download(output)
            self.assertEqual(quality["observations"], 4)
            self.assertEqual(quality["verified_indicator_codes"], sorted(CODES))
            self.assertEqual(quality["geography_codes"], 2)
            self.assertTrue(quality["world_aggregate_present"])
            self.assertFalse(quality["canonical_integration_verified"])
            self.assertTrue(quality["all_output_numeric_keys_and_values_matched_original_raw"])
            self.assertEqual(quality["raw_pages"], 0)
            self.assertEqual(validate_wdi_download(output)["observations"], 4)
            with (output / "processed/wdi_panel.csv").open(newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(next(row for row in rows if row["country_iso3"] == "USA"
                                  and row["year"] == "2020" and
                                  row["indicator"] == CODES[0])["value"], "0.0")
            self.assertFalse((output / "raw").joinpath("WDI_CSV.zip.partial").exists())

    def test_reject_source_corruption_or_normalized_value_manipulation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zip_path = root / "original.zip"
            make_source(zip_path)
            output = root / "release"
            collect_bulk(output, CODES, source_zip=zip_path)
            panel = output / "processed/wdi_panel.csv"
            panel.write_text(panel.read_text().replace("3.2", "999"))
            with self.assertRaisesRegex(ValueError, "SHA256"):
                validate_wdi_download(output)
            collect_bulk(output, CODES, source_zip=zip_path)
            raw = output / "raw/WDI_CSV.zip"
            with raw.open("ab") as file:
                file.write(b"tampered")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                validate_wdi_download(output)

    def test_official_current_bulk_filename_and_archive_folder_layouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i, (name, nested) in enumerate((
                ("WDICSV.csv", False), ("WDICSV.csv", True),
                ("WDIData.csv", True),
            )):
                original = Path(tmp) / ("layout-" + str(i) + ".zip")
                make_source(original, data_member=name, nested=nested)
                parsed = scan_bulk(original, CODES, 1960, 2026)
                self.assertEqual(len(parsed["values"]), 4)
                output = Path(tmp) / ("release-" + str(i))
                collect_bulk(output, CODES, source_zip=original)
                self.assertEqual(validate_wdi_download(output)["observations"], 4)

    def test_reject_original_source_which_is_not_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "missing.zip"
            make_source(source, missing_indicator=True)
            with self.assertRaisesRegex(ValueError, "metadata missing"):
                scan_bulk(source, CODES, 1960, 2026)

    def test_reject_duplicate_native_keys_and_invalid_raw_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, options, expected in [
                ("dup", {"duplicate": True}, "Duplicate official WDI"),
                ("bad", {"malformed": True}, "Nonnumeric WDI"),
                ("nan", {"nonfinite": True}, "Nonfinite WDI"),
            ]:
                source = root / (name + ".zip")
                make_source(source, **options)
                with self.assertRaisesRegex(ValueError, expected):
                    scan_bulk(source, CODES, 1960, 2026)

    def test_reject_unmanifested_extra_and_duplicate_normalized_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "original.zip"
            make_source(source)
            output = root / "release"
            collect_bulk(output, CODES, source_zip=source)
            (output / "secret.txt").write_text("untracked")
            with self.assertRaisesRegex(ValueError, "Untracked"):
                validate_bulk_download(output)
            (output / "secret.txt").unlink()
            panel = output / "processed/wdi_panel.csv"
            with panel.open() as f:
                rows = f.readlines()
            with panel.open("a") as f:
                f.write(rows[1])
            with self.assertRaisesRegex(ValueError, "SHA256"):
                validate_bulk_download(output)


if __name__ == "__main__":
    unittest.main()
