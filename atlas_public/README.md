# World Economic Atlas — public website

**Domain:** worldeconatlas.com

## Public data scope

The site reuses the existing Atlas data-explorer interface with a static adapter that loads **real, verified World Bank WDI source observations**, not sample economic figures.

The first public release is pinned to the November 9, 2026 World Bank download:

- 300,240 numerical source observations
- 32 selected World Development Indicators
- 261 observed geographies: 217 World Bank countries/territories and 44 aggregates
- Annual observations spanning 1960–2025 across the selected indicators

These are not a fully harmonized warehouse or a claim that every Atlas source has been published.

The original private historical Atlas SQLite snapshot retains 2,780,117 source-separated observations, with an additional 2,104,609 indexed source-native records in the materialized edition. **These potentially overlapping layers must not be added together.** They remain private until source licensing and exact-series reconciliation are independently verified.

### License and provenance

Source: World Bank, World Development Indicators, retrieved November 9, 2026. The WDI dataset generally uses **CC BY 4.0**, subject to indicator-specific third-party exceptions. See https://www.worldbank.org/en/about/legal/terms-of-use-for-datasets and the published data attribution file. The website preserves source-defined values without cross-provider harmonization or economic predictions.

## Connect the purchased domain with Cloudflare Pages

The GitHub source is ready for Cloudflare hosting, but a user must authorize the repository connection and custom domain in Cloudflare:

1. Open Cloudflare Dashboard → Workers & Pages → Create application → Pages → Connect to Git.
2. Select GitHub repository **diegoduque16/Global-economic-policy-tool**.
3. Set project name **worldeconatlas** and production branch **main**.
4. Set **Root directory** to **atlas_public**.
5. Set **Build command** to **python build_site.py**.
6. Set **Build output directory** to **dist**.
7. Choose **Save and Deploy**. Cloudflare provides a .pages.dev preview URL.
8. In that Pages project choose **Custom domains → Set up a domain** and enter **worldeconatlas.com**; confirm the DNS changes Cloudflare proposes.
9. Optionally add www.worldeconatlas.com and redirect it to the apex.

Official Cloudflare instructions:
- https://developers.cloudflare.com/pages/get-started/git-integration/
- https://developers.cloudflare.com/pages/configuration/custom-domains/

This site is static, so it needs **no public database, no Cloudflare API key and no Cloudflare billing for a separate data backend**. Following Pages Git integration, publishing changes to the main branch updates the site automatically.

Never upload the private warehouse SQLite database, OneDrive credentials, unpublished datasets or raw source archives to public Pages assets.

## Verified WDI public publishing workflow

The dedicated source-edition publisher is at .github/workflows/worldeconatlas-public-wdi.yml. It **does not collect data anew**. It downloads the retained artifact world-bank-wdi-6 from successful GitHub Actions run **42581151428**, verifies its 576 original raw pages and 300,240 source values against their checksums, classifies the 261 geographic codes against the official World Bank API, and generates only selected annual World Bank observations as public JSON.

The publisher commits only approved atlas_public/data files. It does not alter the canonical warehouse or create a competing collector. Every release has source and version fingerprints.

After the pinned data edition has been published, locally build with:

    python atlas_public/build_site.py

Preview:

    cd atlas_public/dist
    python -m http.server 8765 --bind 127.0.0.1

Then visit http://127.0.0.1:8765/ .

## Interaction and limitations

Visitors can search indicators by label or code, select geographies and years, compare up to eight areas, switch line/bar/scatter/heatmap/table views, and export CSV. Source units and country/aggregate distinctions are shown without implying cross-provider harmonization. Data files and metadata are publicly accessible, while the private Atlas backend is not.

The public site initially contains **only WDI**. Expanding to other source families is a separate data-readiness and licensing task, not an automatic dump of the entire private SQLite database.


## Research-ready source exports (November 2026 edition)

The public build generates research downloads from the **same already verified 32 WDI series**. This is a new convenient *representation* of existing real source values, not an independent new data source.

- `/research/wdi_country_year_long.csv` — source observations with geography code, year, indicator code, numeric value. Country/territory codes only.
- `/research/wdi_country_year_wide.csv` — one row for each observed country-year and one column per WDI series; unavailable cells are **blank, never zero**.
- `/research/wdi_aggregate_year_long.csv` — 44 World Bank aggregate codes, **not added to countries**.
- `/research/wdi_variable_dictionary.csv`, `wdi_indicator_coverage.csv`, `wdi_country_coverage.csv` — definitions, source series links, and counts.
- `/research/research_manifest.json` — input and output SHA256, source run, counts, type assertions. `README.txt` describes citation and limitations.

To regenerate from the verified source tree:

```bash
python atlas_public/research_export.py --data atlas_public/data --out atlas_research_exports
```

Research subset example (after downloading the long CSV and research manifest into one folder):

```bash
python research/query_atlas_wdi.py --release atlas_research_exports \
  --countries USA,GHA --indicators NY.GDP.MKTP.KD.ZG,GC.TAX.TOTL.GD.ZS \
  --from-year 2000 --to-year 2025 --out tax_growth_subset.csv
```

This query refuses source CSVs whose contents do not match the manifest hash and writes a selection audit JSON. For econometric work, researchers still need treatment definition, event dates, source-definition comparability, and a credible counterfactual; these exports alone **do not identify causal effects**. World Bank survey inequality estimates can be discontinuous across methods and survey years.

## Expanded World Bank collector (separate from approved public source edition)

The existing `.github/workflows/world-bank.yml` now uses `open_data/wdi_indicators.json`, curated to **59 indicators** (up from 32). The 27 new codes include youth/sex-specific unemployment, real productivity, income shares, direct/indirect tax revenue, national income, health expenditure, innovation, external flows, and electricity access. Its full run is triggered by a `main` change to that catalog or collector workflow, in addition to the original Monday schedule.

New collection goes to a versioned **GitHub Actions artifact**, not automatically to public Pages or the canonical warehouse. The collector must complete, report no errors, include actual numeric values for all 59 series, and pass the existing raw-API-to-processed-row SHA256/numeric identity audit. A separately audited public release or canonical integration is required before the dashboard or research CSVs can truthfully claim the new values.

The pinned November 9 public publisher deliberately uses `atlas_public/wdi_20261109_definitions.json`, **not** the expanded working catalog. This prevents retroactively relabeling 32-series source data as the expanded 59-series release.
