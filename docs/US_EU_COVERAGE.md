# U.S. and European economic coverage — status and limitations

This package extends **but does not replace** the existing World Bank WDI downloader, which has independently completed a 300,240-observation run across 32 WDI indicator codes (1960–2026 where available). This archive does not include those already-download World Bank observations. The US/Europe connectors have passed offline tests, but **have not yet passed a live GitHub download**.

## Implemented code, awaiting live validation

### United States (FRED graph CSV, 13 selected series)

- Payroll jobs (PAYEMS), private payroll jobs (USPRIV), unemployment rate (UNRATE), labor force participation (CIVPART), job openings (JTSJOL).
- GDP (GDP), real GDP (GDPC1), industrial production (INDPRO), consumer prices (CPIAUCSL).
- Effective fed funds rate (FEDFUNDS), 10-year Treasury yield (GS10), federal debt as share of GDP (GFDEGDQ188S), U.S. recessions (USREC).
- Historical start year varies greatly by series; URL for each download is recorded in the generated manifest. Source series may be revised. Verify FRED provider metadata and units before comparing series.

### Europe (Eurostat JSON-stat 2.0, 3 selected annual series)

- Nominal GDP in million current euros: `nama_10_gdp` (`na_item=B1G`, `unit=CP_MEUR`).
- Annual real GDP growth: `nama_10_gdp` (`na_item=B1G`, `unit=CLV_PCH_PRE`).
- Unemployment rate, total, age 15–74: `une_rt_a` (`sex=T`, `age=Y15-74`, `unit=PC_ACT`).
- 31 country selections: EU 27 and UK, Norway, Switzerland, Iceland. Not the entirety of geographic Europe. Geographic availability varies; not all 31 necessarily produce observations for all three measures.
- Eurostat `geo=EL` and `geo=UK` are crosswalked to ISO-3 `GRC` and `GBR`, respectively.

### Maddison Project historical GDP

- Historical downloader retained from v0.8, via OWID publication of MPD 2023. GDP and GDP per capita back to the year 1 CE **in places where estimates exist**, ending at 1959 for our pre-WDI extension.
- Values are historical reconstructions and may use interpolation; not equivalent to continuously observed national accounts. Preserve original geography and cautions about boundary changes.

## Staged source integrations: not automated yet

| Source | Time coverage / importance | Status |
|---|---|---|
| Jordà–Schularick–Taylor Macrohistory R6 | 18 advanced economies since 1870; 48 real/nominal series: GDP, government revenue/expenditure, debt, unemployment, banks/credit, interest rates and crises | Separate **CSV import utility implemented**, requires locally obtained licensed source and vetted codebook crosswalk; not automated |
| Bank of England Millennium dataset | UK centuries back to 13th and some 1086 benchmarks | Catalogued only; large provider spreadsheet, schema and licensing review required |
| Historical V-Dem | 80 historical polities, institutional measures from 1789 | Catalogued only; integrate as governance context with reported measurement uncertainty |
| V-Dem current dataset v16 | 531 indicators + 251 indices (published March 2026), country-year | Catalogued; select source-appropriate measures rather than blindly all columns |
| BLS and BEA primary data | U.S. employment, wages, GDP, county/state and sector | Series accessible via FRED starter set, but complete primary archives **not ingested** |
| IRS, Treasury, CBO, Census | Tax changes, government spending, distribution and demography | Catalogued; indispensable for policy treatment identification |
| OECD / ILOSTAT / ECB / IMF / Eurostat more themes | European labor, fiscal, demographic, AI / productivity panels | Not ingested except selected Eurostat series |
| AI adoption, occupational exposure, firm AI uptake | Historical AI transition / labor market analysis | Not ingested; policy research needs defensible consistent data and vintage |

## Research guardrails

1. Keep source datasets separate until units, revisions, price basis, regional definitions and missingness are explicitly crosswalked.
2. Annual GDP, monthly payroll employment and unemployment are not interchangeable variables.
3. Source country coverage and indicator coverage must be calculated from each **completed** download; a list of 31 targeted countries is not 31 verified populated national series.
4. U.S. presidential-party attribution is not causal evidence. Event-study/DiD need independently coded policy treatment magnitudes and timing, pretrends, placebo tests, confounder controls and replication.
5. Licenses vary; JST is **CC BY-NC-SA 4.0**, Maddison MPD 2023 **CC BY 4.0**. Do not blindly republish bulk datasets in GitHub.

## Run locally or from GitHub Actions

```bash
python -m unittest discover -s tests -p 'test_us_europe.py' -v
python open_data/us_europe.py --scope smoke --sources all --out data/us_europe
python open_data/us_europe.py --scope full --sources all --out data/us_europe
python -m unittest discover -s tests -p 'test_historical_maddison.py' -v
python open_data/historical_maddison.py --start 1 --end 1959 --out data/historical
```

The provided workflows run each source independently and save source manifests, raw bytes and normalized CSVs as GitHub downloadable artifacts. A live run may discover API changes or provider restrictions, at which point the code should be updated using the logged error rather than inventing successful coverage.

## Official provider documentation

- https://www.rug.nl/ggdc/historicaldevelopment/maddison/releases/maddison-project-database-2023
- https://www.macrohistory.net/database/
- https://www.bankofengland.co.uk/statistics/research-datasets
- https://www.v-dem.net/about/historical-v-dem/
- https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-getting-started/api
- https://fred.stlouisfed.org/help/data/downloading/using-the-download-data-link/
- https://www.bea.gov/open-data
- https://www.bls.gov/data/
- https://www.oecd.org/en/data/insights/data-explainers/2024/09/api.html
