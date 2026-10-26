import json
import tempfile
import unittest
from pathlib import Path
from open_data.imf_extension import collect,parse_full_indicator,get_json_bytes,BASE,INDICATORS

class IMFExtensionTests(unittest.TestCase):
    def test_all_codes_distinct_and_new(self):
        self.assertEqual(len(INDICATORS),13)
        self.assertEqual(len(set(INDICATORS)),13)
        self.assertNotIn('NGDP_RPCH',INDICATORS)
    def test_local_filter_ignores_server_filters(self):
        raw=json.dumps({'values':{'NGDPD':{'USA':{'2019':0,'2020':10,'2027':800},
             'FRA':{'2020':22},'DEU':{'2020':33},'XXX':{'2020':44}}}}).encode()
        rows,details=parse_full_indicator(raw,'NGDPD',
            {'projection-year':2020,'unit':'Billions of U.S. dollars'},
            {'USA','FRA','DEU'},2020,2020,when='test')
        self.assertEqual(len(rows),3)
        self.assertEqual({r['country_iso3'] for r in rows},{'USA','FRA','DEU'})
        self.assertEqual(next(r for r in rows if r['country_iso3']=='USA')['obs_status'],'projection')
        self.assertEqual(details['geographies_outside_admitted_country_catalog'],1)
        self.assertNotIn('2027',{str(r['year']) for r in rows})
    def test_missing_metadata_projection_unknown(self):
        raw=b'{"values":{"pb":{"USA":{"2000":-3}}}}'
        rows,_=parse_full_indicator(raw,'pb',{}, {'USA'},2000,2000,when='test')
        self.assertEqual(rows[0]['obs_status'],'projection_status_not_in_metadata')
        self.assertEqual(rows[0]['unit'],'source_defined_unit_unverified')
    def test_no_cross_origin(self):
        with self.assertRaises(ValueError):
            get_json_bytes('https://other.example/bad',fetcher=lambda _:b'{}')
    def test_empty_series_rejected(self):
        with self.assertRaises(ValueError):
            parse_full_indicator(b'{"values":{"NGDPD":{}}}','NGDPD',{}, {'USA'},2000,2000,when='test')
    def test_full_pilot_validated(self):
        codes=('USA','FRA','DEU','KEN','GHA')
        meta={x:{'unit':'unit','projection-year':2026,'dataset':'WEO'}
              for x in ('NGDPD','NGDPDPC','prim_exp')}
        countries={x:x for x in list(codes)+[f'X{i:03d}' for i in range(160)]}
        def mocked(url):
            if url.endswith('/indicators'):return json.dumps({'indicators':meta}).encode()
            if url.endswith('/countries'):return json.dumps({'countries':countries}).encode()
            code=url.rsplit('/',1)[1]
            data={x:{str(y):float(y) for y in range(2017,2027)} for x in codes}
            data['ARG']={'2000':999,'2020':50}
            return json.dumps({'values':{code:data}}).encode()
        with tempfile.TemporaryDirectory() as d:
            r=collect(d,'pilot',fetcher=mocked,throttle=0)
            self.assertEqual(r['status'],'VALIDATED_MACRO_DATA_NOT_CAUSAL_POLICY_SHOCKS')
            self.assertEqual(r['observations'],150)
            self.assertEqual(r['country_count'],5)
            self.assertEqual(r['source_requests'],5)
            self.assertFalse(r['research_ready'])
            self.assertTrue((Path(d)/'processed'/'imf_extension_panel.csv').is_file())
            rerun=collect(d,'pilot',fetcher=mocked,throttle=0)
            self.assertEqual(rerun['reused_checkpoints'],3)

if __name__=='__main__':unittest.main()
