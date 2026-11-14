import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import shutil
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

    def make_wdi_fixture(self):
        root=self.root/'source_fixture'
        (root/'raw').mkdir(parents=True)
        (root/'processed').mkdir()
        indicator='NY.GDP.MKTP.KD.ZG'
        url='https://api.worldbank.org/v2/country/all/indicator/'+indicator+'?format=json&page=1'
        raw=json.dumps([{'page':1,'pages':1},
                        [{'countryiso3code':'USA','date':'2020','value':2.5}]]).encode()
        sha=hashlib.sha256(raw).hexdigest()
        name=indicator+'_1_'+sha[:12]+'.json'
        (root/'raw'/name).write_bytes(raw)
        record={'country_iso3':'USA','year':2020,'indicator':indicator,'value':2.5,
                'unit':'source_defined','source':'World Bank Indicators API',
                'source_url':url,'downloaded_at_utc':'2026-10-29T00:00:00+00:00',
                'obs_status':'','decimal':''}
        with (root/'processed'/'wdi_panel.csv').open('w',newline='') as out:
            writer=csv.DictWriter(out,fieldnames=list(record))
            writer.writeheader();writer.writerow(record)
        report={'country_query':'all','start':1960,'end':2026,
                'observations':1,'raw_pages':1,'indicator_count':1,
                'errors':[],'conflicts':[],
                'manifest':[{'indicator':indicator,'url':url,'file':name,
                             'sha256':sha,'downloaded_at_utc':'now','returned':1}]}
        (root/'run_report.json').write_text(json.dumps(report))
        return root,report,name

    def test_wdi_full_source_raw_sha_and_numeric_verified(self):
        from unittest.mock import patch
        root,report,name=self.make_wdi_fixture()
        with patch.object(m,'WDI_MIN_ROWS',1):
            result=m.validate_wdi_download(root)
        self.assertEqual(result['observations'],1)
        self.assertEqual(result['raw_pages'],1)
        self.assertFalse(result['canonical_integration_verified'])

    def test_wdi_reject_corrupt_raw_page_and_failed_collector(self):
        from unittest.mock import patch
        root,report,name=self.make_wdi_fixture()
        with patch.object(m,'WDI_MIN_ROWS',1):
            (root/'raw'/name).write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError,'SHA256'):
                m.validate_wdi_download(root)
            shutil.rmtree(root)
            root,report,name=self.make_wdi_fixture()
            report['errors']=[{'indicator':'TEST','error':'failed'}]
            (root/'run_report.json').write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError,'retrieval errors'):
                m.validate_wdi_download(root)

    def test_wdi_reject_unexpected_scope_and_duplicate_numeric_keys(self):
        from unittest.mock import patch
        root,report,name=self.make_wdi_fixture()
        with patch.object(m,'WDI_MIN_ROWS',1):
            report['country_query']='USA'
            (root/'run_report.json').write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError,'full historical global'):
                m.validate_wdi_download(root)
            report['country_query']='all'
            report['observations']=2
            (root/'run_report.json').write_text(json.dumps(report))
            panel=root/'processed'/'wdi_panel.csv'
            with panel.open() as f:
                rows=f.readlines()
            with panel.open('a') as f:
                f.write(rows[-1])
            with self.assertRaisesRegex(ValueError,'Duplicate WDI'):
                m.validate_wdi_download(root)

    def test_wdi_reject_nan_and_unmanifested_raw_files(self):
        from unittest.mock import patch
        root,report,name=self.make_wdi_fixture()
        panel=root/'processed'/'wdi_panel.csv'
        with panel.open() as f:
            rows=f.readlines()
        with patch.object(m,'WDI_MIN_ROWS',1):
            panel.write_text(rows[0]+rows[1].replace(',2.5,',',nan,'))
            with self.assertRaisesRegex(ValueError,'not finite'):
                m.validate_wdi_download(root)
            panel.write_text(''.join(rows))
            (root/'raw'/'untracked.json').write_text('x')
            with self.assertRaisesRegex(ValueError,'Untracked WDI raw'):
                m.validate_wdi_download(root)

    def test_wdi_latest_scheduled_main_only(self):
        future='2026-10-29T00:00:00Z'
        rows=[{'id':6,'run_number':6,'event':'workflow_dispatch','head_branch':'main',
               'status':'completed','conclusion':'success','created_at':future},
              {'id':7,'run_number':7,'event':'schedule','head_branch':'test',
               'status':'completed','conclusion':'success','created_at':future},
              {'id':8,'run_number':8,'event':'schedule','head_branch':'main',
               'status':'completed','conclusion':'failure','created_at':future},
              {'id':5,'run_number':5,'event':'schedule','head_branch':'main',
               'status':'completed','conclusion':'success','created_at':'2026-10-28T00:00:00Z'}]
        self.assertEqual(m.select_wdi_run(rows,self.now),{'id':5,'run_number':5})
        self.assertIsNone(m.select_wdi_run(rows[:3],self.now))

    def test_wdi_backup_new_verified_archive_without_new_collector(self):
        from unittest.mock import patch
        root,report,name=self.make_wdi_fixture()
        now=self.now
        runs=[{'id':99,'run_number':6,'event':'schedule','head_branch':'main',
               'status':'completed','conclusion':'success','created_at':'2026-10-29T00:00:00Z'}]
        private={}
        calls=[]
        def fake_run(cmd,timeout=1800):
            calls.append(cmd)
            if cmd[:2]==['rclone','lsjson']:
                return '[]'
            if cmd[:3]==['gh','run','download']:
                dest=Path(cmd[-1])
                for item in root.rglob('*'):
                    if item.is_file():
                        target=dest/item.relative_to(root)
                        target.parent.mkdir(parents=True,exist_ok=True)
                        shutil.copy2(item,target)
                return ''
            if cmd[:2]==['rclone','copyto']:
                private[cmd[3]]=Path(cmd[2]).read_bytes()
                return ''
            raise AssertionError('Unexpected external command '+repr(cmd))
        def fake_remote_hash(remote):
            return hashlib.sha256(private[remote]).hexdigest()
        with patch.object(m,'WDI_MIN_ROWS',1), \
             patch.object(m,'github_runs',return_value=runs), \
             patch.object(m,'run',side_effect=fake_run), \
             patch.object(m,'remote_digest',side_effect=fake_remote_hash):
            result=m.backup_recent_wdi('diegoduque16/Global-economic-policy-tool',now,self.root)
        self.assertEqual(result['observations'],1)
        self.assertEqual(result['status'],'NEW_WDI_SOURCE_ARCHIVE_PRIVATE_BYTES_VERIFIED')
        self.assertEqual(len(private),3)
        self.assertEqual([cmd[:3] for cmd in calls if cmd[0]=='gh'],
                         [['gh','run','download']])

    def test_wdi_backup_existing_full_remote_hash_audited(self):
        from unittest.mock import patch
        runs=[{'id':99,'run_number':6,'event':'schedule','head_branch':'main',
               'status':'completed','conclusion':'success','created_at':'2026-10-29T00:00:00Z'}]
        digest='a'*64
        name='wdi-source-99.tar.gz'
        archive_root=m.WDI_REMOTE_ROOT+'/run-99'
        expected=[name,name+'.sha256','source_validation.json']
        def fake_run(cmd,timeout=1800):
            if cmd[:2]==['rclone','lsjson']:
                return json.dumps([{'Name':x} for x in expected])
            if cmd[:2]==['rclone','cat'] and cmd[2].endswith('.sha256'):
                return digest+'  '+name+'\n'
            if cmd[:2]==['rclone','cat'] and cmd[2].endswith('.json'):
                return json.dumps({'source_run_id':99,'archive_sha256':digest,'observations':123})
            raise AssertionError('Unexpected remote request')
        with patch.object(m,'github_runs',return_value=runs), \
             patch.object(m,'run',side_effect=fake_run), \
             patch.object(m,'remote_digest',return_value=digest) as verify_remote:
            result=m.backup_recent_wdi('diegoduque16/Global-economic-policy-tool',self.now,self.root)
        self.assertEqual(result['status'],'PREVIOUS_WDI_PRIVATE_ARCHIVE_BYTES_VERIFIED')
        self.assertEqual(result['observations'],123)
        verify_remote.assert_called_once_with(archive_root+'/'+name)

    def test_wdi_backup_failure_is_visible_but_not_a_fake_success(self):
        from unittest.mock import patch
        with patch.object(m,'backup_recent_wdi',side_effect=RuntimeError('private temporary outage')):
            report=m.safe_backup_recent_wdi('diegoduque16/Global-economic-policy-tool',
                                            self.now,self.root)
        self.assertEqual(report['status'],'WDI_PRIVATE_BACKUP_NOT_VERIFIED')
        self.assertEqual(report['error_type'],'RuntimeError')
        self.assertFalse(report['canonical_integration_verified'])

    def test_wdi_new_source_ledger_not_additive(self):
        ledger=m.make_ledger({'validated_raw_csv_rows_NOT_all_numeric':100},{'observations':3},
                             {'observed_numeric_cells':10},{'canonical_observations_previously_verified':5},
                             {'status':'not_run'},
                             {'status':'NEW_WDI_SOURCE_ARCHIVE_PRIVATE_BYTES_VERIFIED','observations':1000})
        self.assertEqual(len(ledger),6)
        self.assertTrue(all(x['additive_to_other_rows']=='no' for x in ledger))

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
