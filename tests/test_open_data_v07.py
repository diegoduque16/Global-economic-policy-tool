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
