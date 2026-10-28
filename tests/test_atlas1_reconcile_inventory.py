import unittest
from research.atlas1_reconcile_inventory import EVIDENCE, reconcile

FAMILIES = {
    'wdi': 300240, 'us_europe': 13448, 'historical': 16395,
    'imf_oecd': 51243, 'labor': 123233, 'itep_tax_rates': 19278,
    'state_tax_policy_actions': 855, 'census_bds': 1521374,
    'tariff_shocks': 185, 'cait_ai_claims': 1872,
    'eurostat_ai': 139, 'imf_gfs': 2579, 'bls_bed': 3444,
    'oecd_cit': 3929, 'bls_jolts': 252390, 'historical_turnover': 2868,
    'bocconi_fiscal': 7672, 'imf_extension': 94524,
    'us_state_qcew': 25625, 'us_state_qcew_historical': 11700,
    'us_state_bea': 13056, 'us_state_tax': 118155,
    'bjk_state_rates': 22124, 'bjk_county_rates': 173789
}
NATIVE = {
    'worldbank_wdi_expanded': 210672, 'bls_qcew_us_national': 108408,
    'census_cbp_states': 1745501, 'us_treasury_mts': 3145,
    'census_bfs_monthly': 36883
}


def fixtures():
    catalog = [{'theme':'test','dataset':name,'priority':'P0' if i<71 else 'P1',
                'current_status':'NOT INGESTED'}
               for i,name in enumerate(list(EVIDENCE)+[f'Unmapped catalog item {i}' for i in range(125-len(EVIDENCE))])]
    baseline = {'source_run_id':'40439631125',
                'manifest_sha256':'c74fa28dabcf58c55afa513bd271a3b465eb121dad61f58c40c7250c2b4c2378',
                'source_family_count':24,'observations':2780117,'families':dict(FAMILIES)}
    quality = {'base_analytical_observations_unchanged':2780117,'base_source_families_unchanged':24,
               'source_snapshot_id':'canonical-native-20261028-40617910011',
               'queryable_source_native_rows_audited':2104609,'source_reports':{k:{'audited_rows':v} for k,v in NATIVE.items()},
               'ready_for_cross_provider_harmonization':False,'ready_for_causal_inference':False}
    panels = {'descriptive_panel_measurements':67842,'treasury_quarantined_measurements':7767,
              'cross_source_harmonized':False,'eligible_for_causal_inference':False}
    romer = {'private_quarterly_rows':756,'private_component_measurements':2268,
             'numeric_source_data_in_public_artifact':False,'causal_inference_ready':False}
    aligned = {'original_policy_rows':756,'joined_policy_outcome_rows':756,'outcome_measurements_present':2940,
               'outcome_missing_cells':{'PAYEMS':0,'UNRATE':36,'GDPC1':24,'GPDIC1':24},
               'causal_inference_ready':False,'numeric_source_values_in_public_report':False}
    return catalog,baseline,quality,panels,romer,aligned


class Atlas1Checks(unittest.TestCase):
    def test_aggregation_accounting_and_catalog_reconciliation(self):
        rows, catalog, report = reconcile(*fixtures())
        self.assertEqual(report['canonical_observations'],2780117)
        self.assertEqual(report['additional_native_records'],2104609)
        self.assertEqual(report['source_records_across_separate_layers_not_deduplicated'],4884726)
        self.assertEqual(report['catalog_entries'],125)
        self.assertEqual(report['catalog_entries_with_explicit_partial_evidence'],len(EVIDENCE))
        self.assertEqual(report['catalog_entries_without_manual_evidence_mapping'],125-len(EVIDENCE))
        self.assertEqual(len(rows),29)
        self.assertFalse(report['causal_research_ready'])
        self.assertTrue(all(x['full_dataset_scope_verified']=='false' for x in catalog))
        self.assertTrue(all(x['research_ready']=='false' for x in catalog))

    def test_bad_fingerprint_rejected(self):
        f=list(fixtures());f[1]['manifest_sha256']='wrong'
        with self.assertRaises(ValueError):reconcile(*f)

    def test_partial_native_or_baseline_rejected(self):
        f=list(fixtures());f[2]['source_reports']['census_cbp_states']['audited_rows']-=1
        with self.assertRaises(ValueError):reconcile(*f)
        f=list(fixtures());f[1]['families']['wdi']-=1
        with self.assertRaises(ValueError):reconcile(*f)

    def test_public_numeric_leak_flag_rejected(self):
        f=list(fixtures());f[4]['numeric_source_data_in_public_artifact']=True
        with self.assertRaises(ValueError):reconcile(*f)

    def test_causal_overclaim_rejected(self):
        f=list(fixtures());f[3]['eligible_for_causal_inference']=True
        with self.assertRaises(ValueError):reconcile(*f)

    def test_outcome_coverage_rejected(self):
        f=list(fixtures());f[5]['outcome_missing_cells']['UNRATE']=35
        with self.assertRaises(ValueError):reconcile(*f)

if __name__=='__main__':unittest.main()
