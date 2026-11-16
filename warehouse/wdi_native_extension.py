#!/usr/bin/env python3
"""Extend the existing Atlas private SQLite ATTACH layer with verified WDI values.

This is a NEW IMMUTABLE VERSION of the same source-native SQLite layer, seeded
from the certified 60-country WID attachment. The massive historical Atlas
SQLite is never downloaded, rewritten or treated as harmonized. World Bank
original bulk source ZIP remains in its SHA-pinned PRIVATE source backup.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tarfile
import tempfile

from open_data.wdi_bulk import (
    BULK_URL, CSV_COLUMNS, GEO, PANEL, RAW_ZIP, TRANSPORT, validate_bulk_download,
)
from warehouse.native_materialize import sha256, dump
from warehouse.wid_native_extension import (
    BASE_RUN, BASE_TARBALL_SHA256, CANONICAL_ROWS, BASE_NATIVE_ROWS,
    EXPECTED_COUNTRIES as WID_COUNTRIES,
    EXPECTED_SELECTED_VALUES as WID_VALUES,
)

WID_SHARD_RUN_ID = 43720165809
WID_SHARD_ARCHIVE_SHA256 = "faa6f785156d208cd71893fd4d8806a38a9ff68047f2fdacb496271b5fbe2122"
WDI_SOURCE_RUN_ID = 43637384781
WDI_SOURCE_ARCHIVE_SHA256 = "6f6253a1626bb0bbcf5c9e3e31c49e5f03736e7c15f1e8f73064f8b1666f8f9a"
WDI_ORIGINAL_BULK_ZIP_SHA256 = "dba33648d6f453a635b076a62b91db5939f5e640c8dcfeabd094219210533100"
WDI_SOURCE_SERIES = 59
WDI_SOURCE_OBSERVATIONS = 572_179
WDI_GEOGRAPHY_CODES = 265
COMBINED_ATTACHMENT_NATIVE_ROWS = WID_VALUES + WDI_SOURCE_OBSERVATIONS
HISTORICAL_PLUS_ATTACHMENT_NATIVE_ROWS = BASE_NATIVE_ROWS + COMBINED_ATTACHMENT_NATIVE_ROWS
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")

WID_REQUIRED = {"wid_source_native.sqlite", "wid_source_manifest.json"}
WDI_REQUIRED = {"run_report.json", PANEL, RAW_ZIP}


def safe_selected_extract(archive_path, dest, expected_root, required):
    """Stream only pinned archive members; no symlinks, traversal or extras."""
    output = Path(dest)
    selected = {}
    with tarfile.open(archive_path, "r:gz") as source:
        members = source.getmembers()
        if not members or len(members) > 250:
            raise ValueError("Unexpected native source backup member inventory")
        for member in members:
            p = PurePosixPath(member.name)
            if (not p.parts or p.is_absolute() or ".." in p.parts or
                member.name.startswith("./") or member.issym() or member.islnk()):
                raise ValueError("Unsafe private source archive member")
            if p.parts[0] != expected_root:
                raise ValueError("Unexpected source archive root")
            rel = str(PurePosixPath(*p.parts[1:])) if len(p.parts) > 1 else ""
            if rel in required:
                if not member.isfile() or rel in selected:
                    raise ValueError("Missing/duplicate source archive file")
                selected[rel] = member
            elif member.isfile() and expected_root == "wdi_source_artifact":
                raise ValueError("Unexpected original World Bank source backup file")
        if set(selected) != set(required):
            raise ValueError("Original private source archive is missing mandatory files")
        if sum(x.size for x in selected.values()) > 800_000_000:
            raise ValueError("Bounded private source-native input exceeds processing budget")
        for rel, member in selected.items():
            p = output / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(member) as reader, p.open("wb") as writer:
                shutil.copyfileobj(reader, writer)
    return output


def seed_certified_wid_attachment(wid_archive, out_sqlite, wid_root):
    if sha256(wid_archive) != WID_SHARD_ARCHIVE_SHA256:
        raise ValueError("Prior certified WID native attachment source archive checksum changed")
    safe_selected_extract(
        wid_archive, wid_root,
        f"atlas-wid-native-shard-20261115-{WID_SHARD_RUN_ID}", WID_REQUIRED,
    )
    info=json.loads((Path(wid_root)/"wid_source_manifest.json").read_text())
    if (info.get("certified_parent_canonical_run_id") != BASE_RUN or
        info.get("parent_complete_archive_sha256") != BASE_TARBALL_SHA256 or
        info.get("country_sources") != WID_COUNTRIES or
        info.get("selected_original_native_numeric_observations") != WID_VALUES or
        info.get("public_release") is not False):
        raise ValueError("Old WID source-native attachment parent/source identity mismatch")
    shutil.copy2(Path(wid_root)/"wid_source_native.sqlite", out_sqlite)
    with sqlite3.connect(out_sqlite) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("WID source-native SQLite integrity failed")
        counts={
            "native_sources": WID_COUNTRIES,
            "native_records": WID_VALUES,
            "atlas_parent_snapshot": 1,
        }
        for table,n in counts.items():
            if db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] != n:
                raise ValueError("Original WID attachment numerical source count changed")
        original = db.execute(
            "SELECT canonical_run_id,original_archive_sha256,original_canonical_observations,"
            "old_native_records,native_warehouse_extended_in_place,source_native_unharmonized "
            "FROM atlas_parent_snapshot"
        ).fetchone()
        if original != (BASE_RUN,BASE_TARBALL_SHA256,CANONICAL_ROWS,BASE_NATIVE_ROWS,0,1):
            raise ValueError("WID attachment parent Atlas warehouse identity changed")


def validate_official_wdi_source(wdi_archive, stage):
    if sha256(wdi_archive) != WDI_SOURCE_ARCHIVE_SHA256:
        raise ValueError("Private WDI verified original source archive digest changed")
    safe_selected_extract(wdi_archive,stage,"wdi_source_artifact",WDI_REQUIRED)
    quality=validate_bulk_download(stage)
    report=json.loads((Path(stage)/"run_report.json").read_text())
    if (quality.get("status") != "WDI_OFFICIAL_BULK_ORIGINAL_ZIP_NUMERICALLY_VERIFIED_NOT_CANONICAL"
        or quality.get("transport") != TRANSPORT
        or quality.get("observations") != WDI_SOURCE_OBSERVATIONS
        or len(quality.get("verified_indicator_codes",[])) != WDI_SOURCE_SERIES
        or quality.get("geography_codes") != WDI_GEOGRAPHY_CODES
        or quality.get("official_bulk_zip_sha256") != WDI_ORIGINAL_BULK_ZIP_SHA256
        or quality.get("all_output_numeric_keys_and_values_matched_original_raw") is not True
        or quality.get("country_only_geographies_verified") is not False
        or report.get("observations") != WDI_SOURCE_OBSERVATIONS
        or report.get("selected_indicator_codes") != quality["verified_indicator_codes"]
        or report.get("source_url") != BULK_URL
        or report.get("canonical_integration_verified") is not False):
        raise ValueError("WDI source report original ZIP-to-panel completeness gate failed")
    return report,quality


def append_wdi_to_native_layer(dbpath, panel, report, quality, *, strict=True):
    """WDI: one native_source identity per official indicator; append only native rows."""
    expected=set(quality["verified_indicator_codes"])
    if strict and (len(expected)!=WDI_SOURCE_SERIES
                   or report["observations"]!=WDI_SOURCE_OBSERVATIONS):
        raise ValueError("WDI native series coverage changed")
    with sqlite3.connect(dbpath) as db:
        db.execute("PRAGMA foreign_keys=ON")
        existing = db.execute("SELECT COUNT(*) FROM native_records").fetchone()[0]
        existing_sources=db.execute("SELECT COUNT(*) FROM native_sources").fetchone()[0]
        if strict and (existing!=WID_VALUES or existing_sources!=WID_COUNTRIES):
            raise ValueError("Prior WID source records not preserved in Atlas attachment")
        bulk_hash=report["bulk_zip_sha256"]
        if not SHA_RE.fullmatch(bulk_hash):
            raise ValueError("Invalid official WDI source ZIP hash")
        series_rows={code:0 for code in expected}
        seen=set()
        db.execute("BEGIN IMMEDIATE")
        try:
            for series in sorted(expected):
                source_id="world_bank_wdi_"+series+"_"+bulk_hash[:12]
                source_details=json.dumps({
                    "native_indicator_code":series,"provider":"World Bank",
                    "source_run_id":WDI_SOURCE_RUN_ID,"original_bulk_zip_sha256":bulk_hash,
                    "original_source_url":BULK_URL,
                    "source_release_stage":"UNHARMONIZED_SOURCE_NATIVE_WDI_59_NOT_PUBLIC",
                    "geography_type":"UNCLASSIFIED_INCLUDES_WORLD_AND_OTHER_AGGREGATES",
                    "unit_definition":"source_defined_NOT_YET_MAPPED_TO_OFFICIAL_UNITS",
                    "canonical_observation_overlap":"POSSIBLE_REQUIRES_EXACT_VINTAGE_KEYS",
                },sort_keys=True)
                db.execute(
                    "INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)",
                    (source_id,bulk_hash,0,0,source_details,
                     "World Bank WDI official bulk ZIP",
                     "SOURCE_NATIVE_UNHARMONIZED",0,json.dumps(CSV_COLUMNS))
                )
            batch=[]
            with Path(panel).open(newline="",encoding="utf-8") as fh:
                reader=csv.DictReader(fh)
                if reader.fieldnames!=CSV_COLUMNS:
                    raise ValueError("Normalized World Bank source panel schema changed")
                for ordinal,row in enumerate(reader,start=1):
                    code=row["indicator"]
                    geo=row["country_iso3"]
                    year=row["year"]
                    if code not in expected or not GEO.fullmatch(geo):
                        raise ValueError("Incorrect WDI indicator/geography in source panel")
                    if not year.isdigit() or len(year)!=4 or not 1960<=int(year)<=2026:
                        raise ValueError("Invalid source-native WDI year")
                    if (row["source_url"]!=BULK_URL or
                        row["source"]!="World Bank WDI official bulk CSV" or
                        row["unit"]!="source_defined" or
                        row["downloaded_at_utc"]!=report["downloaded_at_utc"]):
                        raise ValueError("Source-native WDI original provenance inconsistency")
                    try:
                        value=float(row["value"])
                    except (TypeError,ValueError,OverflowError) as exc:
                        raise ValueError("WDI source value isn't numeric") from exc
                    if not math.isfinite(value):
                        raise ValueError("WDI source value isn't finite")
                    key=(geo,year,code)
                    if key in seen:
                        raise ValueError("Duplicate provider geography/year/series in WDI release")
                    seen.add(key)
                    source_id="world_bank_wdi_"+code+"_"+bulk_hash[:12]
                    series_rows[code]+=1
                    batch.append((
                        source_id,series_rows[code],year,year,geo,code,"",
                        row["value"],json.dumps(row,sort_keys=True,separators=(",",":"))
                    ))
                    if len(batch)>=5000:
                        db.executemany("INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)",batch)
                        batch=[]
            if batch:
                db.executemany("INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)",batch)
            if (len(seen)!=report["observations"] or
                set(series_rows)!=set(quality["observations_by_indicator"]) or
                series_rows!=quality["observations_by_indicator"]):
                raise ValueError("WDI source series counts differ after native SQLite insertion")
            for code,n in series_rows.items():
                sid="world_bank_wdi_"+code+"_"+bulk_hash[:12]
                db.execute("UPDATE native_sources SET original_records=?,indexed_records=? WHERE source_id=?",
                           (n,n,sid))
            if (db.execute("SELECT COUNT(*) FROM native_records").fetchone()[0]
                 != existing + len(seen) or
                db.execute("SELECT COUNT(*) FROM native_sources").fetchone()[0]
                 != existing_sources+len(expected) or
                db.execute("SELECT COUNT(*) FROM native_records WHERE source_id LIKE 'wid_world_%'").fetchone()[0]
                 != existing):
                raise ValueError("Existing source-native WID rows were changed")
            if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                raise ValueError("Native WDI/WID source SQLite integrity failed")
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return {
        "wdi_source_series_added":len(expected),
        "wdi_source_native_rows_added":len(seen),
        "prior_wid_source_native_rows_preserved":existing,
        "native_source_records_in_new_attachment":existing+len(seen),
        "logical_combined_native_rows_including_historical":BASE_NATIVE_ROWS+existing+len(seen),
        "geography_codes_including_unclassified_aggregates":len({k[0] for k in seen}),
        "official_zip_sha256":bulk_hash,
    }


def create_native_source_edition(wid_archive,wdi_archive,out_dir,snapshot_id):
    if not re.fullmatch(r"[a-zA-Z0-9_-]+",snapshot_id):
        raise ValueError("Unsafe versioned private research data edition ID")
    out_dir=Path(out_dir)
    out_dir.mkdir(parents=True,exist_ok=True)
    archive=out_dir/(snapshot_id+".tar.gz")
    receipt_path=out_dir/(snapshot_id+".integration_report.json")
    if archive.exists() or receipt_path.exists():
        raise FileExistsError("Never rewrite an immutable Atlas source-native research edition")
    with tempfile.TemporaryDirectory(dir=out_dir) as temporary:
        work=Path(temporary)
        root=work/snapshot_id
        root.mkdir()
        sqlitefile=root/"native_source_extension.sqlite"
        seed_certified_wid_attachment(wid_archive,sqlitefile,work/"verified_wid_parent")
        wdi_folder=work/"verified_wdi_source"
        report,quality=validate_official_wdi_source(wdi_archive,wdi_folder)
        result=append_wdi_to_native_layer(sqlitefile,wdi_folder/PANEL,report,quality)
        if (result["native_source_records_in_new_attachment"]!=COMBINED_ATTACHMENT_NATIVE_ROWS or
            result["logical_combined_native_rows_including_historical"]!=
            HISTORICAL_PLUS_ATTACHMENT_NATIVE_ROWS):
            raise ValueError("New Atlas WID/WDI native source attachment is not numerically complete")
        dump(root/"source_native_release_manifest.json",{
            "format":"Atlas native source ATTACH layer, SQLite, private and unharmonized",
            "certified_parent_canonical_run_id":BASE_RUN,
            "certified_parent_canonical_archive_sha256":BASE_TARBALL_SHA256,
            "parent_wid_attachment_run_id":WID_SHARD_RUN_ID,
            "parent_wid_attachment_sha256":WID_SHARD_ARCHIVE_SHA256,
            "official_wdi_source_run_id":WDI_SOURCE_RUN_ID,
            "original_wdi_source_archive_sha256":WDI_SOURCE_ARCHIVE_SHA256,
            "original_wdi_bulk_zip_sha256":WDI_ORIGINAL_BULK_ZIP_SHA256,
            "wdi_original_source_url":BULK_URL,
            "wdi_indicator_codes":quality["verified_indicator_codes"],
            "wdi_source_native_values":WDI_SOURCE_OBSERVATIONS,
            "wid_existing_source_native_values":WID_VALUES,
            "native_records_queryable_with_attachment":COMBINED_ATTACHMENT_NATIVE_ROWS,
            "historical_native_records_separate":BASE_NATIVE_ROWS,
            "historical_canonical_observations_separate":CANONICAL_ROWS,
            "all_wdi_geographies_classified_countries_vs_aggregates":False,
            "all_indicator_units_harmonized":False,
            "cross_release_overlapping_values_deduplicated":False,
            "old_canonical_sqlite_modified":False,
            "public_release":False,
            "research_ready_causal":False,
            "private_attachment_usage":"ATTACH DATABASE 'native_source_extension.sqlite' AS atlas_native; "
                  "SELECT * FROM atlas_native.native_records WHERE series_code='NY.GDP.MKTP.KD.ZG' LIMIT 10;",
        })
        with tarfile.open(archive,"w:gz") as tar:
            tar.add(root,arcname=snapshot_id)
        result.update({
            "snapshot_id":snapshot_id,
            "storage_mode":"ATLAS_ATTACHABLE_VERSIONED_SOURCE_NATIVE_EXTENSION",
            "archive_sha256":sha256(archive),
            "pinned_wid_attachment_sha256":WID_SHARD_ARCHIVE_SHA256,
            "pinned_wdi_original_archive_sha256":WDI_SOURCE_ARCHIVE_SHA256,
            "source_original_wdi_zip_numeric_keys_values_verified":True,
            "separate_canonical_observations_unchanged":CANONICAL_ROWS,
            "old_canonical_sqlite_modified":False,
            "public_release":False,
            "causal_ready":False,
        })
        dump(receipt_path,result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wid-archive",type=Path)
    parser.add_argument("--wdi-archive",type=Path)
    parser.add_argument("--out",type=Path,default=Path("native_output"))
    parser.add_argument("--snapshot-id")
    args=parser.parse_args()
    if not args.wid_archive or not args.wdi_archive or not args.snapshot_id:
        parser.error("Pinned WID attachment, original World Bank archive and new snapshot ID required")
    print(json.dumps(
        create_native_source_edition(args.wid_archive,args.wdi_archive,args.out,args.snapshot_id),
        indent=2,
    ))


if __name__=="__main__":
    main()
