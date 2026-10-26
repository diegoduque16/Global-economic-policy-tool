from pathlib import Path
import csv,json,tempfile,unittest
from open_data.imf_ssa_2026 import audit,EXPECTED_INCONSISTENCIES

class IMFSSANarrativeTests(unittest.TestCase):
    def test_published_table_has_specific_known_conflicts(self):
        with tempfile.TemporaryDirectory() as t:
            r=audit(Path('research_data/imf_ssa_2026/table1_transcription.csv'),Path(t))
            self.assertEqual(r['transcribed_country_year_episode_rows'],72)
            self.assertEqual(r['source_stated_episode_count_introduction'],73)
            self.assertEqual(r['source_stated_episode_count_later_sections'],72)
            self.assertEqual(r['arithmetic_consistent_rows'],70)
            self.assertEqual({(a['iso3'],a['year']) for a in r['issues']},EXPECTED_INCONSISTENCIES)
            self.assertFalse(r['warehouse_inclusion_permitted'])
            self.assertFalse(r['causal_inference_ready'])
            self.assertEqual(r['by_country']['AGO'],0)
            self.assertEqual(r['by_country']['COD'],0)
            with open(Path(t)/'audited_episodes.csv',newline='',encoding='utf-8') as f:
                rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),72)
            self.assertEqual(sum(x['validation_status'].startswith('QUARANTINED') for x in rows),2)
    def test_input_mutation_fails_closed(self):
        original=Path('research_data/imf_ssa_2026/table1_transcription.csv').read_bytes()
        with tempfile.TemporaryDirectory() as t:
            file=Path(t)/'bad.csv';file.write_bytes(original.replace(b'KEN,2023',b'KEN,2022'))
            with self.assertRaises(ValueError):audit(file,Path(t)/'result')
    def test_full_coverage_and_no_duplicates(self):
        with tempfile.TemporaryDirectory() as t:
            report=audit(Path('research_data/imf_ssa_2026/table1_transcription.csv'),Path(t))
            self.assertEqual(report['coverage_countries_named_in_paper'],14)
            self.assertEqual(report['countries_with_episode_rows'],12)
            self.assertEqual(sum(report['by_country'].values()),72)

if __name__=='__main__':unittest.main()
