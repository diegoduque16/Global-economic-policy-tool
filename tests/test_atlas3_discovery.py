import csv
import json
import tempfile
import unittest
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec

SCRIPT = Path(__file__).resolve().parents[1] / 'research' / 'atlas3_discover_sdmx_dataflows.py'
spec = spec_from_file_location('atlas3', SCRIPT)
m = module_from_spec(spec)
spec.loader.exec_module(m)


def envelope(*nodes):
    return ('''<?xml version="1.0"?>
<mes:Structure xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
 xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"
 xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common">
 <mes:Structures><str:Dataflows>''' + ''.join(nodes) + '''</str:Dataflows></mes:Structures></mes:Structure>''').encode()


def dataflow(flow='DF_GDP', name='GDP', agency='IMF', version='1.0'):
    return f'''<str:Dataflow id="{flow}" agencyID="{agency}" version="{version}">
    <com:Name xml:lang="fr">French name</com:Name>
    <com:Name xml:lang="en">{name}</com:Name>
    <com:Description xml:lang="en">Economic structure</com:Description>
    </str:Dataflow>'''


class DiscoveryTests(unittest.TestCase):
    def test_dataflow_schema_and_english_name(self):
        r, d = m.parse_sdmx_dataflows(envelope(dataflow()), 'imf')
        self.assertEqual(d, 0)
        self.assertEqual(r[0]['dataflow_key'], 'IMF:DF_GDP:1.0')
        self.assertEqual(r[0]['title'], 'GDP')
        self.assertEqual(r[0]['description'], 'Economic structure')

    def test_bis_provider_metadata_is_distinct(self):
        r, _ = m.parse_sdmx_dataflows(envelope(dataflow(agency='BIS')), 'bis')
        self.assertEqual(r[0]['provider'], 'bis')

    def test_oecd_provider_metadata_is_distinct(self):
        r, _ = m.parse_sdmx_dataflows(envelope(dataflow(agency='OECD')), 'oecd')
        self.assertEqual(r[0]['provider'], 'oecd')

    def test_same_identity_version_may_repeat_without_double_count(self):
        rows, d = m.parse_sdmx_dataflows(envelope(dataflow(), dataflow()), 'imf')
        self.assertEqual((len(rows), d), (1, 1))

    def test_conflicting_duplicate_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            m.parse_sdmx_dataflows(envelope(dataflow(), dataflow(name='Other')), 'imf')

    def test_multiple_versions_preserved(self):
        rows, _ = m.parse_sdmx_dataflows(envelope(dataflow(), dataflow(version='2.0')), 'imf')
        self.assertEqual(len(rows), 2)

    def test_no_dataflows_rejected(self):
        with self.assertRaisesRegex(ValueError, 'No official SDMX'):
            m.parse_sdmx_dataflows(envelope(''), 'imf')

    def test_xml_external_entity_declaration_rejected(self):
        with self.assertRaisesRegex(ValueError, 'DTD or entity'):
            m.parse_sdmx_dataflows(b'<!DOCTYPE x [<!ENTITY ex SYSTEM "file:///etc/passwd">]><x/>', 'imf')

    def test_metadata_only_report_and_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'result'
            result = m.discover('imf', root, retrieve=lambda url: envelope(dataflow()))
            self.assertEqual(result['economic_observations_downloaded'], 0)
            self.assertIsNone(result['time_series_discovered'])
            self.assertFalse(result['research_ready'])
            with (root/'discovered_sdmx_dataflows.csv').open(newline='') as f:
                self.assertEqual(len(list(csv.DictReader(f))), 1)
            checks = json.loads((root/'sha256_manifest.json').read_text())
            for name, meta in checks.items():
                self.assertEqual(m.sha256((root/name).read_bytes()), meta['sha256'])
            with self.assertRaises(FileExistsError):
                m.discover('imf', root, retrieve=lambda url: envelope(dataflow()))

    def test_disallowed_provider_and_bad_response(self):
        with self.assertRaises(ValueError):
            m.parse_sdmx_dataflows(envelope(dataflow()), 'unknown')
        with self.assertRaises(ValueError):
            m.parse_sdmx_dataflows(b'not XML', 'imf')


if __name__ == '__main__':
    unittest.main()
