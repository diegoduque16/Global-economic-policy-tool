"""Public source access/citation guide for World Economic Atlas.

This is a guide to original *official* source portals, not a representation
that their private stored Atlas records are already publicly hosted.
"""
from __future__ import annotations

from html import escape
import hashlib
import json
from pathlib import Path

PROVIDERS = (
    {
        "name": "World Bank — World Development Indicators",
        "access": "Published in Atlas",
        "description": "The approved Atlas public edition contains 32 WDI indicators. Additional collected WDI indicators are not yet in the public edition.",
        "source": "https://data.worldbank.org/indicator",
        "terms": "https://www.worldbank.org/ext/en/legal/terms-conditions/datasets",
        "license": "Generally CC BY 4.0; third-party indicator exceptions may apply",
        "citation": "The World Bank. World Development Indicators, [indicator title]. Retrieved [date]. Original indicator: [link]. Accessed through World Economic Atlas, [Atlas release date].",
    },
    {
        "name": "International Labour Organization — ILOSTAT",
        "access": "Original open data portal",
        "description": "Atlas holds original ILOSTAT source tables in its private data archive. The public ILOSTAT portal offers direct downloads; Atlas integration is not yet published.",
        "source": "https://ilostat.ilo.org/data/",
        "terms": "https://www.ilo.org/global/copyright/lang--en/index.htm",
        "license": "ILO datasets and metadata available as of 3 May 2023 generally CC BY 4.0; restricted microdata and exceptions excluded",
        "citation": "International Labour Organization (ILO). ILOSTAT, [table and indicator title], [reference area and period]. Retrieved [date] from [official table URL].",
    },
    {
        "name": "U.S. Bureau of Labor Statistics",
        "access": "Original open data portal",
        "description": "Official US labour-market data are publicly available, including JOLTS. Atlas has an additional source-native private collection not yet published on this site.",
        "source": "https://www.bls.gov/data/",
        "terms": "https://www.bls.gov/bls/linksite.htm",
        "license": "BLS government publications generally public domain; third-party imagery exceptions",
        "citation": "U.S. Bureau of Labor Statistics. [dataset and series title], [geography and period]. Retrieved [date] from [official series URL].",
    },
    {
        "name": "Eurostat",
        "access": "Original open data portal",
        "description": "Eurostat publishes official European datasets for direct reuse. Source-specific copyright and third-party restrictions must be checked before Atlas republishes a source.",
        "source": "https://ec.europa.eu/eurostat/data/database",
        "terms": "https://ec.europa.eu/eurostat/help/copyright-notice",
        "license": "Generally reusable with attribution; exceptions apply to designated datasets or other materials",
        "citation": "Eurostat. [dataset code and official title], [geography and period]. Extracted [date] from [Eurostat dataset link].",
    },
    {
        "name": "Federal Reserve Economic Data — FRED",
        "access": "Original source portal; rehosting under review",
        "description": "FRED aggregates series from many original producers. Citation does not by itself authorize redistributing every FRED-hosted series; check provider and per-series copyright.",
        "source": "https://fred.stlouisfed.org/",
        "terms": "https://fred.stlouisfed.org/legal/terms/",
        "license": "Series-specific; some data copyrighted, and redistribution may require permission",
        "citation": "[Original producer]. [series title], retrieved from FRED, Federal Reserve Bank of St. Louis; [series URL], accessed [date].",
    },
    {
        "name": "World Inequality Database — WID.world",
        "access": "Original source portal; rehosting under review",
        "description": "WID offers publicly accessible inequality estimates and methodology. Verify applicable terms for each archived vintage and source before bulk republication.",
        "source": "https://wid.world/data/",
        "terms": "https://wid.world/data/",
        "license": "Reuse/redistribution terms to verify for the specific edition",
        "citation": "World Inequality Database (WID.world). [indicator and distribution definition], [country, period and database vintage]. Retrieved [date] from [official dataset URL].",
    },
)


