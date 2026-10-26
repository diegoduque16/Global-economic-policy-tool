# IMF 2026 sub-Saharan Africa narrative fiscal-policy episodes — staged only

Source: Abdel-Latif, Bechchani, David & Lemaire (2026), *A Narrative Fiscal Consolidation Dataset for Sub-Saharan Africa*, IMF Working Paper WP/26/011, **Table 1**, printed pp. 12–14.

Official publication: https://www.imf.org/-/media/files/publications/wp/2026/english/wpiea2026011-source-pdf.pdf

This file is a **manual numeric transcription** of the published Table 1, **not** an official author-provided dataset. The CSV preserves published component and total figures exactly. It is staged separately from `warehouse/snapshot.py` and has **not** been used in causal analysis.

## Source inconsistencies, not to silently fix

- The paper's introduction describes **73 episodes**, but later descriptions use **72**; the published table has **72 country–year event rows**.
- Kenya **2023**: tax 0.45, spending 0.60, published total 2.10; the components sum to **1.05 percentage points of GDP**.
- Kenya **2024**: same discrepancy.
- Angola (AGO) and DRC (COD) have no identified episodes in the table. That is **not a license to assign zeros to unobserved country-years**.

**Validation policy:** All 72 source rows are retained. The two Kenya records are quarantined; 70 other rows pass arithmetic checks. The episode-count conflict and two arithmetic conflicts keep `warehouse_inclusion_permitted=false` until clarified and reviewed. Under no circumstances replace source totals with calculated values silently, or treat missing years as observed no-policy-change years.

## Next verification step

Obtain the authors' downloadable machine-readable dataset or an erratum; reconcile published totals and declared episode count, check fiscal-year/calendar-year mapping and documentary evidence, and independently audit sample construction. Only then introduce a source-separated `imf_ssa_narrative` warehouse family with an explicit readiness flag.

Run: `python open_data/imf_ssa_2026.py`, then `python -m unittest discover -s tests -p 'test_imf_ssa_2026.py' -v`.
