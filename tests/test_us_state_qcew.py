import csv,io,json,tempfile,unittest
from pathlib import Path
from open_data.us_state_qcew import STATES,PILOT_STATES,MEASURES,parse_slice,source_url,collect,number
class StateQCEWTests(unittest.TestCase):
    def mock(self,abbr,year=2024,include_private=True):
        base={'area_fips':STATES[abbr]+'000','year':str(year),'qtr':'A',
              'size_code':'0','disclosure_code':''}
        rows=[]
        cats=[('0','10')]+([('5',c) for c in ('51','54','62','23')] if include_private else [])
        for own,industry in cats:
            rows.append({**base,'own_code':own,'industry_code':industry,
                'annual_avg_emplvl':'10000','annual_avg_estabs':'100',
                'total_annual_wages':'1000000','annual_avg_wkly_wage':'1000','avg_annual_pay':'52000'})
        f=io.StringIO();w=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator=chr(10));w.writeheader();w.writerows(rows)
        return f.getvalue().encode()
    def test_fifty_states_and_dc(self):
        self.assertEqual(len(STATES),51);self.assertEqual(STATES['CT'],'09')
    def test_source_url_rejects_invalid(self):
        self.assertEqual(source_url(2024,'CT'),'https://data.bls.gov/cew/data/api/2024/a/area/09000.csv')
        with self.assertRaises(ValueError):source_url(2020,'CT')
    def test_disaggregation_preserves_owner_and_sector(self):
        rows,stats=parse_slice(self.mock('CT'),2024,'CT','test')
        self.assertEqual(len(rows),25);self.assertEqual(stats['private_sectors'],4)
        dims=[json.loads(x['disaggregation']) for x in rows]
        self.assertIn(('5','51'),{(d['ownership_code'],d['industry_code']) for d in dims})
        self.assertIn(('0','10'),{(d['ownership_code'],d['industry_code']) for d in dims})
    def test_reject_aggregate_only_and_missing_handling(self):
        with self.assertRaises(ValueError):parse_slice(self.mock('CT',include_private=False),2024,'CT','test')
        self.assertIsNone(number('N'));self.assertIsNone(number(''));self.assertEqual(number('0'),0)
    def test_live_pilot_logic_via_mock(self):
        def get(url):
            fips=url.rsplit('/',1)[1][:2];abbr=next(k for k,v in STATES.items() if v==fips)
            return self.mock(abbr)
        with tempfile.TemporaryDirectory() as d:
            r=collect(d,'pilot',get=get,throttle=0)
            self.assertFalse(r['errors']);self.assertEqual(r['observations'],125)
            self.assertEqual(r['state_count'],5);self.assertTrue(r['private_sector_details'])
            panel=Path(d)/'processed'/'us_state_qcew_panel.csv'
            self.assertTrue(panel.is_file())
            with panel.open() as stream:self.assertEqual(sum(1 for _ in stream)-1,125)
if __name__=='__main__':unittest.main()
