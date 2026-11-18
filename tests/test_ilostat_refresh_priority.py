"""No-network ordering tests for the existing guarded ILOSTAT bulk ingester."""
import unittest

from scripts.ilostat_refresh_priority import (
    PRIORITY_LABOUR_TABLES, prioritize_official_tables,
)


class ILOSTATRefreshPriorityTests(unittest.TestCase):
    def test_missing_official_files_first_then_genai_and_selected_labour(self):
        codes = {
            "EMP_ZZZ_NB_A": {}, "EMP_AAA_NB_A": {},
            "LFS_GAI_SEX_AGE_RT_A": {}, "SDG_0852_SEX_AGE_RT_A": {},
            "SDG_0831_SEX_ECO_RT_A": {}, "SDG_0851_SEX_OCU_NB_A": {},
            "EMP_MISSING_NB_A": {},
        }
        archived = {code: {"sha256": "a"*64} for code in codes
                    if code != "EMP_MISSING_NB_A"}
        got = prioritize_official_tables(codes, archived)
        self.assertEqual(got[0], "EMP_MISSING_NB_A")
        self.assertEqual(got[1], "LFS_GAI_SEX_AGE_RT_A")
        self.assertEqual(got[2:5], list(PRIORITY_LABOUR_TABLES))
        self.assertEqual(got[-2:], ["EMP_AAA_NB_A", "EMP_ZZZ_NB_A"])
        self.assertEqual(set(got), set(codes))

    def test_archived_missing_priority_does_not_override_absent_official_sources(self):
        ids = {s: {} for s in PRIORITY_LABOUR_TABLES}
        ids["EMP_MISSING_NB_A"] = {}
        got = prioritize_official_tables(ids, {s: {} for s in PRIORITY_LABOUR_TABLES})
        self.assertEqual(got[0], "EMP_MISSING_NB_A")

    def test_sort_stable_and_no_inferred_tables(self):
        ids = {"SDG_0852_SEX_AGE_RT_A": {}, "AAA_PLAIN_NB_A": {}}
        a = prioritize_official_tables(ids, {})
        b = prioritize_official_tables(dict(reversed(list(ids.items()))), {})
        self.assertEqual(a, b)
        self.assertEqual(set(a), set(ids))

    def test_untrusted_original_source_ids_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid official"):
            prioritize_official_tables({"../../private": {}}, {})
        with self.assertRaisesRegex(ValueError, "Invalid official"):
            prioritize_official_tables({12: {}}, {})


if __name__ == "__main__":
    unittest.main()
