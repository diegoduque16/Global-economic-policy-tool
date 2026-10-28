#!/usr/bin/env python3
"""Atlas 2: discover World Bank indicator metadata and Eurostat dataset/table catalog.

No economic observation values are downloaded; catalog metadata is not evidence of
research-ready data, licensing permission, or actual observed coverage.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

WB_BASE = "https://api.worldbank.org/v2/indicator"
EU_TOC = "https://ec.europa.eu/eurostat/api/dissemination/catalogue/toc/txt?lang=en"
MAX_BYTES = 25_000_000
WB_PER_PAGE = 1000
WB_MIN_UNIQUE = 3000
TOC_COLS = ("title", "code", "type", "last update of data",
            "last table structure change", "data start", "data end", "values")
PROVIDERS = {"worldbank", "eurostat"}

def digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()

def retrieve(url: str, *, attempts=3) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc not in ("api.worldbank.org", "ec.europa.eu"):
        raise ValueError("Unapproved source URL")
    last = None
    for n in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": "EconomicDataAtlas/0.1 (public metadata research)",
                                            "Accept": "application/json,text/plain,*/*"})
            with urlopen(request, timeout=55) as response:
                final = urlparse(response.geturl())
                if final.scheme != "https" or final.netloc not in ("api.worldbank.org", "ec.europa.eu"):
                    raise ValueError("Unexpected redirect host")
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise ValueError("Catalog response unexpectedly large")
                if not body:
                    raise ValueError("Empty catalog response")
                return body
        except HTTPError as exc:
            last = exc
            if exc.code not in (408, 429, 500, 502, 503, 504):
                raise
        except (URLError, TimeoutError) as exc:
            last = exc
        if n + 1 < attempts:
            time.sleep(2 ** n)
    raise RuntimeError(f"Provider retrieval failed after {attempts} attempts: {url}") from last

def parse_wb_page(raw: bytes, expected_page: int, expected_pages=None, expected_total=None):
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid World Bank JSON") from exc
    if not (isinstance(payload, list) and len(payload) == 2
            and isinstance(payload[0], dict) and isinstance(payload[1], list)):
        raise ValueError("World Bank returned an unexpected or API-error payload")
    meta, items = payload
    try:
        page, pages, total = int(meta["page"]), int(meta["pages"]), int(meta["total"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid World Bank pagination metadata") from exc
    if page != expected_page or not 1 <= pages <= 100 or not total >= 0 or page > pages:
        raise ValueError("World Bank pagination or page sequence invalid")
    if expected_pages is not None and (pages != expected_pages or total != expected_total):
        raise ValueError("World Bank reported unstable pagination totals")
    if not items or len(items) > WB_PER_PAGE:
        raise ValueError("World Bank page empty or over capacity")
    result = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            raise ValueError("World Bank indicator missing stable code")
        src = item.get("source")
        if not isinstance(src, dict) or src.get("id") is None:
            raise ValueError("World Bank indicator source missing")
        result.append({
            "provider": "worldbank", "provider_catalog_type": "indicator",
            "source_key": str(src["id"]) + ":" + item["id"].strip(),
            "source_code": item["id"].strip(), "title": str(item.get("name") or ""),
            "database_id": str(src["id"]), "database_name": str(src.get("value") or ""),
            "unit_source": str(item.get("unit") or ""),
            "description_source": str(item.get("sourceNote") or "")[:2000],
            "start_period_source": "", "end_period_source": "",
            "last_update_source": "", "observation_values_reported_source": "",
            "evidence_status": "OFFICIAL_CATALOG_METADATA_NOT_INGESTED",
        })
    return pages, total, result

def discover_worldbank(fetch=retrieve):
    """Inventory distinct official indicator/source identifiers without hiding API repeats.

    The API's `total` is the *number of returned entries*, which can include
    duplicate (source, indicator) identifiers. We preserve raw-occurrence counts
    and exact content hashes, reject contradictory duplicate metadata, and emit
    one search entry per identical identifier. A duplicate on different pages is
    recorded as a possible pagination-stability caveat, not evidence of coverage.
    """
    all_rows, hashes, seen = [], [], {}
    raw_occurrences = 0
    repeats = []
    page = 1
    pages = total = None
    while True:
        url = WB_BASE + f"?format=json&per_page={WB_PER_PAGE}&page={page}"
        raw = fetch(url)
        hashes.append({"page": page, "url": url, "response_sha256": digest(raw), "bytes": len(raw)})
        new_pages, new_total, rows = parse_wb_page(raw, page, pages, total)
        if pages is None:
            pages, total = new_pages, new_total
        raw_occurrences += len(rows)
        for r in rows:
            key = r["source_key"]
            if key in seen:
                prior, first_page = seen[key]
                if prior != r:
                    raise ValueError(f"Conflicting World Bank metadata for duplicated indicator: {key}")
                repeats.append({"source_key": key, "original_page": first_page,
                                "repeated_page": page})
                continue
            seen[key] = (r, page)
            all_rows.append(r)
        if page == pages:
            break
        page += 1
    # Raw API total is NOT necessarily the count of distinct (source, indicator) keys.
    if raw_occurrences != total:
        raise ValueError(f"World Bank page count mismatch: {raw_occurrences} raw entries versus reported {total}")
    if len(all_rows) < WB_MIN_UNIQUE:
        raise ValueError(f"World Bank distinct catalog coverage unexpectedly small: {len(all_rows)}")
    repeated_cross_pages = sum(x['original_page'] != x['repeated_page'] for x in repeats)
    return all_rows, {
        "source_url": WB_BASE, "pages": pages,
        "source_reported_total_entries_including_duplicates": total,
        "raw_indicator_occurrences": raw_occurrences,
        "distinct_source_indicator_keys": len(all_rows),
        "identical_duplicate_occurrences": len(repeats),
        "cross_page_duplicate_occurrences": repeated_cross_pages,
        "duplicate_identifiers_sample": repeats[:25],
        "possible_pagination_instability": repeated_cross_pages > 0,
        "duplicate_content_conflicts_rejected": True,
        "responses": hashes, "catalog_entries": len(all_rows),
    }

def parse_eurostat_toc(raw: bytes):
    try:
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig")), delimiter="\t",
                            quotechar='"', strict=True)
        header = next(reader)
    except (UnicodeError, csv.Error, StopIteration) as exc:
        raise ValueError("Could not parse Eurostat table of contents") from exc
    if tuple(h.strip().lower() for h in header) != TOC_COLS:
        raise ValueError(f"Eurostat catalog schema changed: {header}")
    rows, seen, duplicate_nodes = [], set(), 0
    for line_no, record in enumerate(reader, start=2):
        if not record or (len(record) == 1 and not record[0].strip()):
            continue
        if len(record) > 8 or len(record) < 3:
            raise ValueError(f"Eurostat malformed catalog row {line_no}")
        values = (record + [""] * (8 - len(record)))[:8]
        title, code, kind, updated, changed, start, end, size = (x.strip() for x in values)
        if kind not in ("dataset", "table", "folder"):
            raise ValueError(f"Eurostat unexpected record type at row {line_no}: {kind!r}")
        if kind == "folder":
            continue
        if not code or not title or not re.fullmatch(r"[a-zA-Z0-9_.-]+", code):
            raise ValueError(f"Eurostat missing/bad dataset code at row {line_no}")
        key = kind + ":" + code
        if key in seen:
            duplicate_nodes += 1
            continue  # Same node can appear in different TOC folders; count once.
        seen.add(key)
        if size and not re.fullmatch(r"[0-9]+", size):
            raise ValueError(f"Eurostat invalid advertised value count at row {line_no}")
        rows.append({
            "provider": "eurostat", "provider_catalog_type": kind,
            "source_key": key, "source_code": code, "title": title,
            "database_id": "ESTAT", "database_name": "Eurostat disseminated catalog",
            "unit_source": "", "description_source": "",
            "start_period_source": start, "end_period_source": end,
            "last_update_source": updated, "observation_values_reported_source": size,
            "evidence_status": "OFFICIAL_CATALOG_METADATA_NOT_INGESTED",
        })
    if not rows:
        raise ValueError("No Eurostat dataset/table records discovered")
    return rows, duplicate_nodes

def discover_eurostat(fetch=retrieve):
    raw = fetch(EU_TOC)
    rows, duplicates = parse_eurostat_toc(raw)
    if len(rows) < 1000:
        raise ValueError(f"Eurostat TOC too small: {len(rows)}")
    return rows, {"source_url": EU_TOC, "source_sha256": digest(raw), "source_bytes": len(raw),
                  "catalog_entries": len(rows), "duplicate_toc_placements": duplicates,
                  "datasets": sum(x["provider_catalog_type"] == "dataset" for x in rows),
                  "tables": sum(x["provider_catalog_type"] == "table" for x in rows)}

def export(out: Path, selected=("worldbank", "eurostat"), wb_fetch=retrieve, eu_fetch=retrieve):
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError("Refuse to overwrite populated output directory")
    out.mkdir(parents=True, exist_ok=True)
    reports = {}; rows = []
    for name in selected:
        if name not in PROVIDERS or name in reports:
            raise ValueError("Unexpected/duplicate provider")
        current, report = (discover_worldbank(wb_fetch) if name == "worldbank"
                           else discover_eurostat(eu_fetch))
        rows.extend(current); reports[name] = report
    if not rows:
        raise ValueError("No catalog entries extracted")
    fieldnames = ("provider", "provider_catalog_type", "source_key", "source_code",
                  "title", "database_id", "database_name", "unit_source",
                  "description_source", "start_period_source", "end_period_source",
                  "last_update_source", "observation_values_reported_source",
                  "evidence_status")
    with (out / "discovered_series_metadata.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader(); writer.writerows(rows)
    summary = {"status": "OFFICIAL_METADATA_DISCOVERED_NO_ECONOMIC_OBSERVATIONS_DOWNLOADED",
               "collected_utc": datetime.now(timezone.utc).isoformat(),
               "source_provider_count": len(reports), "discovered_catalog_entries": len(rows),
               "by_provider": reports, "economic_observations_ingested": 0,
               "permission_to_redistribute_underlying_data_verified": False,
               "observed_availability_verified_per_series": False,
               "cross_provider_identity_reconciled": False, "research_ready": False,
               "notes": [
                   "World Bank API indicator entries can include separate database sources; distinct source+code is retained.",
                   "Eurostat catalog includes datasets and predefined tables, which can overlap; counts do not imply unique underlying series.",
                   "Eurostat advertised values and periods are metadata claims, not downloaded or independently verified observations.",
                   "Official dataset metadata alone does not prove licensing rights or analytical comparability."
               ]}
    (out / "atlas2_discovery_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    digest_info = {}
    for file in sorted(out.iterdir()):
        digest_info[file.name] = {"sha256": digest(file.read_bytes()), "bytes": file.stat().st_size}
    (out / "sha256_manifest.json").write_text(json.dumps(digest_info, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": summary["status"], "providers": list(reports),
                      "discovered_catalog_entries": len(rows),
                      "economic_observations_ingested": 0}, indent=2))
    return summary

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", choices=("worldbank", "eurostat", "both"), default="both")
    ap.add_argument("--out", type=Path, default=Path("atlas2_output"))
    args = ap.parse_args()
    providers = tuple(PROVIDERS) if args.provider == "both" else (args.provider,)
    export(args.out, sorted(providers))

if __name__ == "__main__":
    main()
