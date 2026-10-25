# Economic research warehouse — initial audited snapshot pipeline

## What this does

This pipeline runs all five existing full collectors in a single GitHub Actions job, checks that each collector completed without errors or conflicts, verifies that each processed CSV row count matches the source report, and produces a **source-separated, queryable SQLite database** and a compressed, versioned snapshot. The version uses the UTC date and GitHub run ID, with no overwriting. The processed source CSVs, collector reports, and original row JSONs are preserved for audit.

**Source families:** World Bank WDI, U.S. FRED + Eurostat macroeconomic, Maddison historical estimates, IMF + OECD, and ILOSTAT + BLS + Eurostat labor-market data.

## What this does not do

It does **not** deduplicate records across providers, harmonize units or statistical definitions, convert currencies, fix modern/historical boundaries, reconcile ILOSTAT classifications, identify fiscal/monetary policy shocks, estimate causal effects, or claim that the data cover all possible indicators. The observed row total is **not** a harmonized statistical sample. The source providers may revise data between snapshots. IMF provider projections may be present; their exact vintage and flags require additional audit.

## File layout

After the pipeline finishes, the GitHub artifact contains `economic-YYYYMMDD-RUN_ID.tar.gz`, with:

- `warehouse.sqlite`: SQLite database with `observations`, `source_files`, and `snapshots` tables. `observations.original_row_json` preserves every original CSV field; geographic and series dimensions retain their source meanings.
- `sources/<family>/processed/<filename>.csv`: verbatim collector-processed source files.
- `sources/<family>/run_report.json`: the full original collection report, including API sources and metadata.
- `manifest.json`: repository commit, per-source row counts, years, geographical code counts, checksums, and cautions.
- `sources/<family>/raw/`: only present when `include_raw` is selected and provider licensing has been reviewed.

The workflow **rejects** short smoke-test datasets accidentally substituted for full data by requiring reasonable minimum row counts per family. The builder removes incomplete snapshot directories rather than publishing incomplete archives.

## Running

1. Upload `warehouse/snapshot.py`, `tests/test_snapshot.py`, `docs/ECONOMIC_WAREHOUSE.md`, and `.github/workflows/economic-warehouse.yml` to the root repository paths shown here.
2. Navigate to **Actions → Build versioned economic research warehouse → Run workflow**.
3. Start with `publish_release = false` and `include_raw = false`. The first run downloads all full datasets and saves a 90-day GitHub workflow artifact for validation.
4. Inspect the `manifest.json`, database counts and data licenses. If the repository is public, a GitHub Release and its datasets will also be **public**.
5. After reviewing data redistribution permission, start another run with `publish_release = true`. It will publish a tagged, versioned GitHub Release with the tarball and manifest. A Release is not the same as independent backup: keep another copy, and note GitHub can remove assets or the project owner can delete them.

## Preview/query example

Unzip the archive and open the SQLite database with any SQLite viewer or Python:

```python
import sqlite3
connection = sqlite3.connect('economic-YYYYMMDD-RUN_ID/warehouse.sqlite')
print(connection.execute('SELECT family, COUNT(*) FROM observations GROUP BY family').fetchall())
print(connection.execute('SELECT country_or_polity_code, year, series, value, unit FROM observations WHERE family=? AND country_or_polity_code=? LIMIT 10', ('wdi','GHA')).fetchall())
```

## Operational limitations

- GitHub workflow artifacts expire after 90 days. Releases persist until removed but are **public on a public repository**. Public-source accessibility alone does not necessarily grant full redistribution rights; inspect World Bank/IMF/OECD/ILOSTAT/FRED/OWID permissions before posting bulk archives. Raw API payloads are not included by default.
- No central lock prevents source upstream revisions between downloads in the same run; each original collector's retrieval timestamp, URL and checksum are preserved where available.
- Full refresh on every run makes extra API calls. Sources have varying quota and reliability. The workflow fails closed if any one collector fails; it does not publish an incomplete release.
- Each workflow run produces a separate vintage rather than overwriting older data.
- The snapshot contains personally unidentifiable public macroeconomic measurements; it is not a private data repository. Avoid adding private datasets to the public-release workflow.
- Review the minimum observation thresholds when source coverage materially changes. Thresholds prevent accidental smoke/full confusion; they do not independently prove complete coverage.
- No recurring publishing schedule is enabled until a versioned release is validated and redistribution rights are reviewed.
