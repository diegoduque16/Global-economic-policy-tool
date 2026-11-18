"""Official ILO indicator-reference metadata may never replace original row units."""
import unittest

from atlas_web.ilostat_reference import (
    OFFICIAL_REFERENCE, SOURCE_REFERENCE_STAGE, TABLES,
    source_indicator_reference,
)


class ILOSTATPublicReferenceTests(unittest.TestCase):
    def test_all_three_sdg_table_mappings_are_exact_and_reference_only(self):
        self.assertEqual(set(TABLES), {
            "SDG_0831_SEX_ECO_RT_A",
            "SDG_0851_SEX_OCU_NB_A",
            "SDG_0852_SEX_AGE_RT_A",
        })
        for table in TABLES:
            item = source_indicator_reference(table + "_0123456789abcdef")
            self.assertEqual(item["table_id"], table)
            self.assertEqual(item["reference_stage"], SOURCE_REFERENCE_STAGE)
            self.assertEqual(item["reference_url"], OFFICIAL_REFERENCE)
            self.assertFalse(item["source_row_unit_verified"])
            self.assertFalse(item["cross_country_comparison_approved"])
            self.assertEqual(item["period"], "annual")

    def test_official_units_differ_and_earnings_currencies_unresolved(self):
        informal = source_indicator_reference("SDG_0831_SEX_ECO_RT_A_"+"a"*16)
        earnings = source_indicator_reference("SDG_0851_SEX_OCU_NB_A_"+"b"*16)
        unemployment = source_indicator_reference("SDG_0852_SEX_AGE_RT_A_"+"c"*16)
        self.assertEqual(informal["reported_measurement"], "Percentage (%)")
        self.assertEqual(unemployment["reported_measurement"], "Percentage (%)")
        self.assertEqual(earnings["reported_measurement"], "Local currency per hour")
        self.assertIn("Country-specific currency", earnings["caution"])
        self.assertNotIn("USD", earnings["reported_measurement"])

    def test_reject_unsupported_sources_bare_codes_and_invalid_series_digest(self):
        for code in (
            "SDG_0851_SEX_OCU_NB_A",
            "SDG_0851_SEX_OCU_NB_A_123",
            "SDG_0851_SEX_OCU_NB_A_" + "x"*16,
            "SDG_0851_SEX_OCU_NB_A_" + "a"*17,
            "SDG_0852_SEX_DIS_RT_A_" + "a"*16,
            "ILOSTAT_FAKE",
            "SDG_0852_SEX_AGE_RT_A_" + "0"*16 + "_OTHER",
        ):
            self.assertIsNone(source_indicator_reference(code))
        self.assertIsNone(source_indicator_reference(None))


if __name__ == "__main__":
    unittest.main()
