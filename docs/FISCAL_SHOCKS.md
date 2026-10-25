# Fiscal policy shocks — reproducible source-acquisition phase v1

## Purpose and scope

This package advances the economic warehouse from descriptive outcomes toward defensible *policy treatment* data. It **does not** yet run causal inference, claim any jobs created, classify presidents, or deliver validated numeric shocks. It audits the original U.S. tax-shock replication archive directly from the authors' Berkeley site, records individual member filenames, sizes and SHA256 checksums without republishing the original datasets, and provides a fail-closed human-reviewed numeric importer for later work. The original 504,559-observation warehouse and 130,442-row descriptive candidate panel are not modified.

### Primary research sources

1. **United States:** Christina D. Romer and David H. Romer (2010), *The Macroeconomic Effects of Tax Changes: Estimates Based on a New Measure of Fiscal Shocks*, AER 100(3), 763–801. Original author-hosted research archive: https://eml.berkeley.edu/~dromer/papers/DataSet.zip. Paper: https://doi.org/10.1257/aer.100.3.763. Do not confuse the 2010 TAX series with the authors' 2004 MONETARY shocks.
2. **United Kingdom:** James Cloyne (2013), *Discretionary Tax Changes and the Macroeconomy: New Narrative Evidence from the United Kingdom*, AER 103(4), 1507–28. Replication package: https://www.openicpsr.org/openicpsr/project/112651/version/V1/view. The archive exposes `data.xlsx` but the shock variable, units and period need manual codebook validation. This package catalogs that source only.
3. **31 economies:** Gustavo Adler, Cian Allen, Giovanni Ganelli, Daniel Leigh (2024), *An Updated Action-based Dataset of Fiscal Consolidation*, IMF WP/24/210, https://www.imf.org/en/publications/wp/issues/2024/09/27/an-updated-action-based-dataset-of-fiscal-consolidation-554986. 17 OECD economies 1978–2020 and 14 Latin American/Caribbean economies 1989–2020. **Important:** This identifies budget-deficit-motivated consolidations, not a symmetric set of expansionary/stimulus tax changes. The paper describes a downloadable supplement; extract/format must be verified separately.

## What is implemented now

- `policy_shocks/sources.json`: provenance registry, explicit design distinctions and access status.
- `policy_shocks/audit_sources.py`: downloads ONLY the exact reviewed Berkeley ZIP URL via HTTPS and inventories ZIP members, with path-traversal, symlink, zip-bomb, duplicate-path and size guards. The GitHub workflow uploads **JSON metadata only**, never the original source archive. A request redirected to another URL fails closed.
- `policy_shocks/ingest_reviewed.py`: optional LOCAL importer that requires an actual archive audit and an explicitly reviewed spreadsheet exported to CSV. It checks member SHA256 reference, nonmissing reviewer, date, source type, literal signed value, original frequency, codebook URL, unique row, and provenance. It does not automatically infer sign, unit, fiscal motive or exogeneity; independent verification remains required. The template contains **header only, no invented events**.
- `tests/test_fiscal_shocks.py`: offline regression and safety tests.
- `.github/workflows/fiscal-shock-audit.yml`: explicit manual workflow dispatch. Doesn't change the existing warehouse, download large data into GitHub, or publish public source data.

## Activate on GitHub

Upload the `policy_shocks/` folder, `tests/test_fiscal_shocks.py`, `docs/FISCAL_SHOCKS.md` and `.github/workflows/fiscal-shock-audit.yml` into those exact paths in your repository. Then choose **Actions → Audit original fiscal policy shock research sources → Run workflow**. If the job succeeds, download artifact `fiscal-shock-source-audit-1`, extract `romer_2010_archive_inventory.json`, and inspect which source formats/files are present. The original archive is downloaded only in the runner's temporary workspace and is not in the artifact.

A successful audit is **not** successful shock extraction. If the author site changes, has a redirect or blocks CI access, the audit correctly fails without fabricating data. That limitation should be investigated, not bypassed via third-party sources silently. Only after inspecting actual source codebooks should the importer be connected to numeric extraction. The importer presently supports reviewed U.S. records only, not UK/IMF records.

## Human-reviewed conversion

- Open the source archive and codebook locally, find the *published exogenous tax shock* variable(s), not predicted GDP effects, endogenous tax changes, regressors or total tax receipts.
- Record original source member name and member SHA256 from the audit. Record original exact source row reference and column code, observation period (`YYYYQ1`–`YYYYQ4` or annual), original signed numeric shock value, original unit, **explicit sign meaning**, and narrative eligibility, with independent reviewer.
- Copy the header in `policy_shocks/reviewed_shocks_TEMPLATE.csv`; add individually verified numeric records. This is not a place for estimates, invented values, or arbitrary transcriptions.
- Run `python policy_shocks/ingest_reviewed.py --input path/to/reviewed.csv --inventory path/to/romer_2010_archive_inventory.json --registry policy_shocks/sources.json --out reviewed_shock_output`.
- The results retain source-native magnitudes (no interpolation or conversions). Independent re-extraction from source bytes and codebook validation must occur before econometric use. Do **not** fit regressions before resolving intended shock motivation, treatment dates, contemporaneous confounding, anticipation, frequency, sample selection and independent scientific replication.

### Important methodological distinctions

Narrative tax-policy shocks and fiscal consolidation episodes are not interchangeable treatments. Fiscal consolidation is generally contractionary; the series' sign cannot automatically be interpreted as an expansionary tax cut. Do not treat annual spending ratios, tax/GDP or statutory tax rates as exogenous shocks. Policy shocks still require strong identification assumptions and need not be truly random. The project must explicitly test pre-trends, different identification windows, spillovers, revisions, sample-period dependence and null results before interpreting job outcomes.

### License and retention

Downloadable does not imply freely redistributable. We only save source metadata in GitHub Actions. Artifacts expire after 90 days. Keep independent backups of the original warehouse and descriptive layer, and review primary source and replication package terms before hosting any original data publicly.
