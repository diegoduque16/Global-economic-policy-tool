"""Offline WDI original source-to-existing-Atlas-native-attachment integration tests."""
from __future__ import annotations
import csv
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import warehouse.wdi_native_extension as ext
from open_data.wdi_bulk import BULK_URL, collect_bulk
from warehouse.native_materialize import schema, sha256
from test_wdi_bulk import make_source, CODES


def make_wid_attachment(root):
    root=Path(root)
    parent=root/f"atlas-wid-native-shard-20261115-{ext.WID_SHARD_RUN_ID}"
    parent.mkdir(parents=True)
    dbfile=parent/"wid_source_native.sqlite"
    with sqlite3.connect(dbfile) as db:
        db.executescript(schema())
        db.execute("""CREATE TABLE atlas_parent_snapshot (
                canonical_run_id INTEGER NOT NULL,
                original_archive_sha256 TEXT NOT NULL,
                original_canonical_observations INTEGER NOT NULL,
                old_native_records INTEGER NOT NULL,
                native_warehouse_extended_in_place INTEGER NOT NULL,
                source_native_unharmonized INTEGER NOT NULL)""")
        db.execute("INSERT INTO atlas_parent_snapshot VALUES (?,?,?,?,?,?)",
                   (ext.BASE_RUN, ext.BASE_TARBALL_SHA256, ext.CANONICAL_ROWS,
                    ext.BASE_NATIVE_ROWS,0,1))
        db.execute("INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)",
                   ("wid_world_ZZ_aaaaaaaaaaaa","a"*64,1,1,"{}", "WID original",
                    "SOURCE_NATIVE_UNHARMONIZED",0,"[]"))
        db.execute("INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)",
                   ("wid_world_ZZ_aaaaaaaaaaaa",1,"2024","2024","ZZ","sptinc992j",
                    "p0p50","0.4",'{"from":"WID","share":"0.4"}'))
        db.commit()
    (parent/"wid_source_manifest.json").write_text(json.dumps({
        "certified_parent_canonical_run_id":ext.BASE_RUN,
        "parent_complete_archive_sha256":ext.BASE_TARBALL_SHA256,
        "country_sources":1,
        "selected_original_native_numeric_observations":1,
        "public_release":False,
    }))
    archive=root/"parent-wid.tar.gz"
    with tarfile.open(archive,"w:gz") as tar:
        tar.add(parent,arcname=parent.name)
    return archive


def make_wdi_backup(root):
    root=Path(root)
    original=root/"WDI_original_synthetic.zip"
    make_source(original)
    release=root/"source"
    collect_bulk(release,CODES,source_zip=original)
    wdi_archive=root/"wdi-source-synthetic.tar.gz"
    with tarfile.open(wdi_archive,"w:gz") as tar:
        for p in sorted(release.rglob("*")):
            if p.is_file():
                tar.add(p,arcname="wdi_source_artifact/"+str(p.relative_to(release)))
    return wdi_archive,release


