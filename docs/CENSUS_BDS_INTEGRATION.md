# U.S. Census Business Dynamics Statistics (BDS), 2023 release

Official, public-use historical data: https://www2.census.gov/programs-surveys/bds/tables/time-series/2023/
Release period: 1978–2023. Source files: bds2023_st.csv, bds2023_st_fa.csv, bds2023_st_sec.csv.

The collector downloads the Census CSV files directly, without an API key; retains each source file's URL,
SHA-256, schema and original classifications; preserves state, NAICS and firm-age dimensions; and treats
missing/suppressed values as missing rather than zero. The long-form panel is saved as
`data/census_bds/processed/census_bds_panel.csv` and validated using `run_report.json`.

The optional `census_bds` source family in the source-separated SQLite warehouse adds descriptive
job creation, destruction, net creation, firm and establishment metrics. Different BDS stratifications
overlap and MUST NOT be summed. Firm age, state, and industry are not independent observations.
These metrics are not causal policy shocks and cannot by themselves identify job creation
attributable to a law, president or political party. Release vintages and suppression rules
must be kept distinct from BLS data. Rates remain in source-native units pending verification.

The installation workflow restores the validated 21-family consolidated snapshot before appending
BDS, then rejects reductions in any existing source family. The enlarged database is backed up
to the established private OneDrive folder using existing RCLONE_CONFIG_B64 credentials.

References:
- https://www.census.gov/programs-surveys/bds.html
- https://www.census.gov/data/developers/data-sets/business-dynamics.html
- https://www2.census.gov/programs-surveys/bds/tables/time-series/2023/
