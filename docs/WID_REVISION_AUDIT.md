# Atlas WID revision audit: read-only lineage and exact numeric keys

This extension uses the **existing source registry** and the **existing Atlas data watchdog**. It neither adds a scheduled workflow nor edits the original canonical warehouse, private WID database or ILOSTAT checkpoint.

## Evidence established from 3 November 2026

Source: [five successful WID expansion jobs](https://github.com/diegoduque16/Global-economic-policy-tool/actions/runs/41588514160). The small public-safe GitHub Actions artifacts provide per-country **official raw source file SHA256** and a row count. The SHA-verified original 23-country WID source manifest is `research/wid_23_source_manifest.json`.

- Twenty refreshed countries were already in the original 23. The absent-from-refresh original countries are Colombia, France and the United States.
- **All 20 raw source files have different SHA256 fingerprints** from their original sources. This demonstrates a byte-level change in source exports, **not** necessarily revised numeric values.
- The 20 originally retained 17,640 numeric source-native share rows; the refreshed exports retained **17,640** rows in total, with the same per-country counts. Equal counts do not imply equal observation keys or values.
- `research/wid_refresh_20261103_source_fingerprints.json` preserves only country-level source hashes, source-native counts, and the original five artifact IDs/checksums. It contains no protected numeric measurements.
- The existing registry builder compares these with the existing original source manifest; the P0 count remains 71, with no additional claims of warehouse integration or causal research readiness.

## When OneDrive service is functioning: optional *actual-value* comparison

Use **Actions → Atlas - Automatic Economic Data Coverage, Resumption and Backup Watchdog → Run workflow**, with **audit_wid_revisions = true** and **deep_verify = false**. This is the existing workflow; no new job scheduler is installed. The comparison is intentionally **manual-only**, and manual mode does **not** invoke the ILOSTAT auto-repair dispatch.

The manual WID-only run retrieves the original 23-country WID SQLite and its checksum/validation manifests, plus the 20 already archived `wid_source_native_shares.csv` files from SHA-addressed private OneDrive locations. It independently validates the original WID SQLite against its stored checksum and expected 21,430 values across 23 countries. **It does not read the ILOSTAT checkpoint, WID–WDI joined panel, or canonical warehouse**; those dependencies are bypassed to allow the WID comparison to proceed without the unrelated ILOSTAT checkpoint. For each refreshed country, `research/audit_wid_revisions.py` verifies local source identity, row count, parseability, finite numbers and unique keys `(WID area, year, percentile, income/wealth concept)`, then counts:

- matched observations with exactly equal SQLite REAL values;
- matched observations with different numerical values;
- new observation keys; and
- original keys missing from the refresh.

**No individual numeric values are written to GitHub artifacts.** The existing `source_coverage_audit.json` carries only aggregate counts, per-country status, source SHA evidence and a caution on interpretation. **The manual WID-only audit intentionally skips private OneDrive report write-back**: it publishes the metadata-only report as an existing watchdog GitHub Actions artifact, while the normal scheduled watchdog retains its private round-trip backup behavior. Temporary downloaded source rows are deleted by its existing cleanup step.

The changed raw source hashes, whether or not numerical differences are detected, are **not additional country coverage, causal effects or automatically new canonical observations**. Do not promote any observations until definitions, vintages, overlap and research utility are reviewed.

## Outage fail-closed behavior

November 13 ILOSTAT ingestion and daily watchdog failed while reading the private OneDrive checkpoint (the watchdog saw a reported **762,992-byte** object but a **585-byte** transfer). Do not overwrite, reset or restore the checkpoint based solely on these failed reads. The new WID value comparison is not automatically attempted during the outage. Missing, truncated or malformed inputs must cause an audit failure, not an invented zero-difference result.

## Reproduction / validation

The existing registry Actions CI runs three offline synthetic suites: `test_source_registry_reconciliation.py`, `test_wid_revision_audit.py`, and `test_atlas_data_watchdog.py`. These prove accounting and fail-closed behavior but do not substitute for a completed real private-data comparison.

## WID-only isolation correction, 13 November 2026

The original optional WID audit was inadvertently dependent on fetching the ILOSTAT checkpoint and WID–WDI join first. That was incompatible with OneDrive service instability. The existing watchdog now has an isolated `--wid-only-revisions` command path and the existing workflow uses it **only when manually dispatched with `audit_wid_revisions=true`**. Regular daily scheduled execution remains unchanged.

The WID-only run never auto-dispatches ILOSTAT ingestion or attempts to repair checkpoints. In addition, refreshed source variables are checked against the existing WID variable-to-concept validator before comparing values; classification mismatches fail closed. The only private remote access needed is reading the pinned original WID SQLite and 20 refreshed source-native CSVs. Because OneDrive remains unreliable, a successful synthetic test is not evidence that this real comparison has run.
