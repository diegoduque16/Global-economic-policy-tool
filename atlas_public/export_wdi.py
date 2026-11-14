#!/usr/bin/env python3
"""Make a small, attribution-preserving public WDI edition from a VERIFIED Atlas source snapshot.

Input is the existing 2026-11-09 World Bank source artifact: not a new collector.
Never exports other warehouse families, source-native datasets, raw API payloads,
private files, credentials, or a SQLite database. Runs fail closed on any source
SHA/numeric/provenance inconsistency. Output is versioned annual series JSON.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.atlas_data_watchdog import validate_wdi_download  # existing verified source checker

EXPECTED_RUN = 42581151428
EXPECTED_ROWS = 300240
EXPECTED_SERIES = 32
EXPECTED_GEOGRAPHIES = 261
OFFICIAL_TERMS = "https://www.worldbank.org/en/about/legal/terms-of-use-for-datasets"
OFFICIAL_DATA = "https://datatopics.worldbank.org/world-development-indicators/"
WORLD_BANK = "https://api.worldbank.org/v2/country"
API_UA = "WorldEconomicAtlas-VerifiedDataPublisher/1.0 (public WDI edition)"


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_definitions():
    groups = json.loads((ROOT / "atlas_public" / "wdi_20261109_definitions.json").read_text(encoding="utf-8"))
    definitions = {}
    for group, descriptions in groups.items():
        for code, label in descriptions.items():
            if code in definitions:
                raise ValueError("Duplicate WDI indicator in Atlas definitions")
            definitions[code] = {"code": code, "title": label, "topic": group}
    if len(definitions) != EXPECTED_SERIES:
        raise ValueError("Unexpected number of approved Atlas WDI indicators")
    return definitions


def provider_geographies():
    """Source the World Bank's own economy/aggregate classification, never ISO-guess."""
    original = []
    page = 1
    pages = None
    total = None
    records = {}
    while page <= 12:
        url = WORLD_BANK + f"?format=json&per_page=500&page={page}"
        error = None
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": API_UA})
                with urllib.request.urlopen(req, timeout=45) as response:
                    raw = response.read()
                break
            except Exception as exc:
                error = exc
                if attempt < 3:
                    time.sleep(2 * (attempt + 1))
        else:
            raise RuntimeError("Official World Bank geography metadata unavailable") from error
        payload = json.loads(raw)
        if (not isinstance(payload, list) or len(payload) != 2
                or not isinstance(payload[0], dict) or not isinstance(payload[1], list)):
            raise ValueError("Invalid World Bank geography metadata response")
        meta, data = payload
        try:
            num, count, grand = (int(meta[key]) for key in ("page", "pages", "total"))
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError("Missing World Bank geography pagination") from exc
        if num != page or not 1 <= count <= 12 or not 1 <= grand <= 3000:
            raise ValueError("World Bank geography pagination changed")
        if pages is None:
            pages, total = count, grand
        elif pages != count or total != grand:
            raise ValueError("World Bank geography release changed during download")
        original.append({"url": url, "sha256": hashlib.sha256(raw).hexdigest(), "page": page})
        for rec in data:
            code = rec.get("id")
            region = rec.get("region") or {}
            if not isinstance(code, str) or not re.fullmatch("[A-Z]{3}", code) or code in records:
                raise ValueError("Invalid or duplicate provider geography")
            rid = str(region.get("id") or "")
            region_name = str(region.get("value") or "")
            if rid == "NA" and region_name.casefold() == "aggregates":
                kind = "aggregate"
            elif rid and rid != "NA" and region_name:
                kind = "country_or_territory"
            else:
                kind = "unresolved"
            records[code] = {"name": str(rec.get("name") or code), "kind": kind,
                             "region": region_name}
        if page == pages:
            break
        page += 1
    if len(records) != total:
        raise ValueError("Incomplete World Bank country metadata records")
    return records, original