class AtlasWdiNativeResearchExtension(unittest.TestCase):
    def test_private_source_restore_exports_rclone_config_in_same_step(self):
        # Regression for failed production run 43779951282: writing GITHUB_ENV
        # alone only configures the NEXT step, not rclone downloads below.
        workflow=(Path(__file__).resolve().parents[1]/
                  '.github/workflows/materialize-canonical-native.yml').read_text()
        start=workflow.index("      - name: Restore the two certified original-source private archives")
        end=workflow.index("      - name: Make all 59 original World Bank",start)
        script=workflow[start:end]
        self.assertIn('export RCLONE_CONFIG="$RUNNER_TEMP/wdi-native-rclone/rclone.conf"',script)
        self.assertIn('echo "RCLONE_CONFIG=$RCLONE_CONFIG" >> "$GITHUB_ENV"',script)
        self.assertLess(script.index('export RCLONE_CONFIG='),
                        script.index('rclone copyto '))
        self.assertIn('test "$(sha256sum "$wid"',script)
        self.assertIn('test "$(sha256sum "$wdi"',script)

    def test_source_native_shard_preserves_wid_rows_and_aggregates(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            parent=make_wid_attachment(root)
            backup,release=make_wdi_backup(root)
            expected_shas={
                "WID_SHARD_ARCHIVE_SHA256":sha256(parent),
                "WDI_SOURCE_ARCHIVE_SHA256":sha256(backup),
                "WDI_ORIGINAL_BULK_ZIP_SHA256":sha256(release/"raw/WDI_CSV.zip"),
                "WID_COUNTRIES":1,"WID_VALUES":1,
                "WDI_SOURCE_SERIES":2,"WDI_SOURCE_OBSERVATIONS":4,
                "WDI_GEOGRAPHY_CODES":2,
                "COMBINED_ATTACHMENT_NATIVE_ROWS":5,
                "HISTORICAL_PLUS_ATTACHMENT_NATIVE_ROWS":ext.BASE_NATIVE_ROWS+5,
            }
            with unittest.mock.patch.multiple(ext,**expected_shas):
                report=ext.create_native_source_edition(
                    parent,backup,root/"out","test-attach-wid-wdi")
            self.assertEqual(report["wdi_source_series_added"],2)
            self.assertEqual(report["wdi_source_native_rows_added"],4)
            self.assertEqual(report["prior_wid_source_native_rows_preserved"],1)
            self.assertEqual(report["native_source_records_in_new_attachment"],5)
            self.assertEqual(report["geography_codes_including_unclassified_aggregates"],2)
            self.assertEqual(report["logical_combined_native_rows_including_historical"],ext.BASE_NATIVE_ROWS+5)
            self.assertFalse(report["old_canonical_sqlite_modified"])
            with tarfile.open(root/"out/test-attach-wid-wdi.tar.gz","r:gz") as tar:
                names=tar.getnames()
                self.assertIn("test-attach-wid-wdi/native_source_extension.sqlite",names)
                self.assertFalse(any("canonical_warehouse" in x for x in names))
                dbbytes=tar.extractfile("test-attach-wid-wdi/native_source_extension.sqlite").read()
            dbfile=root/"queryable-native.sqlite"
            dbfile.write_bytes(dbbytes)
            with sqlite3.connect(dbfile) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM native_sources").fetchone()[0],3)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM native_records").fetchone()[0],5)
                self.assertEqual(db.execute(
                    "SELECT value_text FROM native_records WHERE source_id LIKE 'wid_world_%'"
                ).fetchone()[0],"0.4")
                self.assertEqual(db.execute(
                    "SELECT value_text FROM native_records WHERE geo_code='USA' "
                    "AND series_code=?", (CODES[0],)).fetchall(),[("0.0",),("3.2",)])
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM native_records WHERE geo_code='WLD'").fetchone()[0],1)
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            self.assertEqual(report["archive_sha256"],
                             sha256(root/"out/test-attach-wid-wdi.tar.gz"))

    def test_original_source_archive_corruption_rejected_before_ingest(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            parent=make_wid_attachment(root)
            backup,release=make_wdi_backup(root)
            with patch.object(ext,"WID_SHARD_ARCHIVE_SHA256",sha256(parent)):
                with patch.object(ext,"WID_COUNTRIES",1), patch.object(ext,"WID_VALUES",1):
                    ext.seed_certified_wid_attachment(
                        parent,root/"copy.sqlite",root/"selected")
            with patch.object(ext,"WDI_SOURCE_ARCHIVE_SHA256",sha256(backup)):
                with patch.object(ext,"WDI_SOURCE_SERIES",2),\
                     patch.object(ext,"WDI_SOURCE_OBSERVATIONS",4),\
                     patch.object(ext,"WDI_GEOGRAPHY_CODES",2),\
                     patch.object(ext,"WDI_ORIGINAL_BULK_ZIP_SHA256",sha256(release/"raw/WDI_CSV.zip")):
                    quality=ext.validate_official_wdi_source(backup,root/"verified")
                    self.assertEqual(quality[1]["observations"],4)
            with backup.open("ab") as stream:
                stream.write(b"TAMPER")
            with self.assertRaisesRegex(ValueError,"digest changed"):
                ext.validate_official_wdi_source(backup,root/"untouched")
            with parent.open("ab") as stream:
                stream.write(b"TAMPER")
            with self.assertRaisesRegex(ValueError,"checksum changed"):
                ext.seed_certified_wid_attachment(parent,root/"unsafe.sqlite",root/"unsafe")

    def test_bad_duplicate_wdi_source_row_never_appended(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            parent=make_wid_attachment(root)
            backup,release=make_wdi_backup(root)
            with patch.object(ext,"WID_SHARD_ARCHIVE_SHA256",sha256(parent)),\
                 patch.object(ext,"WID_COUNTRIES",1),\
                 patch.object(ext,"WID_VALUES",1):
                ext.seed_certified_wid_attachment(parent,root/"db.sqlite",root/"source")
            report=json.loads((release/"run_report.json").read_text())
            quality={"verified_indicator_codes":CODES,
                     "observations_by_indicator":{CODES[0]:3,CODES[1]:1}}
            with (release/"processed/wdi_panel.csv").open() as inp:
                rows=list(csv.DictReader(inp))
            with (root/"repeated.csv").open("w",newline="") as out:
                writer=csv.DictWriter(out,fieldnames=list(rows[0]))
                writer.writeheader();writer.writerows(rows+rows[:1])
            with self.assertRaisesRegex(ValueError,"Duplicate"):
                ext.append_wdi_to_native_layer(
                    root/"db.sqlite",root/"repeated.csv",report,quality,strict=False)
            with sqlite3.connect(root/"db.sqlite") as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM native_records").fetchone()[0],1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM native_sources").fetchone()[0],1)


if __name__=="__main__":
    unittest.main()
