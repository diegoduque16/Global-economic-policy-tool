# Narrative tariff and California AI unemployment panels

These are **two separate, source-native datasets**, collected with
`open_data/tariff_cait_panels.py`. Original workbook schemas are checked
on each run; checksum and provenance live in the per-source reports.
The previously validated extraction was GitHub Actions run 40368987349.

`warehouse/snapshot.py` registers optional `tariff_shocks` and
`cait_ai_claims` source families. Monthly CAIT rows remain monthly,
geographic disaggregation explicitly remains California, and tariff
shocks remain annual. The warehouse's definition-harmonized and
causal-ready flags stay false.

**DO NOT:** interpret tariff instruments as observed tariff rates;
confuse source-native shock signs with tax changes; claim observed
AI causation from CAIT; aggregate both Anthropic and Eloundou claims
together; collapse California claims into nationally representative
USA totals; add shares across exposure groups without checking unit
and denominator; silently turn zero into missing.

Run **Build versioned economic research warehouse** after this installer
to include both optional sources in the full audited snapshot. The
collector is executed inside that main workflow, not a one-time
GitHub artifact dependency. Licensing review is required before
publishing any full dataset publicly, especially CAIT derivatives.
Source studies: https://doi.org/10.3386/w35102 and
https://edd.ca.gov/aitracker .
