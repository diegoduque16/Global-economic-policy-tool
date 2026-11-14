"""Tests for the existing catalog registry's historical evidence reconciliation.

No network, OneDrive, numeric data, or private source files are required.
"""
from __future__ import annotations

import contextlib
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_source_registry as m


class SourceRegistryReconciliationTests(unittest.TestCase):
    def test_p0_is_exact_subset_of_current_catalog(self):
        _, entries = m.build()
        self.assertEqual(len(entries), 125)
        p0 = m.priority_evidence_rows(entries)
        self.assertEqual(len(p0), 71)
        self.assertEqual({r['source_id'] for r in p0},
                         {r['source_id'] for r in entries if r['priority'] == 'P0'})
        self.assertEqual(len({r['source_id'] for r in entries}), 125)
        self.assertEqual(sum(e['research_ready'] is True for e in entries), 0)

    def test_reuses_atlas1_exact_names_without_claiming_dataset_completeness(self):
        _, entries = m.build()
        lookup = {e['dataset']: e for e in entries}
        self.assertTrue(set(m.ATLAS1_FAMILY_MAP).issubset(lookup))
        for dataset, families in m.ATLAS1_FAMILY_MAP.items():
            evidence = lookup[dataset]['evidence_linkage']
            self.assertEqual(evidence['historical_family_ids'], list(families))
            self.assertEqual(evidence['evidence_basis'], 'HISTORICAL_PARTIAL_FAMILY_MAP_NOT_SERIES_VALIDATED')
            self.assertFalse(evidence['exact_series_keys_verified'])
            self.assertFalse(evidence['complete_catalog_source_coverage_verified'])
            self.assertFalse(evidence['source_snapshot_hash_verified_by_registry'])
            self.assertIsNone(lookup[dataset]['record_count_verified'])

    def test_separate_wid_and_ilostat_do_not_become_canonical_or_new_numbers(self):
        _, entries = m.build()
        lookup = {e['dataset']:e for e in entries}
        wid = lookup['World Inequality Database WID']['evidence_linkage']
        self.assertEqual(wid['evidence_basis'], 'HISTORICAL_SEPARATE_SOURCE_RUN_NOT_CANONICAL')
        self.assertEqual(wid['historical_family_ids'], [])
        self.assertEqual(wid['additional_source_stage'], 'SEPARATE_WID_NUMERIC_SNAPSHOT')
        self.assertIsNone(wid['last_warehouse_integrated_at_utc'])
        ilo = lookup['ILOSTAT employment/working time/earnings']['evidence_linkage']
        self.assertEqual(ilo['additional_source_stage'], 'PARTIAL_RAW_BULK_ARCHIVE')
        self.assertIn('labor', ilo['historical_family_ids'])
        self.assertIsNone(ilo['verified_duplicate_observation_count'])
        self.assertTrue(all(lookup[n]['evidence_linkage']['overlap_review'] ==
                            'POTENTIAL_OVERLAP_NEEDS_KEY_AUDIT' for n in m.OVERLAP_REVIEW_REQUIRED))

    def test_unknown_is_not_misclassified_as_absent(self):
        _, entries = m.build()
        unmapped = [e for e in entries if e['dataset'] not in m.ATLAS1_FAMILY_MAP
                    and e['dataset'] not in m.SEPARATE_SOURCE_RUNS]
        self.assertTrue(unmapped)
        for item in unmapped:
            ev = item['evidence_linkage']
            self.assertIn('NOT_PROOF_OF_ABSENCE', ev['evidence_basis'])
            self.assertEqual(ev['historical_family_ids'], [])
            self.assertIsNone(ev['last_collected_at_utc'])
            self.assertIsNone(ev['last_warehouse_integrated_at_utc'])
            self.assertFalse(item['research_ready'])

    def test_unrecognized_historical_mapping_fails_closed(self):
        with patch.dict(m.ATLAS1_FAMILY_MAP, {'Not part of current source catalog': ('wdi',)}):
            with self.assertRaisesRegex(ValueError, 'crosswalk no longer matches'):
                m.build()

    def test_existing_generator_emits_single_p0_view_and_unchanged_catalog_claims(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(m, 'OUTPUT', Path(folder)):
            with contextlib.redirect_stdout(io.StringIO()):
                m.main()
            registry = json.loads((Path(folder)/'registry.json').read_text())
            summary = json.loads((Path(folder)/'registry_summary.json').read_text())
            with (Path(folder)/'priority_p0_evidence.csv').open(newline='',encoding='utf-8') as f:
                p0 = list(csv.DictReader(f))
            self.assertEqual(len(registry),125)
            self.assertEqual(len(p0),71)
            self.assertEqual(summary['priority_p0_evidence_summary']['source_count'],71)
            self.assertFalse(summary['priority_p0_evidence_summary']['observation_counts_are_additive'])
            self.assertEqual(summary['priority_p0_evidence_summary']['exact_series_key_audits_completed'],0)
            self.assertEqual(summary['priority_p0_evidence_summary']['fully_ingested_source_count_proven_by_this_builder'],0)
            self.assertEqual(sum(summary['priority_p0_evidence_summary']['by_evidence_basis'].values()),71)
            self.assertTrue(all(x['research_ready']=='false' for x in p0))
            self.assertTrue(all(x['complete_source_coverage_verified']=='false' for x in p0))
            self.assertTrue(all(x['last_warehouse_integrated_at_utc']=='' for x in p0))
            self.assertEqual(len({x['source_id'] for x in p0}),71)
            wdi = next(x for x in registry if x['dataset']=='World Bank World Development Indicators (32 selected)')
            self.assertEqual(wdi['normalized_status'], 'reported_live_unverified')
            self.assertEqual(wdi['validation_state'], 'NOT_INDEPENDENTLY_VERIFIED')


    def test_wid_refresh_country_scope_is_not_new_geographic_coverage(self):
        result = m.wid_country_refresh_scope()
        self.assertEqual(result['original_country_count'], 23)
        self.assertEqual(result['scheduled_refresh_country_count'], 20)
        self.assertEqual(result['previously_in_original_country_count'], 20)
        self.assertEqual(result['new_country_codes_not_in_original'], [])
        self.assertEqual(result['original_country_codes_not_in_refresh'], ['CO', 'FR', 'US'])
        self.assertFalse(result['same_observation_keys_or_values_verified'])
        self.assertFalse(result['source_vintages_compared'])
        self.assertFalse(result['new_numeric_observations_verified'])
        _, registry = m.build()
        wid = next(x for x in registry if x['dataset'] == 'World Inequality Database WID')
        self.assertEqual(wid['evidence_linkage']['wid_refresh_country_scope'], result)
        self.assertFalse(wid['research_ready'])

    def test_wid_refresh_country_scope_fails_closed_when_matrix_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root/'manifest.json'
            workflow = root/'refresh.yml'
            manifest.write_text(json.dumps({'expected_country_count':2, 'countries':[
                {'wid_area':'GH'}, {'wid_area':'KE'}]}))
            workflow.write_text('countries: GH,KE\n')
            with self.assertRaisesRegex(ValueError, 'matrix'):
                m.wid_country_refresh_scope(manifest, workflow)

    def test_persisted_wid_country_overlap_remains_metadata_only(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(m,'OUTPUT',Path(folder)):
            with contextlib.redirect_stdout(io.StringIO()):
                m.main()
            report = json.loads((Path(folder)/'registry_summary.json').read_text())
            audit = report['wid_country_refresh_scope_audit']
            self.assertEqual(audit['previously_in_original_country_count'],20)
            self.assertEqual(audit['new_country_codes_not_in_original'],[])
            self.assertFalse(audit['new_numeric_observations_verified'])
            self.assertEqual(report['priority_p0_evidence_summary']['exact_series_key_audits_completed'],0)
            with (Path(folder)/'priority_p0_evidence.csv').open(newline='',encoding='utf-8') as f:
                rows = list(csv.DictReader(f))
            wid = next(x for x in rows if x['dataset']=='World Inequality Database WID')
            self.assertEqual(wid['wid_scheduled_country_overlap_count'],'20')
            self.assertEqual(wid['wid_scheduled_new_country_count'],'0')
            self.assertEqual(wid['wid_value_level_revision_audit_complete'],'false')


    def test_exact_public_wdi_series_evidence_is_source_specific_and_noncanonical(self):
        from scripts.audit_public_wdi_series import audit_public_wdi
        result, series = audit_public_wdi()
        self.assertEqual(result['source_series_count'],32)
        self.assertEqual(result['source_observation_count'],300240)
        self.assertEqual(result['country_or_territory_codes'],217)
        self.assertEqual(result['aggregate_codes'],44)
        self.assertEqual(len({s['indicator_code'] for s in series}),32)
        self.assertEqual(sum(s['verified_numeric_keys'] for s in series),300240)
        self.assertEqual(sum(s['country_or_territory_observations']+
                             s['aggregate_observations'] for s in series),300240)
        self.assertTrue(all(s['checked_original_raw_pages_this_run']=='false' for s in series))
        self.assertFalse(result['canonical_inclusion_independently_verified'])
        self.assertFalse(result['complete_world_bank_indicator_universe'])

    def test_registry_exposes_wdi_series_without_claiming_other_sources_ready(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(m,'OUTPUT',Path(folder)):
            with contextlib.redirect_stdout(io.StringIO()):
                m.main()
            p=Path(folder)
            report=json.loads((p/'registry_summary.json').read_text())
            registry=json.loads((p/'registry.json').read_text())
            with (p/'public_wdi_series_evidence.csv').open(newline='') as f:
                series=list(csv.DictReader(f))
            self.assertEqual(len(series),32)
            self.assertEqual(report['priority_p0_evidence_summary'][
                'public_wdi_series_key_audits_completed_not_raw_page_or_canonical'],32)
            self.assertEqual(report['priority_p0_evidence_summary']['exact_series_key_audits_completed'],0)
            wdi=next(x for x in registry if x['dataset']=='World Bank World Development Indicators (32 selected)')
            evidence=wdi['evidence_linkage']['published_wdi_source_edition']
            self.assertEqual(evidence['source_run_id'],42581151428)
            self.assertEqual(evidence['source_observation_count'],300240)
            self.assertFalse(wdi['research_ready'])
            self.assertIsNone(wdi['evidence_linkage']['last_warehouse_integrated_at_utc'])
            self.assertEqual(sum(x['research_ready'] is True for x in registry),0)
            with (p/'priority_p0_evidence.csv').open(newline='') as f:
                rows=list(csv.DictReader(f))
            wdi_row=next(x for x in rows if x['dataset']==wdi['dataset'])
            self.assertEqual(wdi_row['published_wdi_edition_series'],'32')
            self.assertEqual(sum(bool(x['published_wdi_edition_series']) for x in rows),1)

    def test_wid_source_raw_hash_comparison_is_not_numeric_revision_claim(self):
        scope = m.wid_country_refresh_scope()
        aud = scope['source_file_comparison']
        self.assertEqual(aud['country_count'], 20)
        self.assertEqual(aud['raw_source_file_sha256_changed_count'], 20)
        self.assertEqual(aud['raw_source_file_sha256_unchanged_count'], 0)
        self.assertEqual(aud['same_retained_source_row_count_countries'], 20)
        self.assertEqual(aud['baseline_source_rows_for_refreshed_countries'], 17640)
        self.assertEqual(aud['refreshed_source_rows_for_same_countries'], 17640)
        self.assertFalse(aud['exact_observation_keys_compared'])
        self.assertFalse(aud['exact_observation_values_compared'])
        self.assertFalse(aud['new_or_revised_economic_observations_verified'])

    def test_wid_source_hash_evidence_invalid_and_missing_country_fail_closed(self):
        evidence = json.loads(m.WID_REFRESH_FINGERPRINTS.read_text())
        original = json.loads(m.WID_MANIFEST.read_text())
        codes = [x['wid_area'] for x in evidence['source_fingerprints']]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'fingerprints.json'
            bad = json.loads(json.dumps(evidence))
            bad['source_fingerprints'][0]['source_sha256']='x'*64
            p.write_text(json.dumps(bad))
            with self.assertRaisesRegex(ValueError, 'source SHA256'):
                m.compare_wid_raw_source_fingerprints(original, codes, p)
            bad = json.loads(json.dumps(evidence))
            bad['source_fingerprints'].pop()
            p.write_text(json.dumps(bad))
            with self.assertRaisesRegex(ValueError, 'Incomplete WID'):
                m.compare_wid_raw_source_fingerprints(original, codes, p)
            bad = json.loads(json.dumps(evidence))
            bad['source_fingerprints'][0]['retained_source_shares']+=1
            p.write_text(json.dumps(bad))
            with self.assertRaisesRegex(ValueError, 'cumulative refreshed'):
                m.compare_wid_raw_source_fingerprints(original, codes, p)

    def test_source_summary_does_not_claim_data_deduplication(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(m,'OUTPUT',Path(folder)):
            with contextlib.redirect_stdout(io.StringIO()):
                m.main()
            report = json.loads((Path(folder)/'registry_summary.json').read_text())
            self.assertEqual(report['registry_version'], '0.4.0')
            audit = report['wid_country_refresh_scope_audit']['source_file_comparison']
            self.assertEqual(audit['raw_source_file_sha256_changed_count'],20)
            self.assertFalse(audit['exact_observation_keys_compared'])
            self.assertEqual(report['priority_p0_evidence_summary']['exact_series_key_audits_completed'],0)
            self.assertEqual(report['verified_research_ready_count'],0)


if __name__ == '__main__':
    unittest.main()
