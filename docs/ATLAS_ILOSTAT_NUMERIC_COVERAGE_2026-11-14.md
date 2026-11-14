# ILOSTAT native numeric coverage and source inventory — 14 November 2026

## Why this change

Atlas already has a scheduled **resumable ILOSTAT bulk source collector** that
SHA-verifies source CSV.gz files in **private OneDrive**. The latest certified
source-table count before this update was **920 of 1,964** official source
tables (GitHub run 43629083582). A table count, or the total lines in those
tables, is **not** a count of independent economic observations.

Previously the collector verified the CSV/gzip archive and row counts, but did
not measure how many actual finite values were present in the source-native
\`obs_value\` column versus blank/missing/nonfinite or other source markers.

## What the existing workflow now records

The collector reuses the same downloaded source file and one streaming CSV/gzip
read to check original row counts **and** profile values. For newly captured
source files it records, in the **existing private checkpoint**:

- \`numeric_obs_value_rows\`: count of finite numerical values in native \`obs_value\`.
- \`blank_obs_value_rows\`: count of empty native \`obs_value\` fields (not zeros).
- \`other_nonnumeric_or_nonfinite_rows\`: count of literal nonnumeric values,
  strings including \`NaN\`, and infinities (not silently converted or imputed).
- \`obs_status_nonempty_rows\`: count of native observations with nonblank flags.
- \`source_ref_area_distinct_codes\`: number of distinct **source-native**
  reference area codes; NOT a sovereign country count.
- \`first_annual_time_if_present\`, \`last_annual_time_if_present\`: range of
  exact four-digit \`time\` fields; this does not reclassify quarterly/monthly data.
- Raw CSV header, source SHA256 and a strict \`NATIVE_NOT_HARMONIZED\` status.

These counts must sum to the file's original physical row count and the
official ILOSTAT TOC row count when provided. No imputed numerical values are
added. No original source file, private canonical warehouse, or WID panel is
rewritten.

The same pre-existing GitHub Actions metadata report now additionally contains:

- \`source_native_table_inventory.csv\`: one row per verified source table ID,
  original file SHA256, official TOC frequency/vintage, physical CSV rows,
  current-vintage check, per-table numeric profile when available, and explicit
  research restrictions.
- \`source_native_numeric_coverage.json\`: full table file count and **only
  the observed numeric/blank/nonnumeric counts from profiled tables**;
  unprofiled historical tables remain \`UNKNOWN_NOT_ZERO\`.
- These outputs are included in the existing run's \`sha256_manifest.json\`.
  The source checkpoint's SHA256 binds the inventory to the private input
  state that the original immutable checkpoint guard already archives.

This is **not** another downloader, a new scheduled job, or a new warehouse.
The new files are emitted by the existing scheduled workflow
\`.github/workflows/atlas-ilostat-bulk-resumable-ingestion.yml\`.

## Historical coverage is intentionally not fabricated

The existing ~920 historical ILOSTAT tables do not automatically acquire
validated numeric profiles because the metadata-only checkpoint cannot reveal
the underlying value types. They are listed as
\`NOT_PROFILED_HISTORICAL_SOURCE\` until an independent SHA-checked read of
each archived gzip file is performed. As the existing collector advances, newly
verified/retrieved tables will receive numeric profiles. **Never present the
profiled-table sum as all ILOSTAT numeric values.**

Even a table with 100% finite numeric \`obs_value\` has not been certified as a
distinct, comparable series, because different ILOSTAT age, sex, industry,
occupation, source, unit, frequency, geography and source-vintage dimensions
can overlap. The original flags, classification and standard errors must
remain available for subsequent exact source-native key validation.
Redistribution rights require separate review before source numeric data
can be placed on the public website.

## Validation and privacy

New offline synthetic tests exercise the CRC/CSV profiler with zero values,
blank values, invalid/nonfinite tokens, non-annual time, duplicate/missing
required headers, wrong source row counts, inventory profile mismatches,
uncertainty for historical unprofiled files, and explicitly false
canonical/harmonization claims.

The public metadata report contains **no private OneDrive credentials, private
remote paths, or source-native value rows**. It only contains aggregate counts
and provider-defined table IDs, retrieval dates and SHA256.

## Next data gates

1. Confirm the updated ILOSTAT collector's next completed run and inspect its
   source inventory. Do not claim numeric counts until that real run passes.
2. For older collected tables, backfill profiles through verified reads of
   pinned private gzip files **without re-downloading provider datasets**.
   Rate limit transfers and preserve the existing immutable checkpoint history.
3. Select high-value employment, earnings, occupations/hours and AI exposure
   tables and map their **complete original dimensions** and numeric flags.
4. After exact native observation-key and source definition audits, produce a
   separately versioned private ILOSTAT research panel; do not merge it with
   overlapping WDI or canonical labor sources without documented deduplication.