def build(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    cards = []
    bls_manifest_path = destination.parent / "data" / "bls" / "bls_jolts_manifest.json"
    bls_public = None
    if bls_manifest_path.is_file():
        bls_public = json.loads(bls_manifest_path.read_text(encoding="utf-8"))
        archive = bls_manifest_path.parent / "bls_jolts_source_native.csv.zip"
        if (bls_public.get("release_type") != "public_bls_source_native_only" or
            bls_public.get("source_native_records") != 252710 or
            bls_public.get("series_count") != 830 or
            not archive.is_file() or
            hashlib.sha256(archive.read_bytes()).hexdigest() != bls_public.get("csv_zip_sha256")):
            raise ValueError("Unverified BLS public release in Atlas data directory")
    ilo_manifest_path = destination.parent / "data" / "ilostat" / "ilostat_sdg_manifest.json"
    ilo_public = None
    if ilo_manifest_path.is_file():
        ilo_public = json.loads(ilo_manifest_path.read_text(encoding="utf-8"))
        archive = ilo_manifest_path.parent / "ilostat_sdg_labour_source_native.csv.zip"
        if (ilo_public.get("release_type") != "public_ilostat_sdg_annual_source_native"
            or ilo_public.get("source_native_records") != 153116
            or ilo_public.get("finite_numeric_records") != 149148
            or ilo_public.get("original_table_count") != 3
            or not archive.is_file()
            or hashlib.sha256(archive.read_bytes()).hexdigest() != ilo_public.get("file_sha256")):
            raise ValueError("Unverified public ILOSTAT release in Atlas source directory")
    for record in PROVIDERS:
        bls_download = (
            '<p><a href="/data/bls/bls_jolts_source_native.csv.zip" download>'
            'Download verified Atlas BLS JOLTS research CSV (ZIP)</a> · '
            '<a href="/data/bls/bls_jolts_manifest.json">Source manifest and checksums</a></p>'
            if record["name"] == "U.S. Bureau of Labor Statistics" and bls_public else ""
        )
        is_ilo_public = record["name"] == "International Labour Organization — ILOSTAT" and ilo_public
        ilo_download = (
            '<p><a href="/data/ilostat/ilostat_sdg_labour_source_native.csv.zip" download>'
            'Download ILOSTAT annual source records with citations</a> · '
            '<a href="/data/ilostat/ilostat_sdg_manifest.json">Verified source manifest</a></p>'
            if is_ilo_public else ""
        )
        is_bls_public = record["name"] == "U.S. Bureau of Labor Statistics" and bls_public
        access = ("Atlas charts + download + original portal"
                  if is_bls_public or is_ilo_public else record["access"])
        description = (
            "Official annual ILOSTAT SDG labour source-native observations are "
            "chartable in Atlas and downloadable with exact original classifications, "
            "missingness and CC BY 4.0 citation instructions. Other ILOSTAT source "
            "tables still require publication validation."
            if is_ilo_public else
            "The source-native JOLTS values can be charted or downloaded on Atlas "
            "with original series keys and a citation file. Other BLS collections "
            "may remain unpublished." if is_bls_public else record["description"]
        )
        cards.append(
            '<article class="source-card"><h2>' + escape(record["name"]) + '</h2>'
            '<p class="availability">' + escape(access) + '</p>'
            '<p>' + escape(description) + '</p>'
            '<p><strong>Reuse terms:</strong> ' + escape(record["license"]) + '</p>'
            '<p><strong>How to cite:</strong> ' + escape(record["citation"]) + '</p>'
            + bls_download + ilo_download +
            '<p class="links"><a href="' + escape(record["source"], quote=True) +
            '" rel="noopener noreferrer" target="_blank">Explore official dataset</a> · '
            '<a href="' + escape(record["terms"], quote=True) +
            '" rel="noopener noreferrer" target="_blank">License / terms</a></p></article>'
        )
    hosted_note = (
        "Atlas charts World Bank annual values, BLS monthly jobs statistics, "
        "and ILOSTAT annual SDG labour indicators; each source retains separate "
        "definitions, provenance and attribution. "
        if ilo_public else
        "The main Atlas explorer charts verified World Bank annual observations "
        "and U.S. BLS JOLTS monthly observations. The BLS research ZIP includes "
        "original-source citations; other providers remain on their own portals."
        if bls_public else
        "The main Atlas explorer currently hosts only the approved World Bank WDI "
        "release. Other providers below are linked to their original public portals."
    )
    html = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Find and cite economic data | World Economic Atlas</title>
<style>
:root{color-scheme:light;font:16px system-ui,-apple-system,sans-serif;color:#19304f;background:#f4f7fc}
*{box-sizing:border-box}body{margin:0}.wrap{max-width:1060px;margin:auto;padding:28px 20px 70px}
a{color:#205cae}a:focus-visible{outline:3px solid #91b4f8}
nav{margin-bottom:34px;font-size:14px}h1{font-size:clamp(28px,4vw,42px);letter-spacing:-1.3px}
.lead{max-width:800px;color:#526681;line-height:1.7}
.notice{padding:17px 19px;border-radius:12px;background:#eaf1fc;margin:24px 0;line-height:1.65}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-top:28px}
.source-card{border:1px solid #dce5f2;background:#fff;padding:23px;border-radius:14px}
.source-card h2{margin:0 0 10px;font-size:19px}
.source-card p{color:#4f637d;line-height:1.65;font-size:13px}
.source-card strong{color:#233f61}.source-card .availability{font-weight:750;color:#225a89}
.links{margin-bottom:0}.tip{margin:30px 0;color:#526681;line-height:1.7}
@media(max-width:700px){.grid{grid-template-columns:1fr}}
</style></head>
<body><main class="wrap"><nav><a href="/">← Back to Atlas</a></nav>
<h1>Find and cite economic data</h1>
<p class="lead">Explore official economic datasets and cite the exact source used.
Atlas keeps original series identifiers, observation dates, geographical definitions,
source vintages and missing values for reproducibility.</p>
<div class="notice"><strong>What is hosted here?</strong> """ + escape(hosted_note) + """
Source-native records are not automatically comparable across sources. A source being
publicly readable does not necessarily grant permission for bulk rehosting.</div>
<section class="grid" aria-label="Official economic data providers">""" + "".join(cards) + """</section>
<section class="tip"><h2>What to include when citing Atlas data</h2>
<p>For a specific indicator, record its <strong>original producer, dataset or table title,
series identifier (in the citation or research metadata), geography, reference period,
units, source version, retrieval date, and a direct original-source link</strong>.
If you downloaded a verified Atlas edition, additionally name <strong>World Economic Atlas,
the public edition date and its provenance manifest</strong>. Attribute the original provider,
not just Atlas. Verify additional restrictions for any third-party series.</p>
<p>Do not describe these unharmonized original series as an Atlas-estimated or
causally identified policy effect. Different vintages, classifications and source units may
not be comparable without further reconciliation.</p></section>
</main></body></html>"""
    (destination / "index.html").write_text(html, encoding="utf-8")
