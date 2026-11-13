import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import atlas_data_watchdog as m

class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2026,10,30,12,0,tzinfo=timezone.utc)

    def test_checkpoint_counts_nonadditive_raw_rows(self):
        data={'schema_version':1,'last_run_utc':'2026-10-30T11:20:00Z','latest_toc':{'entries':1964},
              'downloaded':{'EMP_TEST_NB_A':{'records_validated':12,'compressed_bytes':100,
              'sha256':'a'*64,'remote':m.ILOSTAT_ROOT+'/indicator-data/EMP_TEST_NB_A/a.csv.gz',
              'state':'NATIVE_NOT_HARMONIZED'}}}
        o=m.verify_ilostat(data,self.now)
        self.assertEqual(o['remaining_bulk_files'],1963)
        self.assertEqual(o['validated_raw_csv_rows_NOT_all_numeric'],12)
        self.assertFalse(o['raw_values_joined_to_warehouse'])
        data['downloaded']['EMP_TEST_NB_A']['records_validated']=-1
        with self.assertRaisesRegex(ValueError,'validated physical'):m.verify_ilostat(data,self.now)

    def test_wid_sqlite_real_type_and_pinned_sha(self):
        path=self.root/'wid.sqlite'
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE wid_numeric_observations(iso3 TEXT, value_fraction REAL);'
                             'CREATE TABLE wid_distribution_panel(posttax_minus_pretax_percentage_points REAL);')
            db.executemany('INSERT INTO wid_numeric_observations VALUES (?,?)',[(x,0.24) for x in ['USA','FRA','GHA']])
            db.execute('INSERT INTO wid_distribution_panel VALUES (3.0)')
        pin={'wid_23country_values.sqlite':m.digest(path)}
        validation={'actual_numeric_observation_rows_in_sqlite':3,'country_count':3,'actual_matched_pretax_posttax_pairs':1}
        old=m.WID_EXPECTED
        try:
            m.WID_EXPECTED=3
            # 23 distinct countries are an intentional real-run safety condition.
            with self.assertRaisesRegex(ValueError,'unexpectedly changed'):m.verify_wid(path,pin,validation)
        finally:m.WID_EXPECTED=old
        with self.assertRaisesRegex(ValueError,'SHA256'):m.check_file_manifest(path,{'wid_23country_values.sqlite':'f'*64},'wid_23country_values.sqlite')

    def test_actual_joined_numeric_cells(self):
        f=self.root/'data.csv'
        with f.open('w',newline='') as fd:
            w=csv.DictWriter(fd,fieldnames=['iso3','year','top1_pretax_income_share','gdp_growth_pct'])
            w.writeheader()
            for code in ['USA','FRA','GHA','GBR','JPN','DEU','IND','AUS','CAN','BRA']:
                for y in range(2010,2020):w.writerow({'iso3':code,'year':y,'top1_pretax_income_share':18.3,'gdp_growth_pct':2.2})
        manifest={'wid_wdi_country_year_numeric.csv':m.digest(f)}
        report={'countries_with_both_sources':10,'country_year_rows_with_both_sources':100,'total_matched_numeric_cells':200,'policy_reforms_joined':0,'causal_effects_estimated':0}
        r=m.verify_joined(f,manifest,report)
        self.assertEqual(r['observed_numeric_cells'],200)
        with self.assertRaisesRegex(ValueError,'differ'):m.verify_joined(f,manifest,{**report,'total_matched_numeric_cells':201})
        with self.assertRaisesRegex(ValueError,'incorrectly labels'):m.verify_joined(f,manifest,{**report,'causal_effects_estimated':1})
        with f.open('a') as fd:fd.write('USA,2010,0.1,1.1\n')
        with self.assertRaisesRegex(ValueError,'SHA256'):m.verify_joined(f,manifest,report)

    def test_repair_throttling(self):
        stale=[{'event':'schedule','status':'completed','conclusion':'success','created_at':'2026-10-27T01:00:00Z'}]
        self.assertEqual(m.choose_ingestion_repair(stale,self.now)['action'],'dispatch_stale')
        recent=[{'event':'schedule','status':'completed','conclusion':'failure','created_at':'2026-10-30T10:00:00Z'}]
        self.assertEqual(m.choose_ingestion_repair(recent,self.now)['action'],'none')
        active=[{'event':'schedule','status':'in_progress','created_at':'2026-10-28T10:00:00Z'}]
        self.assertEqual(m.choose_ingestion_repair(active,self.now)['action'],'none')
        with self.assertRaisesRegex(ValueError,'Future'):m.choose_ingestion_repair([{'event':'schedule','status':'completed','created_at':'2026-11-03T10:00:00Z'}],self.now)

    def test_verified_batch_tarball(self):
        archive=self.root/'economic-batch-123.tar.gz'
        audit=self.root/'audit.json';audit.write_text(json.dumps({'successful':[{'file':'x'}]}))
        with tarfile.open(archive,'w:gz') as t:t.add(audit,arcname='batch_source_results/audit.json')
        sha=self.root/'economic-batch-123.tar.gz.sha256';sha.write_text(m.digest(archive)+'  '+archive.name+'\n')
        _,_,report=m.verify_batch_archive(self.root,123)
        self.assertEqual(report['successful_source_files'],1)
        sha.write_text('f'*64+'  '+archive.name+'\n')
        with self.assertRaisesRegex(ValueError,'SHA256'):m.verify_batch_archive(self.root,123)

    def test_canonical_marker_no_overstatement(self):
        f=self.root/'canonical.sha256';f.write_text(m.CANONICAL_SHA256+'  canonical-20261028-40616230528.tar.gz\n')
        r=m.verify_canonical_source_marker(f)
        self.assertEqual(r['canonical_observations_previously_verified'],2780117)
        self.assertFalse(r['full_archive_bytes_checked_this_run'])

    def test_ledger_does_not_sum_duplicates(self):
        ledger=m.make_ledger({'validated_raw_csv_rows_NOT_all_numeric':100},{'observations':3},{'observed_numeric_cells':10},{'canonical_observations_previously_verified':5},{'status':'not_run'})
        self.assertEqual(len(ledger),5)
        self.assertTrue(all(x['additive_to_other_rows']=='no' for x in ledger))


    def test_isolated_wid_comparison_skips_ilostat_and_all_external_repair(self):
        from unittest.mock import patch
        from research import audit_wid_revisions
        input_dir=self.root/'inputs'
        input_dir.mkdir()
        (input_dir/'wid_sha.json').write_text(json.dumps({'wid_23country_values.sqlite':'a'*64}))
        (input_dir/'wid_validation.json').write_text(json.dumps({'country_count':23}))
        out=self.root/'out'
        mock_result={'country_count':20,
                     'totals':{'refreshed':17640,'unchanged':17637,'changed':3,'added':0,'removed':0,'baseline':17640}}
        with patch.object(m, 'verify_wid', return_value={'observations':21430}) as verify, \
             patch.object(audit_wid_revisions, 'compare', return_value=mock_result) as compare, \
             patch.object(m, 'verify_ilostat', side_effect=AssertionError('ILOSTAT should not be accessed')), \
             patch.object(m, 'verify_joined', side_effect=AssertionError('join should not be accessed')), \
             patch.object(m, 'verify_canonical_source_marker', side_effect=AssertionError('canonical should not be accessed')), \
             patch.object(m, 'github_runs', side_effect=AssertionError('GitHub should not be accessed')), \
             patch.object(m, 'dispatch_ilostat', side_effect=AssertionError('ingestion should not be dispatched')), \
             patch.object(m, 'backup_recent_batch', side_effect=AssertionError('backup should not run')):
            with patch('builtins.print'):
                report=m.main(['--input',str(input_dir),'--out',str(out),'--wid-only-revisions'])
        self.assertEqual(report['status'],'WID_ONLY_EXACT_KEY_AND_VALUE_COMPARISON')
        self.assertFalse(report['auto_ingestion_dispatch'])
        self.assertEqual(report['ilostat_status'],'NOT_ACCESSED_IN_WID_ONLY_MODE')
        self.assertEqual(report['onedrive_backup'],'NOT_ATTEMPTED_MANUAL_READ_ONLY')
        self.assertEqual(verify.call_count,1)
        self.assertEqual(compare.call_count,1)
        recorded=json.loads((out/'source_coverage_audit.json').read_text())
        self.assertEqual(recorded['wid_revision_comparison']['totals']['changed'],3)
        ledger=list(csv.DictReader((out/'source_numeric_coverage_ledger.csv').open()))
        self.assertEqual(len(ledger),2)
        self.assertTrue(all(x['additive_to_other_rows']=='no' for x in ledger))
        checksums=json.loads((out/'output_checksums.json').read_text())
        self.assertEqual(checksums['source_coverage_audit.json'],m.digest(out/'source_coverage_audit.json'))

    def test_isolated_wid_refuses_auto_repair_combination(self):
        with self.assertRaises(SystemExit) as exc:
            m.main(['--input',str(self.root),'--out',str(self.root/'out'),
                    '--wid-only-revisions','--auto-repair'])
        self.assertEqual(exc.exception.code,2)
        self.assertFalse((self.root/'out').exists())

    def test_isolated_wid_fails_closed_on_missing_private_wid_files(self):
        inputs=self.root/'inputs'
        inputs.mkdir()
        with self.assertRaises(FileNotFoundError):
            m.main(['--input',str(inputs),'--out',str(self.root/'out'), '--wid-only-revisions'])

if __name__ == '__main__':unittest.main()
