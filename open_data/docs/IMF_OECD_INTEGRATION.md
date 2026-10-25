# IMF + OECD public sources: Integration 1.0

## Scope and source provenance

**IMF DataMapper v2** — Current API is documented at https://www.imf.org/external/datamapper/api/ . Metadata and source edition from `api/v2/indicators` and country list from `api/v2/countries`. Downloads seven selected series: real GDP growth, inflation, unemployment, general government net lending, gross government debt, and historical public finance revenue and expenditure. The last two come from a **different database** (Public Finances in Modern History) than the WEO series. Provider projections are flagged using `projection-year` when available. **The DataMapper endpoints have not yet been successfully live tested in this release.** IMF fiscal balance is a budget outcome, not an exogenous fiscal shock.

**OECD Global Revenue Statistics** — Official data explorer sample and API query `OECD.CTP.TPS,DSD_REV_COMP_GLOBAL@DF_RSGLOBAL,2.1/..S13._T..PT_B1GQ.A`. Data is general-government **total tax revenue as a share of GDP**. The OECD Global Revenue Statistics covers ~141 economies from 1990 onward (varying). Source: https://www.oecd.org/en/data/datasets/global-revenue-statistics-database.html . API: https://www.oecd.org/en/data/insights/data-explainers/2024/09/api.html . Data may be rate-limited and is subject to OECD terms; we store source URL/date/checksum/metadata.

A tax-revenue ratio is **not a statutory tax rate** nor a narrative tax-policy intervention. Do not interpret correlations with GDP and labor outcomes as caused by taxation. IMF WEO may include projections in the same payload as historical estimates. All series have their source edition tracked; GDP, prices, government revenue, and labor-force variables should not be merged without metadata harmonization.

## Run

```bash
python -m unittest discover -s tests -p 'test_imf_oecd.py' -v
python open_data/imf_oecd.py --scope smoke --source both --out data/imf_oecd
python open_data/imf_oecd.py --scope full --source both --out data/imf_oecd
```

Outputs: `data/imf_oecd/raw/*.raw`, `processed/imf_oecd_panel.csv`, `run_report.json`. Failed source fetches are recorded in `errors`; the command returns a nonzero status on errors, duplicate disagreements, or missing mandatory source data. A GitHub artifact is uploaded **even on failure** for diagnosis.

If smoke fails, inspect logs and `run_report.json` rather than selecting full. Note that these are finite initial source samples, **not complete IMF Government Finance Statistics or all OECD data**.
