# Tax Policy, Inequality, and Shared Prosperity

**Phase 1 (30 October 2026): source-native WID staging and research design.**

This phase extends the existing Global Economic Policy Tool; it **does not** rebuild the registry, warehouse, GitHub Actions pipelines, or OneDrive storage. The existing `open_data/expanded_source_catalog.csv` already lists World Inequality Database (WID), OECD Income Distribution Database (IDD), World Bank Poverty and Inequality Platform (PIP), U.S. Survey of Consumer Finances (SCF), and tax data. **Catalogued is not downloaded; downloaded is not validated; validated is not causal-ready.** The current file set includes importer code for U.S. BEA state GDP/income, but the status of live BEA data needs a separate workflow check.

## Research questions

1. How do different tax policies affect the real incomes and wealth of the bottom 50%, middle 40%, top 10%, and top 1%?
2. How do changes in tax progressivity, capital taxation, social transfers, and consumption taxation interact with wages, employment, investment, output, and fiscal sustainability?
3. Which conclusions hold under credible quasi-experimental or narrative policy-shock designs, and which are only descriptive associations?
4. Do those policy effects differ with AI exposure/adoption, pre-reform inequality, and labor-market institutions? Historical policy estimates **cannot** by themselves identify modern generative AI effects.

## Official source and variable definitions

Download actual source-native files from the official [WID data interface](https://wid.world/data/) or its official bulk export. See the [official codes dictionary](https://wid.world/codes-dictionary/) and [methodology](https://wid.world/methodology/).

- `sptinc992j`: share of **pre-tax national income** for adults, equal-split allocation.
- `sdiinc992j`: share of **post-tax national income** (including in-kind redistribution), same population.
- `scainc992j`: **post-tax disposable income** share, same population; keep separate from national income.
- `shweal992j`: **personal wealth** share; not an income variable.
- Percentiles: `p0p50` (bottom 50%), `p50p90` (middle 40%), `p90p100` (top 10%), `p99p100` (top 1%). **Top 1% is nested inside top 10%; never add them together.**

WID country codes are *native two-letter area codes*, not verified ISO3 geography. WID has regional/historical areas, so exclude them from pooled national causal analyses until mapped and verified. Monetary amounts would require price/base-year and purchasing-power parity checks; Phase 1 ingests only **shares (fractions)**.

## Usage — source files must actually exist

This importer never invents numeric observations, guesses WID private API routes, or tries to silently fetch source files. Obtain genuine CSV/TSV downloads or an official ZIP from WID and stage them locally or in a private approved storage. Inputs are bounded to 300 MB per archive; split larger bulk downloads by country or batch.

```bash
python -m unittest discover -s tests -p 'test_wid_distribution.py' -v
python open_data/wid_distribution.py --input /path/to/WID_source.zip --out data/wid_staging
```

The run creates four source-separated files:

- `wid_source_native_shares.csv`: validated native WID areas, codes, years, percentile, values, checksums and available source/imputation notes.
- `wid_matched_distribution.csv`: each area/year/percentile, with separate pretax, posttax, disposable-income and wealth shares, and a **descriptive** posttax-minus-pretax difference where both shares exist.
- `wid_qa_report.json`: retained counts, coverage gaps, suspicious magnitudes, non-overlapping income/wealth share sum anomalies and skipped dictionary files.
- `source_manifest.json`: source hashes and output hashes for provenance; follow existing OneDrive durable backup policy before production use.

No file is added to the canonical warehouse automatically. A later integration must independently validate geography, source version, imputation assumptions, household allocation, metric definitions, archival continuity, and keyed deduplication against existing families. Raw bulk data must not be committed into a public GitHub repo before reviewing license/redistribution terms.

## No premature causal claims

For example, `(posttax bottom50 share - pretax bottom50 share) * 100` yields **percentage points of difference between distribution concepts**. It does *not* estimate the contribution of one tax law, because taxes, transfers, social insurance, imputation, and economic conditions all matter. Compare policy reforms only using a separately validated event calendar and defensible counterfactual strategy.

The accompanying `research/tax_reform_events_TEMPLATE.csv` specifies the data fields required to start a candidate dated tax-policy event registry. It contains headers only, **no fabricated tax reforms**. The intended workflow is: source a law and government fiscal estimates; independently verify enactment and effective dates; record policy base/rate, affected groups and fiscal scale; pre-register identification assumptions; finally join to verified, temporally aligned outcomes. Treat endogenous policy changes as descriptive unless a research design justifies causal inference.

## Next stages

1. Inventory actual **numeric** WID/IDD/PIP/SCF observations already stored in existing OneDrive snapshots (without rebuilding the warehouse); record catalogued/acquired/validated/integrated counts and duplication flags.
2. Import official WID source CSVs with this validator, retain checksums and metadata, and store validated outputs durably through the existing backup mechanism.
3. Add verified country-year and US state income/wealth metrics to a provenance-aware panel, comparing definitions (pretax vs disposable, equivalized households vs equal-split adults), vintages and imputation.
4. Compile dated tax reforms and social protection changes from IRS, OECD, IMF, national legislation and state official archives; compare enactment vs effective dates and identify plausible exogenous variation.
5. Only after (1)–(4), conduct causal studies with sensitivity tests, lag structures, pre-trends, heterogeneity, placebo leads, alternative specifications, multiple-testing checks and documented limitations.
