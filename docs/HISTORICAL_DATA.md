# Historical economic data extension (pre-1960, v0.8)

This module adds a **separate** auditable, long-run economic dataset, rather than extending WDI values with invented backcasts.

## Initial implemented data connector
- Maddison Project Database (2023), distributed through Our World in Data's public chart API.
- Indicator series: GDP per capita and total GDP (separate series; units and provenance in source metadata).
- Year filter: 1–1959 CE. **The source is sparse before 1800 and not a complete annual panel.** The earliest surviving observation can vary by country and indicator.
- Source links: https://www.rug.nl/ggdc/historicaldevelopment/maddison/releases/maddison-project-database-2023 ; https://ourworldindata.org/grapher/gdp-per-capita-maddison-project-database and https://ourworldindata.org/grapher/gdp-maddison-project-database .

Run from the repository root (Python 3.12, no external packages needed):

```bash
python -m unittest discover -s tests -p 'test_historical_maddison.py' -v
python open_data/historical_maddison.py --start 1 --end 1959 --out data/historical
```

Outputs: `data/historical/raw/*.csv`, raw source metadata JSON, `processed/historical_panel.csv`, `manifest.json` with SHA256, and `run_report.json` with year range, counts and citations. Downloads fail loudly if source responses are malformed or no usable observations are found. No zero-imputation or local interpolation.

## Scientific constraints
1. The Maddison series consists of **reconstructed historical estimates** and is not equivalent to measured modern GDP or payroll jobs. Particularly early centuries are based on sparse benchmarks and assumptions.
2. Pre-1960 country and colonial boundaries may differ. Three-letter entity codes are not validated as sovereign states; do historical-country crosswalks before combining panels.
3. GDP units and deflators can differ substantially from WDI and between historical releases. Keep `unit=source_defined_see_metadata` until verified, and avoid numerical splicing with WDI without documented calibration.
4. The downloader requests complete source CSVs and preserves original hashes and attribution metadata. It does not infer missing annual observations or discover government policy changes.
5. Source license: Maddison Project Database 2023, CC BY 4.0. Cite Bolt & van Zanden (2024) and, for charts or analyses under 12 countries, cite underlying original research as specified on the Groningen release page.
6. The downloader has offline tests only until GitHub Actions runs with network access. Treat live download status as **unverified** until a successful run is observed.

## Next historical sources to evaluate (not yet implemented)
- **Jordà–Schularick–Taylor Macrohistory**: 18 advanced economies, annual series since 1870, 48 macro-financial variables. License CC BY-NC-SA 4.0; not automatically suitable for commercial distribution.
- **Bank of England A Millennium of Macroeconomic Data**: UK long-run prices, wages, monetary and other variables extending into the medieval period; requires workbook schema / licensing review before ingestion.
- **Historical V-Dem**: institutional, democratic and political indicators for about 80 polities back to 1789, with more uncertainty in older data.

No historical causal conclusions are implied by collecting these data.
