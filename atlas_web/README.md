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

Use the **private SHA-verified native source archive** from the existing Atlas production workflow (the November 17 BLS attachment run `44171117929` has 883,118 source-native records). Verify its checksum before extraction; the SQLite file inside is named `native_source_extension.sqlite`. The local server does not automatically fetch private source archives, and it does not publish them to Cloudflare or GitHub. If you open the older canonical-only warehouse, the viewer will still work, but additional native-source counts may be zero. These counts describe only the selected physical database.

## Explore the data

- Choose one original provider group (World Bank WDI, WID, FRED, Eurostat or BLS JOLTS) in the native attachment, or one source family in the historical canonical warehouse.
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
