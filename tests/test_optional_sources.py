"""Source omission must be explicit and restricted to publisher HTTP 403."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from open_data.optional_sources import run_historical_turnover


class HistoricalSourceResilienceTest(unittest.TestCase):
    def test_success_includes_data_and_no_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = SimpleNamespace(returncode=0, stdout='validated', stderr='')
            self.assertTrue(run_historical_turnover('dummy.py', tmp, runner=lambda *a, **kw: result))
            self.assertFalse((Path(tmp)/'collection_warnings').exists())

    def test_only_http_403_can_skip_optional_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = SimpleNamespace(returncode=1, stdout='', stderr='urllib.error.HTTPError: HTTP Error 403: Forbidden')
            self.assertFalse(run_historical_turnover('dummy.py', tmp, runner=lambda *a, **kw: result))
            audit = json.loads((Path(tmp)/'collection_warnings'/'historical_turnover.json').read_text())
            self.assertEqual(audit['status'], 'SOURCE_UNAVAILABLE_HTTP_403')
            self.assertEqual(audit['observations_included'], 0)

    def test_404_or_nonnetwork_errors_are_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            for message in ['urllib.error.HTTPError: HTTP Error 404: Not Found',
                            'ValueError: Unexpected historical source schema']:
                result = SimpleNamespace(returncode=1, stdout='', stderr=message)
                with self.assertRaises(subprocess.CalledProcessError):
                    run_historical_turnover('dummy.py', tmp, runner=lambda *a, **kw: result)
            self.assertFalse((Path(tmp)/'collection_warnings').exists())


if __name__ == '__main__':
    unittest.main()
