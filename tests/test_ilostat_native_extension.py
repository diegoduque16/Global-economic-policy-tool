"""Offline original IL/ILOSTAT source-native import tests. Synthetic values only."""
import csv
import gzip
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.ilostat_native_profile import profile_native_file
from warehouse.native_materialize import sha256
from warehouse.ilostat_native_extension import (
    SOURCE_BASE, append_originals, checkpoint_records, identity,
)


TABLE = "SDG_0852_SEX_AGE_RT_A"
HEADER = ["ref_area","indicator","source","sex","classif1","classif2",
          "time","obs_value","obs_status","note_source"]


def original_rows():
    return [
        ["USA","SDG_0852","ILOEST","SEX_M","AGE_Y15-24","","2024","5.4","",""],
        ["USA","SDG_0852","ILOEST","SEX_M","AGE_Y15-24","","2025","","U",""],
        ["GHA","SDG_0852","ILOEST","SEX_F","AGE_Y15-24","","2025","not reported","M",""],
    ]


class IlostatPrivateNativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.db=self.root/"native.sqlite"
        with sqlite3.connect(self.db) as db:
            db.executescript("""
                CREATE TABLE native_sources (
                    source_id TEXT PRIMARY KEY, raw_sha256 TEXT NOT NULL,
                    original_records INTEGER NOT NULL, indexed_records INTEGER NOT NULL,
                    original_scope TEXT NOT NULL, source_name TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status='SOURCE_NATIVE_UNHARMONIZED'),
                    is_harmonized INTEGER NOT NULL CHECK(is_harmonized=0),
                    fields_json TEXT NOT NULL
                );
                CREATE TABLE native_records (
                    source_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    year_text TEXT, period_text TEXT, geo_code TEXT,
                    series_code TEXT, industry_code TEXT, value_text TEXT,
                    raw_row_json TEXT NOT NULL,
                    PRIMARY KEY(source_id,ordinal)
                ) WITHOUT ROWID;
            """)
            db.execute("INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)",
                ("existing_parent","a"*64,1,1,"{}","parent","SOURCE_NATIVE_UNHARMONIZED",0,"[]"))
            db.execute("INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)",
                ("existing_parent",1,"2022","2022","USA","PARENT","","1.0","{}"))
        self.path=self.root/(TABLE+".csv.gz")
        self.write(original_rows())
        self.entry=self.make_entry()

    def write(self,rows):
        with gzip.open(self.path,"wt",encoding="utf-8",newline="") as output:
            writer=csv.writer(output)
            writer.writerow(HEADER)
            writer.writerows(rows)

    def make_entry(self):
        fingerprint=sha256(self.path)
        return {
            "state": "NATIVE_NOT_HARMONIZED",
            "sha256": fingerprint,
            "remote": SOURCE_BASE+"/indicator-data/"+TABLE+"/"+TABLE+"-20261117-"+fingerprint[:16]+".csv.gz",
            "records_validated": 3,
            "csv_header": HEADER,
            "native_numeric_profile": profile_native_file(self.path,3),
        }

    def test_source_native_rows_preserve_original_dimensions_blanks_and_flags(self):
        result=append_originals(self.db,{TABLE:self.entry},self.root,
            parent_rows=1,parent_sources=1,expected={TABLE:3})
        self.assertEqual(result["original_source_rows_added"],3)
        self.assertEqual(result["finite_numeric_values_added"],1)
        self.assertEqual(result["missing_source_values_retained"],1)
        self.assertEqual(result["other_nonnumeric_values_retained"],1)
        self.assertEqual(result["new_attachment_source_native_rows"],4)
        self.assertEqual(result["original_series_keys"],2)
        with sqlite3.connect(self.db) as db:
            rows=db.execute("SELECT source_id, geo_code,period_text,series_code,value_text,raw_row_json FROM native_records ORDER BY source_id,ordinal").fetchall()
            self.assertEqual(len(rows),4)
            self.assertEqual(rows[0][0],"existing_parent")
            actual=[r for r in rows if r[0].startswith("ilostat_")]
            self.assertEqual(len(actual),3)
            self.assertEqual(actual[0][3],actual[1][3])
            self.assertNotEqual(actual[1][3],actual[2][3])
            self.assertEqual(actual[1][4],"")
            self.assertEqual(json.loads(actual[1][5])["obs_status"],"U")
            self.assertEqual(json.loads(actual[1][5])["disaggregation"]["sex"],"SEX_M")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM native_ilostat_series").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM native_sources").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT industry_code FROM native_records WHERE source_id LIKE 'ilostat_%' LIMIT 1").fetchone()[0],"")

    def test_tampered_original_bytes_fail_and_parent_untouched(self):
        self.path.write_bytes(self.path.read_bytes()+b"altered")
        with self.assertRaisesRegex(ValueError,"SHA-verified"):
            append_originals(self.db,{TABLE:self.entry},self.root,
                parent_rows=1,parent_sources=1,expected={TABLE:3})
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM native_records").fetchone()[0],1)

    def test_bad_annual_time_rolled_back_transactionally(self):
        bad=original_rows()
        bad[1][6]="2025-Q1"
        self.write(bad)
        self.entry=self.make_entry()
        with self.assertRaisesRegex(ValueError,"annual period"):
            append_originals(self.db,{TABLE:self.entry},self.root,
                parent_rows=1,parent_sources=1,expected={TABLE:3})
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM native_records").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM native_sources").fetchone()[0],1)

    def test_changed_parent_rejected(self):
        with self.assertRaisesRegex(ValueError,"Parent private"):
            append_originals(self.db,{TABLE:self.entry},self.root,
                parent_rows=2,parent_sources=1,expected={TABLE:3})

    def test_identical_source_code_only_if_structural_dimensions_match(self):
        r1=dict(zip(HEADER,original_rows()[0]))
        r2=dict(r1)
        r2["time"]="2025"
        r2["obs_status"]="R"
        r3=dict(r2)
        r3["sex"]="SEX_F"
        self.assertEqual(identity(TABLE,r1)[0],identity(TABLE,r2)[0])
        self.assertNotEqual(identity(TABLE,r1)[0],identity(TABLE,r3)[0])

    def test_private_archive_step_exports_config_before_using_rclone(self):
        workflow=(Path(__file__).resolve().parents[1] /
                  ".github/workflows/atlas-ilostat-native-first-sdg.yml").read_text()
        config='export RCLONE_CONFIG="$root/rclone.conf"'
        remote='rclone copyto "$ONEDRIVE_TARGET/snapshots/$PARENT.tar.gz"'
        self.assertIn(config,workflow)
        self.assertIn(remote,workflow)
        self.assertLess(workflow.index(config),workflow.index(remote))
        self.assertIn("if: github.event_name != 'pull_request'",workflow)

    def test_full_three_table_scope_change_requires_recaptured_source(self):
        state={"schema_version":1,"downloaded":{TABLE:self.entry}}
        with self.assertRaisesRegex(ValueError,"Certified official ILOSTAT table absent"):
            checkpoint_records(state)


if __name__=="__main__":
    unittest.main()
