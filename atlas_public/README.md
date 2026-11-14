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
