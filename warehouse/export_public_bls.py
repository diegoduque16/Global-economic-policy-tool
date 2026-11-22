"""Export ONLY original public-domain BLS JOLTS source-native data from a verified Atlas SQLite.

Fail closed if stored BLS coverage, source identities, or numerical structure
do not match the *previously verified* JOLTS edition. Never ship original
private raw-row JSON, unrelated source providers, credentials, or database files.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import zipfile

from warehouse.ilostat_private_chain import load_pointer, verify_parent_db

BLS_PREFIX = "bls_jolts_"
EXPECTED_BLS_SERIES = 830
EXPECTED_BLS_ROWS = 252_710
PUBLIC_LICENSE_URL = "https://www.bls.gov/bls/linksite.htm"
PROVIDER_URL = "https://www.bls.gov/jlt/"
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_RE = re.compile(r"bls_jolts_[A-Za-z0-9_.-]+\Z")
HEADER = ("source_id", "original_month_key", "period", "geography_code", "series_code",
          "industry_code", "original_value_text")
CITE = (
    "Original producer: U.S. Bureau of Labor Statistics (BLS).\n"
    "Dataset: Job Openings and Labor Turnover Survey (JOLTS).\n"
    "Official source: https://www.bls.gov/jlt/\n"
    "Source terms and public-domain status: https://www.bls.gov/bls/linksite.htm\n"
    "Suggested citation: U.S. Bureau of Labor Statistics, Job Openings and "
    "Labor Turnover Survey (JOLTS), [original series], [geography and period], "
    "retrieved in World Economic Atlas source-native edition [edition_id]; "
    "Atlas export [release_sha256], accessed [date].\n"
    "Original series identifiers are kept in the CSV for reproducibility.\n"
    "Source-native values are not harmonized across providers or converted to "
    "common units. Do not sum industries, states or measurement types blindly.\n"
)


def digest(path: Path) -> str:
    hash_ = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hash_.update(chunk)
    return hash_.hexdigest()


def export(db_path: Path, pointer_path: Path, output: Path) -> dict:
    db_path, pointer_path, output = Path(db_path), Path(pointer_path), Path(output)
    pointer = load_pointer(pointer_path)
    verify_parent_db(db_path, pointer)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / "bls_jolts_source_native.csv.zip"
    metadata = output / "bls_jolts_manifest.json"
    sources = []
    count = 0
    numeric_count = 0
    # SQLite opened read only; source-native values are copied, not modified.
    connection = sqlite3.connect("file:" + str(db_path.resolve()) + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        originals = connection.execute(
            "SELECT source_id, source_name, raw_sha256, original_records, indexed_records "
            "FROM native_sources WHERE substr(source_id,1,?)=? ORDER BY source_id",
            (len(BLS_PREFIX), BLS_PREFIX)
        ).fetchall()
        if len(originals) != EXPECTED_BLS_SERIES:
            raise ValueError("Unexpected BLS JOLTS source identities")
        for source_id, source_name, sha, original_rows, indexed_rows in originals:
            if (not SOURCE_RE.fullmatch(source_id) or not SHA_RE.fullmatch(sha) or
                    type(original_rows) is not int or original_rows < 1 or
                    original_rows != indexed_rows):
                raise ValueError("Invalid original BLS source receipt")
            sources.append({"source_id": source_id, "source_name": source_name,
                            "source_sha256": sha, "original_rows": original_rows})
        if sum(s["original_rows"] for s in sources) != EXPECTED_BLS_ROWS:
            raise ValueError("Unexpected verified BLS JOLTS observation count")
        # Use a single streaming ZIP member; fixed columns deliberately omit
        # raw_row_json, private original_scope and unrelated provider data.
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=7) as zipout:
            with zipout.open("bls_jolts_source_native.csv", "w") as member:
                with io.TextIOWrapper(member, encoding="utf-8", newline="", write_through=True) as text:
                    writer = csv.writer(text)
                    writer.writerow(HEADER)
                    current = None
                    for row in connection.execute(
                        "SELECT source_id,year_text,period_text,geo_code,series_code,"
                        "industry_code,value_text FROM native_records "
                        "WHERE substr(source_id,1,?)=? ORDER BY source_id,ordinal",
                        (len(BLS_PREFIX), BLS_PREFIX)
                    ):
                        sid, year, period, geo, series, industry, original_value = row
                        if not SOURCE_RE.fullmatch(sid):
                            raise ValueError("Unexpected source in BLS-only extract")
                        if (not isinstance(year, str) or
                                not re.fullmatch(r"20[0-9]{2}-(0[1-9]|1[0-2])", year) or
                                period != year):
                            raise ValueError("Unexpected original BLS monthly period key")
                        if period and len(period) > 20:
                            raise ValueError("Unexpected original period value")
                        # Refuse possible CSV formula injection from provider strings
                        # instead of silently converting a source-native value.
                        if any(isinstance(value, str) and value.lstrip().startswith(
                            ("=", "+", "@", "\t", "\r")) for value in row):
                            raise ValueError("Unsafe spreadsheet formula-like original field")
                        if original_value not in (None, ""):
                            try:
                                if math.isfinite(float(original_value)):
                                    numeric_count += 1
                            except (ValueError, OverflowError):
                                pass
                        writer.writerow(["" if value is None else value for value in row])
                        count += 1
            zipout.writestr("CITATION_AND_USAGE.txt", CITE.replace(
                "[edition_id]", pointer["edition_id"]
            ))
            zipout.writestr("ORIGINAL_SOURCE_CATALOG.json", json.dumps(
                sources, ensure_ascii=False, separators=(",", ":")
            ))
        if count != EXPECTED_BLS_ROWS:
            raise ValueError("BLS numerical row count changed during export")
        manifest = {
            "schema_version": 1,
            "release_type": "public_bls_source_native_only",
            "original_provider": "U.S. Bureau of Labor Statistics",
            "dataset": "Job Openings and Labor Turnover Survey (JOLTS)",
            "original_provider_url": PROVIDER_URL,
            "terms_url": PUBLIC_LICENSE_URL,
            "source_edition_id": pointer["edition_id"],
            "source_archive_sha256": pointer["archive_sha256"],
            "source_database_sha256": digest(db_path),
            "series_count": len(sources),
            "source_native_records": count,
            "finite_numeric_values": numeric_count,
            "not_harmonized": True,
            "not_cross_provider_comparable": True,
            "csv_zip": archive.name,
            "csv_zip_sha256": digest(archive),
            "citation": "U.S. Bureau of Labor Statistics. Job Openings and Labor Turnover Survey (JOLTS), [original series], [geography, period]. World Economic Atlas original source-native edition " + pointer["edition_id"] + ". Accessed [date].",
        }
        metadata.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return manifest
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--pointer", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    m = export(args.db, args.pointer, args.out)
    print("ATLAS_PUBLIC_BLS_RELEASE_VERIFIED", m["series_count"],
          m["source_native_records"], m["finite_numeric_values"], m["csv_zip_sha256"])


if __name__ == "__main__":
    main()
