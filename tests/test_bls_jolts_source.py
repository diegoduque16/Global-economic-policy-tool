"""Offline original-provider JOLTS replay tests; no network or private credentials."""
import json
import tempfile
import unittest
from pathlib import Path

from open_data.bls_jolts import build, verify_release, SERIES_REGEX


def official_shaped_fixture():
    columns=('series_id','seasonal','industry_code','state_code','area_code',
             'sizeclass_code','dataelement_code','ratelevel_code')
    series=['\t'.join(columns)]
    data=['series_id\tyear\tperiod\tvalue\tfootnote_codes']
    elements=['JO','HI','TS','QU','LD']
    for i in range(41):
        industry='000000' if i<5 else f'{i:06d}'
        element=elements[i] if i<5 else 'JO'
        sid='JTS'+industry+'00'+'00000'+'00'+element+'L'
        assert len(sid)==21 and SERIES_REGEX.fullmatch(sid),sid
        series.append('\t'.join([sid,'S',industry,'00','00000','00',element,'L']))
        for year in range(2000,2016):
            for month in range(1,13):
                data.append(f'{sid}\t{year}\tM{month:02d}\t{100+i}\t')
    return {'jt.series':('\n'.join(series)+'\n').encode(),
            'jt.data.1.AllItems':('\n'.join(data)+'\n').encode()}


class BlsJoltsOriginalSourceTests(unittest.TestCase):
    def test_original_source_replay_and_tamper_gate(self):
        fixture=official_shaped_fixture()
        with tempfile.TemporaryDirectory() as temp:
            def fetch(name, *, max_size, retries=3):
                self.assertIn(name,fixture)
                return fixture[name]
            report=build(out=temp,start=2000,end=2026,fetch=fetch)
            self.assertEqual(report['series_ids'],41)
            self.assertEqual(report['observations'],41*16*12)
            self.assertEqual(len(report['manifest']),2)
            result=verify_release(temp)
            self.assertEqual(result['status'],'BLS_JOLTS_ORIGINAL_NUMERIC_REPLAY_VERIFIED')
            self.assertEqual(result['observations'],report['observations'])
            raw=Path(temp)/'raw'/'jt.series'
            raw.write_bytes(raw.read_bytes()+b'\n# modified after collection')
            with self.assertRaisesRegex(ValueError,'SHA256 mismatch'):
                verify_release(temp)

    def test_reject_duplicate_provider_series_month(self):
        fixture=official_shaped_fixture()
        fixture['jt.data.1.AllItems'] += fixture['jt.data.1.AllItems'].splitlines(keepends=True)[1]
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError,'Duplicate JOLTS monthly observation'):
                build(out=temp,fetch=lambda name,*,max_size,retries=3: fixture[name])

    def test_reject_changed_numeric_panel_after_replay(self):
        fixture=official_shaped_fixture()
        with tempfile.TemporaryDirectory() as temp:
            build(out=temp,fetch=lambda name,*,max_size,retries=3: fixture[name])
            panel=Path(temp)/'processed'/'bls_jolts_panel.csv'
            panel.write_text(panel.read_text().replace('100.0','999.0',1))
            with self.assertRaisesRegex(ValueError,'panel SHA256 mismatch'):
                verify_release(temp)


if __name__=='__main__':
    unittest.main()
