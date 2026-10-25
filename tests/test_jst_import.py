import csv
from pathlib import Path
import tempfile
import unittest
from open_data.jst_import import import_csv

class TestJST(unittest.TestCase):
    def test_explicit_selected_columns(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'source.csv'
            src.write_text('year,iso,variableA,variableB\n1870,USA,123,\n1871,GBR,124,35\n',encoding='utf-8')
            report=import_csv(src,Path(d)/'out',variables=('variableA','variableB'))
            self.assertEqual(report['observations'],3)
            self.assertEqual(report['historical_geography_codes'],['GBR','USA'])
            self.assertTrue((Path(d)/'out/jst_preliminary.csv').exists())
    def test_no_inferred_variables(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'src.csv';p.write_text('year,iso,thing\n1870,USA,12\n')
            with self.assertRaises(ValueError):import_csv(p,Path(d)/'out')
    def test_bad_column(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'src.csv';p.write_text('year,iso,thing\n1870,USA,12\n')
            with self.assertRaises(ValueError):import_csv(p,Path(d)/'out',variables=('missing',))
