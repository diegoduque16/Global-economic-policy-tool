#!/usr/bin/env python3
"""ILOSTAT native-source numeric profiling and metadata-only coverage inventory.

This extends the EXISTING resumable private ILOSTAT source collector.
The original gzip files and numeric values remain private on OneDrive.
A table file is NOT a unique economic series or a canonical observation.
Nothing in this module performs cross-provider harmonization or inference.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
import re

IDENTITY = re.compile(r"[A-Za-z0-9_]{4,100}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
REQUIRED_COLUMNS = {"ref_area", "time", "obs_value"}
PROFILE_STAGE = "SOURCE_NATIVE_ROW_LEVEL_NUMERIC_PROFILE_ONLY_NOT_HARMONIZED"
INVENTORY_COLUMNS = (
    "table_id", "official_toc_frequency", "official_toc_last_update",
    "checkpoint_source_last_update", "toc_vintage_matches_checkpoint",
    "official_toc_advertised_rows", "source_file_sha256",
    "source_file_verified_at_utc", "verified_native_csv_rows",
    "source_native_numeric_rows_profiled", "source_native_blank_values_profiled",
    "source_native_nonfinite_or_nonnumeric_values_profiled",
    "source_native_status_flagged_rows_profiled",
    "observed_source_ref_area_codes_profiled", "first_annual_time_profiled",
    "last_annual_time_profiled", "profile_state",
    "source_native_not_canonical", "cross_provider_overlap_audited",
)
MISSING_VALUE = ""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def profile_native_file(path: Path, expected_rows: str | int | None = None) -> dict:
    """Stream all source records once; CRC, source columns and value types checked.

    Non-numeric markers are counted, NOT transformed into values.
    The complete native multi-dimensional series key is NOT assumed known.
    Geography codes are source-native, not automatically sovereign countries.
    """
    path = Path(path)
    count = numeric = blanks = other = status_flags = 0
    geographies: set[str] = set()
    annual_times: set[int] = set()
    with gzip.open(path, "rt", encoding="utf-8-sig", errors="strict", newline="") as input_file:
        reader = csv.DictReader(input_file)
        header = reader.fieldnames
        if not header or len(set(header)) != len(header) or not REQUIRED_COLUMNS.issubset(header):
            raise ValueError("ILOSTAT source table missing unique required CSV fields")
        if len(header) > 120:
            raise ValueError("Excessive ILOSTAT source columns")
        for record in reader:
            if None in record or any(value is None for value in record.values()):
                raise ValueError("Ragged ILOSTAT native CSV row or field count mismatch")
            count += 1
            value = record["obs_value"].strip()
            if not value:
                blanks += 1
            else:
                try:
                    parsed = float(value)
                except (ValueError, OverflowError):
                    other += 1
                else:
                    if math.isfinite(parsed):
                        numeric += 1
                    else:
                        other += 1
            geo = record["ref_area"].strip()
            if geo:
                geographies.add(geo)
            time = record["time"].strip()
            if re.fullmatch(r"\d{4}", time):
                annual_times.add(int(time))
            if "obs_status" in record and record["obs_status"].strip():
                status_flags += 1
    if count < 1:
        raise ValueError("ILOSTAT source CSV has zero physical data rows")
    if str(expected_rows or "").strip().isdigit() and count != int(expected_rows):
        raise ValueError("ILOSTAT official TOC row count differs from physical CSV")
    if numeric + blanks + other != count:
        raise ValueError("ILOSTAT numeric profile disagrees with physical source rows")
    return {
        "profile_stage": PROFILE_STAGE,
        "records_validated": count,
        "csv_header": list(header),
        "numeric_obs_value_rows": numeric,
        "blank_obs_value_rows": blanks,
        "other_nonnumeric_or_nonfinite_rows": other,
        "obs_status_nonempty_rows": status_flags,
        "source_ref_area_distinct_codes": len(geographies),
        "first_annual_time_if_present": min(annual_times) if annual_times else None,
        "last_annual_time_if_present": max(annual_times) if annual_times else None,
        "all_numeric_values_finite": other == 0,
        "complete_native_observation_key_audited": False,
        "distinct_economic_observations_proven": False,
        "canonical_warehouse_integrated": False,
    }


def build_source_inventory(state: dict, toc: dict, *, checkpoint_sha256: str) -> tuple[list[dict], dict]:
    """Compile a reconstruction-ready, public-safe table inventory from private state.

    Historical source files without a numeric profile remain 'not profiled',
    rather than being mistaken for zero/missing/verified numeric values.
    """
    if state.get("schema_version") != 1 or not isinstance(state.get("downloaded"), dict):
        raise ValueError("Unsupported ILOSTAT checkpoint state")
    if not isinstance(toc, dict) or len(toc) < 1:
        raise ValueError("Missing ILOSTAT official TOC")
    if not SHA256.fullmatch(checkpoint_sha256):
        raise ValueError("Missing original checkpoint SHA256")
    rows = []
    total_native_rows = total_numeric = total_blank = total_other = 0
    profiled = current_toc = 0
    for table, original in sorted(state["downloaded"].items()):
        if not IDENTITY.fullmatch(table) or not isinstance(original, dict):
            raise ValueError("Invalid ILOSTAT checkpoint table identity")
        if original.get("state") != "NATIVE_NOT_HARMONIZED":
            raise ValueError("Unsupported ILOSTAT native data stage")
        sha, physical = original.get("sha256"), original.get("records_validated")
        if not isinstance(sha, str) or not SHA256.fullmatch(sha):
            raise ValueError("ILOSTAT source table SHA256 missing")
        if type(physical) is not int or physical < 1:
            raise ValueError("ILOSTAT source table has invalid physical row count")
        advertised = toc.get(table) or {}
        update = str(advertised.get("last.update", "")).strip()
        prior_update = str(original.get("last_update", "")).strip()
        is_current = bool(advertised and prior_update == update and
                          str(advertised.get("n.records", "")).strip() ==
                          str(original.get("toc_records", "")).strip())
        current_toc += is_current
        p = original.get("native_numeric_profile")
        if p is None:
            stage, numeric, blank, other, flagged, areas, first, last = (
                "NOT_PROFILED_HISTORICAL_SOURCE", "", "", "", "", "", "", "")
        else:
            if not isinstance(p, dict) or p.get("profile_stage") != PROFILE_STAGE:
                raise ValueError("Unrecognized ILOSTAT numeric profile stage")
            vals = [p.get(name) for name in
                    ("records_validated", "numeric_obs_value_rows", "blank_obs_value_rows",
                     "other_nonnumeric_or_nonfinite_rows", "obs_status_nonempty_rows",
                     "source_ref_area_distinct_codes")]
            if (any(type(v) is not int or v < 0 for v in vals) or vals[0] != physical
                    or sum(vals[1:4]) != physical or vals[4] > physical):
                raise ValueError("Source-native profile does not reconcile to physical table")
            if p.get("canonical_warehouse_integrated") is not False:
                raise ValueError("Profile incorrectly claims canonical integration")
            stage = PROFILE_STAGE
            numeric, blank, other, flagged, areas = vals[1:]
            first = p.get("first_annual_time_if_present")
            last = p.get("last_annual_time_if_present")
            for year in (first, last):
                if year is not None and (type(year) is not int or not 1800 <= year <= 2200):
                    raise ValueError("Invalid annual source observation year")
            if (first is None) != (last is None) or (first is not None and first > last):
                raise ValueError("Inconsistent source time boundaries")
            profiled += 1
            total_numeric += numeric
            total_blank += blank
            total_other += other
            first = "" if first is None else first
            last = "" if last is None else last
        total_native_rows += physical
        rows.append(dict(zip(INVENTORY_COLUMNS, (
            table, str(advertised.get("freq", "")), update, prior_update,
            str(is_current).lower(), str(advertised.get("n.records", "")),
            sha, str(original.get("verified_utc", "")), physical,
            numeric, blank, other, flagged, areas, first, last, stage, "true", "false"
        ))))
    report = {
        "schema_version": 1,
        "stage": "ILOSTAT_SOURCE_NATIVE_COVERAGE_NOT_CANONICAL",
        "checkpoint_source_sha256": checkpoint_sha256,
        "official_toc_entries": len(toc),
        "verified_source_table_files": len(rows),
        "source_tables_matching_current_toc_vintage": current_toc,
        "physical_source_native_csv_rows_all_verified_files_NOT_unique_observations": total_native_rows,
        "source_tables_with_numeric_value_profile": profiled,
        "historical_source_tables_not_yet_profiled": len(rows) - profiled,
        "finite_numeric_obs_value_rows_in_profiled_tables_ONLY": total_numeric,
        "blank_obs_value_rows_in_profiled_tables_ONLY": total_blank,
        "nonnumeric_or_nonfinite_rows_in_profiled_tables_ONLY": total_other,
        "numeric_rows_across_unprofiled_tables": "UNKNOWN_NOT_ZERO",
        "country_count_certified": False,
        "series_key_deduplication_completed": False,
        "warehouse_integrated": False,
        "cross_provider_harmonized": False,
        "source_file_counts_additive_to_other_atlas_layers": False,
        "limitation": ("Numeric counts apply only to newly profiled native source tables. "
                       "ILOSTAT table files, rows, flags and classifications may overlap. "
                       "No distinct economic observation count or causal effect is certified."),
    }
    return rows, report


def write_inventory(directory: Path, state: dict, toc: dict, *, checkpoint_sha256: str) -> dict:
    directory = Path(directory)
    rows, report = build_source_inventory(state, toc, checkpoint_sha256=checkpoint_sha256)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "source_native_table_inventory.csv").open(
            "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=INVENTORY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    (directory / "source_native_numeric_coverage.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def plan_private_profile_backfill(state: dict, *, source_root: str,
                                  max_files: int = 5,
                                  max_file_bytes: int = 32 * 1024 * 1024,
                                  max_total_bytes: int = 128 * 1024 * 1024) -> list[dict]:
    """Identify SHA-pinned historical tables for bounded, read-only local scans.

    Never fetch provider data anew; paths must bind the saved provider table ID
    and SHA. A caller independently verifies exact downloaded compressed bytes
    and original SHA before updating the existing private checkpoint.
    """
    if (type(max_files) is not int or not 0 <= max_files <= 20 or
            type(max_file_bytes) is not int or not 1 <= max_file_bytes <= 2 ** 30 or
            type(max_total_bytes) is not int or max_total_bytes < max_file_bytes):
        raise ValueError("Unsafe bounded historical profile backfill limits")
    if not isinstance(state.get("downloaded"), dict) or not source_root.startswith("onedrive:"):
        raise ValueError("Unrecognized private source checkpoint or root")
    selected = []
    total = 0
    for table, entry in sorted(state["downloaded"].items()):
        if len(selected) >= max_files:
            break
        if not isinstance(entry, dict) or entry.get("native_numeric_profile") is not None:
            continue
        sha, remote, size = entry.get("sha256"), entry.get("remote"), entry.get("compressed_bytes")
        if not isinstance(table, str) or not IDENTITY.fullmatch(table):
            raise ValueError("Untrusted historical ILOSTAT table ID")
        if not isinstance(sha, str) or not SHA256.fullmatch(sha):
            raise ValueError("Untrusted historical ILOSTAT source fingerprint")
        if type(size) is not int or size <= 0:
            raise ValueError("Missing historical ILOSTAT compressed source size")
        prefix = source_root.rstrip("/") + "/indicator-data/" + table + "/"
        if not isinstance(remote, str) or not remote.startswith(prefix):
            raise ValueError("Historical source remote outside the existing private archive")
        filename = remote[len(prefix):]
        if not re.fullmatch(re.escape(table) + r"-[A-Za-z0-9]{1,20}-" +
                            re.escape(sha[:16]) + r"\.csv\.gz", filename):
            raise ValueError("Historical source filename does not bind original SHA")
        if size > max_file_bytes or total + size > max_total_bytes:
            continue
        selected.append({
            "id": table, "sha256": sha, "remote": remote,
            "compressed_bytes": size, "original_validated_csv_rows": entry["records_validated"],
            "toc_records": entry.get("toc_records", "")
        })
        total += size
    return selected