def publish(source_dir, output_dir):
    source_dir = Path(source_dir)
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.rglob("*")):
        raise ValueError("Refusing to overwrite an existing public release without a clean output")
    approved = load_definitions()
    report = json.loads((source_dir / "run_report.json").read_text(encoding="utf-8"))
    if report.get("observations") != EXPECTED_ROWS or report.get("indicator_count") != EXPECTED_SERIES:
        raise ValueError("Unexpected source run count; do not silently change public scope")
    # The original Atlas watchdog independently verifies EVERY raw API row and
    # its SHA binding to each normalized country-year-indicator-value record.
    validated = validate_wdi_download(source_dir)
    if (validated["observations"] != EXPECTED_ROWS or
            validated["indicators_with_raw_pages"] != EXPECTED_SERIES or
            not validated["all_output_numeric_keys_and_values_matched_original_raw"] or
            validated["geography_codes"] != EXPECTED_GEOGRAPHIES):
        raise ValueError("WDI numeric source validation failed")
    provider, metadata_pages = provider_geographies()
    grouped = {code: [] for code in approved}
    geos = set()
    total = 0
    with (source_dir / "processed" / "wdi_panel.csv").open(encoding="utf-8", newline="") as src:
        for row in csv.DictReader(src):
            code = row["indicator"]
            geo = row["country_iso3"]
            if code not in approved or geo not in provider:
                raise ValueError("Indicator or geography not present in approved source metadata")
            year = int(row["year"])
            if not 1960 <= year <= 2026:
                raise ValueError("Unexpected WDI source year")
            value = float(row["value"])
            if not -float("inf") < value < float("inf"):
                raise ValueError("Nonfinite WDI source value")
            grouped[code].append([geo, year, value])
            geos.add(geo)
            total += 1
    if total != EXPECTED_ROWS or len(geos) != EXPECTED_GEOGRAPHIES:
        raise ValueError("Public WDI export source has missing records")
    geo_types = {geo: provider[geo]["kind"] for geo in geos}
    if any(kind == "unresolved" for kind in geo_types.values()):
        raise ValueError("Provider geography metadata cannot classify all public codes")
    # The full public WDI source scope has previously been independently
    # classified as 217 country/territory codes and 44 aggregates.
    if (sum(v == "country_or_territory" for v in geo_types.values()) != 217 or
            sum(v == "aggregate" for v in geo_types.values()) != 44):
        raise ValueError("Country-vs-aggregate classification regressed")
    series_dir = output_dir / "series"
    series_dir.mkdir(parents=True)
    records = []
    for code, data in grouped.items():
        data.sort(key=lambda r: (r[1], r[0]))
        if not data:
            raise ValueError("Empty approved WDI indicator")
        years = [r[1] for r in data]
        places = {r[0] for r in data}
        definition = approved[code]
        payload = {"code": code, "title": definition["title"],
                   "topic": definition["topic"], "source": "World Bank WDI",
                   "unit": "source_defined",
                   "rows": data}
        (series_dir / (code + ".json")).write_text(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8")
        records.append({**definition, "rows": len(data), "first_year": min(years),
                        "last_year": max(years), "geographies": len(places)})
    # Read the existing Atlas registry only as planning context; its catalog
    # entries do not imply that additional provider data are publicly released.
    registry_path = ROOT / "data" / "registry" / "registry_summary.json"
    broad = {}
    if registry_path.is_file():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        evidence = registry.get("priority_p0_evidence_summary") or {}
        broad = {
            "source_descriptions": registry.get("source_count", 0),
            "priority_sources": (registry.get("by_priority") or {}).get("P0", 0),
            "exact_series_audits_verified_by_registry":
                evidence.get("exact_series_key_audits_completed", 0),
        }
    catalog = {
        "schema_version": 1,
        "broader_catalog": broad,
        "public_edition": True,
        "project": "World Economic Atlas",
        "public_dataset": "World Bank World Development Indicators — selected indicators",
        "source_run_id": EXPECTED_RUN,
        "source_downloaded_at_utc": report["downloaded_at_utc"],
        "input_manifest_sha256": digest(source_dir / "run_report.json"),
        "input_panel_sha256": digest(source_dir / "processed" / "wdi_panel.csv"),
        "provider_geography_metadata": metadata_pages,
        "published_at_utc": datetime.now(timezone.utc).isoformat(),
        "license": {
            "dataset": "World Bank World Development Indicators",
            "license": "CC BY 4.0 unless indicator-specific metadata states otherwise",
            "terms": OFFICIAL_TERMS,
            "citation": "World Bank, World Development Indicators, accessed 2026-11-09; transformed into source-preserving, annual JSON for World Economic Atlas.",
            "source": OFFICIAL_DATA,
            "third_party_exception": "Check the original provider's indicator metadata for any third-party rights restrictions.",
        },
        "summary": {
            "observations": total, "series": EXPECTED_SERIES,
            "geographies": len(geos),
            "country_or_territory_codes": 217, "aggregate_codes": 44,
            "canonical_warehouse_integrated": False,
            "source_separated_numeric_checks_passed": True,
            "cross_provider_harmonized": False,
            "research_ready": False,
        },
        "series": sorted(records, key=lambda r: (r["topic"], r["title"])),
        "geographies": {code: provider[code] for code in sorted(geos)},
    }
    (output_dir / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8")
    (output_dir / "LICENSE-ATTRIBUTION.txt").write_text(
        "World Bank, World Development Indicators (WDI), accessed November 9, 2026.\n"
        "Public WDI source snapshot transformed into annual indicator JSON by World Economic Atlas.\n"
        "The World Bank WDI dataset is generally licensed CC BY 4.0; individual third-party\n"
        "indicator exceptions may apply. See " + OFFICIAL_TERMS + "\n"
        "This export retains source units. It is not a harmonized Atlas warehouse.\n",
        encoding="utf-8")
    return catalog


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, required=True, help="Extracted original WDI GitHub run artifact")
    ap.add_argument("--output", type=Path, default=ROOT / "atlas_public" / "data")
    args = ap.parse_args()
    output = publish(args.source, args.output)
    print(json.dumps({"published_observations": output["summary"]["observations"],
                      "series": output["summary"]["series"],
                      "geographies": output["summary"]["geographies"],
                      "raw_sha_verified": True, "canonical_warehouse_unchanged": True}))


if __name__ == "__main__":
    main()
