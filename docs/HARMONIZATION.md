# Conservative research-layer harmonization — v1.0

## Current status

This is a tested *proposed extension* to the verified `economic-20261025-40211667703` versioned economic snapshot (504,559 source observations). It becomes a live research layer **only after the GitHub files are installed and the GitHub Actions workflow passes**.

## What it produces

- `descriptive_candidates.csv`: country/territory ISO3-tagged values for a small, explicitly reviewed list of series. Every source retains its own `comparison_group`, original series code, original unit, provider, year and period. This is NOT one harmonized time series.
- `review_summary.csv`: exclusions by family, provider, series and reason with counts. Excluded data is **not deleted** from the immutable warehouse.
- `policy_source_studies.csv`: study bibliography from the repo's `data/policy_event_sources.csv`. These are sources, **not validated policy treatments**. There are no usable numeric, dated policy shocks yet.
- `harmonization_report.json`: audit counts, flags and limitations.
- `sha256_manifest.json`: checksums for all derived files.

## Explicit protection against faulty attribution

- IMF WEO values explicitly marked `projection` do not enter the descriptive panel. Other IMF values still require release-vintage assessment.
- Historical Maddison estimates are kept in the original warehouse and excluded from the modern-country panel, even if a historical code equals a present-day ISO3 identifier.
- All ILOSTAT headcounts are excluded until the source's sex, age, classification and unit codebooks are mapped. This prevents summing overlapping age groups or misreading headcount units.
- World Bank aggregates are excluded when their codes are not ISO 3166-1 alpha-3. A valid ISO3 may be a dependent territory; this list does NOT establish independent sovereignty. Special Kosovo code `XKX` remains pending manual review, not silently mapped.
- World Bank tax revenue / GDP generally refers to central government and excludes most social-security contributions. OECD Global Revenue Statistics covers general-government revenues and tax types including social security. **The two must not be treated as equivalent policies or silently combined.**
- FRED and BLS payroll numbers are both positions rather than persons; their underlying series and vintage may overlap. No deduplication or employment impact calculation is attempted.
- No series joins, price/currency normalization, unit conversion, annualization, interpolation, causal controls or AI impact estimates are performed.

## Provenance and security

- Inputs are the archived SQLite file and its original source manifest, downloaded from a successful warehouse run using the standard GitHub Actions artifact access token with `actions: read` and `contents: read`.
- The harmonizer verifies every inventoried file SHA256 digest against the original archive and checks SQLite integrity and source-family counts before making derived files. It extracts **only** a verified database to a temporary directory, not arbitrary archive members.
- The original warehouse archive remains unchanged and the new panel is a separate artifact.
- Dataset licenses still require review before public Release publication. These GitHub Actions artifacts also expire after 90 days, so **download a second backup copy of the original warehouse**.

## Run

```sh
python -m unittest discover -s tests -p 'test_harmonize.py' -v
python research/harmonize.py \
  --archive economic-20261025-40211667703.tar.gz \
  --iso-codes research/iso3_country_codes.csv \
  --policy-sources data/policy_event_sources.csv \
  --out research_output
```

Run through **Actions → Build conservative harmonized research layer → Run workflow** with defaults for the verified October 25, 2026 warehouse run. On success, review `harmonization_report.json`. No public GitHub Release is created by this workflow. To use a later snapshot, provide the new `warehouse_run_id` and `warehouse_artifact` values.

## Next research phases

1. Obtain and code narrative exogenous fiscal-shock series (Romer–Romer US, UK Cloyne, IMF fiscal-consolidation updates), maintaining observation dates, rationale, unit and empirical identification. Require independent audit and replication source.
2. Add jobs flows and sector data: BLS QCEW/BED, Census Business Dynamics Statistics, QWI, and BEA industry accounts, capturing jurisdiction and revisions.
3. Add AI adoption and diffusion: U.S. Census BTOS AI supplement, Eurostat firm adoption, O*NET occupations, linked to policy and labor sector exposure only when classifications and time periods align.
4. Add WID and household income data, world commodity shocks, electoral/institutional records, industry productivity and PWT; then implement validated counterfactual designs.

**Research integrity:** This initial harmonizer creates transparent descriptive *candidate data*. It does not yet support causal policy conclusions.

## Reference code provenance

The bundled 249-code `research/iso3_country_codes.csv` reference was generated from the ISO 3166-1 alpha-2/alpha-3 listing shipped by the open-source `pycountry` project. It is used only as an initial territory/code screening list. It is **not** an authoritative time-varying list of sovereign countries; temporal and political crosswalks require independent review.

Source methodology to verify before statistical comparisons: [World Bank aggregate API guidance](https://datahelpdesk.worldbank.org/knowledgebase/articles/898614-aggregate-api-queries), [WDI tax revenue definition](https://databank.worldbank.org/metadataglossary/jobs/series/GC.TAX.TOTL.GD.ZS), and [OECD Global Revenue Statistics](https://www.oecd.org/en/data/datasets/global-revenue-statistics-database.html).
