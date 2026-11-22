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
else:
    from research_export import build_research_exports
    from project_brief import build as build_project_brief

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
    # Source coverage is derived only from the already released WDI data.
    availability = build_public_availability(data, catalog)
    (output / "data" / "availability.json").write_text(
        json.dumps(availability, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8")

    shutil.copy2(HERE / "public_adapter.js", output / "public_adapter.js")
    # One public-safe source under version control produces both the project page
    # and its machine-readable context. Never publish private raw assets here.
    build_project_brief(destination=output / "project")
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
            "Explore 300,240 verified World Bank observations from Atlas’s source archive. Other consolidated providers will follow once verified for public distribution.",
        "Private workspace": "Public data edition",
        "Queries your local Atlas SQLite snapshot. No database upload.":
            "Only licensed public WDI values are published. The private Atlas warehouse stays private.",
        "CURRENT SNAPSHOT": "SOURCE RELEASE",
        "Immutable source versions": "WDI archived November 9, 2026",
        "Source-separated observations": "Verified WDI observations",
        "In the canonical observation table": "Public source edition · not harmonized",
        "Source families": "Published source families",
        "Preserved native definitions": "World Bank WDI (selected)",
        "Additional native records": "Geographic areas",
        "Separate layer; may overlap": "Countries, territories and aggregates",
        "Indexed native sources": "Public WDI indicators",
        "Stored in the same SQLite": "Published annual source series",
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
    # Make the public/private availability boundary explicit and navigable.
    # No unpublished datasets or private links/assets are copied into Pages.
    source_access = (
        '<section class="source-access" id="data-access" aria-label="Where to find Atlas data">'
        '<div class="source-access-heading"><span>DATA AVAILABILITY</span>'
        '<h2>What can I explore?</h2>'
        '<p>Start with the verified public World Bank indicators. Other collections '
        'are integrated into the private Atlas research database, not this public website.</p></div>'
        '<div class="source-access-grid">'
        '<article class="source-access-card"><strong>World Bank indicators</strong>'
        '<span class="source-availability public">Explore here</span>'
        '<p>32 published indicators · 300,240 observations. Choose a country '
        'and indicator above to see the data.</p></article>'
        '<article class="source-access-card"><strong>ILOSTAT, US labour statistics, '
        'FRED, Eurostat and inequality data</strong>'
        '<span class="source-availability private">Private research database</span>'
        '<p>Source-native research records have been collected, but these provider '
        'collections are not currently available through this public website.</p>'
        '<a href="https://github.com/diegoduque16/Global-economic-policy-tool/blob/main/atlas_web/README.md" '
        'target="_blank" rel="noopener noreferrer">How to access the private explorer</a>'
        '</article></div>'
        '<p class="source-access-note">Private source records are not yet harmonized '
        'or approved for public release. Atlas does not merge them automatically.</p>'
        '</section>'
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
