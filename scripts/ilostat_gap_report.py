#!/usr/bin/env python3
"""Public-safe, source-ID-level completion register for the existing ILOSTAT downloader.

This reads only the already loaded official indicator TOC and private checkpoint
metadata; it does not download, modify, or expose original observations.
Download coverage and CURRENT source-vintage coverage are different metrics.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
import re

ID = re.compile(r"[A-Za-z0-9_]{4,100}\Z")
STAGES = (
    "not_yet_downloaded",
    "failed_this_day",
    "deferred_oversize_at_cap",
    "verified_older_vintage",
)
FIELDS = ("table_id", "stage", "official_frequency", "official_records_advertised",
          "official_last_update")


def build_gap_report(toc: dict, checkpoint: dict, *, now_day: str,
                     max_file_mib: int = 512) -> tuple[list[dict], dict]:
    """Distinguish absent files from verified-but-stale official source versions.

    Private source paths, SHA values, numeric observations and error messages
    must never appear in this public-safe metadata report.
    """
    if (not isinstance(toc, dict) or not toc or
            checkpoint.get("schema_version") != 1 or
            not isinstance(checkpoint.get("downloaded"), dict) or
            not isinstance(checkpoint.get("failures", {}), dict) or
            not isinstance(checkpoint.get("deferred", {}), dict) or
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}", now_day) or
            type(max_file_mib) is not int or max_file_mib < 64):
        raise ValueError("Malformed official ILOSTAT TOC or checkpoint")
    stored = checkpoint["downloaded"]
    for key in {*toc, *stored, *checkpoint.get("failures", {}), *checkpoint.get("deferred", {})}:
        if not isinstance(key, str) or not ID.fullmatch(key):
            raise ValueError("Untrusted original ILOSTAT source table ID")
    entries = []
    current = historical = 0
    for key, meta in sorted(toc.items()):
        if not isinstance(meta, dict):
            raise ValueError("Invalid official ILOSTAT TOC entry")
        newest = str(meta.get("last.update", ""))
        expected = str(meta.get("n.records", ""))
        record = stored.get(key)
        if record is not None:
            if (not isinstance(record, dict) or record.get("state") != "NATIVE_NOT_HARMONIZED"
                or type(record.get("records_validated")) is not int
                or record["records_validated"] < 1):
                raise ValueError("Unverified archived original ILOSTAT source entry")
            historical += 1
            if record.get("last_update") == newest and str(record.get("toc_records")) == expected:
                current += 1
                continue
            stage = "verified_older_vintage"
        else:
            failure = checkpoint.get("failures", {}).get(key, {})
            deferred = checkpoint.get("deferred", {}).get(key, {})
            if not isinstance(failure, dict) or not isinstance(deferred, dict):
                raise ValueError("Invalid ILOSTAT missing-source status")
            if (deferred.get("last_update") == newest and
                type(deferred.get("max_file_mib")) is int and
                deferred["max_file_mib"] >= max_file_mib):
                stage = "deferred_oversize_at_cap"
            elif failure.get("date") == now_day and failure.get("last_update") == newest:
                stage = "failed_this_day"
            else:
                stage = "not_yet_downloaded"
        entries.append({
            "table_id": key, "stage": stage,
            "official_frequency": str(meta.get("freq", "")),
            "official_records_advertised": expected,
            "official_last_update": newest,
        })
    counts = {stage: sum(item["stage"] == stage for item in entries) for stage in STAGES}
    if current + len(entries) != len(toc):
        raise ValueError("ILOSTAT completion categories do not reconcile")
    if historical < current or historical > len(toc):
        raise ValueError("Invalid verified provider source count")
    result = {
        "schema_version": 1,
        "stage": "OFFICIAL_ILOSTAT_SOURCE_COMPLETION_NOT_NUMERIC_WAREHOUSE",
        "official_toc_table_ids": len(toc),
        "archived_toc_table_ids_any_vintage": historical,
        "official_table_ids_at_current_vintage": current,
        "official_table_ids_not_archived": len(toc) - historical,
        "current_vintage_attention_required": len(entries),
        "attention_by_category": counts,
        "fully_archived_official_toc": historical == len(toc),
        "all_originals_at_current_toc_vintage": current == len(toc),
        "table_files_are_distinct_numeric_series": False,
        "source_files_are_canonical_numeric_rows": False,
        "snapshot_day_utc": now_day,
        "note": "Only official indicator table IDs and publication metadata are exposed. "
                "Original values, source URLs and private archive paths are never copied.",
    }
    return entries, result


def write_gap_report(destination: Path, toc: dict, checkpoint: dict, *,
                     now_day: str, max_file_mib: int = 512) -> dict:
    rows, summary = build_gap_report(toc, checkpoint, now_day=now_day,
                                      max_file_mib=max_file_mib)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / "remaining_official_source_ids.csv").open(
            "w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    (destination / "official_source_completion.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
