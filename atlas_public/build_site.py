#!/usr/bin/env python3
"""Build a static Cloudflare Pages site from the existing Atlas explorer UI.

The published data/ directory contains ONLY the separately approved, verified
World Bank WDI source snapshot; no SQLite, secrets, raw API or other source
families are placed in the public assets. This is not a live private data API.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil

if __package__:
    from .research_export import build_research_exports
else:
    from research_export import build_research_exports

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
BASE_SITE = REPO / "atlas_web" / "static"


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
    shutil.copy2(HERE / "public_adapter.js", output / "public_adapter.js")
    if include_research:
        build_research_exports(data, output / "research")
    html = (output / "index.html").read_text(encoding="utf-8")
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
    (output / "style.css").write_text(css, encoding="utf-8")
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
