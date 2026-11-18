#!/usr/bin/env python3
"""Add three verified original ILOSTAT SDG tables to the SAME private Atlas SQLite attachment.

This is a bounded, source-native ingestion of original table rows, NOT 153k
independent harmonized observations. Missing source values remain missing,
ILOSTAT dimension codes stay intact, and the original gzip SHA and source
checkpoint are required. No data are added to historical canonical or public.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import tarfile
import tempfile

from scripts.ilostat_native_profile import profile_native_file
from warehouse.native_materialize import sha256, dump
from warehouse.us_europe_native_extension import read_members

PARENT_SHA = "ee5327c956bd8e17894f2870ae31a174a8cb088c60997555c344b5c9ea022625"
PARENT_ID = "atlas-native-wid-wdi-econ-bls-20261117-44171117929"
PARENT_RECORDS = 883118
PARENT_SOURCES = 976
EXPECTED = {
    "SDG_0831_SEX_ECO_RT_A": 57488,  # Official SDG 8.3.1 indicator table
    "SDG_0851_SEX_OCU_NB_A": 47322,  # Official SDG 8.5.1 indicator table
    "SDG_0852_SEX_AGE_RT_A": 48306,  # Official SDG 8.5.2 indicator table
}
EXPECTED_ORIGINAL_ROWS = sum(EXPECTED.values())
SOURCE_BASE = "onedrive:Diego/economic research database/atlas/ilostat-bulk-v1"
OFFICIAL_API = "https://rplumber.ilo.org/data/indicator?format=.csv.gz&id="
NO_DIMENSIONS = {"ref_area", "time", "obs_value", "obs_status"}
VALID_ID = re.compile(r"[A-Za-z0-9_]{4,100}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")


def checkpoint_records(state: dict) -> dict:
    if state.get("schema_version") != 1 or not isinstance(state.get("downloaded"), dict):
        raise ValueError("Invalid private official ILOSTAT source checkpoint")
    checked = {}
    for table, expected_rows in EXPECTED.items():
        entry = state["downloaded"].get(table)
        if not isinstance(entry, dict) or entry.get("state") != "NATIVE_NOT_HARMONIZED":
            raise ValueError("Certified official ILOSTAT table absent: " + table)
        if (entry.get("records_validated") != expected_rows or
            type(entry.get("records_validated")) is not int or
            not isinstance(entry.get("sha256"), str) or
            not SHA256.fullmatch(entry["sha256"]) or
            not isinstance(entry.get("csv_header"), list) or
            not isinstance(entry.get("native_numeric_profile"), dict)):
            raise ValueError("Original ILOSTAT numeric scope or source SHA changed: " + table)
        remote = entry.get("remote")
        prefix = SOURCE_BASE + "/indicator-data/" + table + "/"
        if (not isinstance(remote, str) or not remote.startswith(prefix) or
            not re.fullmatch(re.escape(table) + r"-[0-9A-Za-z]{1,20}-" +
                             re.escape(entry["sha256"][:16]) + r"\.csv\.gz",
                             remote[len(prefix):])):
            raise ValueError("Source location not bound to verified original SHA: " + table)
        profile = entry["native_numeric_profile"]
        if (profile.get("records_validated") != expected_rows or
            sum(profile.get(k, -999999) for k in (
                "numeric_obs_value_rows", "blank_obs_value_rows",
                "other_nonnumeric_or_nonfinite_rows")) != expected_rows):
            raise ValueError("Official ILOSTAT native numeric profile differs: " + table)
        checked[table] = entry
    return checked


def identity(table: str, row: dict) -> tuple[str, dict]:
    """Conservative ILO source-defined series: all structural source fields remain.

    A short digest identifies the original dimension combination while the
    full non-value structure remains independently queryable in a side table
    and in the original source row. We never infer comparable units.
    """
    structure = {
        k: str(v if v is not None else "")
        for k, v in sorted(row.items())
        if k not in NO_DIMENSIONS and not k.startswith("note_")
    }
    token = hashlib.sha256(
        json.dumps(structure, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return table + "_" + token, structure


def append_originals(dbpath: Path, checkpoint: dict, source_dir: Path,
                     *, parent_rows: int = PARENT_RECORDS,
                     parent_sources: int = PARENT_SOURCES,
                     expected: dict | None = None) -> dict:
    """Transactional append. Never overwrites an original source table or row."""
    expected = expected or EXPECTED
    verified = checkpoint_records(checkpoint) if expected is EXPECTED else checkpoint
    dbpath, source_dir = Path(dbpath), Path(source_dir)
    report = {"original_source_rows_added": 0, "finite_numeric_values_added": 0,
              "missing_source_values_retained": 0, "other_nonnumeric_values_retained": 0,
              "original_series_keys": 0, "tables": {}, "source_count_added": len(expected)}
    with sqlite3.connect(dbpath) as db:
        row_count = db.execute("SELECT COUNT(*) FROM native_records").fetchone()[0]
        source_count = db.execute("SELECT COUNT(*) FROM native_sources").fetchone()[0]
        if row_count != parent_rows or source_count != parent_sources:
            raise ValueError("Parent private Atlas source-native release changed")
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS native_ilostat_series (
                    source_id TEXT NOT NULL, series_code TEXT NOT NULL,
                    original_dimensions_json TEXT NOT NULL,
                    original_rows INTEGER NOT NULL,
                    PRIMARY KEY (source_id,series_code))
            """)
            for table, n_expected in expected.items():
                if not VALID_ID.fullmatch(table) or not table.endswith("_A"):
                    raise ValueError("Unrecognized official annual ILOSTAT table ID")
                entry = verified[table]
                gz = source_dir / (table + ".csv.gz")
                if (not gz.is_file() or gz.is_symlink() or
                    sha256(gz) != entry["sha256"]):
                    raise ValueError("Original ILOSTAT source file not SHA-verified: " + table)
                profile = profile_native_file(gz, n_expected)
                original_profile = entry.get("native_numeric_profile")
                if original_profile and any(
                    profile[k] != original_profile.get(k) for k in (
                        "records_validated","numeric_obs_value_rows",
                        "blank_obs_value_rows","other_nonnumeric_or_nonfinite_rows")
                ):
                    raise ValueError("Source-original ILOSTAT numeric counts changed")
                source_id = "ilostat_" + table + "_" + entry["sha256"][:12]
                details = {
                    "provider": "ILO / ILOSTAT",
                    "original_provider_table_id": table,
                    "source_original_sha256": entry["sha256"],
                    "source_url": OFFICIAL_API + table,
                    "unit": "source_defined_unverified",
                    "frequency": "annual",
                    "original_rows": n_expected,
                    "source_release_stage": "PRIVATE_SOURCE_NATIVE_UNHARMONIZED",
                    "distinct_statistical_series_certified": False,
                    "cross_provider_overlap_audited": False,
                }
                db.execute("INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)",
                    (source_id, entry["sha256"], n_expected, n_expected,
                     json.dumps(details,sort_keys=True),
                     "ILO / ILOSTAT official original source",
                     "SOURCE_NATIVE_UNHARMONIZED", 0,
                     json.dumps(entry["csv_header"])))
                keys = {}
                rows = 0
                batch = []
                with gzip.open(gz,"rt",encoding="utf-8-sig",errors="strict",newline="") as inp:
                    reader = csv.DictReader(inp)
                    if (reader.fieldnames != entry["csv_header"] or
                        len(set(reader.fieldnames or [])) != len(reader.fieldnames or []) or
                        not {"ref_area", "time", "obs_value"}.issubset(reader.fieldnames or [])):
                        raise ValueError("ILOSTAT original source column definitions changed")
                    for raw in reader:
                        if None in raw or any(v is None for v in raw.values()):
                            raise ValueError("Ragged official ILOSTAT source row")
                        rows += 1
                        area = raw["ref_area"].strip()
                        time = raw["time"].strip()
                        if not area or not re.fullmatch(r"[12][0-9]{3}", time):
                            raise ValueError("Unknown or missing source annual period/geography")
                        code, dimensions = identity(table, raw)
                        encoded = json.dumps(dimensions,sort_keys=True,
                                             ensure_ascii=False,separators=(",",":"))
                        if code in keys and keys[code][0] != encoded:
                            raise ValueError("ILOSTAT original series key SHA collision")
                        keys[code] = (encoded, keys.get(code,("",0))[1]+1)
                        value = raw["obs_value"]
                        if not value.strip():
                            report["missing_source_values_retained"] += 1
                        else:
                            try:
                                x = float(value)
                                finite = math.isfinite(x)
                            except (ValueError,OverflowError):
                                finite = False
                            if finite:
                                report["finite_numeric_values_added"] += 1
                            else:
                                report["other_nonnumeric_values_retained"] += 1
                        source = dict(raw)
                        # Original fields remain unaltered; these named additions
                        # explain provenance and make existing Atlas UI usable.
                        source.setdefault("provider", "ILO / ILOSTAT")
                        source.setdefault("source_url", OFFICIAL_API + table)
                        source.setdefault("frequency", "annual")
                        source.setdefault("unit", "source_defined_unverified")
                        source["disaggregation"] = dimensions
                        batch.append((source_id,rows,time,time,area,code,
                                      raw.get("indicator",""),value,
                                      json.dumps(source,sort_keys=True,
                                                 ensure_ascii=False,separators=(",",":"))))
                        if len(batch)>=4000:
                            db.executemany("INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)",batch)
                            batch.clear()
                if batch:
                    db.executemany("INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)",batch)
                if rows != n_expected:
                    raise ValueError("Verified official ILOSTAT source row count changed")
                db.executemany(
                    "INSERT INTO native_ilostat_series VALUES (?,?,?,?)",
                    [(source_id,code,dims,count) for code,(dims,count) in keys.items()])
                report["tables"][table] = {
                    "original_rows": rows, "source_sha256": entry["sha256"],
                    "dimension_combinations_not_certified_distinct_series": len(keys),
                }
                report["original_source_rows_added"] += rows
                report["original_series_keys"] += len(keys)
            total = db.execute("SELECT COUNT(*) FROM native_records").fetchone()[0]
            total_sources = db.execute("SELECT COUNT(*) FROM native_sources").fetchone()[0]
            if (total != parent_rows + sum(expected.values()) or
                total_sources != parent_sources + len(expected) or
                db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"):
                raise ValueError("Original Atlas parent preservation/integrity failed")
            db.commit()
        except BaseException:
            db.rollback()
            raise
    report.update({
        "parent_original_records_preserved": parent_rows,
        "new_attachment_source_native_rows": total,
        "parent_original_source_ids_preserved": parent_sources,
        "new_attachment_source_ids": total_sources,
        "historical_canonical_modified": False,
        "public_release": False,
        "cross_provider_harmonized": False,
    })
    return report


def build(parent_archive: Path, checkpoint_path: Path, source_dir: Path,
          output: Path, snapshot: str) -> dict:
    if not re.fullmatch(r"atlas-native-[A-Za-z0-9_-]+", snapshot):
        raise ValueError("Invalid new immutable Atlas edition ID")
    if sha256(parent_archive) != PARENT_SHA:
        raise ValueError("Certified 883118-row Atlas native parent SHA changed")
    state = json.loads(Path(checkpoint_path).read_text(encoding="utf-8"))
    checkpoint_records(state)
    output = Path(output)
    output.mkdir(parents=True,exist_ok=True)
    archive = output / (snapshot + ".tar.gz")
    receipt = output / (snapshot + ".integration_report.json")
    if archive.exists() or receipt.exists():
        raise FileExistsError("Refusing to overwrite an existing private edition")
    with tempfile.TemporaryDirectory() as folder:
        members = read_members(parent_archive,PARENT_ID,
            {"native_source_extension.sqlite","source_native_release_manifest.json"})
        if set(members) != {"native_source_extension.sqlite","source_native_release_manifest.json"}:
            raise ValueError("Missing certified original Atlas native parent files")
        parent_manifest = json.loads(members["source_native_release_manifest.json"])
        if (parent_manifest.get("new_attachment_records") != PARENT_RECORDS or
            parent_manifest.get("public_release") is not False):
            raise ValueError("Parent source-native Atlas version inconsistent")
        root=Path(folder)/snapshot
        root.mkdir()
        sqlite=root/"native_source_extension.sqlite"
        sqlite.write_bytes(members["native_source_extension.sqlite"])
        result=append_originals(sqlite,state,source_dir)
        if result["original_source_rows_added"] != EXPECTED_ORIGINAL_ROWS:
            raise ValueError("ILOSTAT 3-table row count changed")
        dump(root/"source_native_release_manifest.json",{
            "parent_archive_sha256":PARENT_SHA,
            "original_source_table_ids":list(EXPECTED),
            **result,"research_ready_causal":False,
            "original_source_unit_definitions_verified":False,
        })
        with tarfile.open(archive,"w:gz") as tar:
            tar.add(root,arcname=snapshot)
    result.update({"snapshot_id":snapshot,"archive_sha256":sha256(archive),
                   "parent_archive_sha256":PARENT_SHA})
    dump(receipt,result)
    return result


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--parent-archive",type=Path,required=True)
    p.add_argument("--checkpoint",type=Path,required=True)
    p.add_argument("--source-dir",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--snapshot-id",required=True)
    a=p.parse_args()
    print(json.dumps(build(a.parent_archive,a.checkpoint,a.source_dir,
                           a.out,a.snapshot_id),indent=2))
