"""Ensure monthly WID expansion truly extends the verified 23-country reference."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "research" / "wid_23_source_manifest.json"
WORKFLOW = ROOT / ".github/workflows/atlas-wid-expand-20-countries.yml"

class WidExpansionScopeTest(unittest.TestCase):
    def test_new_targets_exclude_all_original_23_countries(self):
        baseline = json.loads(BASE.read_text())
        prior = [item["wid_area"] for item in baseline["countries"]]
        self.assertEqual(len(prior), 23)
        self.assertEqual(len(set(prior)), 23)
        workflow = WORKFLOW.read_text()
        groups = re.findall(r"(?m)^          - batch: ([a-z-]+)\n            countries: ([A-Z,]+)$", workflow)
        self.assertEqual(len(groups), 5)
        codes = [code for _, names in groups for code in names.split(",")]
        self.assertEqual(len(codes), 20)
        self.assertEqual(len(set(codes)), len(codes))
        self.assertFalse(set(codes).intersection(prior))
        self.assertTrue(all(re.fullmatch(r"[A-Z]{2}", code) for code in codes))
        self.assertTrue(all(len(names.split(",")) == 4 for _, names in groups))
        self.assertIn("original 23-country numeric source manifest", workflow)
        self.assertIn("Refusing to count historical WID countries as geographic expansion", workflow)

    def test_only_main_triggers_private_source_collection_on_push(self):
        workflow = WORKFLOW.read_text()
        self.assertIn("  push:\n    branches: [main]\n", workflow)
        self.assertIn("  schedule:\n    - cron: '23 5 3 * *'", workflow)
        self.assertIn("RCLONE_CONFIG_B64", workflow)
        self.assertIn("onedrive_verified", workflow)
        self.assertIn("retained_source_shares", workflow)

if __name__ == "__main__":
    unittest.main()
