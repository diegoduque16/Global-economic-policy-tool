"""Synthetic, offline-only tests for WID source-native observation revision audit."""
import csv
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import audit_wid_revisions as audit

FIELDS = ['wid_area', 'year', 'percentile', 'group', 'concept', 'source_variable',
          'share_fraction', 'quality_grade', 'source_file_sha256', 'research_status']


def row(area, year, pct, concept, value, sha):
    return {'wid_area':area,'year':str(year),'percentile':pct,'group':'ignored',
            'concept':concept,'source_variable':{
                'pretax_national_income_share':'sptinc992j',
                'posttax_national_income_share':'sdiinc992j',
                'posttax_disposable_income_share':'scainc992j',
                'personal_wealth_share':'shweal992j',
            }[concept],
            'share_fraction':str(value),'quality_grade':'3',
            'source_file_sha256':sha,'research_status':'source_native_descriptive_not_causal'}


class WidRevisionAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.input = self.root/'refreshed'
        self.input.mkdir()
        self.db = self.root/'baseline.sqlite'
        self.original_sha = {'AU':'a'*64,'GH':'d'*64}
        self.new_sha = {'AU':'b'*64,'GH':'c'*64}
        self.concept_a='pretax_national_income_share'
        self.concept_b='posttax_national_income_share'
        with sqlite3.connect(self.db) as db:
            db.execute('''CREATE TABLE wid_numeric_observations (
                       wid_area TEXT, year INTEGER, percentile TEXT, concept TEXT,
                       value_fraction REAL, original_source_sha256 TEXT)''')
            entries = [
                ('AU',2021,'p99p100',self.concept_a,0.20,self.original_sha['AU']),
                ('AU',2022,'p99p100',self.concept_b,0.30,self.original_sha['AU']),
                ('GH',2020,'p99p100',self.concept_a,0.40,self.original_sha['GH']),
                ('GH',2021,'p99p100',self.concept_b,0.50,self.original_sha['GH']),
            ]
            db.executemany('INSERT INTO wid_numeric_observations VALUES (?,?,?,?,?,?)', entries)
        manifest={'expected_country_count':2,'countries':[
            {'wid_area':country,'source_sha256':self.original_sha[country],
             'source_native_rows':2} for country in ('AU','GH')]}
        self.manifest=self.root/'manifest.json'
        self.manifest.write_text(json.dumps(manifest))
        fingerprints={'schema_version':1,'source_run_id':41588514160,
          'expected_refreshed_country_count':2,
          'expected_refreshed_numeric_source_rows':4,
          'source_fingerprints':[
              {'wid_area':country,'source_sha256':self.new_sha[country],
               'retained_source_shares':2} for country in ('AU','GH')]}
        self.evidence=self.root/'evidence.json'
        self.evidence.write_text(json.dumps(fingerprints))
        self.write_country('AU',[
            row('AU',2021,'p99p100',self.concept_a,0.20,self.new_sha['AU']),
            row('AU',2022,'p99p100',self.concept_b,0.31,self.new_sha['AU'])])
        self.write_country('GH',[
            row('GH',2020,'p99p100',self.concept_a,0.40,self.new_sha['GH']),
            row('GH',2022,'p99p100',self.concept_b,0.50,self.new_sha['GH'])])

    def write_country(self, code, rows):
        with (self.input/f'{code}.csv').open('w',newline='',encoding='utf-8') as f:
            writer=csv.DictWriter(f,fieldnames=FIELDS)
            writer.writeheader();writer.writerows(rows)

    def run_audit(self):
        return audit.compare(self.db,self.input,self.manifest,self.evidence)

    def test_numeric_values_and_keys_are_distinct_from_file_hash_changes(self):
        report=self.run_audit()
        self.assertEqual(report['country_count'],2)
        self.assertEqual(report['totals'],dict(unchanged=2,changed=1,added=1,removed=1,baseline=4,refreshed=4))
        self.assertTrue(report['revisions_are_not_additive_to_canonical'])
        self.assertFalse(report['raw_source_files_reverified_this_run'])
        self.assertEqual(report['research_status'],'DESCRIPTIVE_SOURCE_COMPARISON_NOT_CAUSAL')
        self.assertTrue(all(len(x['retrieved_staged_csv_sha256'])==64 for x in report['by_country']))
        self.assertNotIn('0.31',json.dumps(report))

    def test_duplicate_key_fails_closed(self):
        same=row('AU',2021,'p99p100',self.concept_a,.2,self.new_sha['AU'])
        self.write_country('AU',[same,same])
        with self.assertRaisesRegex(ValueError, 'Duplicate WID'):
            self.run_audit()

    def test_unchanged_raw_sha_or_bad_parent_sha_cannot_sneak_in(self):
        bad=row('AU',2021,'p99p100',self.concept_a,.2,'z'*64)
        self.write_country('AU',[bad,row('AU',2022,'p99p100',self.concept_b,.31,self.new_sha['AU'])])
        with self.assertRaisesRegex(ValueError,'parent raw SHA'):
            self.run_audit()

    def test_missing_staged_source_fail_closed(self):
        (self.input/'GH.csv').unlink()
        with self.assertRaisesRegex(ValueError,'regular local file'):
            self.run_audit()

    def test_metadata_row_counts_strict(self):
        data=json.loads(self.evidence.read_text())
        data['source_fingerprints'][0]['retained_source_shares']=3
        self.evidence.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'row count'):
            self.run_audit()

    def test_causal_overclaim_is_rejected(self):
        rows=[row('AU',2021,'p99p100',self.concept_a,.20,self.new_sha['AU']),
              row('AU',2022,'p99p100',self.concept_b,.31,self.new_sha['AU'])]
        rows[0]['research_status']='causal'
        self.write_country('AU',rows)
        with self.assertRaisesRegex(ValueError,'research readiness'):
            self.run_audit()

    def test_source_variable_and_concept_must_match(self):
        rows=[row('AU',2021,'p99p100',self.concept_a,0.2,self.new_sha['AU']),
              row('AU',2022,'p99p100',self.concept_b,0.3,self.new_sha['AU'])]
        rows[0]['source_variable']='sdiinc992j'
        self.write_country('AU',rows)
        with self.assertRaisesRegex(ValueError, 'concept definition'):
            self.run_audit()

    def test_invalid_value_rejected_not_zero_filled(self):
        rows=[row('AU',2021,'p99p100',self.concept_a,'',self.new_sha['AU']),
              row('AU',2022,'p99p100',self.concept_b,.31,self.new_sha['AU'])]
        self.write_country('AU',rows)
        with self.assertRaisesRegex(ValueError,'Missing WID numeric'):
            self.run_audit()


if __name__=='__main__':
    unittest.main()
