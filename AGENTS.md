# World Economic Atlas — working agreement for future coding sessions

**Before modifying Atlas, read `docs/atlas_project_brief.json`.** This is the version-controlled, public-safe summary of the user's project goals, latest direction, dated decisions, collection stages, evidence URLs, and next actions. A human-readable edition is generated at `/project/` on the public site and a machine-readable copy at `/project/brief.json`.

## Required workflow

1. Read the brief and its most recent source-linked decisions. Treat its date as a snapshot, **not a live status feed**.
2. Check GitHub Actions runs and artifacts directly before claiming that any collection succeeded, a run finished, a checksum was verified, or data were published. Examine numeric observation counts and source IDs, not only job green status.
3. Compare the requested work with **existing** ingestion code, `open_data/expanded_source_catalog.csv`, `data/registry/registry.json`, source-native private archives, and the canonical warehouse. Reuse the existing pipelines unless there is demonstrated need for a new component.
4. The user's highest-priority direction is **complete datasets for a defined source scope, download verified missing native values, and make those values usable**. An inventory or provenance check is an *in-process gate*, not the product. Do not replace data acquisition with audit-focused tasks.
5. Separate original source data, source-native numeric collections, historical canonical snapshots, derived data, and the `32-series` public WDI edition. **Never add their counts together without exact row-level deduplication**, and never call collection an approved public release.
6. Preserve source URLs, identifiers, official unit definitions, geography, period, source vintage, flags, SHA-256, and private backup receipts. No fabricated observations, silent zero-filling or unsafe automatic merging.
7. Keep raw WID/ILOSTAT, private SQLite warehouses, private OneDrive locations, credentials and restricted-license data out of public Cloudflare assets.
8. When a significant change in direction, confirmed dataset-completion result or blocking issue occurs, update **the same** `docs/atlas_project_brief.json` in the implementing PR. Preserve the dated decision history and evidence links; build `atlas_public/build_site.py` and run `tests/test_atlas_public.py` before merging. Don't add another competing status file or dashboard.
9. The site is a static Cloudflare Pages build. A merged GitHub commit is **not proof of a live deployment**. Verify the public URL separately and state deployment uncertainty explicitly.
10. For new conversations about Atlas, use the current brief as context rather than asking the user to re-explain the goal. If access to the brief is unavailable, say so instead of claiming to have checked it.

## Useful reference points

- Project brief source: `docs/atlas_project_brief.json`
- Public briefing page after deployment: https://worldeconatlas.com/project/
- Master dataset catalog: `open_data/expanded_source_catalog.csv`
- Existing project/data registry: `data/registry/registry.json`
- Data-release boundary: `docs/ATLAS_DATA_RELEASE_V1_2026-11-14.md`
- Historical consolidation decision: `docs/ATLAS_CONSOLIDATION_DECISION_2026-11-12.md`
- Current collector status: live GitHub Actions of this repository, **not** the dated values in the brief.

This document guides execution. The GitHub data files, collector artifacts, tests and actual production logs remain authoritative for facts.
