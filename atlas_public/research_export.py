#!/usr/bin/env python3
"""Verified, source-preserving research exports of the existing public WDI edition.

Only approved World Economic Atlas data/ files are read. No HTTP requests,
extra providers, modelled values, imputation, annualisation, or causal analysis.
Country/territory data and World Bank aggregates are exported separately.
"""
from __future__ import annotations

from collections import defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path

SOURCE_NAME = "World Bank World Development Indicators"
FIELDS_LONG = ("geography_code", "year", "indicator_code", "value")
COVERAGE_FIELDS = (
    "indicator_code", "indicator_title", "topic",
    "country_observations", "aggregate_observations", "countries_with_data",
    "earliest_country_year", "latest_country_year", "official_definition_url"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def csv_write(path: Path, fields: tuple | list, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(fields)
        writer.writerows(rows)


def build_research_exports(source: Path, destination: Path) -> dict:
    """Fail closed if any per-series source values contradict the public catalog."""
    source, destination = Path(source), Path(destination)
    catalog_file = source / "catalog.json"
    catalog = json.loads(catalog_file.read_text(encoding="utf-8"))
    summary = catalog.get("summary") or {}
    if catalog.get("public_edition") is not True or summary.get(
        "source_separated_numeric_checks_passed"
    ) is not True:
        raise ValueError("Research export requires an independently verified public WDI edition")
    if summary.get("cross_provider_harmonized") is not False:
        raise ValueError("Research export must not claim cross-provider harmonization")

    definitions = catalog["series"]
    geographies = catalog["geographies"]
    expected_codes = {entry["code"] for entry in definitions}
    actual_codes = {path.stem for path in (source / "series").glob("*.json")}
    if not expected_codes or expected_codes != actual_codes or len(definitions) != len(expected_codes):
        raise ValueError("Public WDI indicator files or definitions do not match catalog")
    if len(geographies) != summary["geographies"]:
        raise ValueError("Public World Bank geography metadata count mismatch")

    destination.mkdir(parents=True, exist_ok=True)
    country_wide = defaultdict(dict)
    country_coverage = defaultdict(lambda: {"indicators": set(), "years": set(), "points": 0})
    indicator_coverage = []
    input_hashes = {"catalog.json": sha256(catalog_file)}
    total = 0
    countries = aggregates = 0
    country_keys = set()
    aggregate_keys = set()

    country_long_path = destination / "wdi_country_year_long.csv"
    aggregates_path = destination / "wdi_aggregate_year_long.csv"
    with country_long_path.open("w", newline="", encoding="utf-8") as country_file, \
         aggregates_path.open("w", newline="", encoding="utf-8") as aggregates_file:
        country_writer = csv.writer(country_file)
        aggregate_writer = csv.writer(aggregates_file)
        country_writer.writerow(FIELDS_LONG)
        aggregate_writer.writerow(FIELDS_LONG)
        for definition in sorted(definitions, key=lambda x: x["code"]):
            code = definition["code"]
            path = source / "series" / (code + ".json")
            input_hashes["series/" + code + ".json"] = sha256(path)
            panel = json.loads(path.read_text(encoding="utf-8"))
            if panel.get("code") != code or not isinstance(panel.get("rows"), list):
                raise ValueError("WDI indicator source file has wrong code or missing rows: " + code)
            if panel.get("title") != definition["title"] or panel.get("topic") != definition["topic"]:
                raise ValueError("WDI indicator definitions differ from approved catalog: " + code)
            if panel.get("unit") != "source_defined":
                raise ValueError("WDI data must retain provider-defined units: " + code)
            seen = set()
            c_count = a_count = 0
            c_geos = set()
            first = last = None
            for record in panel["rows"]:
                if not isinstance(record, list) or len(record) != 3:
                    raise ValueError("Malformed source observation in " + code)
                geo, year, value = record
                if geo not in geographies or type(year) is not int or not 1800 <= year <= 2100:
                    raise ValueError("Invalid provider geography/year for " + code)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("Nonfinite or nonnumeric source value in " + code)
                key = (geo, year)
                if key in seen:
                    raise ValueError("Duplicate WDI year/geography/indicator key in " + code)
                seen.add(key)
                total += 1
                kind = geographies[geo]["kind"]
                if kind == "country_or_territory":
                    country_writer.writerow((geo, year, code, value))
                    country_wide[(geo, year)][code] = value
                    country_coverage[geo]["indicators"].add(code)
                    country_coverage[geo]["years"].add(year)
                    country_coverage[geo]["points"] += 1
                    c_count += 1
                    c_geos.add(geo)
                    first = year if first is None else min(first, year)
                    last = year if last is None else max(last, year)
                    countries += 1
                    country_keys.add(geo)
                elif kind == "aggregate":
                    aggregate_writer.writerow((geo, year, code, value))
                    a_count += 1
                    aggregates += 1
                    aggregate_keys.add(geo)
                else:
                    raise ValueError("Unknown World Bank geography classification: " + geo)
            if len(seen) != definition["rows"] or not seen:
                raise ValueError("WDI observation count differs from verified catalog: " + code)
            if min(y for _, y in seen) != definition["first_year"] or \
               max(y for _, y in seen) != definition["last_year"]:
                raise ValueError("WDI indicator year coverage differs from verified catalog: " + code)
            if len({x for x, _ in seen}) != definition["geographies"]:
                raise ValueError("WDI indicator geography coverage differs from verified catalog: " + code)
            indicator_coverage.append((
                code, definition["title"], definition["topic"], c_count, a_count,
                len(c_geos), first if first is not None else "", last if last is not None else "",
                "https://data.worldbank.org/indicator/" + code
            ))

    if total != summary["observations"] or countries + aggregates != total:
        raise ValueError("Research CSV observations do not match verified public WDI edition")
    if len(country_keys) != summary["country_or_territory_codes"] or \
       len(aggregate_keys) != summary["aggregate_codes"]:
        raise ValueError("Country-vs-aggregate source release coverage changed")

    codes = sorted(expected_codes)
    wide_fields = ["geography_code", "country_name", "region", "year", *codes]
    csv_write(
        destination / "wdi_country_year_wide.csv", wide_fields,
        (
            [geo, geographies[geo]["name"], geographies[geo]["region"].strip(), year]
            + [values.get(code, "") for code in codes]
            for (geo, year), values in sorted(country_wide.items())
        )
    )
    csv_write(destination / "wdi_indicator_coverage.csv", COVERAGE_FIELDS, indicator_coverage)
    csv_write(
        destination / "wdi_country_coverage.csv",
        ["geography_code", "name", "region", "indicators_with_data", "observed_country_years",
         "observations", "earliest_observed_year", "latest_observed_year"],
        (
            [geo, geographies[geo]["name"], geographies[geo]["region"].strip(),
             len(info["indicators"]), len(info["years"]), info["points"],
             min(info["years"]), max(info["years"])]
            for geo, info in sorted(country_coverage.items())
        )
    )
    csv_write(
        destination / "wdi_variable_dictionary.csv",
        ["indicator_code", "title", "topic", "unit", "source", "official_definition_url"],
        (
            [code, next(x["title"] for x in definitions if x["code"] == code),
             next(x["topic"] for x in definitions if x["code"] == code),
             "source_defined", SOURCE_NAME, "https://data.worldbank.org/indicator/" + code]
            for code in codes
        )
    )
    output_hashes = {}
    for path in sorted(destination.glob("*.csv")):
        if path.stat().st_size > 25_000_000:
            raise ValueError("Research CSV exceeds safe Cloudflare Pages asset limit: " + path.name)
        output_hashes[path.name] = {
            "sha256": sha256(path), "bytes": path.stat().st_size
        }
    report = {
        "schema_version": 1,
        "release_source": "World Economic Atlas existing verified public World Bank edition",
        "provider": SOURCE_NAME,
        "source_run_id": catalog["source_run_id"],
        "downloaded_at_utc": catalog["source_downloaded_at_utc"],
        "input_panel_sha256": catalog["input_panel_sha256"],
        "input_files_sha256": input_hashes,
        "output_files": output_hashes,
        "indicators": len(codes),
        "geography_codes": len(geographies),
        "country_or_territory_codes_with_data": len(country_keys),
        "aggregate_codes_with_data": len(aggregate_keys),
        "numeric_observations": total,
        "country_numeric_observations": countries,
        "aggregate_numeric_observations": aggregates,
        "country_year_records": len(country_wide),
        "source_units_retained": True,
        "country_aggregates_never_mixed": True,
        "unobserved_cells_are_blank_not_zero": True,
        "includes_only_actual_source_observations": True,
        "cross_provider_harmonized": False,
        "causal_inference_ready": False,
        "caveat": "Descriptive country-year research data only, not a causal treatment/outcome design."
    }
    (destination / "research_manifest.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (destination / "README.txt").write_text(
        "WORLD ECONOMIC ATLAS — WORLD BANK WDI RESEARCH DOWNLOAD\n\n"
        "Citation: World Bank, World Development Indicators (retrieved November 9, 2026),\n"
        "processed by World Economic Atlas from its separately verified public WDI source release.\n"
        "Official source: https://datatopics.worldbank.org/world-development-indicators/\n"
        "World Bank data terms: https://www.worldbank.org/en/about/legal/terms-of-use-for-datasets\n"
        "CC BY 4.0 generally applies to WDI, subject to specific third-party indicator rights.\n\n"
        "wdi_country_year_long.csv : actual source-defined numeric values by country / year / code.\n"
        "wdi_country_year_wide.csv : outer union of country/territory-years with source codes as\n"
        "  columns. A blank cell is missing, NOT zero. No filling or interpolation.\n"
        "wdi_aggregate_year_long.csv : World Bank regional/income aggregate series, kept separate.\n"
        "wdi_variable_dictionary.csv : labels, units and official series documentation.\n"
        "wdi_indicator_coverage.csv, wdi_country_coverage.csv : missingness and coverage summary.\n"
        "research_manifest.json : source-run IDs, original SHA256 fingerprints, export hashes,\n"
        "  counts and explicit research limitations.\n\n"
        "Read all indicator definitions and source-native units before comparing outcomes.\n"
        "Country/territory classification comes from the World Bank source catalog.\n"
        "These series are descriptive and do not identify effects of taxes, policy or AI.\n",
        encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(Path(__file__).resolve().parent / "data"))
    ap.add_argument("--out", default="research_export")
    args = ap.parse_args()
    result = build_research_exports(Path(args.data), Path(args.out))
    print(json.dumps({k: result[k] for k in (
        "numeric_observations", "country_numeric_observations",
        "aggregate_numeric_observations", "country_year_records"
    )}, indent=2))
