"""Fetch and audit pre-1960 Maddison Project economic estimates via OWID.

Separate reconstructed historical series from modern World Bank observations.
Uses only the Python standard library. This is *not* a causal analysis.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import time
import urllib.request
from pathlib import Path

BASE = "https://ourworldindata.org/grapher"
SOURCES = {
    "gdp_per_capita": "gdp-per-capita-maddison-project-database",
    "gdp": "gdp-maddison-project-database",
}
SUFFIX = "?v=1&csvType=full&useColumnShortNames=false"
CITATION = (
    "Bolt, J. and van Zanden, J. L. (2024). Maddison style estimates of "
    "the evolution of the world economy: A new 2023 update. "
    "Journal of Economic Surveys. DOI: 10.1111/joes.12618. "
    "Original source contributions may require additional citations."
)


def fetch_bytes(url: str, *, retries: int = 3, timeout: int = 60) -> bytes:
    """Download publicly available bytes with bounded retries."""
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "OpenEconomicResearch/0.8 (public research)"}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception:
            if attempt + 1 == retries:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def parse_series(raw: bytes, indicator: str, *, start: int, end: int):
    """Normalize numeric data without filling missing values or changing units.

    OWID grapher CSV format: Entity,Code,Year,<one value column>.
    Three-letter codes are candidate country and historical-polity identifiers;
    verifying state boundaries and ISO validity is a separate research task.
    """
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    required = {"Entity", "Code", "Year"}
    if not required.issubset(headers):
        raise ValueError(f"Unexpected OWID CSV headers: {headers!r}")
    value_fields = [x for x in headers if x not in required and not x.strip().endswith("(Annotations)")]]
    if len(value_fields) != 1:
        raise ValueError(f"Expected one numeric value column, got {value_fields!r}")
    value_field = value_fields[0]
    values = []
    missing, excluded = 0, 0
    for r in reader:
        code = (r.get("Code") or "").strip()
        yearstr = (r.get("Year") or "").strip()
        if not yearstr.isdigit():
            excluded += 1
            continue
        year = int(yearstr)
        if not start <= year <= end:
            continue
        # Aggregates often have OWID_* codes. Do not assert that every 3-letter
        # code represents a modern sovereign country; further mapping is needed.
        if len(code) != 3 or not code.isalpha():
            excluded += 1
            continue
        raw_value = (r.get(value_field) or "").strip()
        if not raw_value:
            missing += 1
            continue
        try:
            number = float(raw_value)
            if not math.isfinite(number):
                raise ValueError("non-finite")
        except ValueError:
            missing += 1
            continue
        values.append({
            "country_or_polity_code": code.upper(),
            "entity_name": (r.get("Entity") or "").strip(),
            "year": year,
            "indicator": indicator,
            "value": number,
            "unit": "source_defined_see_metadata",
            "source": "Maddison Project Database 2023 via Our World in Data",
            "observation_kind": "historical_reconstruction_or_estimate",
        })
    return values, {"source_value_column": value_field, "missing": missing, "excluded": excluded}


def run(out: str | Path, *, start: int = 1, end: int = 1959,
        series: tuple[str, ...] = ("gdp_per_capita", "gdp"),
        fetch=fetch_bytes) -> dict:
    if not 1 <= start <= end <= 1959:
        raise ValueError("Use historical dates 1–1959; modern WDI is kept separate")
    unknown = set(series) - set(SOURCES)
    if unknown or not series:
        raise ValueError(f"Invalid series: {unknown}")
    out = Path(out)
    raw_dir = out / "raw"
    processed_dir = out / "processed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    records = []
    manifest = []
    for name in series:
        slug = SOURCES[name]
        url = f"{BASE}/{slug}.csv{SUFFIX}"
        metadata_url = f"{BASE}/{slug}.metadata.json{SUFFIX}"
        raw = fetch(url)
        metadata_raw = fetch(metadata_url)
        # Fail on HTML/error pages masquerading as metadata.
        json.loads(metadata_raw)
        filename = f"{slug}.csv"
        metadata_filename = f"{slug}.metadata.json"
        (raw_dir / filename).write_bytes(raw)
        (raw_dir / metadata_filename).write_bytes(metadata_raw)
        normalized, stats = parse_series(raw, name, start=start, end=end)
        records.extend(normalized)
        manifest.append({
            "series": name,
            "csv_url": url,
            "metadata_url": metadata_url,
            "raw_file": str(Path("raw") / filename),
            "metadata_file": str(Path("raw") / metadata_filename),
            "csv_sha256": hashlib.sha256(raw).hexdigest(),
            "metadata_sha256": hashlib.sha256(metadata_raw).hexdigest(),
            "downloaded_at_utc": stamp,
            "observations_selected": len(normalized),
            **stats,
        })
    seen = {}
    conflicts = []
    for r in records:
        key = (r["country_or_polity_code"], r["year"], r["indicator"])
        if key in seen and seen[key]["value"] != r["value"]:
            conflicts.append(key)
        seen[key] = r
    if conflicts:
        raise ValueError(f"Conflicting historical values: {conflicts[:5]}")
    if not seen:
        raise ValueError("No historical observations found; check source URLs")
    rows = sorted(seen.values(), key=lambda r: (r["indicator"], r["country_or_polity_code"], r["year"]))
    fields = ["country_or_polity_code", "entity_name", "year", "indicator", "value", "unit", "source", "observation_kind"]
    csv_file = processed_dir / "historical_panel.csv"
    with csv_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    report = {
        "status": "download_complete_not_causally_validated",
        "run_at_utc": stamp,
        "requested_years": [start, end],
        "earliest_observed_year": min(r["year"] for r in rows),
        "latest_observed_year": max(r["year"] for r in rows),
        "observations": len(rows),
        "codes_including_historical_polities": len({r["country_or_polity_code"] for r in rows}),
        "indicator_count": len(series),
        "by_indicator": {k: sum(r["indicator"] == k for r in rows) for k in series},
        "conflicts": conflicts,
        "disclaimer": "Sparse historical estimates; not payroll jobs, policy shocks, or causal effects. No temporal interpolation performed by this program. Dataset producers may use reconstruction or interpolation.",
        "citation": CITATION,
        "license": "Maddison Project Database 2023: CC BY 4.0. Respect original paper attributions.",
        "manifest": manifest,
    }
    (out / "run_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/historical")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=1959)
    parser.add_argument("--series", choices=("all", *SOURCES), default="all")
    args = parser.parse_args()
    series = tuple(SOURCES) if args.series == "all" else (args.series,)
    report = run(args.out, start=args.start, end=args.end, series=series)
    print(json.dumps({k: v for k, v in report.items() if k != "manifest"}, indent=2))


if __name__ == "__main__":
    main()
