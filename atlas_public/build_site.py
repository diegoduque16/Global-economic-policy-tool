#!/usr/bin/env python3
"""Build a static Cloudflare Pages site from the existing Atlas explorer UI.

The published data/ directory contains ONLY the separately approved, verified
World Bank WDI source snapshot; no SQLite, secrets, raw API or other source
families are placed in the public assets. This is not a live private data API.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

if __package__:
    from .research_export import build_research_exports
    from .project_brief import build as build_project_brief
    from .source_guide import build as build_source_guide
    from .build_bls_charts import build as build_bls_charts
    from .build_ilostat_charts import build as build_ilostat_charts
else:
    from research_export import build_research_exports
    from project_brief import build as build_project_brief
    from source_guide import build as build_source_guide
    from build_bls_charts import build as build_bls_charts
    from build_ilostat_charts import build as build_ilostat_charts

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
BASE_SITE = REPO / "atlas_web" / "static"



def build_public_availability(data: Path, catalog: dict) -> dict:
    """Derived country/indicator coverage from only the approved public WDI rows."""
    from collections import defaultdict
    import math

    geos = catalog.get("geographies") or {}
    coverage = defaultdict(dict)
    for indicator in catalog["series"]:
        code = indicator["code"]
        raw = json.loads((data / "series" / (code + ".json")).read_text(encoding="utf-8"))
        if raw.get("code") != code or not isinstance(raw.get("rows"), list):
            raise ValueError("Public availability file does not match approved series: " + code)
        if len(raw["rows"]) != indicator["rows"]:
            raise ValueError("Public availability row count mismatch: " + code)
        grouped = defaultdict(list)
        for point in raw["rows"]:
            if (not isinstance(point, list) or len(point) != 3 or
                    point[0] not in geos or type(point[1]) is not int or
                    not isinstance(point[2], (int, float)) or isinstance(point[2], bool) or
                    not math.isfinite(point[2])):
                raise ValueError("Invalid verified WDI row in availability index: " + code)
            grouped[point[0]].append(point[1])
        for geography, years in grouped.items():
            coverage[geography][code] = [len(years), min(years), max(years)]
    return {
        "schema_version": 1,
        "public_edition": True,
        "source_run_id": catalog["source_run_id"],
        "availability_semantics": "original WDI numeric source-row counts and observed year bounds; not harmonized",
        "by_geography": {geo: dict(sorted(items.items()))
                         for geo, items in sorted(coverage.items())}
    }


def build(data=None, output=None, *, include_research=True):
    data = Path(data or HERE / "data")
    output = Path(output or HERE / "dist")
    catalog_path = data / "catalog.json"
    if not catalog_path.is_file():
        raise FileNotFoundError("Public verified WDI release not built; see atlas_public/README.md")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    summary = catalog["summary"]
    if (catalog.get("public_edition") is not True or
            catalog["source_run_id"] != 42581151428 or
            summary["observations"] != 300240 or
            summary["series"] != 32 or
            summary["geographies"] != 261 or
            summary["aggregate_codes"] != 44 or
            summary["country_or_territory_codes"] != 217 or
            summary["source_separated_numeric_checks_passed"] is not True):
        raise ValueError("Public source release has unexpected provenance or coverage")
    ids = {x["code"] for x in catalog["series"]}
    observed_files = {x.stem for x in (data / "series").glob("*.json")}
    if len(ids) != 32 or observed_files != ids:
        raise ValueError("Public release missing indicator files")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    for name in ("index.html", "style.css", "app.js"):
        shutil.copy2(BASE_SITE / name, output / name)
    shutil.copytree(data, output / "data")
    # Published BLS JOLTS values have their own immutable public provenance.
    # Only this previously-released ZIP is converted into individual chart JSON.
    bls_chart_release = build_bls_charts(data, output)
    ilostat_chart_release = build_ilostat_charts(data, output)
    # Source coverage is derived only from the already released WDI data.
    availability = build_public_availability(data, catalog)
    (output / "data" / "availability.json").write_text(
        json.dumps(availability, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8")

    shutil.copy2(HERE / "public_adapter.js", output / "public_adapter.js")
    # One public-safe source under version control produces both the project page
    # and its machine-readable context. Never publish private raw assets here.
    build_project_brief(destination=output / "project")
    build_source_guide(destination=output / "sources")
    if include_research:
        build_research_exports(data, output / "research")
    html = (output / "index.html").read_text(encoding="utf-8")
    nav_marker = '      </nav>\n      <button id="toggle-filters"'
    if html.count(nav_marker) != 1:
        raise ValueError("Sidebar project brief navigation placement changed")
    html = html.replace(nav_marker,
        '        <a class="nav-link" href="/project/" id="nav-project">'
        '<span class="nav-icon" aria-hidden="true">▤</span> Project brief &amp; decisions</a>\n'
        + nav_marker)
    if '<script src="/app.js" defer></script>' not in html:
        raise ValueError("Atlas UI script insertion point changed")
    html = html.replace('<script src="/app.js" defer></script>',
                        '<script src="/public_adapter.js" defer></script>\n'
                        '  <script src="/app.js" defer></script>')
    replacements = {
        "Atlas — Explore Economic Data": "World Economic Atlas — Public Economic Data",
        "ECONOMIC DATA</small>": "WORLD ECONOMIC DATA</small>",
        "GLOBAL ECONOMIC INTELLIGENCE · DATA FOUNDATION": "WORLD ECONOMIC ATLAS · PUBLIC DATA EDITION",
        "Explore the economic landscape": "Explore the world economy",
        "Visualize real observations from Atlas’s consolidated data warehouse. Browse indicators, compare geographies, explore time periods, and inspect underlying records.":
            "Explore verified World Bank annual data and U.S. Bureau of Labor Statistics monthly JOLTS charts. Choose a source, country and indicator to start.",
        "Private workspace": "Public data edition",
        "Queries your local Atlas SQLite snapshot. No database upload.":
            "Verified public World Bank and BLS JOLTS source data are chartable. Other providers remain under validation.",
        "CURRENT SNAPSHOT": "SOURCE RELEASE",
        "Immutable source versions": "WDI archived November 9, 2026",
        "Source-separated observations": "Verified WDI observations",
        "In the canonical observation table": "Public source edition · not harmonized",
        "Source families": "Published source families",
        "Preserved native definitions": "World Bank WDI + BLS JOLTS",
        "Additional native records": "Geographic areas",
        "Separate layer; may overlap": "Countries, territories and aggregates",
        "Indexed native sources": "Public WDI indicators",
        "Stored in the same SQLite": "Published original annual and monthly source series",
        "Source-native": "Verified WDI",
        "Search by series name or code": "Search by topic or indicator name",
        "Search country name or code": "Search by country or region name",
        "Choose up to eight geographic codes": "Choose up to eight countries or regions",
        "Choose a source, series, and geography to start.": "Choose a country and an indicator to begin.",
        "Select a series to display observations.": "Choose an indicator to display the data.",
        "Your world, in data.": "Explore your country in data.",
        "Atlas Economic Data · Read-only viewer · Underlying sources and vintages should be checked before interpreting results.":
            "World Economic Atlas · Source: World Bank World Development Indicators · CC BY 4.0 (subject to indicator-specific conditions). See /data/LICENSE-ATTRIBUTION.txt. Prepared from a verified 2026-11-09 Atlas source snapshot; not harmonized across providers.",
    }
    for original, updated in replacements.items():
        if original not in html:
            raise ValueError(f"Atlas UI changed; missing public adaptation text: {original}")
        html = html.replace(original, updated)
    attribution = (
        '<section class="public-attribution" aria-label="Data attribution">'
        '<strong>World Bank WDI · Verified November 9, 2026</strong>'
        '<span>32 indicators · 261 geographic areas, including 44 aggregates. '
        'These are historical source values, not a harmonized Atlas release. '
        '<a href="https://www.worldbank.org/en/about/legal/terms-of-use-for-datasets" '
        'target="_blank" rel="noopener noreferrer">Source and license</a>.</span>'
        '</section>'
    )
    marker = '<section class="kpis" aria-label="Warehouse summary">'
    if marker not in html:
        raise ValueError("Public attribution placement not found")
    html = html.replace(marker, attribution + "\n\n        " + marker)
    # Put original dataset citations within reach of public researchers.
    html = html.replace(
        '<section class="public-attribution" aria-label="Data attribution">',
        '<section class="public-attribution" aria-label="Data attribution">'
    )
    # The existing source banner is the natural entry point for terms/citations.
    html = html.replace(
        '>Source and license</a>.</span>',
        '>World Bank terms</a> · <a href="/sources/">How to cite and find other datasets</a>.</span>'
    )
    # Source choice belongs at the start of the charting flow.
    selector = (
        '<div class="quick-field quick-source-field">'
        '<label for="quick-source"><span class="quick-step">1</span> Choose a data source</label>'
        '<select id="quick-source" aria-label="Choose data source">'
        '<option value="wdi">World Bank — economic indicators</option>'
        + ('<option value="bls">U.S. Bureau of Labor Statistics — JOLTS</option>'
           if bls_chart_release else '') +
        ('<option value="ilostat">International Labour Organization — ILOSTAT</option>'
           if ilostat_chart_release else '') +
        '</select>'
        '<small>Select a source to see its actual countries and indicators.</small>'
        '</div>'
    )
    # Guide ILOSTAT selection by actual SDG topic before choosing the exact
    # classification series; no original codes are displayed in the UI.
    topic_selector = (
        '<div class="quick-field quick-topic-field" id="quick-topic-wrap" hidden>'
        '<label for="quick-topic">Choose a labour indicator</label>'
        '<select id="quick-topic" aria-label="Choose an ILOSTAT labour indicator">'
        '<option value="">All three ILOSTAT indicators</option>'
        '<option value="Informal employment">Informal employment</option>'
        '<option value="Average hourly earnings">Average hourly earnings</option>'
        '<option value="Unemployment rate">Unemployment rate</option>'
        '</select>'
        '<small>Then choose the specific statistical breakdown. '
        'Different breakdowns are not automatically comparable.</small>'
        '</div>'
    )
    quick_anchor = '<div class="quick-fields">'
    if html.count(quick_anchor)!=1:
        raise ValueError("Public source selector placement missing")
    html = html.replace(quick_anchor, quick_anchor + selector)
    # Insert a sibling between the country and indicator fields in the
    # existing public quick form, without breaking the original field markup.
    topic_anchor = '<div class="quick-field">\n                <label for="quick-indicator">'
    if html.count(topic_anchor) != 1:
        raise ValueError("Public indicator field placement changed")
    html = html.replace(topic_anchor, topic_selector + topic_anchor)
    html = html.replace('<span class="quick-step">1</span> Choose a country',
                        '<span class="quick-step">2</span> Choose a country')
    html = html.replace('<span class="quick-step">2</span> Choose an indicator',
                        '<span class="quick-step">3</span> Choose an indicator')
    html = html.replace('<span class="quick-step">3</span> Select years',
                        '<span class="quick-step">4</span> Select years')
    # BLS public download is shown only once a previously verified release
    # manifest and ZIP have been added to the published source directory.
    bls_download_available = (output / "data" / "bls" / "bls_jolts_manifest.json").is_file()
    if bls_download_available:
        bls_manifest = json.loads((output / "data" / "bls" / "bls_jolts_manifest.json").read_text())
        if (bls_manifest.get("release_type") != "public_bls_source_native_only" or
                bls_manifest.get("source_native_records") != 252710 or
                bls_manifest.get("series_count") != 830):
            raise ValueError("Unexpected BLS public release manifest")
    # Publish a truthful source selector alongside release-specific citations.
    # This section is derived solely from verified, actually-built public data.
    cards = [
        '<article class="source-access-card"><strong>World Bank — economic indicators</strong>'
        '<span class="source-availability public">Charts available</span>'
        '<p>32 indicators · 300,240 annual observations. Select a country and indicator above.</p></article>'
    ]
    if bls_chart_release:
        cards.append(
            '<article class="source-access-card"><strong>U.S. Bureau of Labor Statistics — JOLTS</strong>'
            '<span class="source-availability public">Charts available</span>'
            '<p>830 original monthly series · 252,710 numeric observations.</p>'
            '<a href="/sources/">BLS download now available · Explore BLS source and citations</a>'
            '</article>'
        )
    if ilostat_chart_release:
        cards.append(
            '<article class="source-access-card"><strong>International Labour Organization — ILOSTAT</strong>'
            '<span class="source-availability public">Charts available</span>'
            '<p>Informal employment, average hourly earnings and unemployment rates; '
            '149,148 verified annual numeric observations across source-defined breakdowns.</p>'
            '<a href="/data/ilostat/ilostat_sdg_labour_source_native.csv.zip" download>'
            'Download original ILOSTAT data and citation file</a> · '
            '<a href="/sources/">ILOSTAT reuse and source citations</a>'
            '</article>'
        )
    cards.append(
        '<article class="source-access-card"><strong>Other Atlas provider collections</strong>'
        '<span class="source-availability private">Private research database</span>'
        '<p>FRED, Eurostat, WID and additional datasets are not yet validated '
        'for public charting; access original provider datasets or use the local explorer.</p>'
        '<a href="/sources/">Find official sources and citation instructions</a> · '
        '<a href="https://github.com/diegoduque16/Global-economic-policy-tool/blob/main/atlas_web/README.md" '
        'target="_blank" rel="noopener noreferrer">How to access the private explorer</a>'
        '</article>'
    )
    source_access = (
        '<section class="source-access" id="data-access" aria-label="Where to find Atlas data">'
        '<div class="source-access-heading"><span>DATA AVAILABILITY</span>'
        '<h2>Choose a verified data source</h2>'
        '<p>Chart source-native observations directly. Only approved datasets '
        'are selectable, with provider attribution and definitions preserved.</p></div>'
        '<div class="source-access-grid">' + "".join(cards) + '</div>'
        '<p class="source-access-note">Original series are not automatically '
        'harmonized. Missing values remain missing; publication does not establish '
        'cross-provider statistical comparability.</p></section>'
    )
    html = html.replace(marker, source_access + "\\n        " + marker)
    project_banner = (
        '<section class="project-link-banner" aria-label="Atlas current direction">'
        '<div><span>PROJECT DIRECTION</span><strong>Where Atlas stands</strong>'
        '<p>Completion-first data collection · verified results · decisions and next steps.</p></div>'
        '<a href="/project/">Read project brief &amp; decision log →</a>'
        '</section>'
    )
    html = html.replace(marker, project_banner + "\n        " + marker)
    if include_research:
        hook = '<section class="catalog-overview" id="catalog-overview" aria-label="Source coverage">'
        if hook not in html:
            raise ValueError("Research download placement in public page missing")
        download = (
            '<section class="research-downloads" aria-label="Research datasets">'
            '<div><span class="research-eyebrow">FOR RESEARCHERS</span>'
            '<h2>Download verified country–year data</h2>'
            '<p>Clean source-preserving data, coverage reports, and provenance for reproducible analysis. '
            'Missing values remain missing; aggregates are separate from countries.</p></div>'
            '<div class="research-links">'
            '<a href="/research/atlas_wdi_research.sqlite.zip" download>Research database (SQLite ZIP) ↓</a>'
            '<a href="/research/wdi_country_year_wide.csv" download>Country–year dataset (wide) ↓</a>'
            '<a href="/research/wdi_country_year_long.csv" download>Research dataset (long) ↓</a>'
            '<a href="/research/wdi_variable_dictionary.csv" download>Variable dictionary ↓</a>'
            '<a href="/research/research_manifest.json" download>Provenance &amp; checksums ↓</a>'
            '</div></section>'
        )
        html = html.replace(hook, download + "\n        " + hook)
    (output / "index.html").write_text(html, encoding="utf-8")
    # The only change to the existing local dashboard JS is a public-specific
    # read-only export hook; local server mode retains its original behavior.
    client = (output / "app.js").read_text(encoding="utf-8")
    original = ' const url=new URL("/api/export",location.origin);'
    addition = (
        ' if(typeof window.ATLAS_PUBLIC_DOWNLOAD==="function"){'
        'window.ATLAS_PUBLIC_DOWNLOAD(args);return;}\n'
        ' const url=new URL("/api/export",location.origin);'
    )
    if original not in client:
        raise ValueError("Original Atlas CSV export hook changed")
    client = client.replace(original, addition)
    # The shared explorer now renders human-readable indicator names directly.
    original_stats = (
        ' $("stat-native").textContent=fmt(x.native_records);\n'
        ' $("stat-native-sources").textContent=fmt(x.native_sources);'
    )
    revised_stats = (
        ' $("stat-native").textContent=fmt(x.public_edition?x.public_geographies:x.native_records);\n'
        ' $("stat-native-sources").textContent=fmt(x.public_edition?x.public_series:x.native_sources);'
    )
    if original_stats not in client:
        raise ValueError("Atlas overview card format changed")
    client = client.replace(original_stats, revised_stats)
    (output / "app.js").write_text(client, encoding="utf-8")
    # Public visitors get one primary country/indicator form. Comparison
    # controls are available on demand rather than duplicated on screen.
    html = html.replace(
        'Advanced filters and comparisons ▾',
        'Compare countries &amp; advanced options ▾'
    )
    (output / "index.html").write_text(html, encoding="utf-8")
    css = (output / "style.css").read_text(encoding="utf-8")
    css += (
        "\n.public-attribution{display:flex;flex-wrap:wrap;gap:7px 14px;"
        "background:#edf4fe;border:1px solid #d8e5f5;border-radius:12px;"
        "padding:15px 19px;margin:0 0 23px;color:#46668f;font-size:11px;line-height:1.6}"
        ".public-attribution strong{color:#214d86}"
        ".public-attribution span{flex:1;min-width:210px}"
        ".public-attribution a{color:#245eb8;text-decoration:underline}\n"
    )
    css += (
        "\n.research-downloads{order:4;display:flex;align-items:center;justify-content:space-between;"
        "gap:24px;margin:4px 0 24px;border:1px solid #d5e4f5;background:#f0f6ff;"
        "padding:23px 26px;border-radius:15px}"
        ".research-downloads h2{font-size:19px;margin:8px 0;color:#24466f}"
        ".research-eyebrow{font-size:10px;letter-spacing:1.3px;font-weight:800;color:#416cb3}"
        ".research-downloads p{font-size:12px;line-height:1.55;color:#657e9c;max-width:650px;margin:0}"
        ".research-links{display:grid;grid-template-columns:1fr 1fr;gap:8px;min-width:370px}"
        ".research-links a{display:block;text-decoration:none;padding:11px 12px;background:#fff;"
        "color:#24568e;font-size:11px;font-weight:700;border:1px solid #d4e1f3;border-radius:8px}"
        ".research-links a:hover{background:#e8f1fc}"
        "@media(max-width:1170px){.research-downloads{flex-direction:column;align-items:stretch}"
        ".research-links{min-width:0}}"
        "@media(max-width:540px){.research-downloads{padding:18px}.research-links{grid-template-columns:1fr}}\n"
    )
    css += (
        "\n.primary-nav a.nav-link{text-decoration:none}"
        ".project-link-banner{display:flex;align-items:center;justify-content:space-between;"
        "gap:18px;background:#fff;border:1px solid #d8e5f5;border-radius:12px;"
        "padding:20px 23px;margin:0 0 23px}"
        ".project-link-banner span{display:block;color:#3f70b5;font-size:10px;font-weight:800;"
        "letter-spacing:1.1px;margin-bottom:6px}"
        ".project-link-banner strong{font-size:17px;color:#203f68}"
        ".project-link-banner p{margin:6px 0 0;color:#607a98;font-size:12px;line-height:1.6}"
        ".project-link-banner a{white-space:nowrap;text-decoration:none;background:#eaf1ff;"
        "color:#245ba2;font-weight:750;padding:12px 15px;border-radius:8px;font-size:12px}"
        ".project-link-banner a:hover{background:#dce9ff}"
        "@media(max-width:720px){.project-link-banner{flex-direction:column;align-items:stretch}"
        ".project-link-banner a{white-space:normal;text-align:center}}\n"
    )
    css += (
        "\\n/* Simplified public navigation: the country-first explorer is primary. */"
        ".side .mobile-toggle{display:block;width:100%;margin:0 0 14px;"
        "border:1px solid #426184;background:#243d60;color:#eff6ff;"
        "border-radius:9px;padding:12px 10px;font-size:12px;font-weight:750}"
        ".side .sidebar-filters{display:none}"
        ".side.show-filters .sidebar-filters{display:block}"
        ".source-access{order:5;background:#fff;border:1px solid #dce6f5;"
        "border-radius:14px;padding:22px 25px;margin:0 0 24px}"
        ".source-access-heading span{color:#356abd;font-size:10px;"
        "letter-spacing:1px;font-weight:800}"
        ".source-access h2{font-size:20px;margin:7px 0}"
        ".source-access-heading p,.source-access-note{color:#60758f;"
        "font-size:12px;line-height:1.65}"
        ".source-access-grid{display:grid;grid-template-columns:1fr 1fr;"
        "gap:14px;margin:18px 0}"
        ".source-access-card{padding:18px;border:1px solid #e0e8f3;"
        "background:#f8fbff;border-radius:12px}"
        ".source-access-card strong{display:block;font-size:14px;margin-bottom:8px}"
        ".source-access-card p{color:#586d86;font-size:12px;line-height:1.6}"
        ".source-access-card a{display:inline-block;font-size:12px;font-weight:700;"
        "color:#245ba2;text-decoration:underline}"
        ".source-availability{display:inline-block;font-size:10px;font-weight:750;"
        "padding:4px 8px;border-radius:20px}"
        ".source-availability.public{color:#226b50;background:#e9f8f0}"
        ".source-availability.private{color:#5b6473;background:#e9edf3}"
        "@media(max-width:720px){.source-access-grid{grid-template-columns:1fr}"
        ".source-access{padding:18px}}\\n"
    )
    css += (
        "\\n.public-citation-panel{background:#f3f7fd;border:1px solid #dce6f4;"
        "border-radius:10px;padding:17px;margin-top:18px;color:#2b4361}"
        ".public-citation-panel strong{font-size:13px}"
        ".public-citation-panel p{font-size:12px;line-height:1.7;overflow-wrap:anywhere}"
        ".citation-actions{display:flex;flex-wrap:wrap;align-items:center;gap:12px}"
        ".citation-actions a{font-size:11px;color:#245db1;text-decoration:underline}"
        ".copy-source-citation{font-size:11px;font-weight:750;padding:9px 13px;"
        "border:1px solid #bed2ed;border-radius:8px;color:#245db1;background:#fff}"
        ".copy-source-citation:hover{background:#e7effb}\\n"
    )
    css += (
        "\\n.quick-source-field{grid-column:1/-1;max-width:580px;"
        "background:#f2f7ff;border:1px solid #d8e7fa;border-radius:12px;"
        "padding:14px 16px}"
        ".quick-source-field select{width:100%;font-weight:750}"
        "@media(max-width:800px){.quick-source-field{max-width:none}}\\n"
    )
    css += (
        "\\n.quick-topic-field[hidden]{display:none!important}"
        ".quick-topic-field{grid-column:1/-1;max-width:580px;"
        "border:1px solid #dce7f6;border-radius:10px;padding:12px 15px;"
        "background:#f7faff}"
        ".quick-topic-field select{width:100%}"
        "@media(max-width:800px){.quick-topic-field{max-width:none}}\\n"
    )
    (output / "style.css").write_text(css, encoding="utf-8")
    # Fingerprint exact rendered public UI assets after *all* modifications.
    # Query-string versioning protects browsers/CDNs from serving a previous
    # script after Pages deploys the latest GitHub main build.
    html = (output / "index.html").read_text(encoding="utf-8")
    assets = {
        "/style.css": output / "style.css",
        "/public_adapter.js": output / "public_adapter.js",
        "/app.js": output / "app.js",
    }
    for route, local_file in assets.items():
        fingerprint = hashlib.sha256(local_file.read_bytes()).hexdigest()[:16]
        old = 'href="' + route + '"' if route.endswith(".css") else 'src="' + route + '"'
        new = old[:-1] + "?v=" + fingerprint + '"'
        if html.count(old) != 1:
            raise ValueError("Expected exactly one public UI asset reference: " + route)
        html = html.replace(old, new)
    (output / "index.html").write_text(html, encoding="utf-8")
    (output / "_headers").write_text(
        "/*\n"
        "  X-Content-Type-Options: nosniff\n"
        "  Referrer-Policy: strict-origin-when-cross-origin\n"
        "  Permissions-Policy: camera=(), microphone=(), geolocation=()\n"
        "  Content-Security-Policy: default-src 'self'; img-src 'self' data:; "
        "style-src 'self'; script-src 'self'; connect-src 'self'; "
        "base-uri 'none'; object-src 'none'; frame-ancestors 'none'\n"
        "\n"
        "/data/*\n"
        "  Cache-Control: public, max-age=3600\n",
        encoding="utf-8")
    for forbidden in ("warehouse.sqlite", "rclone.conf", ".env"):
        if any(p.name == forbidden for p in output.rglob("*")):
            raise ValueError("Private database or credential accidentally published")
    return {"output": str(output), "public_records": summary["observations"],
            "published_series": len(ids), "public_edition": True}


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
