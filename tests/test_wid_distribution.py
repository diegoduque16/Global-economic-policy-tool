"""Deterministic synthetic fixtures. No artificial observation is real WID data."""
from __future__ import annotations
import csv
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from open_data.wid_distribution import build, normalize, parse_rows


class WIDDistributionTests(unittest.TestCase):
    def test_code_population_and_fraction(self):
        row = normalize(dict(country='US', variable='sptinc992j', percentile='p99p100',
                             year='2023', value='0.2', age='992', pop='j'), 'x.csv', 'hash')
        self.assertEqual(row['age_code'], '992')
        self.assertEqual(row['population_unit'], 'j')
        self.assertEqual(row['value_fraction'], '0.20000000000000001')

    def test_preserves_negative_wealth_share(self):
        row = normalize(dict(country='US', variable='shweal992j', percentile='p0p50',
                             year='2023', value='-0.01'), 'x.csv', 'hash')
        self.assertEqual(float(row['value_fraction']), -0.01)

    def test_invalid_age_fails(self):
        with self.assertRaisesRegex(ValueError, 'Age dimension'):
            normalize(dict(country='US', variable='sptinc992j', percentile='p99p100',
                           year='2023', value='0.2', age='999'), 'x.csv', 'hash')

    def test_export_and_descriptive_join(self):
        fixture = 'country,variable,percentile,year,value\n' + '\n'.join([
            'US,sptinc992j,p0p50,2020,0.15', 'US,sdiinc992j,p0p50,2020,0.23',
            'US,sptinc992j,p50p90,2020,0.35', 'US,sptinc992j,p90p100,2020,0.50',
            'US,shweal992j,p0p50,2020,-0.01',
            'US,sptinc992j,p99p100,2020,0.22',
            'US,sptinc999i,p99p100,2020,0.40',
        ]) + '\n'
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); source = base / 'wid.csv';source.write_text(fixture)
            output = base / 'out'
            result = build(source, output)
            self.assertEqual(result['retained_share_observations'], 6)
            self.assertEqual(result['matched_pretax_posttax_pairs'], 1)
            with (output / 'wid_matched_distribution.csv').open() as f:
                rows = list(csv.DictReader(f))
            matched = next(x for x in rows if x['percentile']=='p0p50')
            self.assertAlmostEqual(float(matched['posttax_minus_pretax_share_pp']), 8.0)
            self.assertEqual(matched['income_redistribution_data_available'], 'true')
            self.assertFalse(result['nonoverlapping_share_sum_warnings'])
            self.assertEqual(json.loads((output/'wid_qa_report.json').read_text())['status'],
                             'source_validated_not_warehouse_integrated')

    def test_zip_and_skipped_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'wid.zip'
            with zipfile.ZipFile(source, 'w') as z:
                z.writestr('WID_data_US.csv', 'variable\tpercentile\tyear\tvalue\nsptinc992j\tp99p100\t2020\t0.25\n')
                z.writestr('WID_metadata_US.csv', 'code,description\nabc,description\n')
            out=build(source,root/'out')
            self.assertEqual(out['retained_share_observations'],1)
            self.assertEqual(len(out['metadata_files_skipped']),1)

    def test_duplicate_keys_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'dup.csv'
            source.write_text('country,variable,percentile,year,value\nUS,sptinc992j,p0p50,2020,0.1\nUS,sptinc992j,p0p50,2020,0.2\n')
            with self.assertRaisesRegex(ValueError,'Duplicate WID observation'):
                build(source,root/'output')

    def test_unsafe_zip_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'bad.zip'
            with zipfile.ZipFile(source,'w') as z:
                z.writestr('../WID_data_US.csv','country,variable,percentile,year,value\n')
            with self.assertRaisesRegex(ValueError,'Unsafe ZIP'):
                build(source,root/'out')

    def test_unsupported_schema_not_transformed(self):
        parsed=parse_rows('metadata.csv',b'code,description\nabc,something\n')
        self.assertIsNone(parsed)

if __name__=='__main__':unittest.main()
