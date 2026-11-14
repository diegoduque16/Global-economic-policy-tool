# Atlas consolidation decision — 12 November 2026

Status: **review-only; no production workflow, observation, or OneDrive mutation**  
Scope: Current `main` repository tree, workflow definitions, prior certified run logs, the existing source registry and the existing daily evidence watchdog. The live private OneDrive tree and all original record-level datasets were **not** independently enumerated or compared in this audit.

## Decision

**Do not build an additional ILOSTAT warehouse, source registry, broad collector, or dashboard yet.** Reconcile the existing data paths and then prove one end-to-end, research-relevant pipeline using the already verified datasets. Additional downloads should be tied to missing variables for a specified research design.

### Source-of-truth components to retain

| Role | Existing implementation | Reality / constraint |
|---|---|---|
| Curated source universe | `open_data/expanded_source_catalog.csv` (125 entries; 71 P0) | Planning labels are **not** verified data-availability labels. |
| Generated catalog registry | `scripts/build_source_registry.py`; `data/registry/registry.json` | Records catalog facts and initializes validation fields as unverified. |
| Registry scheduling | `source-registry.yml` and `persist-source-registry.yml` | Both rebuild the same source registry weekly; the second also writes its snapshot to Git. Avoid a third job. |
| Independent data audit | `scripts/atlas_data_watchdog.py`; `atlas-data-pipeline-watchdog.yml` | Daily real-data and checksum checks for select layers, not complete source-by-source lineage across 125 entries. |
| Verified warehouse | `warehouse/snapshot.py`, `warehouse/build_canonical.py`, `warehouse/native_materialize.py` and their workflows | Original pinned canonical version and additional native-source materialization are intentionally distinct. |
| Existing comparison and research | `research/build_descriptive_panels.py`, `research/audit_identification_readiness.py`, `docs/RESEARCH_PROTOCOL.md` | Source-specific descriptive results and an identification audit; no validated causal treatments demonstrated. |
| ILOSTAT resumable raw ingestion | `atlas-ilostat-bulk-resumable-ingestion.yml`, `scripts/ilostat_checkpoint_guard.py` | Continue verified source storage while mapping value-level research utility; new retry fix is on `main`. |

### Quantities from certified or independently logged runs — NOT additive

