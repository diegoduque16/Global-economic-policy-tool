#!/usr/bin/env python3
"""Build a versioned, source-specific SQLite research download from verified WDI CSVs.

No private canonical database, extra providers, interpolation, harmonization,
causal claims or network operations. World Bank region/income aggregates remain
separate from countries in both metadata and query views.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
import zipfile

NAME = "atlas_wdi_research.sqlite"
ARCHIVE = NAME + ".zip"
CSV_COLUMNS = ["geography_code", "year", "indicator_code", "value"]
SCHEMA_VERSION = 1


def digest(path: Path) -> str:
    hash_ = hashlib.sha256()
    with path.open("rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            hash_.update(chunk)
    return hash_.hexdigest()


def build_sqlite_release(directory: Path, catalog: dict, report: dict) -> dict:
    """Read ONLY previous verified public-export CSVs, validate, build compact DB.

    SQL key: (geography_code, year, indicator_code). No missing/zero
    conversion is performed; absent observations simply have no record.
    """
    directory = Path(directory)
    if report.get("numeric_observations") != catalog["summary"]["observations"]:
        raise ValueError("SQLite builder requires complete verified WDI edition")
    if report.get("cross_provider_harmonized") is not False:
        raise ValueError("Cannot call a cross-provider panel a WDI research edition")

    countries = directory / "wdi_country_year_long.csv"
    aggregates = directory / "wdi_aggregate_year_long.csv"
    inputs = {countries.name: digest(countries), aggregates.name: digest(aggregates)}
    db_path = directory / NAME
    zip_path = directory / ARCHIVE
    if db_path.exists() or zip_path.exists():
        raise ValueError("Refusing to overwrite an existing research database archive")

    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA page_size=4096")
        con.executescript("""
            CREATE TABLE geographies (
                geography_code TEXT PRIMARY KEY,
                geography_name TEXT NOT NULL,
                region TEXT NOT NULL,
                geography_kind TEXT NOT NULL
                    CHECK (geography_kind IN ('country_or_territory', 'aggregate'))
            );
            CREATE TABLE indicators (
                indicator_code TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                topic TEXT NOT NULL,
                unit TEXT NOT NULL,
                provider TEXT NOT NULL,
                source_run_id INTEGER NOT NULL,
                source_retrieved_at_utc TEXT NOT NULL,
                original_series_sha256 TEXT NOT NULL
            );
            CREATE TABLE observations (
                geography_code TEXT NOT NULL REFERENCES geographies(geography_code),
                year INTEGER NOT NULL CHECK (year BETWEEN 1800 AND 2100),
                indicator_code TEXT NOT NULL REFERENCES indicators(indicator_code),
                value REAL NOT NULL,
                PRIMARY KEY (geography_code, year, indicator_code)
            ) WITHOUT ROWID;
            CREATE TABLE source_edition (
                property TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE INDEX observations_indicator_year_geo
              ON observations (indicator_code, year, geography_code);
            CREATE VIEW country_observations AS
              SELECT o.geography_code, o.year, o.indicator_code, o.value
              FROM observations AS o
              JOIN geographies AS g USING (geography_code)
              WHERE g.geography_kind = 'country_or_territory';
            CREATE VIEW aggregate_observations AS
              SELECT o.geography_code, o.year, o.indicator_code, o.value
              FROM observations AS o
              JOIN geographies AS g USING (geography_code)
              WHERE g.geography_kind = 'aggregate';
        """)
        geographies = catalog["geographies"]
        series = catalog["series"]
        series_hashes = report["input_files_sha256"]
        con.executemany(
            "INSERT INTO geographies VALUES (?, ?, ?, ?)",
            (
                (code, item["name"], item["region"].strip(), item["kind"])
                for code, item in sorted(geographies.items())
            )
        )
        con.executemany(
            "INSERT INTO indicators VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (item["code"], item["title"], item["topic"], "source_defined",
                 "World Bank World Development Indicators",
                 catalog["source_run_id"], catalog["source_downloaded_at_utc"],
                 series_hashes["series/" + item["code"] + ".json"])
                for item in sorted(series, key=lambda i: i["code"])
            )
        )
        edition = {
            "schema_version": str(SCHEMA_VERSION),
            "source_edition": "WORLD_BANK_WDI_VERIFIED_2026_11_09",
            "source_run_id": str(catalog["source_run_id"]),
            "source_retrieved_at_utc": catalog["source_downloaded_at_utc"],
            "source_catalog_sha256": series_hashes["catalog.json"],
            "country_long_csv_sha256": inputs[countries.name],
            "aggregate_long_csv_sha256": inputs[aggregates.name],
            "cross_provider_harmonized": "false",
            "causal_inference_ready": "false",
            "unobserved_cells_imputed": "false",
            "source_units_retained": "true",
        }
        con.executemany(
            "INSERT INTO source_edition VALUES (?, ?)", sorted(edition.items())
        )
        count = {"country_or_territory": 0, "aggregate": 0}
        observed_places = {"country_or_territory": set(), "aggregate": set()}
        valid_codes = {entry["code"] for entry in series}
        with con:
            for path, kind in (
                (countries, "country_or_territory"),
                (aggregates, "aggregate"),
            ):
                with path.open(newline="", encoding="utf-8") as file:
                    reader = csv.DictReader(file)
                    if reader.fieldnames != CSV_COLUMNS:
                        raise ValueError("Research source CSV columns changed")
                    batch = []
                    for rec in reader:
                        geo, code = rec["geography_code"], rec["indicator_code"]
                        if (geo not in geographies or
                                geographies[geo]["kind"] != kind or code not in valid_codes):
                            raise ValueError("Country/aggregate/source-series identity mismatch")
                        try:
                            year = int(rec["year"])
                            value = float(rec["value"])
                        except (ValueError, OverflowError, TypeError) as exc:
                            raise ValueError("Invalid source WDI CSV number") from exc
                        if not 1800 <= year <= 2100 or not math.isfinite(value):
                            raise ValueError("Out-of-range or nonfinite source WDI value")
                        batch.append((geo, year, code, value))
                        count[kind] += 1
                        observed_places[kind].add(geo)
                        if len(batch) >= 5000:
                            con.executemany(
                                "INSERT INTO observations VALUES (?, ?, ?, ?)", batch
                            )
                            batch.clear()
                    if batch:
                        con.executemany(
                            "INSERT INTO observations VALUES (?, ?, ?, ?)", batch
                        )
        if (count["country_or_territory"] != report["country_numeric_observations"] or
                count["aggregate"] != report["aggregate_numeric_observations"] or
                sum(count.values()) != report["numeric_observations"] or
                len(observed_places["country_or_territory"]) !=
                catalog["summary"]["country_or_territory_codes"] or
                len(observed_places["aggregate"]) != catalog["summary"]["aggregate_codes"]):
            raise ValueError("Source CSV and SQLite observation coverage differ")
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Source-specific research SQLite failed integrity check")
        if con.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Source-specific research SQLite has broken foreign keys")
        checks = {
            "all_observations": con.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
            "country_observations": con.execute("SELECT COUNT(*) FROM country_observations").fetchone()[0],
            "aggregate_observations": con.execute("SELECT COUNT(*) FROM aggregate_observations").fetchone()[0],
            "indicators": con.execute("SELECT COUNT(*) FROM indicators").fetchone()[0],
        }
        if (checks["all_observations"] != sum(count.values()) or
                checks["country_observations"] != count["country_or_territory"] or
                checks["aggregate_observations"] != count["aggregate"] or
                checks["indicators"] != len(series)):
            raise ValueError("SQLite cross-check of actual observed numeric records failed")
        con.commit()
    finally:
        con.close()

    # Publish a compact SQLite archive, not the redundant uncompressed DB;
    # preserve fixed ZIP metadata for deterministic source-edition exports.
    try:
        member = zipfile.ZipInfo(NAME, date_time=(1980, 1, 1, 0, 0, 0))
        member.compress_type = zipfile.ZIP_DEFLATED
        member.external_attr = 0o644 << 16
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=8) as archive:
            with archive.open(member, "w") as output, db_path.open("rb") as source:
                shutil.copyfileobj(source, output, 1024 * 1024)
        with zipfile.ZipFile(zip_path, "r") as archive:
            if archive.namelist() != [NAME] or archive.testzip() is not None:
                raise ValueError("Research SQLite archive integrity verification failed")
            uncompressed_bytes = archive.getinfo(NAME).file_size
        if zip_path.stat().st_size >= 25_000_000:
            raise ValueError("Research SQLite exceeds 25 MB static download asset limit")
        return {
            "archive": ARCHIVE,
            "archive_sha256": digest(zip_path),
            "archive_bytes": zip_path.stat().st_size,
            "uncompressed_sqlite_bytes": uncompressed_bytes,
            "database_schema_version": SCHEMA_VERSION,
            **checks,
            "country_aggregate_separated_in_sql_views": True,
            "cross_provider_harmonized": False,
            "causal_inference_ready": False,
        }
    finally:
        if db_path.exists():
            db_path.unlink()
