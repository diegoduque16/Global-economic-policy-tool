"""Synthetic data only. These are regression tests, NOT real WID observations."""
from __future__ import annotations
import csv
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research.build_wid_numeric_panel import build, parse_variable, verified_manifest

class NumericValueTests(unittest.TestCase):
    def seed(self, root, bad=None):
        root=Path(root)
        (root/'input'/'US').mkdir(parents=True)
        original='a'*64
        rows=[
            dict(wid_area='US',year='2024',percentile='p0p50',concept='pretax_national_income_share',
                 source_variable='sptincj992',share_fraction='0.15',source_file_sha256=original,
                 research_status='source_native_descriptive_not_causal',quality_grade='3'),
            dict(wid_area='US',year='2024',percentile='p0p50',concept='posttax_national_income_share',
                 source_variable='sdiinc992j',share_fraction='0.23',source_file_sha256=original,
                 research_status='source_native_descriptive_not_causal',quality_grade='3'),
            dict(wid_area='US',year='2024',percentile='p99p100',concept='personal_wealth_share',
                 source_variable='shwealj992',share_fraction='0.35',source_file_sha256=original,
                 research_status='source_native_descriptive_not_causal',quality_grade='2'),
        ]
        if bad=='text':rows[0]['share_fraction']='not a number'
        if bad=='duplicate':rows[1]=rows[0].copy()
        if bad=='bad_sha':rows[0]['source_file_sha256']='b'*64
        p=root/'input'/'US'/'wid_source_native_shares.csv'
        with p.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
        manifest={'schema_version':'1.0','source_remote_prefix':'onedrive:Somewhere',
                  'expected_country_count':1,'expected_numeric_values':3,
                  'expected_pretax_posttax_pairs':1,
                  'countries':[{'wid_area':'US','iso3':'USA',
                                'source_sha256':original,'source_native_rows':3,
                                'pretax_posttax_pairs':1,'source_run':'12345'}]}
        (root/'manifest.json').write_text(json.dumps(manifest))
        return root/'manifest.json',root/'input',root/'out'

    def test_real_numbers_materialized_in_sqlite(self):
        with tempfile.TemporaryDirectory() as t:
            m,s,o=self.seed(t)
            report=build(m,s,o)
            self.assertEqual(report['actual_numeric_observation_rows_in_sqlite'],3)
            self.assertEqual(report['actual_matched_pretax_posttax_pairs'],1)
            with sqlite3.connect(o/'wid_23country_values.sqlite') as db:
                total=db.execute('SELECT COUNT(*) FROM wid_numeric_observations').fetchone()[0]
                value=db.execute('''SELECT value_fraction FROM wid_numeric_observations
                    WHERE concept='pretax_national_income_share' ''').fetchone()[0]
                difference=db.execute('''SELECT posttax_minus_pretax_percentage_points
                    FROM wid_distribution_panel WHERE percentile='p0p50' ''').fetchone()[0]
                self.assertEqual(total,3)
                self.assertAlmostEqual(value,0.15)
                self.assertAlmostEqual(difference,8)
            self.assertTrue((o/'sha256_manifest.json').exists())
            self.assertEqual(report['status'],'REAL_NUMERIC_VALUES_IMPORTED_INTO_PRIVATE_RESEARCH_SQLITE_NOT_CANONICAL')

    def test_reject_metadata_only_non_numeric(self):
        with tempfile.TemporaryDirectory() as t:
            m,s,o=self.seed(t,'text')
            with self.assertRaisesRegex(ValueError,'Non-numeric observation'):
                build(m,s,o)

    def test_reject_duplicate_measurement(self):
        with tempfile.TemporaryDirectory() as t:
            m,s,o=self.seed(t,'duplicate')
            with self.assertRaisesRegex(ValueError,'Duplicate WID numeric key'):
                build(m,s,o)

    def test_reject_wrong_source_hash(self):
        with tempfile.TemporaryDirectory() as t:
            m,s,o=self.seed(t,'bad_sha')
            with self.assertRaisesRegex(ValueError,'source country/hash mismatch'):
                build(m,s,o)

    def test_reject_missing_source(self):
        with tempfile.TemporaryDirectory() as t:
            m,s,o=self.seed(t)
            (s/'US'/'wid_source_native_shares.csv').unlink()
            with self.assertRaisesRegex(FileNotFoundError,'NO NUMERIC SOURCE FILE'):
                build(m,s,o)

    def test_reject_false_manifest_counts(self):
        with tempfile.TemporaryDirectory() as t:
            m,s,o=self.seed(t)
            obj=json.loads(m.read_text());obj['expected_numeric_values']=4;m.write_text(json.dumps(obj))
            with self.assertRaisesRegex(ValueError,'Manifest source-native numeric row totals disagree'):
                verified_manifest(m)

    def test_supported_definition_codes(self):
        self.assertEqual(parse_variable('sptinc992j'),'pretax_national_income_share')
        self.assertEqual(parse_variable('sptincj992'),'pretax_national_income_share')
        self.assertEqual(parse_variable('shwealj992'),'personal_wealth_share')
        self.assertIsNone(parse_variable('sptinc999i'))

if __name__=='__main__':unittest.main()
