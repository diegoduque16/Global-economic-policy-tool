#!/usr/bin/env python3
"""Atlas 3: discover public IMF/OECD/BIS SDMX *dataflow metadata*, not data values.

Run one provider at a time while validating public endpoints. Python stdlib only.
This does not claim all time series, values, licenses, or historical coverage.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

# Public metadata URLs backed by official IMF, OECD and BIS API documentation.
# Endpoint success/format must still be validated during a live GitHub Action.
ENDPOINTS = {
    "imf": "https://api.imf.org/external/sdmx/2.1/dataflow/all/all/latest?references=none",,
    "oecd": "https://sdmx.oecd.org/public/rest/v1/dataflow/all/all/latest?references=none",
    "bis": "https://stats.bis.org/api/v2/structure/dataflow/all/all/latest",
}
HOSTS = {"api.imf.org", "sdmx.oecd.org", "stats.bis.org"}
MAX_BYTES = 64_000_000
FIELDS = ("provider", "agency_id", "dataflow_id", "version", "title",
          "description", "dataflow_key", "provider_catalog_type", "evidence_status")
STATUS = "OFFICIAL_DATAFLOW_METADATA_ONLY_NO_OBSERVATIONS_INGESTED"


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def fetch(url: str) -> bytes:
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname not in HOSTS:
        raise ValueError("Unapproved metadata host or protocol")
    headers = {"User-Agent": "EconomicDataAtlas/0.2 public economic metadata discovery",
               "Accept": "application/vnd.sdmx.structure+xml;version=2.1, application/xml;q=0.8"}
    last = None
    for i in range(3):
        try:
            with urlopen(Request(url, headers=headers), timeout=90) as resp:
                final = urlsplit(resp.geturl())
                if final.scheme != "https" or final.hostname not in HOSTS:
                    raise ValueError("Unsafe provider redirect")
                raw = resp.read(MAX_BYTES + 1)
                if not raw or len(raw) > MAX_BYTES:
                    raise ValueError("Missing or excessively large SDMX response")
                return raw
        except HTTPError as exc:
            last = exc
            if exc.code not in {408, 429, 500, 502, 503, 504}:
                raise
        except (URLError, TimeoutError) as exc:
            last = exc
        if i < 2:
            time.sleep(2 ** i)
    raise RuntimeError(f"Provider metadata request exhausted retries: {url}") from last


def localname(tag: str) -> str:
    return str(tag).split("}")[-1].split(":")[-1]


def texts(item: ET.Element, kind: str) -> str:
    entries = []
    for node in item:
        if localname(node.tag) != kind:
            continue
        value = " ".join((node.text or "").split())
        if not value:
            continue
        language = node.attrib.get("{http://www.w3.org/XML/1998/namespace}lang", "")
        entries.append((language.lower(), value))
    for language, value in entries:
        if language.startswith("en"):
            return value
    return entries[0][1] if entries else ""


def parse_sdmx_dataflows(raw: bytes, provider: str):
    if provider not in ENDPOINTS:
        raise ValueError("Unknown provider")
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Reject unexpected XML DTD or entity declaration")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid SDMX XML from {provider}") from exc
    rows = []
    seen = {}
    repeats = 0
    for node in root.iter():
        if localname(node.tag) != "Dataflow":
            continue
        agency = (node.get("agencyID") or "").strip()
        flow = (node.get("id") or "").strip()
        version = (node.get("version") or "unspecified").strip()
        if not agency or not flow or not version:
            raise ValueError(f"Incomplete SDMX dataflow identity from {provider}")
        if not re.fullmatch(r"[A-Za-z0-9_@.+-]{1,150}", flow):
            raise ValueError(f"Unsafe/unexpected SDMX dataflow ID from {provider}: {flow!r}")
        key = f"{agency}:{flow}:{version}"
        row = {"provider": provider, "agency_id": agency, "dataflow_id": flow,
               "version": version, "title": texts(node, "Name"),
               "description": texts(node, "Description"), "dataflow_key": key,
               "provider_catalog_type": "sdmx_dataflow",
               "evidence_status": STATUS}
        if key in seen:
            repeats += 1
            if seen[key] != row:
                raise ValueError(f"Conflicting SDMX metadata for duplicate {key}")
            continue
        seen[key] = row
        rows.append(row)
    if not rows:
        raise ValueError(f"No official SDMX Dataflow records found for {provider}; schema or endpoint may differ")
    return rows, repeats


def discover(provider: str, out: Path, retrieve=fetch):
    if provider not in ENDPOINTS:
        raise ValueError("Unknown provider")
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError("Refuse to overwrite an existing discovery snapshot")
    url = ENDPOINTS[provider]
    raw = retrieve(url)
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        raise ValueError("Unexpected source response size/type")
    records, repeats = parse_sdmx_dataflows(raw, provider)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "discovered_sdmx_dataflows.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(records)
    summary = {
        "catalog_status": STATUS,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "official_endpoint": url,
        "response_sha256": sha256(raw),
        "response_bytes": len(raw),
        "distinct_dataflow_entries": len(records),
        "identical_duplicate_entries": repeats,
        "economic_observations_downloaded": 0,
        "time_series_discovered": None,
        "research_ready": False,
        "redistribution_license_verified": False,
        "note": "Dataflows are containers, NOT enumerated indicator/geography/time series; list does not demonstrate numeric coverage."
    }
    (out / "atlas3_sdmx_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    checks = {p.name: {"sha256": sha256(p.read_bytes()), "bytes": p.stat().st_size}
              for p in sorted(out.iterdir()) if p.is_file()}
    (out / "sha256_manifest.json").write_text(json.dumps(checks, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("provider", "distinct_dataflow_entries", "identical_duplicate_entries", "economic_observations_downloaded")}, indent=2))
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", required=True, choices=sorted(ENDPOINTS))
    ap.add_argument("--out", type=Path, default=Path("atlas3_sdmx_output"))
    args = ap.parse_args()
    discover(args.provider, args.out)


if __name__ == "__main__":
    main()
