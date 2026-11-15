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
    def test_new_eurostat_employment_and_hourly_wages_source_dimensions(self):
        from urllib.parse import urlsplit,parse_qs
        params={
            'employment_rate_20_64': {'indic_em':'EMP_LFS','sex':'T','age':'Y20-64','unit':'PC_POP'},
            'hourly_wages_salaries': {'lcstruct':'D11','nace_r2':'B-S_X_O','unit':'EUR'},
            'hourly_total_labour_cost': {'lcstruct':'D1_D4_MD5','nace_r2':'B-S_X_O','unit':'EUR'},
        }
        for name,selected in params.items():
            with self.subTest(name=name):
                query=parse_qs(urlsplit(eu_url(name,2008)).query)
                self.assertEqual(query['freq'],['A'])
                self.assertEqual(query['sinceTimePeriod'],['2008'])
                for key,value in selected.items(): self.assertEqual(query[key],[value])
                dimensions={'freq':'A',**selected}
                axes={**dimensions,'geo':'DE','time':'2024'}
                raw=json.dumps({'id':list(axes),
                    'size':[1]*len(axes),
                    'dimension':{k:{'category':{'index':{v:0}}} for k,v in axes.items()},
                    'value':{'0':0 if name.startswith('employment') else 22.5},
                    'status':{'0':'p'}}).encode()
                rows,skipped=parse_jsonstat(raw,name,start=2008,end=2026)
                self.assertEqual(skipped,0)
                self.assertEqual(len(rows),1)
                self.assertEqual(rows[0]['country_iso3'],'DEU')
                self.assertEqual(rows[0]['series'],name)
                self.assertEqual(rows[0]['status_flag'],'p')
                self.assertEqual(rows[0]['value'],0 if name.startswith('employment') else 22.5)
                # A response with a different native structure must NEVER
                # be silently normalized under the requested series ID.
                mismatched={**axes, **({'unit':'NAC'} if name.startswith('hourly')
                                     else {'age':'Y15-64'})}
                wrong=json.dumps({'id':list(mismatched),
                    'size':[1]*len(mismatched),
                    'dimension':{k:{'category':{'index':{v:0}}} for k,v in mismatched.items()},
                    'value':{'0':22.5}}).encode()
                rejected,_=parse_jsonstat(wrong,name,start=2008,end=2026)
                self.assertEqual(rejected,[])

    def test_eight_additional_official_fred_series_are_native_and_distinct(self):
        expected={
            'CES0500000003':('monthly','US dollars per hour'),
            'OPHNFB':('quarterly','index 2017=100'),
            'ULCNFB':('quarterly','index 2017=100'),
            'MEHOINUSA672N':('annual','2025 C-CPI-U dollars'),
            'PCE':('monthly','billions of US dollars'),
            'PSAVERT':('monthly','percent'),
            'JTSHIL':('monthly','thousands of hires'),
            'JTSQUL':('monthly','thousands of quits'),
        }
        self.assertEqual(len(FRED),21)
        for code,(frequency,units) in expected.items():
            with self.subTest(series=code):
                self.assertIn(code,FRED)
                self.assertEqual(FRED[code][1],frequency)
                self.assertTrue(FRED[code][2].startswith(units))
                self.assertEqual(fred_url(code),
                                 'https://fred.stlouisfed.org/graph/fredgraph.csv?id='+code)
                raw=('DATE,'+code+'\n2025-01-01,2.75\n2025-02-01,.\n').encode()
                rows,skipped=parse_fred(raw,code,start=1900,end=2026)
                self.assertEqual(skipped,1)
                self.assertEqual(len(rows),1)
                self.assertEqual(rows[0]['series'],code)
                self.assertEqual(rows[0]['value'],2.75)
                self.assertEqual(rows[0]['unit'],FRED[code][2])
                self.assertEqual(rows[0]['frequency'],frequency)
                self.assertEqual(rows[0]['provider'],
                                 'FRED (St. Louis Fed; original series provider varies)')
        self.assertEqual(len(EUROSTAT),6)

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
