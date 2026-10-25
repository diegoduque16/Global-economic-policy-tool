"""Offline archive safety and numeric review-input validation tests."""
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from policy_shocks import audit_sources as a
from policy_shocks import ingest_reviewed as r

ROOT=Path(__file__).resolve().parents[1]

def zippy(items):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in items:z.writestr(name,data)
    return stream.getvalue()

class FiscalShockTests(unittest.TestCase):
    def test_original_registry(self):
        reg=json.loads((ROOT/'policy_shocks/sources.json').read_text())
        self.assertEqual(len(reg['sources']),3)
        self.assertEqual(reg['sources'][0]['replication_url'],a.ALLOWED_URL)
        self.assertNotEqual(reg['sources'][0]['design'],reg['sources'][2]['design'])

    def test_zip_inventory_hashes_no_values(self):
        b=zippy([('replication/data.csv','year,measure\n1950,9\n')]);v=a.audit_zip(b)
        self.assertEqual(v['member_count'],1)
        self.assertEqual(v['source_archive_sha256'],hashlib.sha256(b).hexdigest())
        self.assertEqual(v['numeric_shocks_imported'],0)
        self.assertNotIn('1950,9',json.dumps(v))

    def test_zip_reject_traversal(self):
        with self.assertRaises(ValueError):a.audit_zip(zippy([('../oops.txt','bad')]))

    def test_zip_reject_backslash(self):
        with self.assertRaises(ValueError):a.audit_zip(zippy([('..\\oops.txt','bad')]))

    def test_zip_reject_duplicate(self):
        with self.assertRaises(ValueError):a.audit_zip(zippy([('Foo.csv','x'),('foo.csv','y')]))

    def test_zip_reject_wrong_study(self):
        with self.assertRaises(ValueError):a.audit_zip(zippy([('a.csv','1')]),source_id='unknown')

    def test_download_domain_allowlist(self):
        with self.assertRaises(ValueError):a.retrieve('https://example.org/other.zip')

    def test_missing_inventory_fails_closed(self):
        with self.assertRaises(FileNotFoundError):r.reviewed_to_rows('missing.csv','missing.json',ROOT/'policy_shocks/sources.json')

    def prep(self,t,**overrides):
        base=Path(t)
        mem='source.csv';blob=b'row,shock\n2,-0.5\n';h=hashlib.sha256(blob).hexdigest()
        inv={'source_id':'romer_romer_2010','download_is_local_fixture':False,
             'source_archive_sha256':'b'*64,'members':[{'member':mem,'sha256':h}]}
        (base/'inventory.json').write_text(json.dumps(inv))
        data=dict(zip(r.FIELDS,['romer_romer_2010',mem,h,'row2','USA','1950Q1',
             'quarterly','-0.5','pct_GDP_reported','taxexog',
             'positive_equals_tax_increase','tax','narrative_exogenous_tax',
             'appendix_table_1','https://example.edu/codebook','Reviewer','2026-10-25','accepted']))
        data.update(overrides)
        with (base/'review.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=r.FIELDS);w.writeheader();w.writerow(data)
        return base/'review.csv',base/'inventory.json'

    def read(self,t,**changes):
        a1,b1=self.prep(t,**changes)
        return r.reviewed_to_rows(a1,b1,ROOT/'policy_shocks/sources.json')

    def test_accept_reviewed_source_values_but_not_effects(self):
        with tempfile.TemporaryDirectory() as t:
            x,inv=self.read(t)
            self.assertEqual(x[0]['shock_original_value'],'-0.5')
            self.assertFalse(inv['download_is_local_fixture'])
            review,inventory=Path(t)/'review.csv',Path(t)/'inventory.json'
            r.build(review,inventory,ROOT/'policy_shocks/sources.json',Path(t)/'output')
            report=json.loads((Path(t)/'output/reviewed_policy_shock_report.json').read_text())
            self.assertFalse(report['causal_estimation_ready'])

    def test_reject_unreviewed(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(ValueError,'explicitly accepted'):self.read(t,review_status='pending')

    def test_reject_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(ValueError,'digest mismatch'):self.read(t,source_member_sha256='a'*64)

    def test_reject_wrong_study(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(ValueError,'source does not match'):self.read(t,study_id='cloyne_2013')

    def test_reject_wrong_policy_class(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(ValueError,'family not verified'):self.read(t,policy_family='spending')

    def test_reject_bad_date(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(ValueError,'invalid period'):self.read(t,period='1950Q5')

    def test_reject_fabricated_future_period(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(ValueError,'conservative research scope'):self.read(t,period='2025Q1')

    def test_reject_nonfinite(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(ValueError,'nonfinite'):self.read(t,shock_original_value='nan')

    def test_reject_fake_audit(self):
        with tempfile.TemporaryDirectory() as t:
            review,inventory=self.prep(t)
            j=json.loads(inventory.read_text());j['download_is_local_fixture']=True
            inventory.write_text(json.dumps(j))
            with self.assertRaisesRegex(ValueError,'genuine'):r.reviewed_to_rows(review,inventory,ROOT/'policy_shocks/sources.json')

if __name__=='__main__':unittest.main()
