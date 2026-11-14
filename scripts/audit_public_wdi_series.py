#!/usr/bin/env python3
"""Audit real, published WDI series in the EXISTING Atlas source registry.

Checks the checked-in, already source-verified public WDI edition. Reports
series-level keys, numbers, geography types and SHA256; it NEVER asserts that
these records are integrated into the canonical warehouse, that all 125 catalog
sources are covered, or that original raw API pages are rechecked in this audit.
No network or private OneDrive access.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path


PUBLIC_DATA = Path("atlas_public/data")
BASELINE_DEFINITIONS = Path("atlas_public/wdi_20261109_definitions.json")
EXPECTED_RUN_ID = 42581151428
EXPECTED_OBSERVATIONS = 300240
EXPECTED_SERIES = 32
EXPECTED_COUNTRIES = 217
EXPECTED_AGGREGATES = 44

EVIDENCE_FIELDS = [
    "provider", "indicator_code", "indicator_title", "topic",
    "source_run_id", "source_downloaded_at_utc",
    "source_edition_file", "source_file_sha256",
    "verified_numeric_keys", "country_or_territory_observations",
    "aggregate_observations", "country_codes", "aggregate_codes",
    "first_year", "last_year", "source_unit",
    "verified_public_file_numeric_values", "checked_original_raw_pages_this_run",
    "canonical_warehouse_inclusion_verified", "exact_cross_provider_overlap_audited"
]


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as file:
        for part in iter(lambda: file.read(1024 * 1024), b""):
            checksum.update(part)
    return checksum.hexdigest()


def audit_public_wdi(data: Path = PUBLIC_DATA,
                     definitions: Path = BASELINE_DEFINITIONS) -> tuple[dict, list[dict]]:
    """Fail closed on any observed file/metadata mismatch or duplicate numeric key."""
    data = Path(data)
    catalog = json.loads((data / "catalog.json").read_text(encoding="utf-8"))
    scope = catalog.get("summary", {})
    expected = json.loads(Path(definitions).read_text(encoding="utf-8"))
    approved = {code: (topic, title) for topic, series in expected.items()
                for code, title in series.items()}
    if (catalog.get("public_edition") is not True
            or catalog.get("source_run_id") != EXPECTED_RUN_ID
            or scope.get("observations") != EXPECTED_OBSERVATIONS
            or scope.get("series") != EXPECTED_SERIES
            or scope.get("country_or_territory_codes") != EXPECTED_COUNTRIES
            or scope.get("aggregate_codes") != EXPECTED_AGGREGATES
            or scope.get("source_separated_numeric_checks_passed") is not True
            or scope.get("canonical_warehouse_integrated") is not False
            or scope.get("cross_provider_harmonized") is not False):
        raise ValueError("Unexpected WDI public source edition provenance/scope")
    catalog_entries = catalog.get("series", [])
    series_codes = [entry["code"] for entry in catalog_entries]
    if (len(series_codes) != EXPECTED_SERIES or len(set(series_codes)) != EXPECTED_SERIES
            or set(series_codes) != set(approved)):
        raise ValueError("Published WDI catalog does not match pinned indicator definitions")
    series_dir = data / "series"
    if {p.name for p in series_dir.iterdir() if p.is_file()} != {
            code + ".json" for code in approved}:
        raise ValueError("Unexpected or missing public WDI numeric source files")
    geographies = catalog.get("geographies", {})
    if len(geographies) != EXPECTED_COUNTRIES + EXPECTED_AGGREGATES:
        raise ValueError("Unexpected public WDI geography classification")
    if (sum(g.get("kind") == "country_or_territory" for g in geographies.values()) != EXPECTED_COUNTRIES
            or sum(g.get("kind") == "aggregate" for g in geographies.values()) != EXPECTED_AGGREGATES):
        raise ValueError("World Bank published geography classifications incomplete")

    rows = []
    totals = 0
    actual_countries, actual_aggregates = set(), set()
    hashes = {}
    for entry in sorted(catalog_entries, key=lambda x: x["code"]):
        code = entry["code"]
        file = series_dir / (code + ".json")
        blob = json.loads(file.read_text(encoding="utf-8"))
        if (blob.get("code") != code
                or blob.get("topic") != approved[code][0]
                or blob.get("title") != approved[code][1]
                or blob.get("unit") != "source_defined"
                or not isinstance(blob.get("rows"), list)):
            raise ValueError("WDI series label or unit not matched to pinned public edition: " + code)
        seen, c_geos, a_geos, years = set(), set(), set(), []
        count_c = count_a = 0
        for source in blob["rows"]:
            if not isinstance(source, list) or len(source) != 3:
                raise ValueError("Malformed numeric WDI observation: " + code)
            geo, year, value = source
            if (geo not in geographies or type(year) is not int or not 1800 <= year <= 2100
                    or isinstance(value, bool) or type(value) not in (int, float)
                    or not math.isfinite(value)):
                raise ValueError("Invalid numeric WDI geography, year, or value: " + code)
            if (geo, year) in seen:
                raise ValueError("Duplicate public WDI geography-year-indicator key: " + code)
            seen.add((geo, year))
            years.append(year)
            kind = geographies[geo]["kind"]
            if kind == "country_or_territory":
                count_c += 1
                c_geos.add(geo)
                actual_countries.add(geo)
            elif kind == "aggregate":
                count_a += 1
                a_geos.add(geo)
                actual_aggregates.add(geo)
            else:
                raise ValueError("Unclassified WDI geography: " + geo)
        if (not seen or len(seen) != entry["rows"]
                or len(c_geos | a_geos) != entry["geographies"]
                or min(years) != entry["first_year"]
                or max(years) != entry["last_year"]):
            raise ValueError("WDI series key counts or coverage contradict catalog: " + code)
        totals += len(seen)
        hashes["series/" + code + ".json"] = digest(file)
        rows.append(dict(zip(EVIDENCE_FIELDS, [
            "World Bank WDI", code, entry["title"], entry["topic"],
            catalog["source_run_id"], catalog["source_downloaded_at_utc"],
            "atlas_public/data/series/" + code + ".json", hashes["series/" + code + ".json"],
            len(seen), count_c, count_a, len(c_geos), len(a_geos),
            min(years), max(years), "source_defined",
            "true", "false", "false", "false"
        ])))
    if (totals != EXPECTED_OBSERVATIONS or len(actual_countries) != EXPECTED_COUNTRIES
            or len(actual_aggregates) != EXPECTED_AGGREGATES):
        raise ValueError("WDI published numeric keys do not match verified source release")
    hashes["catalog.json"] = digest(data / "catalog.json")
    report = {
        "status": "PUBLIC_WDI_SOURCE_EDITION_KEYS_AND_VALUES_VERIFIED_NOT_CANONICAL",
        "source_run_id": EXPECTED_RUN_ID,
        "source_downloaded_at_utc": catalog["source_downloaded_at_utc"],
        "source_observation_count": totals,
        "source_series_count": len(rows),
        "country_or_territory_codes": len(actual_countries),
        "aggregate_codes": len(actual_aggregates),
        "input_catalog_sha256": hashes["catalog.json"],
        "series_file_sha256": hashes,
        "last_successful_collector_indicated_by_pinned_edition": EXPECTED_RUN_ID,
        "original_api_pages_independently_rechecked_by_this_registry_audit": False,
        "canonical_inclusion_independently_verified": False,
        "cross_provider_deduplication_completed": False,
        "complete_world_bank_indicator_universe": False,
        "research_ready_catalog_source": False,
        "nonadditive_with_historical_canonical_and_native_layers": True,
        "next_gate": "VERIFY_NEW_WDI_SOURCE_RUN_THEN_KEYED_OVERLAP_AND_CANONICAL_VINTAGE",
    }
    return report, rows


def write_series_csv(path: Path, rows: list[dict]) -> None:
    with Path(path).open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=EVIDENCE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
