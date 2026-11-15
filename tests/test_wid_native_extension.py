"""Synthetic-only controls for integrating official WID source rows into EXISTING Atlas SQLite."""
import csv
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import warehouse.wid_native_extension as native

from open_data.wid_live_pilot import FIELDS, CONCEPTS
from warehouse.native_materialize import schema
from warehouse.wid_native_extension import (
    EXPECTED_SELECTED_VALUES, WID_RUNS, append_to_existing_sqlite,
    load_expected_scopes, read_receipts, validate_shares, selftest,
    extract_certified_native_sqlite_core, base_inventory_checked, sha256,
)


def example_rows(country="ZZ", original_sha="a" * 64):
    return [
        dict(zip(FIELDS, [country, "2024", "p0p50", "bottom_50",
                          concept, "s"+variable+"992j", str(value),
                          "not_provided_in_source", original_sha,
                          "source_native_descriptive_not_causal"]))
        for concept, variable, value in [
            ("pretax_national_income_share", "ptinc", .30),
            ("posttax_national_income_share", "diinc", .32),
        ]
    ]


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer=csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


class WidNativeExtensionTest(unittest.TestCase):
    def test_thin_core_extraction_keeps_existing_sqlite_and_references_parent_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            old=root/"old/canonical-native-test"
            (old/"canonical_warehouse").mkdir(parents=True)
            sqlitefile=old/"canonical_warehouse/warehouse.sqlite"
            sqlitefile.write_bytes(b"original-existing-sqlite")
            manifestfile=old/"canonical_warehouse/manifest.json"
            manifestfile.write_text(json.dumps({"total_source_observations":2780117,
                                                 "native_record_count_unharmonized":2104609}))
            original=old/"source_native/giant_legacy_sources.csv"
            original.parent.mkdir(parents=True)
            original.write_bytes(b"legacy-source-data-stays-in-parent-archive")
            inv={"canonical_observations":2780117,"canonical_source_families":24,
                 "native_rows_materialized":2104609,
                 "native_records_are_queryable_in_same_sqlite":True,
                 "source_files":[{"path":str(p.relative_to(old)),
                                  "bytes":p.stat().st_size,"sha256":sha256(p)}
                                 for p in [sqlitefile,manifestfile,original]]}
            (old/"unified_inventory.json").write_text(json.dumps(inv))
            tarball=root/"original.tar.gz"
            with tarfile.open(tarball,"w:gz") as t:
                t.add(old,arcname="canonical-native-test")
            sparse=extract_certified_native_sqlite_core(tarball,root/"selected")
            self.assertEqual(sparse.name,"canonical-native-test")
            self.assertEqual((sparse/"canonical_warehouse/warehouse.sqlite").read_bytes(),
                             sqlitefile.read_bytes())
            self.assertFalse((sparse/"source_native/giant_legacy_sources.csv").exists())
            verified,_=base_inventory_checked(sparse)
            self.assertEqual(verified["native_rows_materialized"],2104609)
            sqlitefile.write_bytes(b"tampered-in-parent")
            with self.assertRaisesRegex(ValueError,"checksum"):
                base_inventory_checked(old)

    def test_small_attached_shard_is_queryable_without_copying_historical_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            info={"rows":2,"source_sha":"a"*64,"year_min":2024,"year_max":2024,
                  "matched_pairs":1,
                  "counts_by_concept":{CONCEPTS["ptinc"]:1,CONCEPTS["diinc"]:1},
                  "source_bytes":150,"run_id":99,"batch":"synthetic"}
            def private_fixture(manifest,stage):
                p=Path(stage)/"selected/ZZ/wid_source_native_shares.csv"
                write_csv(p,example_rows())
                manifest["ZZ"]["selected_csv_sha256"]=sha256(p)
                manifest["ZZ"]["original_official_source_url"] = (
                    "https://wid.world/bulk_download/WID_data_ZZ.csv")
            with (patch.object(native,"load_expected_scopes",return_value={"ZZ":{"rows":2}}),
                  patch.object(native,"read_receipts",return_value={"ZZ":info}),
                  patch.object(native,"download_private_source_files",side_effect=private_fixture),
                  patch.object(native,"EXPECTED_SELECTED_VALUES",2),
                  patch.object(native,"EXPECTED_COUNTRIES",1)):
                report=native.materialize_attachable_wid_shard(
                    root/"receipts",root/"private","test-wid-attach")
            self.assertEqual(report["added_native_wid_numeric_records"],2)
            self.assertTrue(report["original_canonical_sqlite_was_modified"] is False)
            self.assertEqual(report["logical_atlas_native_records_with_attachment"],2104611)
            tarball=root/"private/test-wid-attach.tar.gz"
            self.assertEqual(sha256(tarball),report["archive_sha256"])
            with tarfile.open(tarball,"r:gz") as src:
                names=src.getnames()
                self.assertIn("test-wid-attach/wid_source_native.sqlite",names)
                self.assertFalse(any("warehouse.sqlite" in name for name in names))
                data=src.extractfile("test-wid-attach/wid_source_native.sqlite").read()
            dbpath=root/"local-shard.sqlite"
            dbpath.write_bytes(data)
            with sqlite3.connect(dbpath) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM native_sources").fetchone()[0],1)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM native_records").fetchone()[0],2)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM atlas_parent_snapshot").fetchone()[0],1)
                self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            self.assertTrue(report["private_attachment_not_a_physical_single_db_merge"])

    def test_frozen_all_three_successful_waves_exact_no_overlap(self):
        scope=load_expected_scopes()
        self.assertEqual(len(scope),60)
        self.assertEqual(sum(item["rows"] for item in scope.values()),EXPECTED_SELECTED_VALUES)
        self.assertEqual({x["run_id"] for x in scope.values()},set(WID_RUNS))
        self.assertEqual({sum(x["run_id"]==run for x in scope.values()) for run in WID_RUNS},{20})
        original=json.loads((Path(__file__).resolve().parents[1]/
                             "research/wid_23_source_manifest.json").read_text())
        self.assertFalse(set(scope)&{x["wid_area"] for x in original["countries"]})

    def test_offline_source_native_selftest(self):
        self.assertTrue(selftest())

    def test_batch_receipts_reconcile_every_original_source_hash_and_rows(self):
        expected=load_expected_scopes()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for run in WID_RUNS:
                batches={}
                for area,item in expected.items():
                    if item["run_id"]==run:
                        batches.setdefault(item["batch"],[]).append((area,item))
                self.assertEqual(len(batches),5)
                for batch, countries in batches.items():
                    success=[
                        {"area":code,"source_file_sha256":"a"*64,
                         "source_bytes":100,"retained_source_shares":info["rows"],
                         "matched_pretax_posttax_pairs":180,
                         "first_observed_year":2000,"last_observed_year":2024,
                         "onedrive_verified":True,
                         "counts_by_concept":{}}
                        for code,info in countries
                    ]
                    receipt={"batch":batch,"successful":success,"failed":[],
                             "country_codes":[c for c,_ in countries],
                             "count_successful_countries":4,"count_failed_countries":0,
                             "source_rows_added_to_canonical_warehouse":0,
                             "raw_wid_data_published_to_github":False,
                             "verified_share_observations":sum(i["rows"] for _,i in countries)}
                    p=root/str(run)/f"{batch}.json"
                    p.parent.mkdir(parents=True,exist_ok=True)
                    p.write_text(json.dumps(receipt))
            verified=read_receipts(root,expected)
            self.assertEqual(len(verified),60)
            self.assertEqual(sum(x["rows"] for x in verified.values()),40908)
            target=next((root/str(WID_RUNS[0])).glob("*.json"))
            broken=json.loads(target.read_text())
            broken["successful"][0]["source_file_sha256"]="INVALID"
            target.write_text(json.dumps(broken))
            with self.assertRaisesRegex(ValueError,"SHA"):
                read_receipts(root,expected)

    def test_strict_source_record_validation_prevents_wrong_identity_and_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"wid_source_native_shares.csv"
            rows=example_rows()
            info={"rows":2,"source_sha":"a"*64,"year_min":2024,"year_max":2024,
                  "matched_pairs":1,"counts_by_concept":
                  {CONCEPTS["ptinc"]:1,CONCEPTS["diinc"]:1}}
            write_csv(path,rows)
            self.assertEqual(len(validate_shares(path,info,"ZZ")),2)
            write_csv(path,rows+rows[:1])
            with self.assertRaisesRegex(ValueError,"repeated"):
                validate_shares(path,info,"ZZ")
            write_csv(path,[dict(rows[0],wid_area="US"),rows[1]])
            with self.assertRaisesRegex(ValueError,"geographic"):
                validate_shares(path,info,"ZZ")
            write_csv(path,[dict(rows[0],research_status="harmonized"),rows[1]])
            with self.assertRaisesRegex(ValueError,"harmonized"):
                validate_shares(path,info,"ZZ")

    def test_insert_adds_to_existing_native_tables_only(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            path=root/"selected/ZZ/wid_source_native_shares.csv"
            write_csv(path,example_rows())
            dbpath=root/"base.sqlite"
            with sqlite3.connect(dbpath) as db:
                db.execute("CREATE TABLE observations (id INTEGER PRIMARY KEY)")
                db.execute("INSERT INTO observations VALUES (1)")
                db.execute("CREATE TABLE source_files (family TEXT PRIMARY KEY)")
                db.execute("INSERT INTO source_files VALUES ('historic')")
                db.executescript(schema())
                db.execute("INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)",
                           ("previous","b"*64,1,1,"","previous",
                            "SOURCE_NATIVE_UNHARMONIZED",0,"[]"))
                db.execute("INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)",
                           ("previous",1,"2020","2020","US","LBR","","1","{}"))
                db.commit()
            info={"rows":2,"source_sha":"a"*64,"year_min":2024,"year_max":2024,
                  "matched_pairs":1,"counts_by_concept":{CONCEPTS["ptinc"]:1,CONCEPTS["diinc"]:1},
                  "original_official_source_url":"https://wid.world/bulk_download/WID_data_ZZ.csv",
                  "source_bytes":150,"selected_csv_sha256":"c"*64,
                  "run_id":123,"batch":"synthetic"}
            result=append_to_existing_sqlite(dbpath,root/"selected",{"ZZ":info},
                                             strict_base=False)
            self.assertEqual(result["added_native_wid_numeric_records"],2)
            with sqlite3.connect(dbpath) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM observations").fetchone()[0],1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM source_files").fetchone()[0],1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM native_sources").fetchone()[0],2)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM native_records").fetchone()[0],3)
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM native_records WHERE source_id='previous'").fetchone()[0],1)
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM native_records WHERE source_id LIKE 'wid_world_ZZ_%'").fetchone()[0],2)
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")


if __name__=="__main__":
    unittest.main()
