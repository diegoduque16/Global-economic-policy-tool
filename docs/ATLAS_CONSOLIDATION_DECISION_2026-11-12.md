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
