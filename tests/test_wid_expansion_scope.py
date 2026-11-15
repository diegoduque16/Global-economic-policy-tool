"""Ensure monthly WID expansion truly extends the verified 23-country reference."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "research" / "wid_23_source_manifest.json"
WORKFLOW = ROOT / ".github/workflows/atlas-wid-expand-20-countries.yml"
PREVIOUS = ROOT / "research/wid_expansion_20261114_completed_scope.json"
WAVE2 = ROOT / "research/wid_expansion_20261114_wave2_completed_scope.json"

class WidExpansionScopeTest(unittest.TestCase):
    def test_new_targets_exclude_all_original_23_countries(self):
        baseline = json.loads(BASE.read_text())
        prior = [item["wid_area"] for item in baseline["countries"]]
        self.assertEqual(len(prior), 23)
        self.assertEqual(len(set(prior)), 23)
        workflow = WORKFLOW.read_text()
        groups = re.findall(r"(?m)^          - batch: ([a-z0-9-]+)\n            countries: ([A-Z,]+)$", workflow)
        self.assertEqual(len(groups), 5)
        codes = [code for _, names in groups for code in names.split(",")]
        self.assertEqual(len(codes), 20)
        self.assertEqual(len(set(codes)), len(codes))
        self.assertFalse(set(codes).intersection(prior))
        completed=json.loads(PREVIOUS.read_text())
        self.assertEqual(completed['source_run_id'],43640485476)
        self.assertEqual(completed['new_country_count'],20)
        saved={code:count for batch in completed['new_country_batches']
               for code,count in batch['countries'].items()}
        self.assertEqual(len(saved),20)
        self.assertEqual(sum(saved.values()),14472)
        self.assertEqual(set(saved).intersection(prior),set())
        self.assertEqual(set(codes).intersection(saved),set())
        wave2 = json.loads(WAVE2.read_text())
        self.assertEqual(wave2['source_run_id'],43643338607)
        self.assertEqual(wave2['source_run_conclusion'],'success')
        self.assertEqual(wave2['new_country_count'],20)
        self.assertTrue(wave2['private_onedrive_sha_verified_for_each_country'])
        saved2={code:count for batch in wave2['new_country_batches']
                for code,count in batch['countries'].items()}
        self.assertEqual(len(saved2),20)
        self.assertEqual(sum(saved2.values()),13532)
        self.assertEqual(set(saved2)&(set(prior)|set(saved)),set())
        self.assertEqual(set(codes)&set(saved2),set())
        self.assertEqual(len(set(prior)|set(saved)|set(saved2)),63)
        self.assertEqual(len(set(prior)|set(saved)|set(saved2)|set(codes)),83)
        self.assertTrue(all(re.fullmatch(r"[A-Z]{2}", code) for code in codes))
        self.assertTrue(all(len(names.split(",")) == 4 for _, names in groups))
        self.assertIn("research/wid_23_source_manifest.json", workflow)
        self.assertIn("exclude all 63 previously verified geographic codes", workflow)
        self.assertIn("Refusing to count any of the original or prior 40 verified WID countries as expansion",workflow)
        self.assertIn("research/wid_expansion_20261114_completed_scope.json",workflow)
        self.assertIn("research/wid_expansion_20261114_wave2_completed_scope.json",workflow)

    def test_only_main_triggers_private_source_collection_on_push(self):
        workflow = WORKFLOW.read_text()
        self.assertIn("  push:\n    branches: [main]\n", workflow)
        self.assertIn("  schedule:\n    - cron: '23 5 3 * *'", workflow)
        self.assertIn("RCLONE_CONFIG_B64", workflow)
        self.assertIn("onedrive_verified", workflow)
        self.assertIn("retained_source_shares", workflow)

if __name__ == "__main__":
    unittest.main()
