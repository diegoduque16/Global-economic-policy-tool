"""World Bank country/aggregate geography crosswalk from version-pinned API pages.

Shared by the existing WDI collector and Atlas watchdog. These are World Bank
economies/territories, not a sovereign-state census. ISO membership alone must
never be used to infer a World Bank aggregate.
"""
from __future__ import annotations

from collections import Counter
import json
import re

BASE = "https://api.worldbank.org/v2/country"
COUNTRY = "WORLD_BANK_COUNTRY_OR_TERRITORY"
AGGREGATE = "WORLD_BANK_AGGREGATE"
UNKNOWN = "UNRESOLVED_WORLD_BANK_METADATA"
CODE = re.compile(r"[A-Z]{3}\Z")


def metadata_page(raw: bytes, page: int) -> tuple[int, int, list[dict]]:
    """Validate one original provider JSON response without changing its bytes."""
    obj = json.loads(raw)
    if not isinstance(obj, list) or len(obj) != 2 or not isinstance(obj[0], dict):
        raise ValueError("Invalid World Bank country metadata response")
    meta, rows = obj
    try:
        number, pages, total = (int(meta[k]) for k in ("page", "pages", "total"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid World Bank country metadata pagination") from exc
    if (number != page or not 1 <= pages <= 12 or page > pages
            or not 1 <= total <= 3000 or not isinstance(rows, list)
            or not rows or len(rows) > 500):
        raise ValueError("Invalid World Bank country metadata page/count")
    for item in rows:
        if not isinstance(item, dict) or not CODE.fullmatch(str(item.get("id", ""))):
            raise ValueError("Invalid World Bank geography code")
        if not isinstance(item.get("name"), str) or not item["name"].strip():
            raise ValueError("World Bank geography name missing")
        region = item.get("region")
        if not isinstance(region, dict) or not isinstance(region.get("id"), str) or not isinstance(region.get("value"), str):
            raise ValueError("World Bank provider region metadata missing")
    return pages, total, rows


def country_map(source_pages: list[bytes]) -> dict[str, dict]:
    if not source_pages or len(source_pages) > 12:
        raise ValueError("World Bank geographic metadata source pages absent")
    observed = {}
    expected_pages = expected_total = None
    for i, raw in enumerate(source_pages, 1):
        pages, total, entries = metadata_page(raw, i)
        if expected_pages is None:
            expected_pages, expected_total = pages, total
        elif (pages, total) != (expected_pages, expected_total):
            raise ValueError("World Bank country metadata page totals changed mid-collection")
        for item in entries:
            code = item["id"]
            if code in observed:
                raise ValueError("World Bank country metadata contains duplicate codes")
            region = item["region"]
            rid = region["id"]
            rv = region["value"].strip()
            if rid == "NA" and rv.casefold() == "aggregates":
                kind = AGGREGATE
            elif rid not in ("NA", "") and rv and rv.casefold() != "aggregates":
                kind = COUNTRY
            else:
                kind = UNKNOWN
            observed[code] = {
                "code": code, "name": item["name"],
                "region_id": rid, "region_name": rv, "geography_type": kind,
            }
    if len(source_pages) != expected_pages or len(observed) != expected_total:
        raise ValueError("Incomplete World Bank country metadata pages")
    return observed


def classify_observed(metadata: dict[str, dict], observed_counts: dict[str, int]) -> tuple[list[dict], dict]:
    """Classify observed source codes; never discard the original observations."""
    rows = []
    by_status = Counter()
    row_status = Counter()
    for code, count in sorted(observed_counts.items()):
        if not CODE.fullmatch(code) or type(count) is not int or count < 1:
            raise ValueError("Invalid WDI observed geography source count")
        item = metadata.get(code)
        record = dict(item) if item is not None else {
            "code": code, "name": "", "region_id": "",
            "region_name": "", "geography_type": UNKNOWN,
        }
        record["observation_rows"] = count
        rows.append(record)
        by_status[record["geography_type"]] += 1
        row_status[record["geography_type"]] += count
    return rows, {
        "geography_codes": len(rows),
        "country_or_territory_codes": by_status[COUNTRY],
        "aggregate_codes": by_status[AGGREGATE],
        "unresolved_codes": by_status[UNKNOWN],
        "country_or_territory_observation_rows": row_status[COUNTRY],
        "aggregate_observation_rows": row_status[AGGREGATE],
        "unresolved_observation_rows": row_status[UNKNOWN],
        "country_vs_aggregate_resolved": by_status[UNKNOWN] == 0 and bool(rows),
        "all_source_observations_preserved": True,
        "cross_provider_harmonized": False,
        "canonical_warehouse_updated": False,
    }
