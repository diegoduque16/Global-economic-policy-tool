# World Bank WDI 59-series recovery — November 14, 2026

## Failure and recovery

The 59-indicator World Bank API ingest run
[43630664421](https://github.com/diegoduque16/Global-economic-policy-tool/actions/runs/43630664421)
failed with **zero source observations**, after timeouts and widespread HTTP
502/503 errors from `api.worldbank.org`. The old, separately verified
32-series public edition remains unchanged.

The World Bank provides two official source deliveries:

1. Indicators API v2, retained for narrow smoke queries and compatibility
   (`python open_data/ingest.py --transport api`).
2. The official complete WDI CSV ZIP archive:
   `https://databank.worldbank.org/data/download/WDI_CSV.zip`,
   for the full all-country, all-59-series recovery
   (`python open_data/ingest.py --transport bulk --countries all --start 1960 --end 2026 --group all`).

The bulk ZIP is **not** an API response and must not be described or
validated as if it were one. It is the World Bank's official dataset release,
which may have a distinct source vintage, country/aggregate scope and
indicator-specific redistribution restrictions.

## New source validation

`open_data/wdi_bulk.py` streams the provider's original full WDI CSV ZIP
(500 MiB cap, retries, HTTPS, World Bank domains only). It preserves the
**exact original ZIP bytes** and SHA256, validates the archive structure and
official series metadata, and reads every selected, original numeric cell
directly from `WDIData.csv`. It does not fill in blanks, exclude aggregates,
turn missing into zero, alter provider units or hide duplicates. Each of
the configured 59 indicator codes must appear in the metadata AND contain
real finite numeric observations.

The resulting source-native `wdi_panel.csv` contains actual
country/geography-year-indicator keys and value, unit, retrieved timestamp,
and bulk source URL. It is only a **source-separated staging panel**.

The independent production validator in
`scripts/atlas_data_watchdog.py` dispatches by source transport. For bulk
downloads it **reopens and re-parses the retained original ZIP** and requires
every normalized key and value to match, with no missing, duplicated or
unexpected rows. It also checks the original ZIP SHA256, processed CSV SHA256,
manifest scope and prohibits untracked artifact files.

Both verified 32-series API-derived data and 59-series official bulk-derived
data are preserved as different source editions; no historical public release
is silently overwritten.

## Production pipeline

`.github/workflows/world-bank.yml` runs the official bulk source for
**full global** refreshes while preserving the original API smoke test.
Its existing validated-source GitHub artifact and private OneDrive watchdog
backup path are reused. A failure still blocks validation and publication.
`world-bank.yml` itself is already a full-run push trigger: merge to `main`
starts a new ingestion without creating a second scheduled collector.

A fully successful source build confirms the 59 official indicator codes,
actual observation count, original bulk source hash and source-native
geography count. It is **not** automatic publication on
worldeconatlas.com; release-specific licensing, country-versus-aggregate
classification and audit of the new source vintage must come first.

## Source documentation

- World Bank WDI official bulk data: https://datatopics.worldbank.org/world-development-indicators/
- WDI bulk CSV ZIP: https://databank.worldbank.org/data/download/WDI_CSV.zip
- World Bank World Development Indicators API: https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api
- World Bank data terms: https://www.worldbank.org/en/about/legal/terms-of-use-for-datasets

No third-party mirror or inferred economic data is used.

## Verified recovery outcome (14 November 2026)

The independently validated full source download
[run 43637384781](https://github.com/diegoduque16/Global-economic-policy-tool/actions/runs/43637384781)
**completed successfully** following the updated bulk layout handling
([PR #19](https://github.com/diegoduque16/Global-economic-policy-tool/pull/19)).

- **59** of 59 configured WDI indicator codes were present in the original
  World Bank series metadata and contributed actual numeric source values.
- **572,179** country/territory/aggregate–year–indicator numerical values
  were source-preserved and then independently matched to the original ZIP.
- **265** distinct source geography codes (including income and regional
  aggregates). Do not report them as 265 independent countries.
- **Zero** ingestion errors and **zero** key/value conflicts.
- Original official `WDI_CSV.zip` SHA256:
  `dba33648d6f453a635b076a62b91db5939f5e640c8dcfeabd094219210533100`.
- GitHub Actions artifact: `world-bank-wdi-9`, artifact ID
  `14129679689` (compressed artifact size 84,513,186 bytes).
- Download provenance is explicitly
  `WORLD_BANK_OFFICIAL_WDI_BULK_CSV`, **not** the API endpoint that
  failed earlier. The official source ZIP may have an older source
  release vintage than its retrieval date.

## Permanent original-source backup: independently verified November 14, 2026

The existing data coverage/backup watchdog was triggered to secure the
successful 59-series full source artifact before its GitHub Actions retention
expired. The first backup attempt [run 43642665591](https://github.com/diegoduque16/Global-economic-policy-tool/actions/runs/43642665591)
**correctly failed closed**: direct execution of
`scripts/atlas_data_watchdog.py` did not include the repository's sibling
`open_data/` validator on Python's import path. This was repaired with a
standalone isolated-process regression test in
[PR #26](https://github.com/diegoduque16/Global-economic-policy-tool/pull/26).

The successful reattempt
[run 43643193303](https://github.com/diegoduque16/Global-economic-policy-tool/actions/runs/43643193303)
proved:
- `wdi_source_backup` status
  `NEW_WDI_SOURCE_ARCHIVE_PRIVATE_BYTES_VERIFIED`.
- Exact full WDI source run ID **43637384781** and **572,179** numeric
  observations; no synthetic values added and no re-download from provider.
- Independent raw official WDI ZIP-to-panel numeric key/value verification
  **before** private archive creation.
- A permanent private OneDrive archive containing the original downloaded
  source ZIP, verified normalized panel and run report, with its restored
  archive bytes independently matching SHA-256
  `6f6253a1626bb0bbcf5c9e3e31c49e5f03736e7c15f1e8f73064f8b1666f8f9a`.
- Private watchdog control-report copies separately round-trip verified.
- GitHub workflow concluded **success**; no public raw data were published.

**Remaining gates, not backup:** source-series country/aggregate geography
classification, indicator-level licensing exceptions, official release
vintage review, source-native/canonical exact-key integration, and a separately
versioned public release. The public website still uses its independently
verified **32-series** source edition; the private 59-series archive is not
evidence that the website or canonical warehouse has been upgraded.
