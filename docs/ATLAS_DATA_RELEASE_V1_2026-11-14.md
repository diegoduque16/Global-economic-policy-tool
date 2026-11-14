# Atlas source-evidence / research release v1 — 14 November 2026

## Scope and decision

Use the **existing** Atlas source registry, verified WDI public research exports,
OneDrive watchdog backup and canonical warehouse. Do not build another warehouse,
registry, downloader, or scheduler.

This change audits the real 32-series public WDI data already committed to the
repository and makes their series-level evidence visible in the current source
registry. It also protects successful full World Bank refreshes triggered by
changes to the catalog/workflow, and avoids repeated requests during an upstream
World Bank outage.

## What is actually verified

| Layer | Known evidence | Allowed interpretation |
| --- | --- | --- |
| Public WDI edition | World Bank run `42581151428`, 300,240 original-source-verified numeric observations, 32 series, 217 country/territory codes, 44 source-defined aggregate codes | Published source edition; country-year research exports generated and tested, **not** cross-provider causal research |
| Public series registry audit | `scripts/audit_public_wdi_series.py` checks **all checked-in public rows**, indicator identities, non-finite/duplicate keys, geography labels, per-series counts, years, SHA256 | 32 **public-edition numeric series** audited; original API raw pages not independently fetched again and canonical inclusion not established |
| Historical canonical SQLite | 2,780,117 rows / 24 families in SHA-pinned historical snapshot; private | Baseline only. Do not sum with WDI edition or ILOSTAT raw files |
| Latest ILOSTAT bulk | 14 November successful run `43629083582` verified 920 of 1,964 source-native tables, 20 new unique tables, no table lost | Raw source table files, **not** numeric warehouse rows or verified cross-provider comparability |
| World Bank expansion | 59 curated indicator codes (27 additions). Run `43630664421` **FAILED** with zero downloaded rows due to broad World Bank API 502/503 and read-timeout errors | Do **not** represent as newly acquired numbers; protect the 32-series release |
| WID | 21,430 historical numeric records, 23 countries; refreshed 20 countries' files need exact observation revision audit | Separate source, not certified current warehouse inclusion or increased geographic coverage |
| Curated registry | 125 catalog entries; 71 P0; 23 partial historical family mappings + 1 separate WID mapping + 47 without explicit evidence mapping | Discovery / planning only, not 125 ingested datasets |

## Release files and the exact source gate

The existing public Pages build generates under `/research/`:
- `wdi_country_year_long.csv` — observed country/territory source values only.
- `wdi_country_year_wide.csv` — country-year values with blank missing cells.
- `wdi_aggregate_year_long.csv` — **separate** regional/income aggregates.
- `wdi_variable_dictionary.csv` and country/indicator coverage CSVs.
- `research_manifest.json` — immutable input/output digests and release constraints.

The existing registry builds:
- `public_wdi_series_evidence.csv` — exactly 32 series, actual checked-in numeric
  key counts, source dates, raw JSON file SHA256 and geography counts.
- `priority_p0_evidence.csv`, `registry.json`, `registry_summary.json` —
  original source catalog claims and auditable stage separation.
  
**Critical distinction:** The existing registry's exact **raw-API-to-canonical**
key audits remain at zero. The added 32 public-file key audits are **not**
independent raw-source downloads, cross-source deduplication, complete WDI
coverage, or certified warehouse integration.

## Data-preservation fix

`scripts/atlas_data_watchdog.py` previously selected only successful
`schedule` WDI runs on `main`. Approved catalog expansion also triggers
a **full** collector run via `push` on `main`. The selector now considers
successful `schedule` and `push` mainline runs, excluding manual
`workflow_dispatch` smoke tests, branches, in-progress and failed runs.
Regardless of run event, the backup still independently validates the
original raw page SHA256 values, the normalized numerical panel and full
country/time scope **before** placing an archive in private OneDrive.
A failed collector (including run `43630664421`) is not backed up or published
as valid data. The existing daily watchdog owns this mechanism.

## World Bank API outage controls

The 14 November global 59-series retrieval failed at the provider:
repeated HTTP 502/503 and read timeouts, leading to zero source values. In
`open_data/ingest.py` three consecutive transient all-country failures
now trigger an explicit `upstream_outage_abort` flag and mark remaining
indicators as `NOT_ATTEMPTED_UPSTREAM_SERVICE_UNAVAILABLE`, rather than
spending an entire run retrying the same unavailable provider. The run is
incomplete, its failure report is retained, and it cannot be released.
Non-transient invalid-indicator errors still remain fully reported.

## Next promotion gates

1. Re-run the **existing** World Bank collector after provider recovery;
   check all 59 indicator identities and original numeric raw-page checksums.
2. Persist and round-trip verify a new successful full source artifact in
   the pre-existing private OneDrive archive using the watchdog, recording
   the exact source run, run number and SHA256.
3. Confirm source scope, licensed redistribution rights, provider-specific
   definitions, coverage and country-versus-aggregate classes. Build a
   **separately versioned** 59-series release, without overwriting the pinned
   32-series public version or claiming a fresh canonical snapshot.
4. Continue existing ILOSTAT collection, but reconcile individual table IDs,
   value flags, geography, sector/age/sex classifications and duplicate
   source-native observation keys before including any numbers in the
   research dataset.
5. Complete the WID 20-country refresh by numeric observation-key/value
   comparison, not by hashes or source-row counts alone.
6. Resolve the 47 P0 sources without explicit evidence mappings through
   actual workflow/artifact/OneDrive inventory, starting with fiscal and
   labor indicators needed for a stated research design. Unknown is not absent.
7. Audit the seven artifact-only scheduled source workflows against existing
   long-term OneDrive snapshot references before adding backup functionality.
8. Produce an independently versioned canonical/analytical snapshot only
   after definition, unit, missingness, overlap, and key-level evidence passes.
   Preserve the old canonical archive and rollback path.

No causal study or autonomous policy finding should be advertised until policy
treatment dates, source numeric magnitudes and counterfactual assumptions
have their own independent validation.
