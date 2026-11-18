# Atlas Data Explorer

A **local, private interactive website** over the **existing Atlas SQLite economic warehouse or verified source-native attachment**, not a second database or collection pipeline. It visualizes source-separated economic observations without falsely combining incompatible sources.

## Start

Use a **verified, locally extracted** Atlas SQLite file. The historical canonical warehouse and the newer multi-provider **source-native attachment** are separate physical SQLite files, available as distinct verified private snapshots. The private five-provider 883,118-record attachment is usable on its own—there is no need to download the >10GB historical canonical database just to query its newer source records.

Run from the repository root:

    python atlas_web/server.py --db "/absolute/private/path/to/native_source_extension.sqlite"

To open the older source-separated canonical warehouse instead:

    python atlas_web/server.py --db "/absolute/private/path/to/warehouse.sqlite"

Then open **http://127.0.0.1:8765/** in a browser.

Requirements: Python 3.10+ and a modern browser. No external JavaScript libraries or pip dependencies are required. The database is opened **read-only**.

Use the **private SHA-verified native source archive** from an existing Atlas production workflow. The November 17 BLS attachment run `44171117929` contains 883,118 source-native rows; the separately versioned three-indicator ILOSTAT extension is being added under its own immutable, private release verification. Verify its checksum before extraction; the SQLite file inside is named `native_source_extension.sqlite`. The local server does not automatically fetch private source archives, and it does not publish them to Cloudflare or GitHub. If you open the older canonical-only warehouse, the viewer will still work, but additional native-source counts may be zero. These counts describe only the selected physical database.

## Explore the data

- Choose an original provider group (World Bank WDI, WID, FRED, Eurostat, BLS JOLTS, and—in the separately verified ILOSTAT extension—ILO/ILOSTAT) or a historical canonical source family. Provider groups are not statistically harmonized.
- Search source series IDs and, for JOLTS, descriptive labels such as job openings, hires, quits and layoffs. The original 830 BLS series IDs encode the actual state/industry dimensions.
- Separate verified countries and territories from World Bank aggregate areas using the existing approved 261-code WDI public catalog (including 44 aggregates); retain unclassified codes as unclassified rather than guessing.
- Select up to eight geographies and a year range.
- Change between **line, bar, scatter, heatmap and table** views. Annual measurements use year spacing, while verified monthly (YYYY-MM or YYYY-MM-DD) and quarterly (YYYY-Q1 through YYYY-Q4) series use an explicitly labeled original-period axis. Mixed, malformed, duplicate or ambiguous periods are blocked from charts rather than silently annualized.
- Inspect recorded values, frequencies, source units and provider attribution.
- Export filtered source observations as CSV; private native exports include original provider source ID and selected source-native state/industry dimensions.
- See source-separated canonical rows and additional native records **as separate layers**.
- See the broader existing Atlas source registry separately from data queryable in the selected snapshot.

No economic values are embedded as demo fixtures in the website. Each visualization requests actual records from the selected SQLite snapshot.

## What consolidation means

The existing Atlas data foundation has multiple **physical** database editions; the rows are not yet one harmonized statistical table. The local explorer opens **one selected database at a time**:

| SQLite layer | Meaning | Visualization |
| --- | --- | --- |
| observations | Source-separated source-family values, with provider, geography, period, value, unit and provenance | Plot where one series has a consistent unit and an unambiguous annual period |
| native_records | Original provider records indexed in the separately verified private attachment, without being silently added to observations | Filter by provider, exact series ID, geography and year. Annual values can be charted when definitions and periods permit; monthly/quarterly values remain available in Table and CSV pending a period-aware plotting implementation |
| source_files, native_sources, snapshots | The evidence describing the actual source collections in that snapshot | Source catalog and status metrics |

**Do not add canonical and native row counts.** Data may overlap. Values across providers may have different units and definitions. The viewer does not interpolate, impute, aggregate, cross-source deduplicate or claim causal inference.

The broader Atlas collection includes sources **not necessarily present in this snapshot**, including archived WID sources and ongoing ILOSTAT bulk ingestion. These require provenance, validation and exact-series reconciliation before inclusion in the next versioned unified database. The site's source catalog distinguishes that larger source universe from the currently queryable records.

## Existing source-of-truth workflows

Use existing workflow snapshots and private OneDrive storage instead of building another warehouse:

- .github/workflows/materialize-canonical-native.yml
- .github/workflows/build-canonical-economic-warehouse.yml
- .github/workflows/consolidate-economic-warehouse-onedrive.yml
- warehouse/snapshot.py
- warehouse/build_canonical.py
- warehouse/native_materialize.py

The materialized snapshot generally stores its actual SQLite file in canonical_warehouse/warehouse.sqlite within an extracted versioned archive. Obtain and verify the appropriate private archive through the existing workflow before starting the website.

**Do not upload private database archives to the public GitHub repository or GitHub Pages.**

## Privacy and data quality

- The server binds to 127.0.0.1 by default and **rejects external binds without authentication**.
- Put an authenticated reverse proxy or Cloudflare Access Tunnel in front of it before offering remote access.
- Source SQL queries are parameterized; browser text is rendered safely.
- The visible table is limited to 2,000 records. CSV exports refuse to silently truncate selections over 10,000 records.
- The site charts strict original monthly and quarterly periods on dedicated period axes; it blocks inconsistent frequency encodings, duplicate geography/period keys, conflicting units and truncated selections. Original BLS state/industry codes are shown in Table and preserved in CSV; data are not annualized or harmonized.
- Suppressed or nonnumeric source-native values are not transformed into zero.
- It never edits the SQLite database or modifies GitHub collection schedules.

