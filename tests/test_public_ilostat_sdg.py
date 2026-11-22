"""Synthetic fail-closed tests for public ILOSTAT annual labour SDG export."""
import csv
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from warehouse.native_materialize import schema
from warehouse.export_public_ilostat_sdg import export

TABLES=("SDG_0831_SEX_ECO_RT_A","SDG_0851_SEX_OCU_NB_A","SDG_0852_SEX_AGE_RT_A")

class ILOSTATReleaseTests(unittest.TestCase):
    def setUp(self):
        td=tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root=Path(td.name)
        self.db=self.root/"native.sqlite"
        self.pointer=self.root/"pointer.json"
        self.pointer.write_text("{}")
        with sqlite3.connect(self.db) as db:
            db.executescript(schema())
            db.execute("""CREATE TABLE native_ilostat_series
                (source_id TEXT,series_code TEXT,original_dimensions_json TEXT,
                original_rows INTEGER, PRIMARY KEY(source_id,series_code))""")
            for j,table in enumerate(TABLES):
                sid="ilostat_"+table+"_"+str(j)*12
                raw_sha=str(j)*64
                code=table+"_abcdef0123456789"
                db.execute("INSERT INTO native_sources VALUES(?,?,?,?,?,?,?,?,?)",
                    (sid,raw_sha,2,2,"{}",table,"SOURCE_NATIVE_UNHARMONIZED",0,"[]"))
                db.execute("INSERT INTO native_ilostat_series VALUES(?,?,?,?)",
                    (sid,code,json.dumps({"sex":"SEX_T","classif1":"_T"}),2))
                db.executemany("INSERT INTO native_records VALUES(?,?,?,?,?,?,?,?,?)",[
                    (sid,1,"2020","2020","USA",code,"","12.5",json.dumps({"private_secret":"EXCLUDE"})),
                    (sid,2,"2021","2021","USA",code,"","",json.dumps({"private_secret":"EXCLUDE"}))
                ])
            db.execute("INSERT INTO native_sources VALUES(?,?,?,?,?,?,?,?,?)",
                ("worldbank_wdi","e"*64,1,1,"{}", "other",
                 "SOURCE_NATIVE_UNHARMONIZED",0,"[]"))
            db.execute("INSERT INTO native_records VALUES(?,?,?,?,?,?,?,?,?)",
                ("worldbank_wdi",1,"2020","2020","USA","WB","", "9999","{}"))

    def perform(self):
        fake={table:(table,2) for table in TABLES}
        with patch("warehouse.export_public_ilostat_sdg.TABLES",fake),\
             patch("warehouse.export_public_ilostat_sdg.TOTAL",6),\
             patch("warehouse.export_public_ilostat_sdg.EXPECTED_FINITE",3),\
             patch("warehouse.export_public_ilostat_sdg.load_pointer",
                   return_value={"edition_id":"test-edition","archive_sha256":"a"*64}),\
             patch("warehouse.export_public_ilostat_sdg.verify_parent_db",
                   return_value=frozenset(TABLES)):
            return export(self.db,self.pointer,self.root/"public")

    def test_only_ilo_statistical_source_fields_and_citations(self):
        manifest=self.perform()
        self.assertEqual(manifest["source_native_records"],6)
        self.assertEqual(manifest["finite_numeric_records"],3)
        self.assertEqual(manifest["original_table_count"],3)
        with zipfile.ZipFile(self.root/"public"/manifest["file"]) as z:
            csv_string=z.read("ilostat_sdg_labour_source_native.csv").decode()
            rows=list(csv.DictReader(io.StringIO(csv_string)))
            self.assertEqual(len(rows),6)
            self.assertNotIn("9999",csv_string)
            self.assertNotIn("EXCLUDE",csv_string)
            self.assertEqual(rows[0]["year"],"2020")
            self.assertEqual(rows[1]["source_status"],"source_missing_or_nonnumeric")
            self.assertIn("International Labour Organization",
                          z.read("CITATION_AND_USAGE.txt").decode())

    def test_source_mismatch_fails_closed(self):
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE native_sources SET indexed_records=999 WHERE source_id LIKE 'ilostat_%'")
        with self.assertRaisesRegex(ValueError,"Invalid ILOSTAT original source receipt"):
            self.perform()

if __name__=="__main__":
    unittest.main()
