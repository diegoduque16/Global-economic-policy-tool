# U.S. state-government tax collections — historical source

## Official source and scope

U.S. Census Bureau, Annual Survey of State Government Tax Collections, original historical Excel workbook:
https://www2.census.gov/programs-surveys/stc/datasets/historical/STC-Historical-DB.xlsx

The 1951–2025 target is the Census fiscal-year annual panel. 2026-looking labels present in the historical workbook are deliberately excluded pending source verification. The source is for **state-government-only collections**, not state-plus-local totals. Washington, D.C. coverage is recorded empirically rather than assumed. The Census fiscal-year ending date field is retained as `fiscal_year_end_raw`; economic outcomes are not automatically aligned with tax policy effective dates.

## Measures, metadata and non-causal limits

Every original `Cnnn` composite and `Tnn` category is a distinct series, named `CENSUS.STC.<code>`, with the original sheet header label preserved in `disaggregation.tax_label`. Values are nominal **thousands of U.S. dollars**; cross-year inflation normalization is left to downstream analyses with proper deflators.

* `X` / missing values remain missing and are not represented by zero observations.
* Explicit source zeros are preserved as zero values.
* Aggregate and component taxes overlap. **Never add totals and subcomponents together** to calculate tax collections.
* `classification_era` records a pre-2005 versus later boundary for historical comparability reviews; official codebook checks are required before long-run code-specific causal estimates.
* Source filename, retrieval date, workbook SHA-256, field labels, state identity, and state-reported fiscal-year ending string are retained for audit.
* Tax collections react to economic activity and compliance as well as laws; they are **not** exogenous tax-law changes or statutory tax rates.
* State and local government fiscal data, documented policy enactments, tax rates, budget changes, and matched outcome series must be researched separately.

## Installation

Run the dedicated installation GitHub Action; it compiles/tests, performs a five-state 2023–2025 pilot, performs a 1951–2025 full source validation, checks audit metadata, and commits only importer/tests/integration code if successful. The next ordinary economic-warehouse workflow will fetch fresh public data and generate a newly versioned snapshot. Do not publish a public GitHub release or raw-source bundle without source-license review.
