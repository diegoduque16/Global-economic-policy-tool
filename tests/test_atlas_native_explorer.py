"""The existing Atlas localhost explorer can read the private five-provider native shard.

Synthetic input only; no OneDrive credentials or private source values in CI.
"""
import csv
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas_web.server import NativeAttachmentStore, geography_reference


class PrivateNativeExplorerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"native_source_extension.sqlite"
        with sqlite3.connect(self.path) as db:
            db.executescript("""
                CREATE TABLE native_sources (
                  source_id TEXT PRIMARY KEY, raw_sha256 TEXT,
                  original_records INTEGER, indexed_records INTEGER,
                  original_scope TEXT, source_name TEXT, status TEXT,
                  is_harmonized INTEGER, fields_json TEXT
                );
                CREATE TABLE native_records (
                  source_id TEXT,ordinal INTEGER,year_text TEXT,
                  period_text TEXT,geo_code TEXT,series_code TEXT,
                  industry_code TEXT,value_text TEXT,raw_row_json TEXT,
                  PRIMARY KEY(source_id,ordinal)
                );
                CREATE INDEX idx_native_source_series_period
                  ON native_records(source_id,series_code,period_text);
            """)
            sid="JTS000000000000000JOL"
            sources=[
                ("world_bank_wdi_SP.POP.TOTL_abc",2,{"native_indicator_code":"SP.POP.TOTL",
                    "provider":"World Bank","original_source_url":"https://data.worldbank.org/"}),
                ("fred_UNRATE_abc",1,{"original_source":"FRED","unit":"percent",
                    "source_url":"https://fred.stlouisfed.org/graph/fredgraph.csv?id=UNRATE"}),
                ("bls_jolts_"+sid+"_abc",2,{
                    "original_provider_series_id":sid,"unit":"thousands_of_vacancies",
                    "state_code":"00","industry_code":"000000","frequency":"monthly",
                    "source_url":"https://download.bls.gov/pub/time.series/JT/jt.data.1.AllItems"}),
                ("wid_world_BG_abc",1,{"provider":"WID","unit":"percent"}),
            ]
            for id,n,meta in sources:
                db.execute("INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)",
                           (id,"h",n,n,json.dumps(meta),"source","SOURCE_NATIVE_UNHARMONIZED",0,"[]"))
            dims={"series_id":sid,"state_code":"00","industry_code":"000000",
                  "dataelement_code":"JO","ratelevel_code":"L","seasonal":"S"}
            payload=[
                ("world_bank_wdi_SP.POP.TOTL_abc",1,"2020","2020","USA","SP.POP.TOTL","", "330",
                    {"provider":"World Bank","unit":"source_defined","source_url":"https://worldbank.org"}),
                ("world_bank_wdi_SP.POP.TOTL_abc",2,"2020","2020","WLD","SP.POP.TOTL","", "7800",
                    {"provider":"World Bank","unit":"source_defined","source_url":"https://worldbank.org"}),
                ("fred_UNRATE_abc",1,"2021","2021-01-01","USA","UNRATE","", "6.5",
                    {"provider":"FRED","unit":"percent","frequency":"monthly"}),
                ("bls_jolts_"+sid+"_abc",1,"2025-06","2025-06","USA",sid,"", "120",
                    {"provider":"US Bureau of Labor Statistics JOLTS",
                     "value":120,"year":2025,"period":"2025-06",
                     "unit":"thousands_of_vacancies","frequency":"monthly",
                     "source_url":"https://download.bls.gov/pub/time.series/JT/jt.data.1.AllItems",
                     "disaggregation":json.dumps(dims)}),
                ("bls_jolts_"+sid+"_abc",2,"2025-07","2025-07","USA",sid,"", "125",
                    {"provider":"US Bureau of Labor Statistics JOLTS",
                     "value":125,"year":2025,"period":"2025-07",
                     "unit":"thousands_of_vacancies","frequency":"monthly",
                     "source_url":"https://download.bls.gov/pub/time.series/JT/jt.data.1.AllItems",
                     "disaggregation":json.dumps(dims)}),
                ("wid_world_BG_abc",1,"2020","2020","BG","sptinc992j","", "12.1",
                    {"provider":"WID","unit":"percent","percentile":"p99p100"}),
            ]
            db.executemany("INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)",
                           [tuple(row[:8])+(json.dumps(row[8]),) for row in payload])
        self.store=NativeAttachmentStore(self.path)

    def test_verified_wdi_aggregate_reference_is_reused(self):
        names, kinds = geography_reference()
        self.assertEqual(kinds["AFE"], "aggregate")
        self.assertEqual(names["AFE"], "Africa Eastern and Southern")
        self.assertEqual(kinds["USA"], "country_or_territory")
        self.assertEqual(kinds["WLD"], "aggregate")
        self.assertNotIn("ZZZ", kinds)

    def test_provider_groups_and_native_only_counts(self):
        o=self.store.overview()
        self.assertEqual(o["native_records"],6)
        self.assertEqual(o["canonical_records"],0)
        self.assertEqual(o["native_sources"],4)
        self.assertEqual(o["native_provider_groups"],4)
        self.assertFalse(o["counts_additive"])
        families={f["id"]:f for f in self.store.families()}
        self.assertEqual(families["wdi"]["observations"],2)
        self.assertEqual(families["bls_jolts"]["observations"],2)
        self.assertNotIn("source_separated",{f["layer"] for f in self.store.families()})

    def test_country_series_and_original_source_units(self):
        rows=self.store.rows("wdi","source_native","SP.POP.TOTL",["USA"],2020,2021)
        self.assertEqual(rows["returned"],1)
        self.assertEqual(rows["rows"][0]["value"],330)
        self.assertEqual(rows["rows"][0]["unit"],"source_defined")
        self.assertTrue(rows["can_plot"])
        self.assertEqual(rows["rows"][0]["source_url"],"https://worldbank.org")
        geos = {r["code"]:r for r in self.store.geographies("wdi","source_native","SP.POP.TOTL")}
        self.assertEqual(geos["WLD"]["geography_type"], "aggregate")
        self.assertEqual(geos["USA"]["geography_type"], "country_or_territory")
        self.assertEqual(self.store.geographies("wid","source_native","sptinc992j")[0]["geography_type"], "unclassified")
        self.assertEqual(self.store.series("fred","source_native")[0]["name"],"UNRATE")

    def test_bls_search_monthly_period_not_mislabeled_annual(self):
        code="JTS000000000000000JOL"
        results=self.store.series("bls_jolts","source_native","job openings")
        self.assertEqual(len(results),1)
        self.assertIn("state 00",results[0]["title"])
        rows=self.store.rows("bls_jolts","source_native",code,["USA"],2025,2025)
        self.assertEqual(rows["returned"],2)
        self.assertEqual([r["period"] for r in rows["rows"]],["2025-06","2025-07"])
        self.assertEqual([r["year"] for r in rows["rows"]],[2025,2025])
        self.assertEqual(rows["rows"][0]["state_code"],"00")
        self.assertEqual(rows["rows"][0]["industry_code"],"000000")
        self.assertFalse(rows["can_plot"])
        self.assertIn("Nonannual periods"," ".join(rows["chart_blockers"]))
        output=self.store.export_csv("bls_jolts","source_native",code,["USA"],2025,2025)
        values=list(csv.DictReader(io.StringIO(output)))
        self.assertEqual(len(values),2)
        self.assertEqual(values[0]["source_id"],"bls_jolts_"+code+"_abc")
        self.assertEqual(values[0]["state_code"],"00")
        self.assertIn("series_id",values[0]["disaggregation"])

    def test_bad_filter_does_not_mix_provider_layers(self):
        with self.assertRaisesRegex(ValueError,"source-native"):
            self.store.rows("wdi","source_separated","SP.POP.TOTL",[],2020,2021)
        with self.assertRaisesRegex(ValueError,"source-native"):
            self.store.series("invalid-provider","source_native")
        with self.assertRaisesRegex(ValueError,"up to eight"):
            self.store.rows("wdi","source_native","SP.POP.TOTL",["USA"]*9,2020,2021)

    def test_connection_is_read_only(self):
        with self.store.connect() as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM native_records")


if __name__=="__main__":
    unittest.main()
