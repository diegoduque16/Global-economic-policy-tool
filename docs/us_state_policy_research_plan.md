# U.S. state-level economic-policy research expansion

The BLS QCEW state annual panel covers 51 jurisdictions (50 states plus the District of Columbia) and the most recent five full years (2021–2025), subject to source access and complete-pilot checks.

The all-ownership total (`own_code=0`, `industry_code=10`) captures annual covered employment, wages and establishments; industry-sector records use `own_code=5` **private-sector-only** NAICS sector categories. Do **not** sum the all-owner total with private sectors or treat the latter as all-owner industry totals. All measured counts and pay values are source-native and nominal; missing and suppressed values are not zeros. Industry code and NAICS vintage changes must be accounted for before time-series causal comparisons.

The collector is activated by the existing `open_data/imf_oecd.py --scope full` hook, rather than modifying an Actions workflow file that the GitHub token is not authorized to edit. It is a *source-separated, descriptive outcome family* in `warehouse/snapshot.py`, not evidence of tax/spending policy shocks.

Next steps: BEA state GDP and real income, Census state and local government finance, BLS LAUS unemployment, broader QCEW annual historical bulk back to 1975, documented state tax-rate and spending law changes, occupation-level AI exposure, and a dated cross-state policy-event registry. All require provider-specific units, revision histories, jurisdiction harmonization and analysis of confounders.
