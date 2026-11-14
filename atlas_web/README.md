# Atlas Data Explorer

A **local, private interactive website** over the **existing Atlas SQLite economic warehouse**, not a second database or collection pipeline. It visualizes actual consolidated economic observations.

## Start

Use a **verified, locally extracted** Atlas warehouse snapshot. The preferred existing native-materialized snapshot contains both canonical observations and indexed source-native records in **one** warehouse.sqlite.

Run from the repository root:

    python atlas_web/server.py --db "/absolute/private/path/to/warehouse.sqlite"

Then open **http://127.0.0.1:8765/** in a browser.

Requirements: Python 3.10+ and a modern browser. No external JavaScript libraries or pip dependencies are required. The database is opened **read-only**.

If only a canonical-only snapshot is available, the viewer will still work, but additional native-source counts will be zero. That describes the selected snapshot, not the whole Atlas project.

## Explore the data

- Choose one integrated source family or source-native collection.
- Search actual series codes in SQLite.
- Select up to eight geographies and a year range.
- Change between **line, bar, scatter, heatmap and table** views.
- Inspect recorded values, frequencies, source units and provider attribution.
- Export filtered source observations as CSV.
- See source-separated canonical rows and additional native records **as separate layers**.
- See the broader existing Atlas source registry separately from data queryable in the selected snapshot.

No economic values are embedded as demo fixtures in the website. Each visualization requests actual records from the selected SQLite snapshot.

## What consolidation means

The existing Atlas database has:

| SQLite layer | Meaning | Visualization |
| --- | --- | --- |
| observations | Source-separated source-family values, with provider, geography, period, value, unit and provenance | Plot where one series has a consistent unit and an unambiguous annual period |
| native_records | Original provider records indexed in the same snapshot, without being silently added to observations | Exploratory charts where values and periods permit; suppressed values remain visible in the table |
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
- The site blocks misleading annual charts for duplicate-year, mixed-unit or sub-annual selections; the original values remain accessible in Table.
- Suppressed or nonnumeric source-native values are not transformed into zero.
- It never edits the SQLite database or modifies GitHub collection schedules.

## Tests

    python -m unittest discover -s tests -p 'test_atlas_web.py' -v
    node --check atlas_web/static/app.js

The existing Atlas source-registry CI executes these tests whenever the website changes.

## Not yet a public website

This version is a functioning local/private website; it is **not yet hosted at a public URL**. For hosted use, deploy this same read-only service behind authentication, retaining the private SQLite and versioned snapshot lineage. More advanced monthly and quarterly charting, geographic labels, and verified cross-provider harmonization can be added without changing the core architecture.
