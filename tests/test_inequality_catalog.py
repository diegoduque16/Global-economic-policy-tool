"""Read-only catalog audit regression tests."""
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research.audit_inequality_catalog import audit


class CatalogAuditTest(unittest.TestCase):
    def test_duplicate_and_presence(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'catalog.csv'
            p.write_text('theme,dataset,current_status,official_source_url\n'
                         'Inequality,World Inequality Database WID,CATALOGUED,https://wid.world/data/\n'
                         'Inequality,World Inequality Database WID,CATALOGUED,https://wid.world/data/\n'
                         'Other,Historical V-Dem,NOT INGESTED,https://example.org/\n')
            d = audit(p)
            self.assertEqual(d['registry_entries'], 3)
            self.assertEqual(d['distinct_dataset_names'], 2)
            self.assertIn('world inequality database wid', d['duplicate_dataset_names'])
            self.assertEqual(d['inequality_focus']['WID'][0]['actual_numeric_observations'],
                             'not_verified_by_catalog')

if __name__ == '__main__': unittest.main()
