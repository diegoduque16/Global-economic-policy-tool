"""Build Atlas's public BLS JOLTS chart files from its SHA-verified release ZIP.

This transforms only the already-published, 252710-row source-native BLS CSV.
It does not open or upload private databases, raw JSON or other providers.
"""
from __future__ import annotations

from collections import defaultdict
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

RELEASE_COUNT = 252_710
SOURCE_COUNT = 830
MEASURES = {
    "JO": "Job openings", "HI": "Hires", "TS": "Total separations",
    "QU": "Quits", "LD": "Layoffs and discharges",
}

def build(data: Path, output: Path) -> dict | None:
    root = Path(data) / "bls"
    manifest_file = root / "bls_jolts_manifest.json"
    archive = root / "bls_jolts_source_native.csv.zip"
    if not manifest_file.exists() and not archive.exists():
        return None
    if not manifest_file.is_file() or not archive.is_file():
        raise ValueError("Partial public BLS release must not be charted")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if (manifest.get("release_type") != "public_bls_source_native_only"
        or manifest.get("series_count") != SOURCE_COUNT
        or manifest.get("source_native_records") != RELEASE_COUNT
        or manifest.get("finite_numeric_values") != RELEASE_COUNT
        or manifest.get("not_harmonized") is not True
        or hashlib.sha256(archive.read_bytes()).hexdigest() != manifest.get("csv_zip_sha256")):
        raise ValueError("BLS public source provenance or counts failed")
    target = Path(output) / "data" / "bls" / "charts"
    target.mkdir(parents=True, exist_ok=True)
    grouped = defaultdict(list)
    source_details = {}
    with zipfile.ZipFile(archive) as z:
        if set(z.namelist()) != {
            "bls_jolts_source_native.csv", "CITATION_AND_USAGE.txt",
            "ORIGINAL_SOURCE_CATALOG.json"
        }:
            raise ValueError("Unexpected public BLS archive members")
        sources = json.loads(z.read("ORIGINAL_SOURCE_CATALOG.json"))
        if not isinstance(sources, list) or len(sources) != SOURCE_COUNT:
            raise ValueError("Original BLS source registry mismatch")
        for source in sources:
            sid = source["source_id"]
            if (not re.fullmatch(r"bls_jolts_[A-Za-z0-9_.-]+", sid)
                or sid in source_details or
                not re.fullmatch(r"[0-9a-f]{64}", source["source_sha256"])):
                raise ValueError("Invalid BLS source identity")
            source_details[sid] = source
        with z.open("bls_jolts_source_native.csv") as stream:
            rows = csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8"))
            if rows.fieldnames != [
                "source_id","original_month_key","period","geography_code",
                "series_code","industry_code","original_value_text"
            ]:
                raise ValueError("BLS archive column contract changed")
            count = 0
            for row in rows:
                sid = row["source_id"]
                period = row["period"]
                if (sid not in source_details or
                    not re.fullmatch(r"20[0-9]{2}-(0[1-9]|1[0-2])", period)
                    or row["original_month_key"] != period or
                    row["geography_code"] != "USA"):
                    raise ValueError("Unexpected original BLS month or geography")
                try:
                    value = float(row["original_value_text"])
                except ValueError as ex:
                    raise ValueError("Non-numeric BLS chart observation") from ex
                if not (-1e18 < value < 1e18):
                    raise ValueError("Invalid BLS numeric range")
                if row["series_code"] == "":
                    raise ValueError("Missing original series identifier")
                grouped[sid].append([period, value])
                count += 1
    if count != RELEASE_COUNT or len(grouped) != SOURCE_COUNT:
        raise ValueError("BLS chart rows do not match verified public release")
    catalog = []
    for i, (sid, detail) in enumerate(sorted(source_details.items())):
        observations = sorted(grouped[sid])
        if len(observations) != detail["original_rows"] or len({
            row[0] for row in observations
        }) != len(observations):
            raise ValueError("BLS chart has missing or duplicated original months")
        # Preserve official descriptive names whenever the receipt provides them.
        source_name = detail.get("source_name", "").strip()
        label = source_name if source_name and source_name != sid else "BLS JOLTS series"
        filename = f"{i:04d}.json"
        (target / filename).write_text(json.dumps({
            "source_id":sid,"source_name":label,"rows":observations
        },separators=(",",":"))+"\n",encoding="utf-8")
        catalog.append({
            "id":sid, "file":filename, "title":label,
            "rows":len(observations), "first_year":int(observations[0][0][:4]),
            "last_year":int(observations[-1][0][:4]),
            "original_series_id":sid.removeprefix("bls_jolts_"),
        })
    result = {
        "schema_version":1, "source":"U.S. Bureau of Labor Statistics — JOLTS",
        "source_url":"https://www.bls.gov/jlt/",
        "source_archive_sha256":manifest["source_archive_sha256"],
        "csv_zip_sha256":manifest["csv_zip_sha256"],
        "citation":manifest["citation"],"source_native":True,
        "frequency":"monthly","geography":"USA",
        "source_native_records":count,"series_count":len(catalog),
        "series":catalog,
    }
    (target / "catalog.json").write_text(
        json.dumps(result,separators=(",",":"))+"\n",encoding="utf-8")
    return result
