# State economic outcomes expansion: BEA GDP and personal income

**Research role:** Join BEA annual state-level production and income outcomes to BLS QCEW annual employment and wages by `disaggregation.state_abbr` and `year`. No inference from raw correlations.

**Provenance:** Official public BEA regional ZIP archives SAGDP.zip and SAINC.zip; retain archive and selected CSV SHA256, retrieval timestamps, table codes, BEA original units and native source description. Do not silently update a published/vintage-dependent estimate in an existing snapshot.

**State scope:** 50 states and Washington D.C.; exclude U.S. total, BEA regions and other territories. The warehouse's canonical `country_iso3=USA` is retained; states are explicit in `disaggregation.state_abbr`, `state_fips`, and `state_bea_geo_fips`.

**Metrics:** Real state GDP (SAGDP1 line 1), nominal state GDP (SAGDP1 line 3), personal income (SAINC1 line 1), population (SAINC1 line 2), per capita income (SAINC1 line 3).

**Potential later phase:** State government annual tax revenues, state and local expenditures, QCEW county variation, and dated state tax *legislative* changes. Avoid presenting GDP changes and tax receipts as exogenous policy shocks. State GDP and personal income are revised by BEA and are different economic measures. The state collection is not definition harmonized with WDI.

**Known limitations:** GDP is workplace production; personal income measures residents. BEA nominal and real dollars must not be compared without respecting the source unit/price base. Some years may be revised or unavailable. Data availability does not imply policy or causal identification.
