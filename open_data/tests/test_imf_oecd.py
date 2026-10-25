import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from open_data import imf_oecd as m

IMF_META={'indicators':{'NGDP_RPCH':{'unit':'percent','label':'GDP growth','dataset':'WEO',
                'source':'WEO (October 2026)','projection-year':2026},
               'GGXWDG_NGDP':{'unit':'% GDP','label':'Debt','dataset':'WEO'},
               'rev':{'unit':'% GDP','dataset':'FPP'}}}
COUNTRIES={'countries':{'USA':{'label':'United States'},'FRA':{'label':'France'},'DEU':{'label':'Germany'}}}
IMF_VAL={'values':{'NGDP_RPCH':{'USA':{'2024':2.5,'2026':2.2},'FRA':{'2024':1.3},'WORLD':{'2024':3.2}}}}
OECD_CSV=('REF_AREA,TIME_PERIOD,OBS_VALUE,SECTOR,STANDARD_REVENUE,UNIT_MEASURE\n'
          'USA,2024,25.7,S13,_T,PT_B1GQ\nFRA,2024,43.2,S13,_T,PT_B1GQ\n'
          'USA,2024,100,S13,T_1000,PT_B1GQ\n')

class TestIMFOECD(unittest.TestCase):
 def test_imf_parse(self):
  rows=m.parse_imf(json.dumps(IMF_VAL).encode(),'NGDP_RPCH',IMF_META['indicators'],start=2023,end=2026,
      allowed=set(COUNTRIES['countries']),url='api',timestamp='now')
  self.assertEqual(len(rows),3)
  self.assertEqual({r['data_status'] for r in rows},{'historical_or_current','projection'})
  self.assertNotIn('WORLD',[r['country_code'] for r in rows])
 def test_imf_missing_schema(self):
  with self.assertRaises(ValueError):m.parse_imf(b'{}','NGDP_RPCH',{},start=2000,end=2026,allowed=None,url='url',timestamp='now')
 def test_oecd_filtered(self):
  rows=m.parse_oecd(OECD_CSV.encode(),start=2020,end=2026,url='api',timestamp='now')
  self.assertEqual(len(rows),2);self.assertEqual(rows[0]['indicator'],'TAX_TOTAL_PCT_GDP')
 def test_oecd_schema(self):
  with self.assertRaises(ValueError):m.parse_oecd(b'Country,value\nUSA,22\n',start=2000,end=2026,url='x',timestamp='x')
 def test_urls(self):
  self.assertIn('NGDP_RPCH/USA/FRA/DEU',m.imf_urls('NGDP_RPCH',scope='smoke'))
  self.assertIn('PT_B1GQ.A?',m.oecd_url(start=2020,end=2026))
 def test_offline_run(self):
  def fetch(url):
   if url.endswith('/indicators'):return json.dumps(IMF_META).encode()
   if url.endswith('/countries'):return json.dumps(COUNTRIES).encode()
   if '/NGDP_RPCH/' in url:return json.dumps(IMF_VAL).encode()
   if '/GGXWDG_NGDP/' in url:return json.dumps({'values':{'GGXWDG_NGDP':{'USA':{'2024':112}}}}).encode()
   if 'sdmx.oecd.org' in url:return OECD_CSV.encode()
   raise ValueError(url)
  with tempfile.TemporaryDirectory() as tmp:
   r=m.run(tmp,scope='smoke',source='both',start=2020,fetch=fetch)
   self.assertEqual(r['observations'],6);self.assertFalse(r['errors'])
   self.assertEqual(len(r['coverage']),2)
   self.assertTrue((Path(tmp)/'processed/imf_oecd_panel.csv').exists())
   self.assertTrue((Path(tmp)/'run_report.json').exists())
 def test_errors_recorded(self):
  with tempfile.TemporaryDirectory() as tmp:
   r=m.run(tmp,fetch=lambda url: (_ for _ in ()).throw(ValueError('offline')))
   self.assertTrue(r['errors']);self.assertEqual(r['observations'],0)
 def test_bad_dates(self):
  with self.assertRaises(ValueError):m.run('ignored',start=2000,end=1999)

if __name__=='__main__':unittest.main()
