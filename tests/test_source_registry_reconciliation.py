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


if __name__ == '__main__':
    unittest.main()
