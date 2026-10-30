"""Synthetic, dependency-free tests; synthetic rows are NOT real source data."""
import csv, json, sqlite3, tempfile, unittest
from pathlib import Path
from research import build_first_economic_panel as m

class JoinTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name)
  self.wid=self.root/'wid.sqlite';self.wh=self.root/'warehouse.sqlite'
  with sqlite3.connect(self.wid) as db:
   db.executescript('CREATE TABLE source_files (wid_area TEXT); CREATE TABLE wid_numeric_observations (iso3 TEXT,year INTEGER,percentile TEXT,concept TEXT,value_fraction REAL);')
   for i in range(12):
    iso='T'+str(i).zfill(2);db.execute('INSERT INTO source_files VALUES (?)',(iso,))
    for yr in range(2010,2020):
     db.execute('INSERT INTO wid_numeric_observations VALUES (?,?,?,?,?)',(iso,yr,'p99p100','pretax_national_income_share',0.2+i*0.002))
     db.execute('INSERT INTO wid_numeric_observations VALUES (?,?,?,?,?)',(iso,yr,'p0p50','posttax_national_income_share',0.23+i*0.001))
  with sqlite3.connect(self.wh) as db:
   db.executescript('CREATE TABLE source_files (family TEXT); CREATE TABLE observations (family TEXT,country_or_polity_code TEXT,year INTEGER,series TEXT,value REAL,provider TEXT,period TEXT,unit TEXT,source_status TEXT);')
   db.executemany('INSERT INTO source_files VALUES (?)',[(str(x),) for x in range(24)])
   for i in range(12):
    iso='T'+str(i).zfill(2)
    for yr in range(2010,2020):
     db.execute('INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?)',('wdi',iso,yr,'NY.GDP.PCAP.KD.ZG',2+0.1*i,'WorldBank',str(yr),'source_defined','observed'))
  self.dictionary=self.root/'definitions.json'
  self.dictionary.write_text(json.dumps({'growth':{'NY.GDP.PCAP.KD.ZG':'x'},'labor':{x:x for x in list(m.WDI_TARGETS)[3:5]},'other':{x:x for x in [*list(m.WDI_TARGETS)[:2],*list(m.WDI_TARGETS)[2:3],*list(m.WDI_TARGETS)[5:]]}}))
  self.old=(m.WID_EXPECTED,m.WID_COUNTRIES,m.CANONICAL_EXPECTED)
  m.WID_EXPECTED=240;m.WID_COUNTRIES=12;m.CANONICAL_EXPECTED=120
  self.addCleanup(lambda:(setattr(m,'WID_EXPECTED',self.old[0]),setattr(m,'WID_COUNTRIES',self.old[1]),setattr(m,'CANONICAL_EXPECTED',self.old[2])))
 def run_build(self):return m.build(self.wid,self.wh,self.dictionary,self.root/'out')
 def test_real_numeric_country_year_join(self):
  r=self.run_build();self.assertEqual(r['country_year_rows_with_both_sources'],120)
  self.assertEqual(r['countries_with_both_sources'],12)
  self.assertEqual(r['total_matched_numeric_cells'],360)
  with (self.root/'out'/'wid_wdi_country_year_numeric.csv').open() as f:records=list(csv.DictReader(f))
  self.assertEqual(len(records),120);self.assertAlmostEqual(float(records[0]['top1_pretax_income_share']),20.)
  self.assertAlmostEqual(float(records[0]['gdp_per_capita_growth_pct']),2.)
  self.assertEqual(r['causal_effects_estimated'],0)
 def test_reject_nonreal_wid(self):
  with sqlite3.connect(self.wid) as db:db.execute("UPDATE wid_numeric_observations SET value_fraction='metadata' WHERE iso3='T00'")
  with self.assertRaisesRegex(ValueError,'non-REAL'):self.run_build()
 def test_reject_conflicting_wdi_values(self):
  with sqlite3.connect(self.wh) as db:db.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?)",('wdi','T00',2012,'NY.GDP.PCAP.KD.ZG',99,'WorldBank','2012','source_defined','observed'))
  m.CANONICAL_EXPECTED=121
  with self.assertRaisesRegex(ValueError,'Conflicting WDI'):self.run_build()
 def test_reject_missing_wdi_target_definitions(self):
  self.dictionary.write_text('{}')
  with self.assertRaisesRegex(ValueError,'Selected WDI'):self.run_build()
 def test_reject_metadata_only_macro(self):
  with sqlite3.connect(self.wh) as db:db.execute('DELETE FROM observations')
  m.CANONICAL_EXPECTED=0
  with self.assertRaisesRegex(ValueError,'No numeric WDI'):self.run_build()
 def test_exploratory_correlations_no_causal(self):
  rows=[{'year':2024,'top1_pretax_income_share':i,'gdp_per_capita_growth_pct':i*2,'top1_wealth_share':None,'bottom50_posttax_income_share':None,'gdp_per_capita_ppp_constant':None,'unemployment_modelled_ilo_pct':None,'tax_revenue_pct_gdp':None} for i in range(1,8)]
  x=m.compute_exploration(rows);self.assertEqual(len(x),1);self.assertAlmostEqual(x[0]['pearson_r_exploratory'],1);self.assertIn('NOT_CAUSAL',x[0]['interpretation'])
 def test_read_only_input(self):
  b=m.digest(self.wid);c=m.digest(self.wh);self.run_build();self.assertEqual(b,m.digest(self.wid));self.assertEqual(c,m.digest(self.wh))
if __name__=='__main__':unittest.main()
