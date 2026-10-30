"""Synthetic fixtures, no invented observation is a real WID datapoint."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from open_data.wid_live_pilot import parse_code, validate_csv, summarize, run, official_url

class WidCountryPilotTests(unittest.TestCase):
    def test_both_version_orderings(self):
        self.assertEqual(parse_code('sptinc992j'), 'pretax_national_income_share')
        self.assertEqual(parse_code('sptincj992'), 'pretax_national_income_share')
        self.assertEqual(parse_code('sdiincj992'), 'posttax_national_income_share')
        self.assertEqual(parse_code('shwealj992'), 'personal_wealth_share')
        self.assertIsNone(parse_code('sptinc999i'))
    def test_semicolon_country_csv_and_match(self):
        csv = ('variable;percentile;year;value;quality\n'
               'sptincj992;p0p50;2021;0.13;3\n'
               'sdiinc992j;p0p50;2021;0.21;4\n'
               'shwealj992;p0p50;2021;-0.01;2\n').encode()
        rows, counts, lo, hi = validate_csv(csv, 'US', 'hash123')
        self.assertEqual((len(rows), lo, hi), (3, 2021, 2021))
        self.assertEqual(rows[0]['quality_grade'], '3')
        matched, pairs = summarize(rows)
        self.assertEqual(pairs, 1)
        self.assertAlmostEqual(float(matched[0]['posttax_minus_pretax_pp']), 8.0)
    def test_untrusted_country_rejected(self):
        raw = b'country,variable,year,percentile,value\nFR,sptincj992,2021,p0p50,0.1\n'
        with self.assertRaisesRegex(ValueError, 'differs'):
            validate_csv(raw, 'US', 'hash')
    def test_duplicate_legacy_and_current_rejected(self):
        raw = b'variable,percentile,year,value\nsptincj992,p0p50,2020,0.1\nsptinc992j,p0p50,2020,0.1\n'
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            validate_csv(raw, 'US', 'hash')
    def test_missing_skipped_and_country_only(self):
        raw = b'variable\tpercentile\tyear\tvalue\nsptincj992\tp0p50\t2020\t\nsptincj992\tp0p50\t2021\t0.11\n'
        rows, counts, _, _ = validate_csv(raw, 'FR', 'hash')
        self.assertEqual(len(rows), 1)
        self.assertEqual(counts['missing_source_value_skipped'], 1)
    def test_fake_end_to_end_no_backup(self):
        def fake(country):
            return b'variable,percentile,year,value\nsptincj992,p99p100,2020,0.21\n', official_url(country)
        with tempfile.TemporaryDirectory() as folder:
            result = run(['US','FR'], Path(folder)/'out', backup=False, downloader=fake)
            self.assertEqual(result['numeric_values_retrieved'],2)
            self.assertFalse(result['onedrive_verified'])
            pub = json.loads((Path(folder)/'out/public_summary.json').read_text())
            self.assertNotIn('share_fraction', str(pub))
    def test_bad_year_fails(self):
        raw = b'variable,percentile,year,value\nsptincj992,p0p50,notayear,0.1\n'
        with self.assertRaisesRegex(ValueError, 'Invalid year'):
            validate_csv(raw,'US','hash')
    def test_url_validation(self):
        with self.assertRaises(ValueError): official_url('..')
        self.assertEqual(official_url('US'), 'https://wid.world/bulk_download/WID_data_US.csv')

if __name__=='__main__': unittest.main()
