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
if __name__=='__main__':unittest.main()
