# Labor-market integration — v1.1 (BLS / ILOSTAT / Eurostat)

**Status: code generated and offline tested; live collection not yet verified.**

This extension supplements, but does not replace, existing FRED, Eurostat, WDI, IMF and OECD downloads.

## Source choices

- **U.S. BLS Public Data API v1** (no API key): payroll employment, private payroll employment, unemployment, private-sector average hourly earnings, private-sector average weekly hours. Monthly data; the no-registration API allows only a limited time window, here most recent ten years in the `full` run. See https://www.bls.gov/developers/api_signature.htm and https://www.bls.gov/developers/api_FAQs.htm. Historic backfill via separately reviewed BLS archival downloads is still necessary. BLS payroll counts are *thousands of jobs*, not persons.
- **ILOSTAT public API**, `https://rplumber.ilo.org/data/indicator/`: modeled or source-reported unemployment and employment headcounts by classification. Retains *all* source disaggregation fields per observation and calls the unit `source_defined_confirm_ILOSTAT_metadata` until independently verified, so it never silently interprets age categories as country-wide totals. Current indicator IDs selected for initial live testing are `UNE_2UNE_SEX_AGE_NB_A` and `EMP_2EMP_SEX_AGE_NB_A`. The smoke run targets the U.S. only; full run requests international availability and can be relatively large or rate-limited. See https://ilostat.ilo.org/data/bulk/ and the ILO-maintained Rilostat package documentation.
- **Eurostat EU Labour Force Survey** `lfsi_emp_a`: Annual employment rate for ages 20–64, total sex, 31 targeted country codes (EU27 + UK, Norway, Switzerland, Iceland), 2003+ where available; most countries have fewer years. Preserve flags for series breaks. These are *employment rates*, not employment counts. Official page: https://ec.europa.eu/eurostat/databrowser/product/view/lfsi_emp_a.

## Run and inspect

```bash
python -m unittest discover -s tests -p 'test_labor_markets.py' -v
python open_data/labor_markets.py --scope smoke --source all --out data/labor
python open_data/labor_markets.py --scope full --source all --out data/labor
# When diagnosing a source:
python open_data/labor_markets.py --scope smoke --source ilostat --out data/labor
```

The GitHub workflow includes separate `source` and `scope` switches. Run `smoke`, then inspect `run_report.json` for each provider, then run `full` only after resolving errors.

Output `data/labor/processed/labor_panel.csv`, source-specific immutable raw payloads, retrieval timestamps, SHA-256, JSON status/error report. GitHub artifacts are retained 90 days and are **not** a permanent research warehouse.

## Research integrity

* Never directly compare US payroll job *positions* to Eurostat population employment *rates* or ILO modelled headcounts without appropriate crosswalks, harmonized units and denominators.
* Preserve seasonality, data revisions, reference periods, sample populations, geography, ILO classifications and observed versus modeled flags.
* Country-aggregate comparisons, policy treatments, wage adjustments, industrial classifications, sector productivity and causal models remain future work.
* GDP and employment change under different macroeconomic factors; don't attribute outcomes to parties or laws from correlations alone.

## Known risks and backlog

1. Unregistered BLS API quotas (25 requests/day) and limited historical window.
2. ILOSTAT indicator schema or country filter can change; test the public API live before declaring success.
3. Eurostat includes structural/series-break flags and changing survey definitions.
4. ILOSTAT units, modeled observations and source-method metadata require a separate reference lookup and crosswalk.
5. Monthly/annual series need an explicit conversion policy and reconciled calendar before any causal analyses.
