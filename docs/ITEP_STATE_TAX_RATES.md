# ITEP historical state rates — evidence-gated integration

Source: Institute on Taxation and Economic Policy (ITEP), *Historical State Tax Rate Data*, File Version 2026.2 (June 2026), https://itep.org/historical-state-tax-rate-data/.

This collector captures explicitly labeled state top statutory personal and corporate income rates, and state general sales rates, in **percentage points** as of **December 31** of each calendar year. It excludes 2026-2027 provisional rates and does not conflate a year-end change with a verified legislative enactment or effective date. For state-years that are missing, it never imputes zero. ITEP omits most local taxes, excludes tax base changes from its rate measure, and includes certain surcharges within headline top rates; see ITEP methodology.

The workbook parser fails closed unless it recognizes official file structure unambiguously and confirms substantial coverage for all three tax types. An independent schema metadata artifact is retained even if full integration cannot proceed. The source workbook is neither committed nor archived. The new family remains separate from the prior tax collections, policy action records, and OECD corporate tax series.

The workflow restores exactly all previously validated source families from GitHub run 40437086084, validates original snapshot file hashes and record counts, adds source-native ITEP rate observations, and checks the complete resulting SQLite snapshot before uploading a new versioned artifact and verifying the private OneDrive backup under `Diego/economic research database/snapshots`. All old-family counts must be preserved unchanged. Checksums are recorded. The workflow is manual-only to avoid rerunning costly large warehouse builds with no new ITEP release.

The `derived_year_end_changes_not_legal_events.csv` file is a descriptive comparison table only. It must never be treated as a list of legal changes or a causal shock series; for that, use documented effective dates and independently reviewed tax legislation. Rates may differ from actual average/effective tax burdens because of brackets, tax bases, credits, exemptions, deductions, and taxpayer mix.

If the workbook's layout differs from the supported explicitly labeled formats, download `itep-source-audit-*` from GitHub Actions, inspect `itep_workbook_schema_audit.json`, and update the parser before a live import. Do not remove verification gates or insert guessed observations to force success.
