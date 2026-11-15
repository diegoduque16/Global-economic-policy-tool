#!/usr/bin/env python3
"""Extend the EXISTING certified Atlas canonical/native SQLite with verified WID CSVs.

Never alters canonical observations, existing native records, or old archives.
The only new rows go into existing native_sources/native_records tables. Original
WID country CSVs remain in PRIVATE OneDrive; this derived warehouse is PRIVATE.
No harmonization, imputation, causal claim, or public website publication.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone

from open_data.wid_live_pilot import FIELDS, CONCEPTS, GROUPS, STORE, OFFICIAL_TEMPLATE
from warehouse.native_materialize import extract_safe, sha256, read_json, dump

REPO = Path(__file__).resolve().parents[1]
BASE_RUN = 40617910011
BASE_TARBALL_SHA256 = "dfe43456207e8681c4a79470d39578718c51670ceeb43cf4c68a379fdc61d17b"
CANONICAL_ROWS = 2_780_117
CANONICAL_FAMILIES = 24
BASE_NATIVE_ROWS = 2_104_609
BASE_NATIVE_SOURCES = 5
WID_RUNS = (43640485476, 43643338607, 43681169408)
COUNTRIES_PER_RUN = 20
EXPECTED_COUNTRIES = 60
EXPECTED_SELECTED_VALUES = 40_908
EXPECTED_NATIVE_RECORDS = BASE_NATIVE_ROWS + EXPECTED_SELECTED_VALUES
SCOPE_FILES = (
    "research/wid_expansion_20261114_completed_scope.json",
    "research/wid_expansion_20261114_wave2_completed_scope.json",
    "research/wid_expansion_20261115_wave3_completed_scope.json",
)
VALID_SHA = re.compile(r"[0-9a-f]{64}\Z")
VALID_CODE = re.compile(r"[A-Z]{2}\Z")


def load_expected_scopes(repo=REPO):
    baseline = read_json(Path(repo) / "research/wid_23_source_manifest.json")
    old = {item["wid_area"] for item in baseline["countries"]}
    if len(old) != 23:
        raise ValueError("Certified WID 23-country baseline changed")
    expected = {}
    for run_id, filename in zip(WID_RUNS, SCOPE_FILES):
        scope = read_json(Path(repo) / filename)
        if (scope["source_run_id"] != run_id or scope["source_run_conclusion"] != "success"
            or scope["new_country_count"] != COUNTRIES_PER_RUN
            or not scope["private_onedrive_sha_verified_for_each_country"]):
            raise ValueError("WID source run/coverage scope is not a certified success")
        counted = 0
        new = {}
        batches = scope["new_country_batches"]
        if len(batches) != 5:
            raise ValueError("Expected five independently verified source batches")
        for batch in batches:
            countries = batch["countries"]
            if len(countries) != 4 or sum(countries.values()) != batch["verified_numeric_values"]:
                raise ValueError("Country row count differs from frozen batch source receipt")
            for code, n in countries.items():
                if not VALID_CODE.fullmatch(code) or not isinstance(n, int) or n < 1:
                    raise ValueError("Invalid WID official country identity or row count")
                if code in new or code in old or code in expected:
                    raise ValueError("WID overlap against existing source country identity")
                new[code] = {"rows": n, "batch": batch["batch"], "run_id": run_id}
                counted += n
        if len(new) != COUNTRIES_PER_RUN or counted != scope["source_native_verified_numeric_values"]:
            raise ValueError("WID expansion published count does not match source rows")
        expected.update(new)
    if len(expected) != EXPECTED_COUNTRIES or sum(x["rows"] for x in expected.values()) != EXPECTED_SELECTED_VALUES:
        raise ValueError("Inconsistent WID 60-country source completion totals")
    return expected


def read_receipts(receipts_dir, expected):
    """GitHub Actions artifacts are metadata receipts, never original WID rows."""
    receipts_dir = Path(receipts_dir)
    manifest = {}
    for run in WID_RUNS:
        folder = receipts_dir / str(run)
        files = list(folder.rglob("*.json"))
        if len(files) != 5:
            raise ValueError(f"Expected exactly 5 completed WID batch receipts from run {run}")
        seen_batches = set()
        for path in files:
            batch = read_json(path)
            if batch.get("batch") in seen_batches:
                raise ValueError("Duplicate WID batch receipt")
            seen_batches.add(batch["batch"])
            if (batch.get("count_successful_countries") != 4 or
                batch.get("count_failed_countries") != 0 or batch.get("failed") != [] or
                batch.get("raw_wid_data_published_to_github") is not False or
                batch.get("source_rows_added_to_canonical_warehouse") != 0):
                raise ValueError("One or more official WID source files were not fully verified")
            successful = batch.get("successful") or []
            if len(successful) != 4 or sorted(batch["country_codes"]) != sorted(x["area"] for x in successful):
                raise ValueError("Batch source-country receipt identities do not match")
            total = 0
            for item in successful:
                code = item["area"]
                scope = expected.get(code)
                if not scope or scope["run_id"] != run or scope["batch"] != batch["batch"] or code in manifest:
                    raise ValueError("WID receipt source identity absent from pinned scope")
                source_sha = item.get("source_file_sha256")
                if not isinstance(source_sha, str) or not VALID_SHA.fullmatch(source_sha):
                    raise ValueError("WID official original CSV lacks source SHA-256")
                if (not item.get("onedrive_verified") or
                    item.get("retained_source_shares") != scope["rows"] or
                    item.get("source_bytes", 0) <= 0 or item.get("source_bytes", 0) > 190*1024*1024 or
                    item.get("matched_pretax_posttax_pairs", 0) < 1):
                    raise ValueError("WID private source proof and numerical scope mismatch")
                manifest[code] = dict(scope, source_sha=source_sha,
                                      source_bytes=item["source_bytes"],
                                      matched_pairs=item["matched_pretax_posttax_pairs"],
                                      year_min=item["first_observed_year"],
                                      year_max=item["last_observed_year"],
                                      counts_by_concept=item.get("counts_by_concept", {}))
                total += scope["rows"]
            if total != batch.get("verified_share_observations"):
                raise ValueError("WID batch selected share numerical total mismatch")
    if set(manifest) != set(expected):
        raise ValueError("Missing WID source-native country receipts")
    return manifest


def validate_shares(path, info, code):
    """Verify exact original variable/period/geography keys before native indexing."""
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != FIELDS:
            raise ValueError(f"{code}: unexpected WID native share schema")
        rows = []
        unique = set()
        years = []
        concept_count = {c: 0 for c in CONCEPTS.values()}
        pairs = {}
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"{code}: malformed selected source-native record")
            if row["wid_area"] != code or row["source_file_sha256"] != info["source_sha"]:
                raise ValueError(f"{code}: unexpected native geographic or original source SHA")
            if (row["percentile"] not in GROUPS or
                row["group"] != GROUPS[row["percentile"]] or
                row["concept"] not in CONCEPTS.values() or
                not VALID_CODE.fullmatch(row["wid_area"])):
                raise ValueError(f"{code}: invalid original WID measure/percentile")
            if row["source_variable"] not in (
                "s" + concept + suffix
                for concept in CONCEPTS if CONCEPTS[concept] == row["concept"]
                for suffix in ("992j", "j992")
            ):
                raise ValueError(f"{code}: misleading source variable code")
            if row["research_status"] != "source_native_descriptive_not_causal":
                raise ValueError("WID native observations were unexpectedly labeled as harmonized")
            year = row["year"]
            if not year.isdigit() or len(year) != 4 or not 1700 <= int(year) <= 2027:
                raise ValueError(f"{code}: invalid WID year")
            value = float(row["share_fraction"])
            if not math.isfinite(value) or not (-5 <= value <= 5):
                raise ValueError(f"{code}: nonfinite or suspect income/wealth source share")
            key = (code, year, row["percentile"], row["concept"])
            if key in unique:
                raise ValueError(f"{code}: repeated source-native country/year/group/concept")
            unique.add(key)
            years.append(int(year))
            concept_count[row["concept"]] += 1
            if row["concept"] in (CONCEPTS["ptinc"], CONCEPTS["diinc"]):
                pkey = (year, row["percentile"])
                pairs.setdefault(pkey, set()).add(row["concept"])
            rows.append(row)
    if (len(rows) != info["rows"] or min(years) != info["year_min"] or
        max(years) != info["year_max"] or
        sum(v == {CONCEPTS["ptinc"], CONCEPTS["diinc"]} for v in pairs.values())
        != info["matched_pairs"]):
        raise ValueError(f"{code}: official numeric coverage / matched pair receipt mismatch")
    for concept, count in concept_count.items():
        if count != info["counts_by_concept"].get(concept, 0):
            raise ValueError(f"{code}: source-native concept count mismatch")
    return rows


def copy_remote_file(remote, local):
    local = Path(local)
    local.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["rclone", "copyto", remote, str(local), "--retries", "3", "--low-level-retries", "5"]
    subprocess.run(cmd, check=True, timeout=900)


def download_private_source_files(manifest, stage):
    """Reverify both official original source SHA and saved selected source CSV."""
    stage = Path(stage)
    for code, info in sorted(manifest.items()):
        remote = f"{STORE}/{code}/{info['source_sha'][:24]}"
        original = stage / "original_temporarily" / f"WID_data_{code}.csv"
        copy_remote_file(remote + f"/WID_data_{code}.csv", original)
        try:
            if original.stat().st_size != info["source_bytes"] or sha256(original) != info["source_sha"]:
                raise ValueError(f"{code}: private original official WID CSV changed since verified run")
        finally:
            original.unlink(missing_ok=True)
        selected = stage / "selected" / code / "wid_source_native_shares.csv"
        copy_remote_file(remote + "/wid_source_native_shares.csv", selected)
        rows = validate_shares(selected, info, code)
        info["selected_csv_sha256"] = sha256(selected)
        info["original_official_source_url"] = OFFICIAL_TEMPLATE.format(country=code)
        info["selected_native_rows"] = len(rows)
    shutil.rmtree(stage / "original_temporarily", ignore_errors=True)


def base_inventory_checked(root):
    inv = read_json(root / "unified_inventory.json")
    if (inv.get("canonical_observations") != CANONICAL_ROWS or
        inv.get("canonical_source_families") != CANONICAL_FAMILIES or
        inv.get("native_rows_materialized") != BASE_NATIVE_ROWS or
        not inv.get("native_records_are_queryable_in_same_sqlite")):
        raise ValueError("Certified original Atlas canonical/native parent snapshot changed")
    for item in inv["source_files"]:
        p = root / item["path"]
        if (not p.is_file() or p.is_symlink() or
            p.stat().st_size != int(item["bytes"]) or sha256(p) != item["sha256"]):
            raise ValueError("Existing certified warehouse snapshot file checksum mismatch")
    manifest = read_json(root / "canonical_warehouse/manifest.json")
    if (manifest.get("total_source_observations") != CANONICAL_ROWS or
        manifest.get("native_record_count_unharmonized") != BASE_NATIVE_ROWS):
        raise ValueError("Existing canonical/native source manifest changed")
    return inv, manifest


def append_to_existing_sqlite(database, selected_dir, manifest, *, strict_base=True):
    """Append 60 new source identities to the EXISTING native_* tables, never observations."""
    db = sqlite3.connect(database)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        if strict_base:
            checks = {
                "observations": CANONICAL_ROWS, "source_files": CANONICAL_FAMILIES,
                "native_sources": BASE_NATIVE_SOURCES, "native_records": BASE_NATIVE_ROWS,
            }
            for table, n in checks.items():
                count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if count != n:
                    raise ValueError(f"Existing {table} count is {count}, expected {n}")
        else:
            checks = {"observations": db.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
                      "source_files": db.execute("SELECT COUNT(*) FROM source_files").fetchone()[0],
                      "native_sources": db.execute("SELECT COUNT(*) FROM native_sources").fetchone()[0],
                      "native_records": db.execute("SELECT COUNT(*) FROM native_records").fetchone()[0]}
        cur = db.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            for code, info in sorted(manifest.items()):
                rows = validate_shares(Path(selected_dir) / code / "wid_source_native_shares.csv", info, code)
                source_id = f"wid_world_{code}_{info['source_sha'][:12]}"
                details = json.dumps({
                    "source_url": info["original_official_source_url"],
                    "provider_country_code": code, "source_run_id": info["run_id"],
                    "source_batch": info["batch"], "original_source_bytes": info["source_bytes"],
                    "selected_native_csv_sha256": info["selected_csv_sha256"],
                    "scope": "selected_wid_income_and_wealth_distribution_native_shares_only",
                    "canonical_integration": False,
                }, sort_keys=True)
                cur.execute("INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)",
                            (source_id, info["source_sha"], len(rows), len(rows), details,
                             "WID.world official country CSV", "SOURCE_NATIVE_UNHARMONIZED",
                             0, json.dumps(FIELDS)))
                payload = [(source_id, i, row["year"], row["year"], code,
                            row["source_variable"], row["percentile"], row["share_fraction"],
                            json.dumps(row, sort_keys=True, separators=(",", ":")))
                           for i, row in enumerate(rows, start=1)]
                cur.executemany("INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)", payload)
            if strict_base and sum(info["rows"] for info in manifest.values()) != EXPECTED_SELECTED_VALUES:
                raise ValueError('Production source-native row count differs from verified scope')
            if strict_base:
                for table, count in checks.items():
                    new = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    wanted = count + (len(manifest) if table == "native_sources" else
                                      EXPECTED_SELECTED_VALUES if table == "native_records" else 0)
                    if new != wanted:
                        raise ValueError("Existing canonical data changed during native WID extension")
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Extended Atlas SQLite integrity check failed")
            db.commit()
        except BaseException:
            db.rollback()
            raise
        added=sum(x["rows"] for x in manifest.values())
        return {"added_wid_country_source_files": len(manifest),
                "added_native_wid_numeric_records": added,
                "total_native_numeric_records": checks["native_records"] + added,
                "canonical_observations_unchanged": checks["observations"],
                "old_native_records_unchanged": True}
    finally:
        db.close()


def extend(base_archive, receipts, out_dir, snapshot_id, repo=REPO):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", snapshot_id):
        raise ValueError("Unsafe private snapshot ID")
    base_archive = Path(base_archive)
    if sha256(base_archive) != BASE_TARBALL_SHA256:
        raise ValueError("Certified source-parent canonical archive SHA256 mismatch")
    expected = load_expected_scopes(repo)
    manifest = read_receipts(receipts, expected)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"{snapshot_id}.tar.gz"
    report_path = out_dir / f"{snapshot_id}.integration_report.json"
    if archive.exists() or report_path.exists():
        raise FileExistsError("Refusing to overwrite an existing versioned Atlas native release")
    with tempfile.TemporaryDirectory(dir=out_dir) as temporary:
        temp = Path(temporary)
        root = extract_safe(base_archive, temp / "unpacked")
        inv, old_manifest = base_inventory_checked(root)
        selected_dir = root / "source_native" / "wid_world_selected"
        download_private_source_files(manifest, root / "source_native")
        # The selected files already landed in source_native/selected/.
        actual_selected = root / "source_native" / "selected"
        if actual_selected != selected_dir:
            actual_selected.rename(selected_dir)
        result = append_to_existing_sqlite(root / "canonical_warehouse/warehouse.sqlite",
                                           selected_dir, manifest)
        for code, info in manifest.items():
            original = selected_dir / code / "wid_source_native_shares.csv"
            if sha256(original) != info["selected_csv_sha256"]:
                raise ValueError("Selected original source-native CSV changed during warehouse extension")
        original_inventory = root / "unified_inventory.json"
        parent_manifest = root / "parent_pre_wid_warehouse_manifest.json"
        shutil.copy2(root / "canonical_warehouse/manifest.json", parent_manifest)
        old_manifest["derived_from_snapshot_id"] = old_manifest["snapshot_id"]
        old_manifest["snapshot_id"] = snapshot_id
        old_manifest["native_record_count_unharmonized"] = EXPECTED_NATIVE_RECORDS
        old_manifest["wid_world_expansion_country_sources"] = EXPECTED_COUNTRIES
        old_manifest["wid_world_expansion_source_native_rows"] = EXPECTED_SELECTED_VALUES
        for entry in old_manifest["files"]:
            if entry["path"] == "warehouse.sqlite":
                dbfile = root / "canonical_warehouse/warehouse.sqlite"
                entry["sha256"] = sha256(dbfile)
                entry["bytes"] = dbfile.stat().st_size
        dump(root / "canonical_warehouse/manifest.json", old_manifest)
        inv["parent_canonical_package_id"] = inv["snapshot_id"]
        inv["snapshot_id"] = snapshot_id
        inv["native_rows_materialized"] = EXPECTED_NATIVE_RECORDS
        inv["native_source_rows"]["wid_world_official_expansion_60_country_files"] = EXPECTED_SELECTED_VALUES
        inv["wid_world_official_countries_added"] = EXPECTED_COUNTRIES
        inv["native_rows_are_harmonized"] = False
        inv["warehouse_updated_with_staged_rows"] = False
        inv["wid_world_extension_created_utc"] = datetime.now(timezone.utc).isoformat()
        inv["wid_source_expansion_provenance"] = "source_native/wid_world_receipts.json"
        inv["warning"] = ("WID income/wealth share records indexed as 60 separate verified, source-vintage "
                          "native identities; NOT added to the 2,780,117 canonical observation rows, "
                          "not harmonized and not prepared for causal inference.")
        dump(root / "source_native/wid_world_receipts.json",
             {"baseline_snapshot_id": BASE_RUN,
              "run_ids": WID_RUNS, "verified_country_sources": EXPECTED_COUNTRIES,
              "source_native_selected_values": EXPECTED_SELECTED_VALUES,
              "countries": manifest,
              "source_native_not_harmonized": True,
              "public_release": False})
        inv["source_files"] = [
            {"path": str(p.relative_to(root)), "sha256": sha256(p), "bytes": p.stat().st_size}
            for p in sorted(root.rglob("*")) if p.is_file() and p != original_inventory
        ]
        dump(original_inventory, inv)
        # Preserve original WID rows only in private snapshot.
        root.rename(temp / snapshot_id)
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(temp / snapshot_id, arcname=snapshot_id)
        report = dict(result, snapshot_id=snapshot_id,
                      parent_native_snapshot_run_id=BASE_RUN,
                      source_wid_collection_runs=list(WID_RUNS),
                      archive_sha256=sha256(archive),
                      source_original_csv_sha256_verified_for_each_country=True,
                      source_native_sqlite_extension_only=True,
                      canonical_observations_modified=False,
                      public_release=False,
                      research_ready_harmonized=False)
        dump(report_path, report)
    return report


def selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        csvpath = root / "wid_source_native_shares.csv"
        code = "ZZ"
        original_sha = "a" * 64
        rows = [
            dict(zip(FIELDS, [code, "2024", "p0p50", "bottom_50", concept,
                              "s"+source+"992j", str(value), "not_provided_in_source",
                              original_sha, "source_native_descriptive_not_causal"]))
            for concept, source, value in
            [("pretax_national_income_share","ptinc",0.3),
             ("posttax_national_income_share","diinc",0.32)]
        ]
        with csvpath.open("w", newline="") as f:
            writer=csv.DictWriter(f,fieldnames=FIELDS)
            writer.writeheader();writer.writerows(rows)
        info=dict(rows=2, source_sha=original_sha, year_min=2024, year_max=2024,
                  matched_pairs=1, counts_by_concept={CONCEPTS["ptinc"]:1,CONCEPTS["diinc"]:1})
        parsed=validate_shares(csvpath,info,code)
        assert len(parsed)==2
        invalid=rows[:]
        invalid[1]=dict(rows[1],source_file_sha256="b"*64)
        with csvpath.open("w", newline="") as f:
            writer=csv.DictWriter(f,fieldnames=FIELDS)
            writer.writeheader();writer.writerows(invalid)
        try:
            validate_shares(csvpath,info,code)
        except ValueError:
            pass
        else:
            raise AssertionError("Source hash mutation was accepted")
        expected=load_expected_scopes()
        assert len(expected)==60 and sum(i["rows"] for i in expected.values())==40908
        print("WID_NATIVE_SOURCE_EXTENSION_OFFLINE_TEST_PASSED")
    return True


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-archive",type=Path)
    ap.add_argument("--receipts",type=Path)
    ap.add_argument("--out",type=Path,default=Path("native_output"))
    ap.add_argument("--snapshot-id")
    ap.add_argument("--self-test",action="store_true")
    args=ap.parse_args()
    if args.self_test:
        selftest();return
    if not args.base_archive or not args.receipts or not args.snapshot_id:
        ap.error("Verified base archive, 15 batch receipts and new immutable snapshot ID are required")
    print(json.dumps(extend(args.base_archive,args.receipts,args.out,args.snapshot_id),indent=2))


if __name__ == "__main__":
    main()
