"""Regression tests for the unified read-only Atlas SQLite explorer."""
import csv
import io
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from atlas_web.server import AtlasStore, AtlasHandler, ThreadingHTTPServer


class AtlasWebTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.path = Path(self.t.name)/"warehouse.sqlite"
        with sqlite3.connect(self.path) as db:
            db.executescript("""
                CREATE TABLE source_files (
                    family TEXT PRIMARY KEY,csv_rows INTEGER,first_year INTEGER,
                    last_year INTEGER,country_codes INTEGER,series_count INTEGER
                );
                CREATE TABLE observations (
                    family TEXT, provider TEXT,country_or_polity_code TEXT,
                    year INTEGER,period TEXT,series TEXT,value REAL,unit TEXT,
                    frequency TEXT,source_status TEXT,source_url TEXT
                );
                CREATE TABLE snapshots (snapshot_id TEXT,created_utc TEXT,data_harmonized INTEGER);
                CREATE TABLE native_sources (
                    source_id TEXT,source_name TEXT,indexed_records INTEGER
                );
                CREATE TABLE native_records (
                    source_id TEXT,geo_code TEXT,year_text TEXT,period_text TEXT,
                    series_code TEXT,value_text TEXT
                );
            """)
            db.execute("INSERT INTO snapshots VALUES(?,?,?)",("atlas-test","2026-11-14T00:00:00Z",0))
            db.executemany("INSERT INTO source_files VALUES(?,?,?,?,?,?)",[
                ("wdi",4,2020,2021,2,2),("labor",2,2020,2020,1,1)
            ])
            db.executemany("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",[
                ("wdi","World Bank Indicators API","USA",2020,"2020","GDP",2.5,"percent","annual","","https://example.org"),
                ("wdi","World Bank Indicators API","USA",2021,"2021","GDP",3.1,"percent","annual","","https://example.org"),
                ("wdi","World Bank Indicators API","GBR",2020,"2020","GDP",1.2,"percent","annual","","https://example.org"),
                ("wdi","World Bank Indicators API","USA",2020,"2020","POP",1000,"persons","annual","","https://example.org"),
                ("labor","BLS","USA",2020,"2020-Q1","WAGES",12.0,"USD","quarterly","","https://example.org"),
                ("labor","BLS","USA",2020,"2020-Q2","WAGES",14.0,"USD","quarterly","","https://example.org"),
            ])
            db.executemany("INSERT INTO native_sources VALUES (?,?,?)",[
                ("wdi_expanded","Expanded World Bank",2),("qcew","QCEW raw",2)
            ])
            db.executemany("INSERT INTO native_records VALUES (?,?,?,?,?,?)",[
                ("wdi_expanded","USA","2020","2020","GDP", "2.5"),
                ("wdi_expanded","USA","2021","2021","GDP", "S"),
                ("qcew","01","2020","2020-Q1","EMP","125"),
                ("qcew","01","2020","2020-Q2","EMP","127"),
            ])
        self.store=AtlasStore(self.path)

    def test_overview_keeps_canonical_and_native_separate(self):
        o=self.store.overview()
        self.assertEqual(o["canonical_records"],6)
        self.assertEqual(o["native_records"],4)
        self.assertEqual(o["canonical_families"],2)
        self.assertEqual(o["native_sources"],2)
        self.assertEqual(o["snapshots"][0]["snapshot_id"],"atlas-test")
        self.assertFalse(o["counts_additive"])
        self.assertFalse(o["research_ready"])
        self.assertIsNotNone(o["catalog"])
        self.assertGreater(o["catalog"]["source_descriptions"],100)
        self.assertGreater(o["catalog"]["priority_sources"],50)
        self.assertIn("exact_series_audits_verified_by_registry",o["catalog"])

    def test_series_country_filters_and_original_values(self):
        self.assertEqual([x["name"] for x in self.store.series("wdi","source_separated","GD")],["GDP"])
        self.assertEqual({x["code"] for x in self.store.geographies("wdi","source_separated","GDP")},{"USA","GBR"})
        geos={r["code"]:r["name"] for r in self.store.geographies("wdi","source_separated","GDP")}
        self.assertEqual(geos["USA"],"United States")
        self.assertEqual(geos["GBR"],"United Kingdom")
        got=self.store.rows("wdi","source_separated","GDP",["USA","GBR"],2020,2021)
        self.assertEqual(got["returned"],3)
        self.assertTrue(got["can_plot"])
        self.assertEqual([x["value"] for x in got["rows"]],[1.2,2.5,3.1])
        self.assertEqual(got["units"],["percent"])
        self.assertTrue(got["not_cross_provider_harmonized"])

    def test_native_suppression_is_not_cast_to_zero(self):
        r=self.store.rows("wdi_expanded","source_native","GDP",["USA"],2020,2021)
        self.assertEqual(r["returned"],2)
        self.assertEqual(r["rows"][0]["value"],2.5)
        self.assertEqual(r["rows"][1]["raw_value"],"S")
        self.assertIsNone(r["rows"][1]["value"])
        self.assertTrue(r["can_plot"])
        self.assertIn("suppressed"," ".join(r["chart_warnings"]).lower())
        self.assertFalse(r["not_cross_provider_harmonized"] is False)

    def test_subannual_values_are_not_misplotted_as_one_annual_point(self):
        r=self.store.rows("labor","source_separated","WAGES",["USA"],2020,2021)
        self.assertEqual(r["returned"],2)
        self.assertFalse(r["can_plot"])
        self.assertIn("Multiple observations within a year"," ".join(r["chart_blockers"]))
        q=self.store.rows("qcew","source_native","EMP",["01"],2020,2020)
        self.assertFalse(q["can_plot"])

    def test_invalid_family_limit_and_filter_rejected(self):
        with self.assertRaisesRegex(ValueError,"Choose a source"):
            self.store.rows("imaginary","source_separated","GDP",[],2020,2021)
        with self.assertRaisesRegex(ValueError,"up to eight"):
            self.store.rows("wdi","source_separated","GDP",["USA"]*9,2020,2021)
        with self.assertRaisesRegex(ValueError,"year range"):
            self.store.rows("wdi","source_separated","GDP",[],2022,2020)

    def test_export_csv_and_no_silent_truncation(self):
        text=self.store.export_csv("wdi","source_separated","GDP",["USA"],2020,2021)
        rows=list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]["value"],"2.5")
        old_limit=sys.modules["atlas_web.server"].CSV_LIMIT
        try:
            sys.modules["atlas_web.server"].CSV_LIMIT=1
            with self.assertRaisesRegex(ValueError,"exceeds"):
                self.store.export_csv("wdi","source_separated","GDP",["USA"],2020,2021)
        finally:
            sys.modules["atlas_web.server"].CSV_LIMIT=old_limit

    def test_http_serves_warehouse_json_and_private_assets(self):
        class Handler(AtlasHandler):
            store=self.store
        http=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        worker=threading.Thread(target=http.serve_forever,daemon=True)
        worker.start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        base="http://127.0.0.1:"+str(http.server_address[1])
        with urlopen(base+"/api/overview") as r:
            data=json.load(r)
            self.assertEqual(data["native_records"],4)
        with urlopen(base+"/api/rows?family=wdi&layer=source_separated&series=GDP&geos=USA&start=2020&end=2021") as r:
            data=json.load(r)
            self.assertEqual(data["returned"],2)
            self.assertTrue(data["can_plot"])
        for path in ("/","/style.css","/app.js"):
            with urlopen(base+path) as r:
                self.assertEqual(r.status,200)
                self.assertGreater(len(r.read()),100)
                self.assertEqual(r.headers.get("Cache-Control"),"no-store")
        with self.assertRaises(HTTPError) as e:
            urlopen(base+"/api/rows?family=wdi&layer=unknown&series=GDP")
        self.assertEqual(e.exception.code,400)

if __name__=="__main__":
    unittest.main()
