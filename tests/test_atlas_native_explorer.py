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

from atlas_web.server import NativeAttachmentStore, geography_reference, plot_period_granularity


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
        self.assertTrue(rows["can_plot"])
        self.assertEqual(rows["plot_granularity"], "monthly")
        self.assertFalse(rows["chart_blockers"])
        output=self.store.export_csv("bls_jolts","source_native",code,["USA"],2025,2025)
        values=list(csv.DictReader(io.StringIO(output)))
        self.assertEqual(len(values),2)
        self.assertEqual(values[0]["source_id"],"bls_jolts_"+code+"_abc")
        self.assertEqual(values[0]["state_code"],"00")
        self.assertIn("series_id",values[0]["disaggregation"])

    def test_ilostat_source_original_sdg_dimensions_and_missing_flags(self):
        source_id="ilostat_SDG_0852_SEX_AGE_RT_A_abc123"
        code="SDG_0852_SEX_AGE_RT_A_0123456789abcdef"
        dims={"indicator":"SDG_0852","source":"ILOEST",
              "sex":"SEX_F","classif1":"AGE_Y15-24","classif2":""}
        original={"ref_area":"GHA","time":"2024","obs_value":"5.7",
                  "obs_status":"E","provider":"ILO / ILOSTAT",
                  "frequency":"annual","unit":"source_defined_unverified",
                  "source_url":"https://rplumber.ilo.org/data/indicator?format=.csv.gz&id=SDG_0852_SEX_AGE_RT_A",
                  "disaggregation":dims}
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE native_ilostat_series (
                source_id TEXT NOT NULL, series_code TEXT NOT NULL,
                original_dimensions_json TEXT NOT NULL,
                original_rows INTEGER NOT NULL,
                PRIMARY KEY(source_id,series_code))""")
            db.execute("INSERT INTO native_sources VALUES (?,?,?,?,?,?,?,?,?)",
                (source_id,"a"*64,1,1,
                 json.dumps({"provider":"ILO / ILOSTAT","unit":"source_defined_unverified"}),
                 "original ILOSTAT", "SOURCE_NATIVE_UNHARMONIZED",0,"[]"))
            db.execute("INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)",
                (source_id,1,"2024","2024","GHA",code,"","5.7",json.dumps(original)))
            db.execute("INSERT INTO native_ilostat_series VALUES (?,?,?,?)",
                (source_id,code,json.dumps(dims),1))
        store=NativeAttachmentStore(self.path)
        family={f["id"]:f for f in store.families()}
        self.assertEqual(family["ilostat"]["observations"],1)
        self.assertEqual(store.overview()["native_records"],7)
        selected=store.series("ilostat","source_native",q="Unemployment")
        self.assertEqual(len(selected),1)
        self.assertIn("AGE_Y15-24",selected[0]["title"])
        self.assertEqual(selected[0]["name"],code)
        # Multi-term search must match the indicator AND original source
        # classifications, rather than requiring contiguous text.
        exact=store.series("ilostat","source_native",q="Unemployment SEX_F AGE_Y15-24")
        self.assertEqual([x["name"] for x in exact],[code])
        self.assertEqual(store.series("ilostat","source_native",q="Unemployment SEX_M"),[])
        rows=store.rows("ilostat","source_native",code,["GHA"],2024,2024)
        self.assertTrue(rows["can_plot"])
        self.assertEqual(rows["plot_granularity"],"annual")
        self.assertEqual(rows["rows"][0]["sex"],"SEX_F")
        self.assertEqual(rows["rows"][0]["classif1"],"AGE_Y15-24")
        self.assertEqual(rows["rows"][0]["original_source_code"],"ILOEST")
        self.assertIn("E",rows["rows"][0]["source_status"])
        csv_data=list(csv.DictReader(io.StringIO(store.export_csv(
            "ilostat","source_native",code,["GHA"],2024,2024))))
        self.assertEqual(csv_data[0]["sex"],"SEX_F")
        self.assertEqual(csv_data[0]["original_source_code"],"ILOEST")
        self.assertTrue(any("unverified" in x.lower()
                            for x in rows["chart_warnings"]))
        # The same original indicator may appear in multiple countries,
        # but unverified source units cannot be silently charted together.
        second=dict(original,ref_area="USA",obs_value="6.2",obs_status="")
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO native_records VALUES (?,?,?,?,?,?,?,?,?)",
                       (source_id,2,"2024","2024","USA",code,"","6.2",json.dumps(second)))
            db.execute("UPDATE native_sources SET original_records=2,indexed_records=2 WHERE source_id=?",
                       (source_id,))
        store=NativeAttachmentStore(self.path)
        combined=store.rows("ilostat","source_native",code,["GHA","USA"],2024,2024)
        self.assertEqual(combined["returned"],2)
        self.assertFalse(combined["can_plot"])
        self.assertTrue(any("units have not been verified" in x
                            for x in combined["chart_blockers"]))
        self.assertEqual(len(list(csv.DictReader(io.StringIO(
            store.export_csv("ilostat","source_native",code,["GHA","USA"],2024,2024))))),2)

    def test_bad_filter_does_not_mix_provider_layers(self):
        with self.assertRaisesRegex(ValueError,"source-native"):
            self.store.rows("wdi","source_separated","SP.POP.TOTL",[],2020,2021)
        with self.assertRaisesRegex(ValueError,"source-native"):
            self.store.series("invalid-provider","source_native")
        with self.assertRaisesRegex(ValueError,"up to eight"):
            self.store.rows("wdi","source_native","SP.POP.TOTL",["USA"]*9,2020,2021)

    def test_period_axis_fails_closed_on_mixed_or_invalid_original_dates(self):
        rows = [{"frequency":"monthly","period":"2025-02"},
                {"frequency":"monthly","period":"2025-03"}]
        self.assertEqual(plot_period_granularity(rows),"monthly")
        self.assertEqual(plot_period_granularity([
            {"frequency":"monthly","period":"2025-02-01"},
            {"frequency":"monthly","period":"2025-03-01"}]),"monthly")
        self.assertEqual(plot_period_granularity([
            {"frequency":"quarterly","period":"2025-Q1"},
            {"frequency":"quarterly","period":"2025-Q2"}]),"quarterly")
        self.assertEqual(plot_period_granularity([
            {"frequency":"annual","period":"2020"},
            {"frequency":"annual","period":"2021"}]),"annual")
        self.assertEqual(plot_period_granularity([
            {"frequency":"monthly","period":"2025-02-31"}]),"unsupported")
        self.assertEqual(plot_period_granularity([
            {"frequency":"monthly","period":"2025-02"},
            {"frequency":"monthly","period":"2025-03-01"}]),"unsupported")
        self.assertEqual(plot_period_granularity([
            {"frequency":"monthly","period":"2025-03"},
            {"frequency":"quarterly","period":"2025-Q2"}]),"unsupported")
        self.assertEqual(plot_period_granularity([
            {"frequency":"monthly","period":"2025"}]),"unsupported")

    def test_connection_is_read_only(self):
        with self.store.connect() as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM native_records")


if __name__=="__main__":
    unittest.main()
