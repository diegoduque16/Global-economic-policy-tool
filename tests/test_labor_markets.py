import json
import tempfile
import unittest
from pathlib import Path
from open_data import labor_markets as m


class LaborTest(unittest.TestCase):
    def test_bls_monthly_and_annual(self):
        obj={'status':'REQUEST_SUCCEEDED','Results':{'series':[{'seriesID':'CES0000000001','data':[
            {'year':'2025','period':'M01','value':'158,000','footnotes':[]},
            {'year':'2025','period':'M13','value':'158500'},
            {'year':'2023','period':'M12','value':'140000'}]}]}}
        rows=m.parse_bls(json.dumps(obj).encode(),allowed={'CES0000000001'},start=2024,end=2026,url='mock',stamp='now')
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['period'],'2025-01')
        self.assertEqual(rows[0]['value'],158000.)

    def test_bls_missing_values_are_skipped_not_zero(self):
        obj = {'status': 'REQUEST_SUCCEEDED', 'Results': {'series': [
            {'seriesID': 'CES0000000001', 'data': [
                {'year': '2025', 'period': 'M01', 'value': '-'},
                {'year': '2025', 'period': 'M02', 'value': ''},
                {'year': '2025', 'period': 'M03', 'value': 'N/A'},
                {'year': '2025', 'period': 'M04', 'value': None},
                {'year': '2025', 'period': 'M05', 'value': '158,100'},
                {'year': '2025', 'period': 'M06', 'value': '0'},
                {'year': '2025', 'period': 'M07', 'value': 'nan'},
            ]},
        ]}}
        rows = m.parse_bls(json.dumps(obj).encode(),
                           allowed={'CES0000000001'}, start=2024, end=2026,
                           url='mock', stamp='now')
        self.assertEqual([r['period'] for r in rows], ['2025-05', '2025-06'])
        self.assertEqual([r['value'] for r in rows], [158100., 0.])

    def test_bls_rejected(self):
        with self.assertRaises(ValueError):
            m.parse_bls(b'{"status":"REQUEST_FAILED","message":["rate limit"]}',allowed=set(m.BLS_SERIES),start=2024,end=2026,url='mock',stamp='now')

    def test_bls_ten_years(self):
        raw=json.loads(m.bls_body(['CES0000000001'],2017,2026))
        self.assertEqual(raw['startyear'],'2017')
        self.assertEqual(raw['endyear'],'2026')

    def test_ilostat_preserves_age_groups(self):
        raw=("ref_area,time,indicator,sex,classif1,obs_value,obs_status\n"
             "USA,2022,UNE_2UNE_SEX_AGE_NB,SEX_T,AGE_YTHADULT_Y15-24,111,A\n"
             "USA,2022,UNE_2UNE_SEX_AGE_NB,SEX_T,AGE_YTHADULT_YGE15,555,B\n"
             "USA,2023,UNE_2UNE_SEX_AGE_NB,SEX_T,AGE_YTHADULT_YGE15,\n").encode()
        rows=m.parse_ilostat(raw,indicator='UNE_2UNE_SEX_AGE_NB_A',start=2020,end=2026,url='mock',stamp='now')
        self.assertEqual(len(rows),2)
        self.assertNotEqual(rows[0]['disaggregation'],rows[1]['disaggregation'])
        self.assertEqual(rows[1]['status_flag'],'{"obs_status": "B"}')

    def test_ilostat_bad_schema(self):
        with self.assertRaises(ValueError):
            m.parse_ilostat(b'a,b,c\nUSA,2024,1',indicator='x',start=2020,end=2026,url='mock',stamp='now')

    def test_eurostat_layout(self):
        obj={'id':['geo','time','freq','indic_em','sex','age','unit'],
             'size':[2,2,1,1,1,1,1],
             'dimension':{
                 'geo':{'category':{'index':{'DE':0,'EL':1}}},
                 'time':{'category':{'index':{'2024':0,'2025':1}}},
                 'freq':{'category':{'index':{'A':0}}},
                 'indic_em':{'category':{'index':{'EMP_LFS':0}}},
                 'sex':{'category':{'index':{'T':0}}},
                 'age':{'category':{'index':{'Y20-64':0}}},
                 'unit':{'category':{'index':{'PC_POP':0}}}},
             'value':{'0':82.1,'1':82.2,'2':77.0,'3':77.1},'status':{'2':'b'}}
        rows=m.parse_eurostat(json.dumps(obj).encode(),start=2003,end=2026,url='mock',stamp='now')
        self.assertEqual(len(rows),4)
        self.assertEqual(rows[2]['country_iso3'],'GRC')
        self.assertEqual(rows[2]['status_flag'],'b')

    def test_eurostat_reject_bad(self):
        with self.assertRaises(ValueError):
            m.parse_eurostat(b'{}',start=2003,end=2026,url='mock',stamp='now')

    def test_sources_smoke_and_audit(self):
        bls=json.dumps({'status':'REQUEST_SUCCEEDED','Results':{'series':[{'seriesID':sid,'data':[
          {'year':'2025','period':'M01','value':'12','footnotes':[]}]} for sid in ('CES0000000001','LNS14000000')]}}).encode()
        ilostat=b'ref_area,time,indicator,sex,obs_value\nUSA,2025,UNE_2UNE_SEX_AGE_NB,SEX_T,33\n'
        euro=json.dumps({'id':['geo','time','freq','indic_em','sex','age','unit'],
          'size':[1,1,1,1,1,1,1],
          'dimension':{k:{'category':{'index':{v:0}}} for k,v in {
            'geo':'DE','time':'2025','freq':'A','indic_em':'EMP_LFS','sex':'T','age':'Y20-64','unit':'PC_POP'}.items()},
          'value':{'0':80.0}}).encode()
        def fake(url,**kwargs):
            if url.startswith(m.BLS_API): return bls
            if url.startswith(m.ILOSTAT_API): return ilostat
            if url.startswith(m.EUROSTAT_API): return euro
            raise ValueError('unknown source')
        with tempfile.TemporaryDirectory() as t:
            result=m.run(t,scope='smoke',source='all',fetch=fake)
            self.assertEqual(result['observations'],4)
            self.assertFalse(result['errors'])
            self.assertEqual(len(result['manifest']),3)
            self.assertTrue((Path(t)/'processed/labor_panel.csv').exists())
            self.assertTrue((Path(t)/'run_report.json').exists())

    def test_bad_source_validation(self):
        with self.assertRaises(ValueError):
            m.run('not_used',source='missing')


if __name__=='__main__':unittest.main()
