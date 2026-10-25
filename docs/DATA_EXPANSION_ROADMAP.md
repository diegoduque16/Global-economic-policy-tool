# Global Economic Policy Tool — Comprehensive data strategy (2026-10-25)

## Status audit (do not confuse source discovery with implemented ingestion)

- **Completed, tested live**: World Bank WDI (32 configured indicators, 300,240 observations in a full successful run), US FRED (13 selected series, part of 13,448 combined USA + Europe observations in a successful full run), Eurostat (3 selected annual series, part of the same combined run), Maddison historical GDP/GDP per capita (16,395 observations from 1–1959, sparse reconstructed estimates).
- **Code exists, not populated**: JST Macrohistory CSV import helper; source must be acquired under its licensing terms.
- **Everything else** in the new source register is **catalogued or proposed**, not live. The source register is a planning inventory rather than proof of download or legal redistributability.
- Datasets remain **separate GitHub Actions downloadable artifacts**, not yet a normalized, persisted, validated panel. Artifacts currently retained for 90 days. This is not a perpetual research warehouse.

## Why comprehensiveness matters

A causal research program needs four independent kinds of evidence: (1) **outcomes** (employment, total hours, wages, GDP, TFP, disposable income and wealth); (2) **policy treatments** (dated and coded tax reforms, fiscal spending legislation, monetary policy surprises, trade reforms and AI regulation); (3) **confounders and mechanisms** (commodity shocks, wars, business cycles, population, productivity, demographics, institutions, education, trade, sector composition); and (4) **AI exposure and adoption**, while keeping exposure indices distinct from observed uptake or employment effects. An ever-larger indicator count without valid identification is not progress.

## Wave 1: best scientific returns on ingestion effort

| Integration | Why first | Example public source |
|---|---|---|
| **IMF GFS, WEO + fiscal consolidations** | Real public finances, cyclically adjusted context, narrated exogenous tax/spending changes | https://data.imf.org/ and https://www.imf.org/en/publications/wp/issues/2024/09/27/an-updated-action-based-dataset-of-fiscal-consolidation-554986 |
| **OECD Revenue Statistics, Taxing Wages, national accounts/productivity** | Actual revenue composition, labor tax wedges, internationally comparable growth and productivity | https://www.oecd.org/en/data/insights/data-explainers/2024/09/api.html |
| **ILOSTAT bulk** | Employed workers, hours worked, earnings, disaggregated occupations and generative-AI exposure | https://ilostat.ilo.org/data/ |
| **BLS CES/QCEW/BED + Census QWI/BDS** | True U.S. net/gross jobs; industry, county, business age and worker flows | https://www.bls.gov/cew/downloadable-data-files.htm and https://www.census.gov/data/developers/data-sets.html |
| **Eurostat LFS, government COFOG, business statistics** | Harmonized European jobs, labor structure, fiscal functions, productivity | https://ec.europa.eu/eurostat/en/web/main/data/bulkdownload |
| **World Inequality Database** | Distribution of income and capital/labor shares, not simply average GDP growth | https://wid.world/data/ |
| **BTOS AI supplement + Eurostat `isoc_eb_ai`** | Actual firm adoption and effects; key AI-economy transition indicators | https://www.census.gov/hfp/btos/data_downloads and https://ec.europa.eu/eurostat/web/products-eurostat-news/w/ddn-20251211-2 |
| **ILOSTAT AI exposure / O*NET tasks** | Mechanisms linking technologies, occupations, sectors and wages | https://ilostat.ilo.org/data/ and https://www.onetcenter.org/database.html |
| **ParlGov / V-Dem / Congress.gov** | Governments, presidents, legislature and enacted reforms; institutional confounders | https://www.parlgov.org/ and https://v-dem.net/data/the-v-dem-dataset/ and https://api.congress.gov/ |
| **WTO tariff actions + UN Comtrade** | Reforms and trade exposure with dated implementation, independent of who was president | https://ttd.wto.org/en/download |
| **Vintage data + crosswalks** | Prevent lookahead bias, double counting, historical country mistakes, unit mismatches | https://fred.stlouisfed.org/ |

## Wave 2: mechanisms and alternative explanatory variables

1. BEA industry national accounts, input-output tables, county and state GDP; OECD international input-output tables, PWT TFP/capital.
2. UN World Population Prospects, UNESCO UIS, OECD adult skills (PIAAC), WHO health, migration and education.
3. BIS credit and ECB monetary/banking datasets, IMF financial sector, commodity and energy prices.
4. Historical JST series since ~1870, Bank of England historical wages/prices, Historical V-Dem, plus government tax archives.
5. Detailed U.S. state and local taxation, IRS SOI, CBO legislation scores, Census state/county employment changes.

## Wave 3: frontier AI and long-run research

- Stanford AI Index dataset-by-dataset rights; Census longitudinal technology surveys, occupational task exposure and firm AI adoption.
- USPTO PatentsView and OECD patent indicators, open research outputs, compute and electricity (with licenses reviewed).
- Tax code changes, industrial policy, social protection, inequality and AI governance with documented enactment and implementation dates.
- Independently replicated causal analysis (event studies, synthetic controls, local projections, natural experiments, difference-in-differences, IV), plus pre-registration of hypotheses and red-team robustness review.

## Make the research reproducible (required infrastructure)

For **every series**, store `provider`, `source_id`, `geography_id`, `observation_period`, `frequency`, `measure`, `unit`, `price_base`, `seasonal_adjustment`, `observation_type`, `release_date`, `revision_vintage`, `retrieved_utc`, `source_url`, `license`, `sha256`, `confidence/source_flag`. Every policy event also needs `policy_family`, `announcement_date`, `legislative_enactment_date`, `effective_date`, `actual_policy_magnitude`, `implementation_scope`, `official_document_url`, `motivation/anticipation`, `comparison_design`, and independent coding review.

### Guardrails

- WDI rows combine observations from countries **and World Bank aggregates**; do not treat all as sovereign states in panel regressions.
- Historic GDP from 1 CE is reconstructed, heavily intermittent and not comparable to 21st century measured payroll jobs.
- Some AI surveys changed methodology (US BTOS AI wording changed November 2025); Eurostat enterprise coverage (>=10 employees) has a different denominator. Do not splice without calibration.
- Party-in-office comparisons need overlapping control of Congress, state policy, central bank decisions, world shocks, anticipatory effects, and lags.
- Respect dataset licenses, particularly JST noncommercial share-alike and restricted microdata.
- Include negative results, counterevidence, heterogeneity, confidence intervals and missingness statistics to reduce motivated reasoning.
- Keep full raw data out of the repo when large, sensitive or redistribution-restricted. Save durable warehouse snapshots outside 90-day GitHub artifacts; schedule the new expansion workflow separately once connectors are stable.

## Implementation state

This roadmap and `open_data/expanded_source_catalog.csv` provide **documentation only**, not functioning new downloaders or autonomous agents. To use it in the GitHub repository, upload the two files preserving their relative paths. Existing successful World Bank, US/Europe and historical Actions workflows remain unchanged.
