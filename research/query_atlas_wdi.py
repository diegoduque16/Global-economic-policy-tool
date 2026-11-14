#!/usr/bin/env python3
"""Reproducibly select actual WDI source values from an Atlas research release.

Accepts only country/territory long CSV downloaded from the verified Atlas
public research export. Checks exact input SHA256 from research_manifest.json
before producing any subset; no modelled/missing values are filled.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def split_codes(value: str, pattern: str, what: str) -> set[str]:
    codes = {x.strip().upper() for x in value.split(",") if x.strip()}
    if not codes or any(not re.fullmatch(pattern, x) for x in codes):
        raise ValueError("Invalid " + what + " selector; use comma-separated source codes")
    return codes


def subset(directory: Path, destination: Path, countries: set[str],
           indicators: set[str], start: int, end: int) -> dict:
    directory, destination = Path(directory), Path(destination)
    manifest = json.loads((directory / "research_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("includes_only_actual_source_observations") is not True or \
       manifest.get("country_aggregates_never_mixed") is not True or \
       manifest.get("causal_inference_ready") is not False:
        raise ValueError("Research release does not carry required source integrity assertions")
    input_file = directory / "wdi_country_year_long.csv"
    target = manifest["output_files"].get(input_file.name) or {}
    if not re.fullmatch(r"[a-f0-9]{64}", str(target.get("sha256", ""))) or \
       input_file.stat().st_size != target.get("bytes") or \
       digest(input_file) != target["sha256"]:
        raise ValueError("Atlas research source CSV failed SHA256/size provenance check")
    if start > end or start < 1800 or end > 2100:
        raise ValueError("Invalid research year range")
    if destination.resolve() == input_file.resolve():
        raise ValueError("Refusing to overwrite verified Atlas source data")
    output = []
    seen = set()
    with input_file.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != ["geography_code", "year", "indicator_code", "value"]:
            raise ValueError("Unexpected WDI country-year column definitions")
        for row in reader:
            country, code = row["geography_code"], row["indicator_code"]
            if country not in countries or code not in indicators:
                continue
            year = int(row["year"])
            if not start <= year <= end:
                continue
            key = (country, year, code)
            if key in seen:
                raise ValueError("Duplicate source country/year/indicator: " + str(key))
            seen.add(key)
            output.append((country, year, code, row["value"]))
    if not output:
        raise ValueError("No source values match these selectors; inspect indicator coverage")
    output.sort(key=lambda x: (x[0], x[2], x[1]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(("geography_code", "year", "indicator_code", "value"))
        writer.writerows(output)
    audit = {
        "source_release_run_id": manifest["source_run_id"],
        "input_sha256": target["sha256"],
        "selection_file_sha256": digest(destination),
        "countries": sorted(countries),
        "indicators": sorted(indicators),
        "year_start": start, "year_end": end,
        "actual_numeric_observations": len(output),
        "descriptive_only_not_causal": True,
        "missing_values_not_imputed": True,
    }
    audit_path = destination.with_suffix(".audit.json")
    audit_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    return audit


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--release", type=Path, required=True, help="Folder containing downloaded research CSV and manifest")
    ap.add_argument("--countries", required=True, help="World Bank three-letter codes, e.g. USA,GHA")
    ap.add_argument("--indicators", required=True, help="World Bank series codes separated by commas")
    ap.add_argument("--from-year", type=int, default=1960)
    ap.add_argument("--to-year", type=int, default=2025)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    countries = split_codes(args.countries, r"[A-Z]{3}", "countries")
    indicators = split_codes(args.indicators, r"[A-Z0-9._]{2,80}", "indicators")
    audit = subset(args.release, args.out, countries, indicators, args.from_year, args.to_year)
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
