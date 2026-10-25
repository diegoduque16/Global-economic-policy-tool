import json
import tempfile
import unittest
from open_data.us_europe import FRED, EUROSTAT, EU, eu_url, fred_url, parse_fred, parse_jsonstat, run

FRED_RAW=b'DATE,PAYEMS\n1939-01-01,31000\n1950-01-01,.\n2025-01-01,160000\n'
EU_RAW=json.dumps({'id':['freq','na_item','unit','geo','time'],'size':[1,1,1,2,2],
    'dimension':{'freq':{'category':{'index':{'A':0}}},'na_item':{'category':{'index':{'B1G':0}}},
    'unit':{'category':{'index':{'CP_MEUR':0}}},'geo':{'category':{'index':{'DE':0,'FR':1}}},
    'time':{'category':{'index':{'2023':0,'2024':1}}}},'value':{'0':100,'1':200,'2':300,'3':400}}).encode()

class TestRegion(unittest.TestCase):
    def test_fred_series(self):
        items,skipped=parse_fred(FRED_RAW,'PAYEMS',start=1900,end=2026)
        self.assertEqual(len(items),2); self.assertEqual(skipped,1);self.assertEqual(items[0]['country_iso3'],'USA')
    def test_date_filter(self):
        items,_=parse_fred(FRED_RAW,'PAYEMS',start=2000,end=2026)
        self.assertEqual(len(items),1)
    def test_reject_html(self):
        with self.assertRaises(ValueError):parse_fred(b'<html>Bad</html>','PAYEMS')
    def test_euro_flattened(self):
        items,_=parse_jsonstat(EU_RAW,'gdp_nominal',start=2023,end=2024)
        self.assertEqual(len(items),4);self.assertEqual(items[3]['country_iso3'],'FRA');self.assertEqual(items[3]['value'],400)
    def test_euro_reject_bad_json(self):
        with self.assertRaises(ValueError):parse_jsonstat(b'{"error":"Bad filter"}','gdp_nominal')
    def test_url_encodes_query(self):
        self.assertIn('na_item=B1G',eu_url('gdp_nominal',1990)); self.assertIn('id=PAYEMS',fred_url('PAYEMS'))
    def test_mocked_smoke_and_manifests(self):
        def fetch(url): return EU_RAW if 'eurostat' in url else FRED_RAW.replace(b'PAYEMS',b'USPRIV') if 'id=USPRIV' in url else FRED_RAW
        with tempfile.TemporaryDirectory() as out:
            report=run(out,'smoke','all',start=1900,end=2026,fetch=fetch)
            self.assertEqual(report['tasks_passed'],3);self.assertEqual(len(report['errors']),0)
            self.assertEqual(report['observations'],8)
            self.assertEqual(len(report['manifest']),3)
    def test_fails_if_no_observations(self):
        with tempfile.TemporaryDirectory() as out:
            report=run(out,'smoke','us',fetch=lambda u:b'<html>bad</html>')
            self.assertEqual(report['observations'],0);self.assertEqual(len(report['errors']),2)

if __name__=='__main__':unittest.main()
