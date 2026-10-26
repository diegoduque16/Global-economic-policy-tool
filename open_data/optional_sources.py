"""Execute the optional historical turnover collector without silently losing provenance.

Only a specifically recognized HTTP 403 from the publisher is treated as
an unavailable optional dataset. Other download, code or validation failures
remain fatal; this never manufactures or imputes historical observations.
"""
from __future__ import annotations
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

HISTORICAL_URL = 'https://www.labormarketupdate.net/items/data/historicallts.json'
FORBIDDEN_MARKER = 'urllib.error.HTTPError: HTTP Error 403: Forbidden'


def run_historical_turnover(collector_script, data_dir, *, runner=subprocess.run):
    """Return True if collected; False ONLY for publisher HTTP 403."""
    collector_script = Path(collector_script)
    data_dir = Path(data_dir)
    cmd = [sys.executable, str(collector_script)]
    result = runner(cmd, capture_output=True, text=True, check=False)
    if result.stdout:
        print(result.stdout, end='' if result.stdout.endswith('\n') else '\n')
    if result.stderr:
        print(result.stderr, file=sys.stderr, end='' if result.stderr.endswith('\n') else '\n')
    if result.returncode == 0:
        return True
    if FORBIDDEN_MARKER not in (result.stderr or ''):
        raise subprocess.CalledProcessError(result.returncode, cmd,
                                             output=result.stdout, stderr=result.stderr)
    warn_dir = data_dir / 'collection_warnings'
    warn_dir.mkdir(parents=True, exist_ok=True)
    warning = {
        'family': 'historical_turnover',
        'status': 'SOURCE_UNAVAILABLE_HTTP_403',
        'source_url': HISTORICAL_URL,
        'observations_included': 0,
        'retrieved_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'explanation': 'Publisher returned HTTP 403 during this snapshot. Optional historical series omitted; no values estimated, backfilled or marked zero.',
        'recovery': 'Future full builds automatically retry the original source.',
    }
    (warn_dir / 'historical_turnover.json').write_text(
        json.dumps(warning, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('Historical turnover source HTTP 403: separately recorded and omitted from this build.', file=sys.stderr)
    return False