## Tests

    python -m unittest discover -s tests -p 'test_atlas_web.py' -v
    python -m unittest discover -s tests -p 'test_atlas_native_explorer.py' -v
    node --check atlas_web/static/app.js

The existing Atlas source-registry CI executes these tests whenever the website changes.

## Not yet a public website

This version is a functioning local/private website; it is **not yet hosted at a public URL**. For hosted use, deploy this same read-only service behind authentication, retaining the private SQLite and versioned snapshot lineage. Monthly and quarterly original source-period charting is now supported for safe selections. Geographic coverage and verified cross-provider harmonization remain separate follow-up work.

### Original ILOSTAT SDG data (private edition only)

The source-native ILOSTAT importer targets three already backed-up official annual SDG indicator tables: 8.3.1 (informal employment), 8.5.1 (average hourly earnings), and 8.5.2 (unemployment rate). Their 153,116 physical source rows, including blank/flagged/nonnumeric values, are **not 153,116 independent harmonized observations**. They are indexed separately in the existing private SQLite with original table IDs, source hashes, source provenance, annual periods, source status, sex and classification codes, plus full original source JSON. The queryable series IDs encode the full ILO structural dimension key and preserve exact disaggregation. No unit is guessed from a suffix; unit is marked source-defined/unverified until official unit definitions are reconciled. No private source data are uploaded to GitHub or Cloudflare. The new edition must pass SHA-replay, SQLite record preservation, and private restored-archive checksum verification before it can be counted as a completed release.

### Optional, SHA-verified official ILOSTAT code labels (private originals only)

The existing ILOSTAT bulk collector has already archived source-original ILO dictionaries into private OneDrive with SHA-256 receipts in its checkpoint. Restore the relevant original dictionary CSV files **privately** with the same original names `cl_indicator.csv`, `cl_ref_area.csv`, `cl_source.csv`, `cl_sex.csv`, `cl_age.csv`, `cl_classif1.csv`, `cl_classif2.csv`, and `cl_obs_status.csv`, along with the original private `state.json` checkpoint. Then start the **existing** localhost explorer with these additional optional arguments:

```bash
python atlas_web/server.py \
  --db "/absolute/private/path/to/native_source_extension.sqlite" \
  --ilostat-checkpoint "/absolute/private/path/to/state.json" \
  --ilostat-dictionary-dir "/absolute/private/path/to/code-lists"
```

Every local original CSV must match the exact source SHA-256, expected record count, and private OneDrive source receipt; corrupted/duplicate/unknown code lists abort loading. Original code keys stay visible in the list, table and CSV while genuine ILO source labels are shown alongside, allowing searches such as `unemployment female 15 to 24` **only when those words actually appear in the original ILO dictionaries**. Without the optional verified dictionaries, existing numeric queries keep working with exact original codes and an explicit missing-label warning.

The dictionary receipts are source-level snapshots, **not proof that the dictionaries match each individual row's current official data vintage**. Labels are never used to alter a numeric value, original unit, survey version, geographic type or source status flag. Country codes not separately ISO/officially classified remain unclassified. Reference labels are for *private read-only research exploration*; the public Cloudflare site and historical canonical warehouse are unaffected. The production smoke workflow tests exactly these private originals on the existing 1,036,234-row six-provider native SQLite edition without making any original data public.

### ILOSTAT original series research filters and comparability

The local/private six-provider explorer contains 20,167 source-original ILOSTAT structural dimension combinations across the three first SDG indicator tables. **The number of combinations is not a count of unique, harmonized economic indicators.** Search ILOSTAT by multiple separate terms, such as `unemployment SEX_F AGE_Y15-24`, `earnings SEX_F`, or the exact original `SDG_0852` source code. Every term must match the indicator name, original hashed series identity, or recorded source classification. This filters to original sex, age, occupation and source definitions without translating those codes into fabricated harmonized categories.

Because the original native ILOSTAT values currently have `source_defined_unverified` units, **cross-country charts are blocked** for these observations, including the earnings table where raw numeric values might use currencies that cannot be placed on a common scale. Single-country source-original annual trends remain available with an explicit unverified-unit warning. Multi-country original source values and suppressed/status-flagged observations remain available through the read-only Table and CSV export, retaining exact source-dimension codes. Official units, rates and PPP/currency definitions must be matched to the original ILOSTAT dictionaries and released vintages before these cross-country series may be declared comparable.

### ILO public measurement descriptions (reference only)

The existing private six-provider explorer now provides a separately labeled ILOSTAT public-indicator reference for each of three first SDG table IDs. These are **reference definitions**, not transformations of original numeric values and **not** source-row unit certification:

- `SDG_0831_SEX_ECO_RT_A`: Proportion of informal employment in total employment, by sex and economic activity — percent (%).
- `SDG_0851_SEX_OCU_NB_A`: Average hourly earnings of employees by sex and occupation — local currency per hour. National currency codes, price bases, definitions and source surveys remain unverified at the row level.
- `SDG_0852_SEX_AGE_RT_A`: Unemployment rate by sex and age — percent (%).

Official reference: https://ilostat.ilo.org/databases/ilosdg/ . The exact original numeric row `unit` remains `source_defined_unverified` in the SQLite, API and CSV; the explanatory ILO label is displayed separately with a source link and caution. Cross-country comparisons are **not approved** merely because two indicators share the broad label `percent`. Matching survey sources, classification systems, update vintages and national standards must be verified first. The underlying private Atlas edition remains 1,036,234 source-native rows; no public source-data publication results from this change.