| Source and stage | Latest audited evidence used here | Reconciliation classification | Next check |
|---|---|---|---|
| Canonical SQLite, 24 families | 2,780,117 observations; certified historical snapshot, run [40617910011](https://github.com/diegoduque16/Global-economic-policy-tool/actions/runs/40617910011) | Historical certified baseline; **not** a continuously merged live snapshot | Hash and query current chosen production snapshot before advertising freshness |
| Five indexed source-native collections | 2,104,609 source-native records, materialized in the historical integrated snapshot | Stored separately; **not** new canonical observations | Source codes and series-level identity against canonical |
| World Bank WDI selected | 300,240 observations refreshed [9 Nov](https://github.com/diegoduque16/Global-economic-policy-tool/actions/runs/42581151428) | Successful collector; canonical refresh **not proved** | Compare collected artifact SHA and version to pinned canonical; identify 32 series |
| WDI expanded native | 210,672 records in native materialization | Potential same-provider overlaps; **not established duplicates** | Compare source series, year, geo, unit and release |
| Canonical mixed labor family | 123,233 source observations (ILOSTAT + BLS + Eurostat) | Has existing ILOSTAT content | Build exact provider and indicator keys before using new ILOSTAT bulk |
| ILOSTAT bulk archive | 840 of 1,964 verified source files from successful [12 Nov morning](https://github.com/diegoduque16/Global-economic-policy-tool/actions/runs/43207368259), with loss count 0 | Physically verified raw files; not harmonized; number of unique numerical cells **not established** | Compare ID/measurements and latest immutable checkpoint; do not add file counts to observation counts |
| WID distribution data | 21,430 values; 23 countries; 4,408 pre/post pairs (historical audited dataset) | Separate numeric data; potential overlap with recurring country-source refresh | Check country/indicator/year/source-vintage before counting new observations |
| WID–WDI exploratory join | 1,322 country-years and 13,969 numeric cells from [31 Oct repaired run](https://github.com/diegoduque16/Global-economic-policy-tool/actions/runs/41170503770) | Derived observations; not independent raw coverage or causal inference | Compare join vintage to latest independent WDI/WID snapshots |
| Eurostat enterprise AI | 139 collected observations in [9 Nov run](https://github.com/diegoduque16/Global-economic-policy-tool/actions/runs/42598919606) | Selected adoption measure; not global occupation-level causal panel | Inspect aggregate codes, flags and vintage |
| Descriptive research candidates | 67,842 retained in [Phase 5](https://github.com/diegoduque16/Global-economic-policy-tool/actions/runs/40623397720) | Derived source-specific descriptive panels | Verify retained series provide the first pilot's necessary outcomes |
| Fiscal policy events | Historical Romer alignment: 756 quarter rows / 2,940 outcome cells; Phase 6 readiness audit certifies **zero** independently validated treatments | Exploratory; not causal-ready | Validate actual policy shock values, dates, motivation, controls and research design |

**Caution:** In a prior 4 Nov audit, multiple ILOSTAT jobs reported unchanged cumulative counts while ingesting distinct IDs. The November 12 guard recorded 840 with zero prior IDs lost; any new reconciliation must work from actual ID sets and immutable snapshot hashes, not job success flags alone. The November 12 evening run failed during pre-ingestion JSON parsing; the guarded retry fix and 14 synthetic tests were subsequently merged and passed on `main`. A new live ingestion test is still required.

## What must be reconciled, not rebuilt

Implement the next change **inside the existing source-registry/watchdog ecosystem**.

1. Reconcile each existing catalog source ID with a provider-native series identity and already stored assets. A *catalog source* is not a *source family*, *bulk file*, *individual numerical observation*, or *research panel*; keep separate IDs and grain.
2. Distinguish fields: `catalog_reported_status`, `observed_evidence_stage`, `last_verified_snapshot`, `last_collector_success`, `last_warehouse_inclusion`, `last_research_panel_inclusion`, `overlap_review`, `data_vintage`, `verification_time`, `private_artifact_reference`, and `evidence_citation`.
3. Do not infer `INGESTED` from provider-name similarity or `NOT_INGESTED` from the static 2026-10 catalog. Unknown is a valid status. Reuse the watchdog's existing `source_coverage_audit.json` and `source_numeric_coverage_ledger.csv`, rather than emitting a competing ledger.
4. Express possible overlaps as `POTENTIAL_OVERLAP_NEEDS_KEY_AUDIT`; prove duplicates only using consistent provider, observation key, geography, period, unit, adjustment, source series and vintage. Preserve genuinely different revisions as separately attributable source versions.
5. Require reconciled links from collector artifact -> raw/private SHA -> validated source-native representation -> versioned canonical publication -> downstream research panel, without changing the historical canonical archive.
6. Report `last_collected` and `last_integrated` separately. A green collector or watchdog run is **not** proof of current warehouse or research-panel inclusion.

### Scope and non-goals for the next change

- First pass: 71 P0 curated sources, with an evidence-level breakdown; prioritize WDI, WID, ILOSTAT, tax/fiscal policy shocks, wages/jobs and AI exposure. No assertion that 71 have full series or that 104 catalog labels correspond to truly absent data.
- Produce an explicit **gap report**: already-usable research measures, verified but not yet mapped, potential overlap, genuinely missing, and research-design blocked.
- No new collector endpoints, no raw-data uploads to the public repository, no deletions, no source-native numeric row concatenation, no changes to existing scheduled workflows during reconciliation.
- Retain research and warehouse historical run IDs until replacement workflow lineage has independent tests and documented rollback.

## First pilot, before expansion

Primary candidate: **independently documented U.S. narrative tax shocks -> employment, real wages and investment**. The existing research protocol and Romer evidence offer a starting point, but a valid pilot requires verifying source numerical policy shocks and their date conventions, outcome units/frequency and releases, anticipation, identification assumptions, effective sample, pretrends/placebos and confidence intervals. Do **not** label this as causal-ready until independent verification succeeds.

Stop and produce a missing-evidence report if no valid shock treatment series exists. Do not fill gaps with modeled or fabricated events; do not interpret WID pretax/posttax share differences as tax-policy effects. If this pilot fails its identification requirements, choose a descriptive question and label it correctly rather than claiming causal attribution.

### Acceptance criteria

1. One consistent source-to-asset crosswalk for prioritized existing sources, with every status supported by source-specific evidence or explicitly unknown.
2. Existing registry and watchdog remain the only scheduled evidence surfaces; no competing registry/collector/warehouse job.
3. Zero unexplained reductions in certified source counts; overlapping layers are never summed.
4. A reproducible pilot manifest identifies source and vintage, valid analytic sample, assumptions, exclusions and validation status.
5. New canonical or analytical versions can be reconstructed from exact authenticated source snapshots; immutable baseline survives unchanged.
6. Report provenance and numerical coverage by variable, geography, period and treatment, not headline aggregate row counts.

## Duplicate/legacy workflow triage (no changes in this review)

Repository tree on 12 Nov: **112 workflow YAML files**. GitHub history: **350 runs**, **212 successful**, **136 failed**, one cancelled and one skipped. One obsolete BEA installer was responsible for **92 failed runs**; do not classify this as the current canonical BEA system being entirely broken.

Potential overlapping families: original/resilient/canonical warehouse build and native materialization; repeated BEA, ITEP, Bocconi, QCEW, WID installer versions; two weekly source-registry builders. Before disabling any workflow, map each to its external side effects, last successful run, artifacts, replacement, and downstream dependencies. Preserve reproducibility and rollback.

## Decision sequence

**First:** reconcile existing evidence and map overlaps (read-only).  
**Second:** prove the complete research pilot with existing observations and policy evidence, or produce an explicit blocker report.  
**Third:** integrate only genuinely missing, validated series into the existing native/warehouse path.  
**Fourth:** simplify legacy triggers once replacements and data consumers are proven.  
**Later:** extend autonomous research and any user interface on top of defensible, provenance-linked results.

This document is an evidence-based implementation decision, **not a claim that the full source inventory or an independently replicated scientific result is complete**.

## P0 reconciliation implementation, 12 November 2026

Rather than creating a new registry or monitoring job, the existing `scripts/build_source_registry.py` now reuses the **24 explicit partial catalog-to-family mappings** from `research/atlas1_reconcile_inventory.py`. The Atlas 1 map is historical, source-family-level evidence—not current series-level provenance or evidence that an entire named catalog dataset was downloaded. The builder also attaches independently identified **separate WID numeric** and **ILOSTAT raw bulk** run references without calling either canonical or duplicate-free.

The existing registry produces `priority_p0_evidence.csv` with 71 rows, in the **same existing artifact**. The existing `persist-source-registry.yml` is updated to persist the CSV when the catalog, builder schema, or crosswalk fingerprint changes; no third registry, collector, or scheduled workflow was added. Regression tests run under the existing source-registry and persistence workflows.

| P0 classification | Number | What it means |
|---|---:|---|
| Historical partial catalog-to-family mapping | 23 | Provider/source-family evidence exists, **not** an exact series-level match or complete dataset audit |
| Separate historical WID numeric run | 1 | Numeric WID source exists outside the original mapped canonical families |
| No explicit source-specific mapping yet | 47 | Requires investigation; **not evidence of data absence** |
| **Total P0 sources** | **71** | **0 exact series-key audits completed by the registry builder** |

The registry flags **3 potential overlap reviews** (WDI, WID, ILOSTAT). It intentionally records zero proven fully ingested catalog sources **by this builder**, and does not modify the existing `validation_state=NOT_INDEPENDENTLY_VERIFIED`, which concerns the builder's own value-level verification. The next milestone is to examine actual source IDs, series keys, geography, period, unit, adjustment and vintage for these three before any numeric consolidation.

CI: [six passing source-registry reconciliation tests](https://github.com/diegoduque16/Global-economic-policy-tool/actions/runs/43305296628). No private source data or warehouse mutation was made in the reconciliation implementation.


---

## 14 November 2026: data-first continuation and non-duplication checkpoint

**Current instruction supersedes the research-pilot sequencing above:** continue collecting, preserving, reconciling, validating, and preparing economic source data. **Do not initiate new economic studies, causal analysis, results generation, or policy recommendations yet.** The historical pilot proposal above is retained as context only, not an approved next task.

### Basis and limits of this audit

Reconciled relevant October–November Atlas conversations and stocktake decisions, the full current GitHub `main` workflow filename/SHA inventory, the detailed 4 November 111-workflow register, the 12 recurring YAML definitions, the 12 November consolidation decision, and metadata for all **391 recorded GitHub Actions runs** through 14 November. This establishes existing workflow roles, historical run outcomes and likely overlap, **not** a byte-level re-audit of all private OneDrive source files or individual execution logs. The repository has **112 workflow YAML files**: one additional checkpoint-guard tests workflow beyond the earlier 111-file register.

### Existing infrastructure to reuse (no duplicate systems)

| Component | Existing owner/path | Preserve / verify |
|---|---|---|
| Curated source catalog | `open_data/expanded_source_catalog.csv` (125 descriptions, 71 P0) | Catalog entries and legacy `LIVE` statuses are **not** certified actual series-level coverage |
| Catalog-to-evidence register | `scripts/build_source_registry.py`, `source-registry.yml`, `persist-source-registry.yml` | One registry; both existing weekly workflows build it, and the persistence workflow commits snapshots; do **not** add a third |
| Cross-provider discovery | Atlas 1–3 inventory and five-/six-provider discovery workflows | Previously discovered provider catalogs are metadata, not new numeric observations; check historical archives before repeating discovery |
| ILOSTAT raw source collection | `atlas-ilostat-bulk-resumable-ingestion.yml` + `scripts/ilostat_checkpoint_guard.py` | Existing three-times-daily resumable loader with immutable checkpoint backups. Nov 14 run [43587300803](https://github.com/diegoduque16/Global-economic-policy-tool/actions/runs/43587300803) verifies **900 / 1,964** source-native files, **20 newly added**, **0 lost**, **1,064 remaining**. No canonical integration is implied |
| WID numeric source and refresh | `atlas-wid-23-real-numeric-panel_REDOWNLOAD.yml`, `atlas-wid-expand-20-countries.yml` | Original 21,430 verified values in 23 countries; Nov 3 refresh covers 20 **existing** countries, not 20 new ones. Exact key/value revision comparison remains outstanding |
| Data health and backup | `atlas-data-pipeline-watchdog.yml`, private OneDrive/rclone snapshots and checksum guards | [Nov 14 watchdog](https://github.com/diegoduque16/Global-economic-policy-tool/actions/runs/43579495054) successful. Avoid adding another auditor or warehouse without proving missing functionality |
| Canonical archive | Existing `warehouse/` modules and pinned snapshot workflow | Historical **2,780,117 observations across 24 families**. Separate source-native collections are **not additive** and have not been proven integrated into a refreshed canonical release |

The existing source registry's 71 P0 rows currently classify **23 historical partial family mappings, 1 separate WID numeric source, and 47 without explicit mapping**. Unknown is not absent, and no exact series-level audits are certified by the registry builder. This is the primary reconciliation backlog, not an authorization to re-download 47 purportedly missing datasets.

### Workflow overlap and failure interpretation

- The November 4 workflow register identified **24 review candidates** among 111 files and **12 scheduled workflows**. The new additional workflow is `atlas-ilostat-checkpoint-guard-tests.yml`, a test, not another collector. All 12 recurring definitions are still present on `main` as of this review.
- **93 failed runs** came from the legacy `install-us-state-bea-gdp-income.yml`. `install-us-state-bea-verified-v3.yml` separately succeeded. Do not restart or rewrite BEA ingestion on the strength of the legacy failure count.
- `atlas-first-wid-wdi-research 2.yml` and `atlas-first-wid-wdi-research-2.yml` have identical Git blob SHAs. Research workflows are **deferred**, not needed to progress data collection. Do not remove either until historical consumers and cross-workflow references are checked.
- Parallel or successive installer versions also exist for ITEP tax rates, Bocconi plans, QCEW historical data, BJK, and other providers. Compare their code, output locations, successful successor runs and downstream references before disabling any. A successful workflow is not evidence of research readiness.
- Seven scheduled workflows inspected (`world-bank.yml`, `eurostat-ai-adoption.yml`, `historical-us-outcomes.yml`, `imf-fiscal-source-investment.yml`, `us-investment-coverage.yml`, `expand-economic-sources-batch.yml`, `audit-tariff-and-cait.yml`) publish Actions artifacts but have no direct OneDrive/rclone step in their YAML. Verify whether their outputs have another durable, checksum-verified storage route **before** creating backup infrastructure; artifact retention can expire.
- [Draft PR #7](https://github.com/diegoduque16/Global-economic-policy-tool/pull/7) adds ILOSTAT checkpoint filename/SHA binding tests (32 CI tests passed). It is **not merged** and needs a real private-checkpoint compatibility check. [Merged PR #6](https://github.com/diegoduque16/Global-economic-policy-tool/pull/6) already isolates the *manual* WID value audit from unrelated ILOSTAT checkpoint reads; do not implement that separation again.

### Agreed data-only next sequence

1. **Evidence and storage mapping:** extend the existing registry/watchdog evidence with source-native provider/table/series IDs, license, retrieval UTC, release vintage, SHA, row counts, geographic/period coverage, storage pointer, last collector success, last canonical incorporation and exact validation state. Distinguish genuinely missing from unknown, merely collected, verified raw, validated numeric and canonical.
2. **Verify durable preservation:** trace the seven artifact-based schedules to retained private snapshots; repair broken backup paths only after identifying the current writer and immutable snapshot conventions. Do not expose raw/private source data publicly.
3. **Complete existing collection:** allow the scheduled guarded ILOSTAT loader to continue; track unique table ID set growth and immutable checkpoint history, not just green run badges. Audit the 20-country WID refresh by exact observed source keys and numeric values, not by changed file hashes.
4. **Make acquired data research-ready without running studies:** inspect source definitions, numeric typing and missingness flags; units, frequency, geography and vintages; duplicate keys/overlap with existing families; source licenses; ranges and continuity; compute auditable source- and series-level coverage. Quarantine uncertain observations rather than silently harmonizing them.
5. **Expand real coverage selectively:** review the 47 unmapped P0 source labels against all existing workflows and archived source evidence, then extend an existing collector only when a genuinely missing, open and retrievable source/series is proven. Keep raw data in versioned private storage. No new generic downloader, alternate registry, dashboard or scheduled research agent.
6. **Publish independently validated incremental canonical versions later:** only after a source-specific transformation passes integrity/definition/deduplication tests; preserve the immutable canonical baseline and provide explicit rollback and provenance.

**Change-control gate:** before any new workflow, collector, scheduler, registry, archive or warehouse, identify the nearest existing implementation, its latest actual run, its stored source identity, its output directory, private snapshot lineage and why it cannot be extended. The default is to improve the existing path, not duplicate it. No deletion or disabling is authorized by this documentation review.
