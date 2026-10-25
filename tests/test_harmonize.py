"""Offline tests for source-separated audited descriptive panel; no API access."""
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest
from research import harmonize as h

ROOT=Path(__file__).resolve().parents[1]
ISO=ROOT/'research/iso3_country_codes.csv'


def row(*, oid=1, family='wdi', provider='World Bank Indicators API', geo='USA', year=2020,
        period='2020', series='NY.GDP.MKTP.KD.ZG',value=2.5, unit='source_defined',freq='annual',
        disagg='{}',status='{}'):
    return {'observation_id':oid,'family':family,'provider':provider,
        'country_or_polity_code':geo,'year':year,'period':period,'series':series,'value':value,
        'unit':unit,'frequency':freq,'disaggregation_json':disagg,'source_status':status}


def archive_fixture(base,rows,bad_hash=False,bad_count=False):
    source=base/'economic-test-1';source.mkdir()
    db=source/'warehouse.sqlite'
    with sqlite3.connect(db) as con:
        con.execute('''CREATE TABLE observations (
            observation_id INTEGER, family TEXT, provider TEXT, country_or_polity_code TEXT,
            year INTEGER,period TEXT,series TEXT,value REAL,unit TEXT,frequency TEXT,
            disaggregation_json TEXT,source_status TEXT)''')
        con.executemany('INSERT INTO observations VALUES (:observation_id,:family,:provider,:country_or_polity_code,:year,:period,:series,:value,:unit,:frequency,:disaggregation_json,:source_status)',rows)
    payload=db.read_bytes()
    counts={}
    for r in rows:
        counts[r['family']]=counts.get(r['family'],0)+1
    meta={'snapshot_id':'economic-test-1','total_source_observations':len(rows)+(1 if bad_count else 0),
          'source_families':{family:{'rows':n} for family,n in counts.items()},
          'files':[{'path':'warehouse.sqlite','sha256':('f'*64 if bad_hash else hashlib.sha256(payload).hexdigest()),'bytes':len(payload)}]}
    (source/'manifest.json').write_text(json.dumps(meta))
    dst=base/'warehouse.tar.gz'
    with tarfile.open(dst,'w:gz') as tar:tar.add(source,arcname='economic-test-1')
    return dst


class HarmonizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.codes=h.iso_codes(ISO)

    def test_iso_reference(self):
        self.assertIn('USA',self.codes)
        self.assertIn('GHA',self.codes)
        self.assertNotIn('WLD',self.codes)
        self.assertNotIn('AFE',self.codes)
        self.assertGreater(len(self.codes),240)

    def test_wdi_aggregate_not_country(self):
        self.assertEqual(h.classify(row(geo='WLD'),self.codes,snapshot_id='s')[1],
                        'non_iso3_or_aggregate_geography')

    def test_historic_polity_is_not_modern_country_observation(self):
        self.assertEqual(h.classify(row(family='historical',geo='USA'),self.codes,snapshot_id='s')[1],
                        'historical_polity_or_estimate_review')

    def test_ilostat_disaggregations_not_silently_totaled(self):
        r=row(family='labor',provider='ILOSTAT',series='UNE_2UNE_SEX_AGE_NB_A',
              disagg='{"sex":"SEX_T","classif1":"AGE_Y15-24"}')
        self.assertEqual(h.classify(r,self.codes,snapshot_id='s')[1],
                         'ilostat_disaggregation_and_unit_metadata_not_verified')

    def test_imf_projection_excluded(self):
        r=row(family='imf_oecd',provider='IMF DataMapper',series='NGDP_RPCH',
              status=json.dumps({'data_status':'projection'}))
        self.assertEqual(h.classify(r,self.codes,snapshot_id='s')[1],
                         'imf_projection_not_realized')

    def test_tax_definitions_are_not_merged(self):
        w,_=h.classify(row(series='GC.TAX.TOTL.GD.ZS'),self.codes,snapshot_id='s')
        o,_=h.classify(row(family='imf_oecd',provider='OECD Global Revenue Statistics',
                            series='TAX_TOTAL_PCT_GDP'),self.codes,snapshot_id='s')
        self.assertEqual(w['concept'],o['concept'])
        self.assertNotEqual(w['comparison_group'],o['comparison_group'])
        self.assertIn('not_cross_source_comparable',w['review_flags'])

    def test_payroll_fred_bls_variants_separated(self):
        f,_=h.classify(row(family='us_europe',provider='FRED (St. Louis Fed; original series provider varies)',
                             series='PAYEMS',period='2020-01',freq='monthly'),self.codes,snapshot_id='s')
        b,_=h.classify(row(family='labor',provider='US BLS public API v1',
                             series='CES0000000001',period='2020-01',freq='monthly'),self.codes,snapshot_id='s')
        self.assertEqual(f['concept'],b['concept'])
        self.assertNotEqual(f['comparison_group'],b['comparison_group'])
        self.assertEqual(f['causal_use'],'not_ready')

    def test_unknown_indicator_does_not_enter_panel(self):
        self.assertEqual(h.classify(row(series='unknown'),self.codes,snapshot_id='s')[1],
                         'unmapped_or_unverified_measure')

    def test_policy_registry_studies_not_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            p=directory/'studies.csv'
            with p.open('w',newline='') as f:
                wr=csv.DictWriter(f,fieldnames=['event_id','country','policy_type','status','shock_value','shock_unit','source_url'])
                wr.writeheader();wr.writerow({'event_id':'ROMER','country':'USA','policy_type':'tax',
                  'status':'awaiting_numeric_extraction','shock_value':'','shock_unit':'',
                  'source_url':'https://www.nber.org/'})
            result=h.audit_policy_sources(p,directory/'audit.csv',self.codes)
            self.assertEqual(result['source_studies'],1)
            self.assertEqual(result['usable_dated_numeric_policy_shocks'],0)
            self.assertIn('source_study_not_policy_event',(directory/'audit.csv').read_text())

    def test_build_snapshot_persists_correct_counts(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t)
            rows=[row(oid=1),row(oid=2,geo='WLD'),row(oid=3,family='labor',provider='ILOSTAT',series='UNE_2UNE_SEX_AGE_NB_A')]
            archive=archive_fixture(base,rows)
            report=h.build(archive,base/'out',ISO)
            self.assertEqual(report['source_observations'],3)
            self.assertEqual(report['descriptive_candidates'],1)
            self.assertEqual(report['excluded_from_descriptive_panel'],2)
            self.assertFalse(report['claims']['causal_estimation_ready'])
            with (base/'out/descriptive_candidates.csv').open() as f:
                self.assertEqual(len(list(csv.DictReader(f))),1)
            self.assertTrue((base/'out/sha256_manifest.json').exists())

    def test_reject_changed_database_hash(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t)
            archive=archive_fixture(base,[row()],bad_hash=True)
            with self.assertRaisesRegex(ValueError,'Checksum mismatch'):
                h.build(archive,base/'out',ISO)
            self.assertFalse((base/'out').exists())

    def test_reject_manifest_total_mismatch(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t)
            archive=archive_fixture(base,[row()],bad_count=True)
            with self.assertRaisesRegex(ValueError,'Manifest row count mismatch'):
                h.build(archive,base/'out',ISO)

    def test_prevent_overwrite(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t);out=base/'out';out.mkdir();(out/'previous.txt').write_text('keep')
            with self.assertRaises(FileExistsError):h.build('bad.tar.gz',out,ISO)
            self.assertEqual((out/'previous.txt').read_text(),'keep')


if __name__=='__main__':unittest.main()
