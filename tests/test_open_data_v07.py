import sys,unittest,json,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'open_data'))
from ingest import normalize,run
class TestOpenData(unittest.TestCase):
 def test_normalize(self):
  x=normalize({'countryiso3code':'USA','date':'2020','value':2.5},'X','https://example.org','now')
  self.assertEqual((x['country_iso3'],x['year'],x['value']),('USA',2020,2.5))
 def test_missing_is_not_zero(self):
  self.assertIsNone(normalize({'countryiso3code':'USA','date':'2020','value':None},'X','url','now'))
 def test_bad_country(self):
  self.assertIsNone(normalize({'countryiso3code':'','date':'2020','value':2},'X','url','now'))
 def test_reject_nonfinite_and_non_numeric_values(self):
  for value in (float('nan'),float('inf'),-float('inf'),'NaN','Infinity','bad',True):
   with self.subTest(value=str(value)):
    with self.assertRaisesRegex(ValueError,'numeric observation'):
     normalize({'countryiso3code':'USA','date':'2020','value':value},'X','url','now')
 def test_reject_out_of_range_year(self):
  def mock(url):
   return json.dumps([{'page':1,'pages':1},[{'countryiso3code':'USA','date':'2019','value':3.3}]]).encode()
  with tempfile.TemporaryDirectory() as tmp:
   result=run(tmp,'USA',2020,2021,['TEST'],mock)
   self.assertEqual(result['observations'],0)
   self.assertIn('outside requested years',result['errors'][0]['error'])
   self.assertEqual(result['validation_state'],'COLLECTION_INCOMPLETE_OR_CONFLICTING')
 def test_reject_mismatched_pagination(self):
  def mock(url):
   return json.dumps([{'page':2,'pages':2},[{'countryiso3code':'USA','date':'2020','value':3.3}]]).encode()
  with tempfile.TemporaryDirectory() as tmp:
   result=run(tmp,'USA',2020,2021,['TEST'],mock)
   self.assertEqual(result['observations'],0)
   self.assertIn('page number/count',result['errors'][0]['error'])
 def test_reject_partial_indicator_series_on_invalid_value(self):
  def mock(url):
   page=2 if 'page=2' in url else 1
   val='NaN' if page==2 else 3.3
   return json.dumps([{'page':page,'pages':2},[{'countryiso3code':'USA','date':str(2019+page),'value':val}]]).encode()
  with tempfile.TemporaryDirectory() as tmp:
   result=run(tmp,'USA',2020,2021,['TEST'],mock)
   self.assertEqual(result['observations'],0)
   self.assertEqual(len(result['errors']),1)
   self.assertEqual(result['raw_pages'],2)
   self.assertEqual(result['validation_state'],'COLLECTION_INCOMPLETE_OR_CONFLICTING')
 def test_valid_result_remains_noncanonical(self):
  def mock(url):
   return json.dumps([{'page':1,'pages':1},[{'countryiso3code':'USA','date':'2020','value':3.3}]]).encode()
  with tempfile.TemporaryDirectory() as tmp:
   result=run(tmp,'USA',2020,2021,['TEST'],mock)
   self.assertEqual(result['observations'],1)
   self.assertEqual(result['validation_state'],'COLLECTED_BASIC_FORMAT_CHECKS_PASSED_NOT_CANONICAL')
 def test_pagination_manifest(self):
  def mock(url):
   page=2 if 'page=2' in url else 1
   return json.dumps([{'page':page,'pages':2},[{'countryiso3code':'USA','date':str(2019+page),'value':page}]]).encode()
  with tempfile.TemporaryDirectory() as tmp:
   r=run(tmp,'USA',2020,2021,['TEST'],mock)
   self.assertEqual((r['raw_pages'],r['observations'],len(r['errors'])),(2,2,0))
   self.assertEqual(len(list((Path(tmp)/'raw').glob('*.json'))),2)
 def test_failed_fetch_reported(self):
  with tempfile.TemporaryDirectory() as tmp:
   r=run(tmp,'USA',2020,2021,['X'],lambda u: (_ for _ in ()).throw(OSError('offline')))
   self.assertEqual(r['observations'],0);self.assertEqual(len(r['errors']),1)
 def test_world_bank_provider_geography_full_collection(self):
  from wdi_geography import COUNTRY,AGGREGATE
  indicator='NY.GDP.MKTP.KD.ZG'
  def mock(url):
   if '/country?' in url:
    entries=[
      {'id':'USA','name':'United States','region':{'id':'NAC','value':'North America'}},
      {'id':'WLD','name':'World','region':{'id':'NA','value':'Aggregates'}},
      {'id':'XKX','name':'Kosovo','region':{'id':'ECS','value':'Europe & Central Asia'}},
    ]
    return json.dumps([{'page':1,'pages':1,'total':3},entries]).encode()
   rows=[{'indicator':{'id':indicator},'countryiso3code':code,
          'date':'2020','value':value}
         for code,value in [('USA',2.5),('WLD',1.2),('XKX',3.6)]]
   return json.dumps([{'page':1,'pages':1},rows]).encode()
  with tempfile.TemporaryDirectory() as tmp:
   result=run(tmp,'all',2020,2026,[indicator],mock)
   self.assertEqual(result['errors'],[])
   self.assertEqual(result['observations'],3)
   geo=result['geography_metadata']
   self.assertEqual(geo['status'],'PROVIDER_COUNTRY_AGGREGATE_METADATA_VERIFIED')
   self.assertEqual(geo['summary']['country_or_territory_codes'],2)
   self.assertEqual(geo['summary']['aggregate_codes'],1)
   self.assertTrue(geo['summary']['country_vs_aggregate_resolved'])
   import csv
   with (Path(tmp)/'processed'/'wdi_geography.csv').open() as f:
    got={r['code']:r for r in csv.DictReader(f)}
   self.assertEqual(got['WLD']['geography_type'],AGGREGATE)
   self.assertEqual(got['XKX']['geography_type'],COUNTRY)
   self.assertEqual(got['USA']['geography_type'],COUNTRY)
   self.assertEqual(len(list((Path(tmp)/'geography_raw').glob('*.json'))),1)
 def test_world_bank_provider_metadata_incomplete_is_not_assumed(self):
  indicator='NY.GDP.MKTP.KD.ZG'
  def mock(url):
   if '/country?' in url:
    # Country metadata has an unresolved provider code. The original data
    # still exist, but aggregate/country classification is not complete.
    return json.dumps([{'page':1,'pages':1,'total':1},
                       [{'id':'USA','name':'United States','region':{'id':'NAC','value':'North America'}}]]).encode()
   return json.dumps([{'page':1,'pages':1},
                      [{'indicator':{'id':indicator},'countryiso3code':'WLD',
                        'date':'2020','value':1.3}]]).encode()
  with tempfile.TemporaryDirectory() as tmp:
   r=run(tmp,'all',2020,2026,[indicator],mock)
   self.assertEqual(r['errors'],[])
   self.assertEqual(r['geography_metadata']['status'],'PROVIDER_GEOGRAPHY_PARTIALLY_UNRESOLVED')
   self.assertEqual(r['geography_metadata']['summary']['unresolved_observation_rows'],1)
 def test_world_bank_provider_rejects_inconsistent_region(self):
  from wdi_geography import metadata_page,country_map,UNKNOWN
  raw=json.dumps([{'page':1,'pages':1,'total':1},
                  [{'id':'WLD','name':'World','region':{'id':'NA','value':'Not clear'}}]]).encode()
  self.assertEqual(country_map([raw])['WLD']['geography_type'],UNKNOWN)
  with self.assertRaisesRegex(ValueError,'page/count'):
   metadata_page(raw,2)
  raw2=json.dumps([{'page':1,'pages':1,'total':2},
                   [{'id':'USA','name':'United States','region':{'id':'NAC','value':'North America'}}]]).encode()
  with self.assertRaisesRegex(ValueError,'Incomplete'):
   country_map([raw2])

if __name__=='__main__':unittest.main()
