#!/usr/bin/env python3
"""Bounded next-batch ILOSTAT original-row append to the EXISTING private Atlas SQLite.

Input is a locally restored, independently SHA-verified private six-provider
native attachment and exact source files from the existing ILOSTAT checkpoint.
This module does not download source data, publish source rows, or harmonize
statistical measures. It never edits the parent database in place.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import tempfile

from warehouse.ilostat_native_extension import (
    EXPECTED, SOURCE_BASE, SHA256, VALID_ID, append_originals,
)
from warehouse.native_materialize import sha256

PARENT_ROWS = 1_075_484
PARENT_SOURCES = 982
# Original source table IDs included in the exact SHA-pinned 1,075,484-row
# private parent. These must never be selected for ingestion a second time.
ALREADY_INTEGRATED = frozenset(EXPECTED) | frozenset({
    "EMP_STEM_SEX_OCU_NB_A",
    "HOW_TEMP_SEX_OCU_DSB_NB_A",
    "HOW_XEES_SEX_OCU_DSB_NB_A",
})
MAX_TABLES = 3
MAX_NEW_ROWS = 250_000
MAX_SOURCE_BYTES = 128 * 1024 * 1024


def verify_selection(state: dict, table_ids: list[str], source_dir: Path) -> tuple[dict, dict]:
    """Select real archived annual sources, with no guessed IDs or row totals."""
    downloaded = state.get("downloaded")
    if state.get("schema_version") != 1 or not isinstance(downloaded, dict):
        raise ValueError("Unknown ILOSTAT private checkpoint schema")
    if not 1 <= len(table_ids) <= MAX_TABLES or len(set(table_ids)) != len(table_ids):
        raise ValueError("Select 1 to 3 distinct original ILOSTAT tables")
    checked, counts = {}, {}
    for table in table_ids:
        if (not isinstance(table, str) or not VALID_ID.fullmatch(table)
                or not table.endswith("_A") or table in ALREADY_INTEGRATED):
            raise ValueError("Unapproved, nonannual or already indexed ILOSTAT table")
        entry = downloaded.get(table)
        if not isinstance(entry, dict) or entry.get("state") != "NATIVE_NOT_HARMONIZED":
            raise ValueError("Original ILOSTAT table not saved: " + table)
        count, fingerprint = entry.get("records_validated"), entry.get("sha256")
        if (type(count) is not int or not 1 <= count <= MAX_NEW_ROWS
                or not isinstance(fingerprint, str)
                or not SHA256.fullmatch(fingerprint)):
            raise ValueError("Unverified ILOSTAT table row count or SHA: " + table)
        header = entry.get("csv_header")
        if (not isinstance(header, list) or len(set(header)) != len(header)
                or not {"ref_area", "time", "obs_value"}.issubset(header)):
            raise ValueError("Missing original ILOSTAT table header: " + table)
        prefix = SOURCE_BASE + "/indicator-data/" + table + "/"
        remote = entry.get("remote")
        if (not isinstance(remote, str) or not remote.startswith(prefix)
                or not remote.endswith("-" + fingerprint[:16] + ".csv.gz")):
            raise ValueError("Original source path not bound to source SHA: " + table)
        path = source_dir / (table + ".csv.gz")
        if (path.is_symlink() or not path.is_file()
                or not 1 <= path.stat().st_size <= MAX_SOURCE_BYTES
                or sha256(path) != fingerprint):
            raise ValueError("Source gzip missing, too large or SHA-invalid: " + table)
        checked[table] = entry
        counts[table] = count
    if sum(counts.values()) > MAX_NEW_ROWS:
        raise ValueError("ILOSTAT batch exceeds maximum original rows")
    return checked, counts



def select_verified_tables(state: dict) -> list[str]:
    """Choose bounded, actually archived annual labour tables by original IDs.

    Uses the private checkpoint as the sole authority for IDs, row counts,
    headers and source hashes; heuristic ranking never invents series labels
    or asserts economic comparability.
    """
    originals = state.get("downloaded")
    if state.get("schema_version") != 1 or not isinstance(originals, dict):
        raise ValueError("Invalid private ILOSTAT source selection checkpoint")
    options = []
    for table, entry in originals.items():
        if (not isinstance(table, str) or not VALID_ID.fullmatch(table)
                or not table.endswith("_A") or table in ALREADY_INTEGRATED
                or not isinstance(entry, dict)
                or entry.get("state") != "NATIVE_NOT_HARMONIZED"):
            continue
        n = entry.get("records_validated")
        profile = entry.get("native_numeric_profile")
        if (type(n) is not int or not 1 <= n <= 75_000
                or not isinstance(profile, dict)
                or profile.get("records_validated") != n
                or type(profile.get("numeric_obs_value_rows")) is not int
                or profile["numeric_obs_value_rows"] < 1
                or any(type(profile.get(key)) is not int or profile[key] < 0
                       for key in ("blank_obs_value_rows",
                                   "other_nonnumeric_or_nonfinite_rows"))
                or sum(profile[key] for key in
                       ("numeric_obs_value_rows", "blank_obs_value_rows",
                        "other_nonnumeric_or_nonfinite_rows")) != n
                or not isinstance(entry.get("csv_header"), list)
                or not {"ref_area", "time", "obs_value"}.issubset(entry["csv_header"])
                or len(set(entry["csv_header"])) != len(entry["csv_header"])):
            continue
        fingerprint = entry.get("sha256")
        remote = entry.get("remote")
        prefix = SOURCE_BASE + "/indicator-data/" + table + "/" + table + "-"
        if (not isinstance(fingerprint, str)
                or not SHA256.fullmatch(fingerprint)
                or not isinstance(remote, str)
                or not remote.startswith(prefix)
                or not re.fullmatch(r"[0-9A-Za-z]{1,20}-" +
                                    fingerprint[:16] + r"\.csv\.gz",
                                    remote[len(prefix):])):
            continue
        # Prioritize original occupation/earnings/employment/labour indicators;
        # no inferred ILO labels or unit definitions are attached.
        names = table.split("_")
        if "OCU" in names:
            priority = 0
        elif table.startswith(("EMP_", "EAR_", "UNE_", "EAP_", "HOW_")):
            priority = 1
        else:
            priority = 2
        options.append((priority, n, table))
    chosen, total = [], 0
    for _, n, table in sorted(options):
        if total + n <= MAX_NEW_ROWS:
            chosen.append(table)
            total += n
        if len(chosen) == MAX_TABLES:
            break
    if not chosen:
        raise ValueError("No eligible source-verified annual ILOSTAT batch in private checkpoint")
    return chosen


def append_next_batch(parent: Path, parent_sha: str, checkpoint: Path,
                      sources: Path, tables: list[str], destination: Path) -> dict:
    """Produce an exclusive new SQLite edition and public-safe count report."""
    parent = Path(parent)
    destination = Path(destination)
    if (not isinstance(parent_sha, str) or not SHA256.fullmatch(parent_sha)
            or not parent.is_file() or parent.is_symlink()
            or sha256(parent) != parent_sha):
        raise ValueError("Parent private Atlas SQLite SHA not verified")
    if destination.exists() or destination.resolve() == parent.resolve():
        raise FileExistsError("Never overwrite parent or existing private Atlas edition")
    state = json.loads(Path(checkpoint).read_text(encoding="utf-8"))
    checked, counts = verify_selection(state, tables, Path(sources))
    with sqlite3.connect(f"file:{parent.resolve()}?mode=ro", uri=True) as db:
        rows = db.execute("SELECT COUNT(*) FROM native_records").fetchone()[0]
        identities = db.execute("SELECT COUNT(*) FROM native_sources").fetchone()[0]
        existing = {row[0] for row in db.execute("SELECT source_id FROM native_sources")}
        if rows != PARENT_ROWS or identities != PARENT_SOURCES:
            raise ValueError("Parent is not the certified six-provider Atlas edition")
        for table, entry in checked.items():
            new_source_id = "ilostat_" + table + "_" + entry["sha256"][:12]
            if new_source_id in existing or any(
                    sid.startswith("ilostat_" + table + "_") for sid in existing):
                raise ValueError("Original ILOSTAT source already indexed: " + table)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".atlas-ilostat-") as temp:
        staged = Path(temp) / "native_source_extension.sqlite"
        shutil.copy2(parent, staged)
        report = append_originals(
            staged, checked, sources, parent_rows=PARENT_ROWS,
            parent_sources=PARENT_SOURCES, expected=counts,
        )
        if (report["original_source_rows_added"] != sum(counts.values())
                or report["new_attachment_source_native_rows"] != PARENT_ROWS + sum(counts.values())
                or report["new_attachment_source_ids"] != PARENT_SOURCES + len(counts)):
            raise ValueError("Private Atlas appended row totals did not reconcile")
        with sqlite3.connect(staged) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("New Atlas SQLite integrity failed")
        report.update({
            "parent_sha256": parent_sha,
            "new_database_sha256": sha256(staged),
            "table_ids": sorted(tables),
            "not_harmonized": True,
            "public_release": False,
            "snapshot_requires_private_backup_and_sha_restore": True,
        })
        os.replace(staged, destination)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-db", type=Path, required=True)
    parser.add_argument("--parent-sha256", required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--table", action="append", required=True)
    parser.add_argument("--output-db", type=Path, required=True)
    args = parser.parse_args()
    result = append_next_batch(
        args.parent_db, args.parent_sha256, args.checkpoint,
        args.source_dir, args.table, args.output_db,
    )
    # Numeric summary and original-source SHA metadata only; no private rows.
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
