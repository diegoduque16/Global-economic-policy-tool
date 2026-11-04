"""Offline, synthetic-only tests. No OneDrive or GitHub network calls."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import ilostat_checkpoint_guard as guard


def sample(ids):
    return {'schema_version': 1, 'downloaded': {
        i: {'sha256': 'a' * 64, 'state': 'NATIVE_NOT_HARMONIZED', 'records_validated': 10}
        for i in ids}}


class CheckpointGuardTests(unittest.TestCase):
    def test_addition_increments_distinct_files(self):
        result = guard.compare_states(sample(['EMP_A']), sample(['EMP_A', 'EMP_B']))
        self.assertEqual(result['new_distinct_tables'], 1)
        self.assertEqual(result['current_verified'], 2)

    def test_repeated_download_is_not_new_count(self):
        result = guard.compare_states(sample(['EMP_A']), sample(['EMP_A']))
        self.assertEqual(result['new_distinct_tables'], 0)

    def test_revalidating_same_id_is_permitted(self):
        a = sample(['EMP_A'])
        b = sample(['EMP_A'])
        b['downloaded']['EMP_A']['sha256'] = 'b' * 64
        result = guard.compare_states(a, b)
        self.assertEqual(result['refreshed_table_files'], 1)
        self.assertEqual(result['new_distinct_tables'], 0)

    def test_dropped_ids_hard_fail(self):
        with self.assertRaisesRegex(ValueError, 'REGRESSION'):
            guard.compare_states(sample(['EMP_A', 'EMP_B']), sample(['EMP_B', 'EMP_C']))

    def test_invalid_source_counts_hard_fail(self):
        source = sample(['EMP_A'])
        source['downloaded']['EMP_A']['records_validated'] = 0
        with self.assertRaisesRegex(ValueError, 'verified CSV rows'):
            guard.parse_checkpoint(json.dumps(source).encode())

    def test_invalid_source_hash_hard_fail(self):
        source = sample(['EMP_A'])
        source['downloaded']['EMP_A']['sha256'] = 'not a sha'
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            guard.parse_checkpoint(json.dumps(source).encode())

    def test_invalid_schema_hard_fail(self):
        with self.assertRaisesRegex(ValueError, 'schema'):
            guard.parse_checkpoint(b'{"schema_version":99,"downloaded":{}}')

    def test_private_history_has_unset_baseline(self):
        result = guard.assert_current_against_history(sample(['EMP_A']), None)
        self.assertEqual(result['audit_baseline'], 'not_yet_established')

    def test_zero_files_allowed_only_as_initial_checkpoint(self):
        result = guard.parse_checkpoint(json.dumps(sample([])).encode())
        self.assertEqual(len(result['downloaded']), 0)


if __name__ == '__main__':
    unittest.main()
