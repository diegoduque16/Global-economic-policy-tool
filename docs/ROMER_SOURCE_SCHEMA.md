# Romer–Romer 2010 — workbook schema and codebook inspection

This extension inspects the original author-hosted archive already fetched successfully by the **Audit original fiscal policy shock research sources** workflow. It uses only the original documented source archive and its recorded checksums. The already-uploaded `romer_2010_archive_inventory.json` established the following SHA-256 hashes:

- ZIP: `c6c3b4888f3de596abf56bc136e2175ec5a4aa0dab137e79e261984a0a84701c`
- `DataSet/Romer&RomerFiscalData.xlsx`: `e0803df2b6a541041ef80c9dee050fc65bd75ea5ac57926f67483d1ee94dadd3`
- `DataSet/ReadMe.pdf`: `ce9fba22a5c5ff1bb0b0675f65154f8c5e45592277b73814157d9fdc285fa1b7`

## What it does

Downloads the ZIP anew, verifies the archive and two key file hashes, inspects workbook sheets, dimensions, text headers, defined names, and selected identifiers in original RATS code. It extracts only short keyword-linked portions of `ReadMe.pdf`, with page numbers. The output is `romer_workbook_schema_review.json`.

**No numeric source values, full workbook content, PDF text, or RATS program code are published.** Nothing is appended to the economic warehouse. Sheet names, text labels, and candidate variable identifiers are useful for manual review only, not proof of correct exogeneity classification.

The program fails closed if the original archive changes after the Oct. 25, 2026 fingerprinted audit. Source fingerprints should only be changed after manual review.

## Installation

Upload preserving exact paths:

1. `policy_shocks/inspect_romer.py`
2. `tests/test_inspect_romer.py`
3. `docs/ROMER_SOURCE_SCHEMA.md`
4. `.github/workflows/romer-schema-audit.yml`

Then run **Actions → Inspect Romer Romer source workbook and codebook → Run workflow**.

On completion, review the JSON artifact and author manuscript/ReadMe to establish exact exogenous shock column, time period, units, sign, anticipatory timing and classification. Only then use `policy_shocks/ingest_reviewed.py` with independently reviewed observations. If any ambiguity remains, do not import as a valid policy treatment.

Source: https://eml.berkeley.edu/~dromer/papers/DataSet.zip ; DOI: https://doi.org/10.1257/aer.100.3.763
