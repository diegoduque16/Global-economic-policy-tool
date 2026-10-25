# Romer–Romer (2010) tax variable mapping | evidence-gated phase 2

## Current status

The fiscal archive audit verified 27 members, including `DataSet/Romer&RomerFiscalData.xlsx` and `DataSet/ReadMe.pdf`. A schema inspection established that the **Tax Measures** worksheet is dimension `A1:AT264`, with three versions of the authors' tax measures. The new workflow automates the next step: **discovering and auditing the exact labels and layout for the three versions**, including the authors' exogenous/endogenous categories.

**It does not yet extract numeric tax policy shocks or estimate employment impacts.** This is deliberate. The source cells containing variable names are distinct from the observations, and the date alignment, ratio scale, and category semantics require verification against the original codebook and paper before longitudinal statistics can be responsibly used.

## Installation (GitHub Desktop: single batch)

1. Make sure GitHub Desktop has cloned `diegoduque16/Global-economic-policy-tool`.
2. Unzip `Romer_Tax_Research_Batch_v2.zip`, then copy the *four contained folders* into the **root of your cloned repository**. Merge folders; do not replace/delete existing files. The new files are:
   - `policy_shocks/romer_tax_mapping.py`
   - `tests/test_romer_tax_mapping.py`
   - `docs/ROMER_TAX_MAPPING.md`
   - `.github/workflows/romer-tax-mapping.yml`
3. In GitHub Desktop, verify four changed files, commit with message `Add reviewed Romer tax series mapping audit`, and click **Push origin**.
4. In GitHub Actions, choose **Audit Romer tax shock variable mapping**, then **Run workflow** (branch `main`).
5. Download the artifact `romer-tax-mapping-1` if you wish, but you can also ask ChatGPT to inspect the workflow report using the GitHub connector.

## What the code verifies

- Identical SHA-256 fingerprint for the original authors' ZIP and XLSX, blocking source drift.
- Workbook contains **Tax Measures** and the expected historical layout.
- Distinct versions: liabilities excluding retroactive changes, liabilities including retroactive changes, and present-value measures.
- Tax classification rows: spending-driven, countercyclical, deficit-driven, long-run growth, endogenous combined, exogenous combined, and all legislated.
- Candidate variable labels and definition text at source-cell coordinates.
- Per-column numeric occupancy counts and likely date-text markers, **without disclosing numeric source values**.
- Structured metadata marking what remains unresolved; never fabricates quarterly timestamps, units, signs or causal identification.

## Next gate before numeric extraction

1. Determine the correct source variable code/column for `Exogenous Tax Changes` (and deficit vs long-run components), preferably ratio to GDP and **nonretroactive liabilities as the paper's baseline**, with the two alternatives retained separately.
2. Verify quarterly index columns against the source; do not infer dates solely from row numbers.
3. Establish whether ratios are fraction units or percent-of-GDP points and ensure a tax increase has the authors' original sign.
4. Confirm whether zeros are meaningful and missing/blank values are not zeros.
5. Compare reproduction statistics against the paper (period 1947–2007) and independently review any policy assignment.
6. Only then write an isolated source-specific numeric importer and replicated models. No paper's internal causal classification by itself proves exogeneity in a new research design.

## Notes

- The metadata artifact is a temporary GitHub Actions artifact (90-day default); retain backup.
- Do not push or publicly republish the original source data until permission and attribution requirements are evaluated.
- This package does not alter the 504,559-row versioned warehouse or the 130,442-row descriptive candidate panel.
- Primary study: Romer & Romer (2010), *American Economic Review*, https://doi.org/10.1257/aer.100.3.763
- Authors' archive: https://eml.berkeley.edu/~dromer/papers/DataSet.zip
