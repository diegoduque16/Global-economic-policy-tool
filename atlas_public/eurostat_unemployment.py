"""Publish a bounded, source-preserving Eurostat annual unemployment release.

Official API Statistics JSON-stat 2.0 only. This deliberately selects six
sex/age definitions from one original dataset; no inferred or mixed units.
Data source and raw response SHA are retained for reproducibility.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DATASET = "une_rt_a"
BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
SOURCE_URL = "https://ec.europa.eu/eurostat/databrowser/view/une_rt_a/default/table?lang=en"
COPYRIGHT = "https://ec.europa.eu/eurostat/help/copyright-notice"
PARAMETERS = (
    ("lang", "EN"), ("freq", "A"), ("unit", "PC_ACT"),
    ("sex", "T"), ("sex", "F"), ("sex", "M"),
    ("age", "Y15-74"), ("age", "Y15-24"),
)
URL = BASE + DATASET + "?" + urlencode(PARAMETERS)
MAX_BYTES = 15_000_000
SEXS = {"T": "Total", "F": "Women", "M": "Men"}
AGES = {"Y15-74": "ages 15–74", "Y15-24": "ages 15–24"}
EXPECTED_DIMS = {"freq", "unit", "sex", "age", "geo", "time"}
MAX_CELLS = 300_000


def decode(raw: bytes, *, captured: str | None = None) -> dict:
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("Eurostat official source empty or too large")
    result = json.loads(raw)
    if not isinstance(result, dict) or result.get("error"):
        raise ValueError("Eurostat API returned an error")
    ids = result.get("id")
    sizes = result.get("size")
    categories = result.get("dimension")
    if (not isinstance(ids, list) or len(ids) != len(EXPECTED_DIMS) or
        set(ids) != EXPECTED_DIMS or not isinstance(sizes, list) or
        len(sizes) != len(ids) or not isinstance(categories, dict) or
        any(type(n) is not int or not 1 <= n <= 500 for n in sizes)):
        raise ValueError("Eurostat JSON-stat dimensions changed")
    total = math.prod(sizes)
    if not 200 <= total <= MAX_CELLS:
        raise ValueError("Eurostat source has unexpected dimensional coverage")
    order = {}
    names = {}
    for dim, size in zip(ids, sizes):
        info = categories.get(dim, {}).get("category", {})
        index = info.get("index")
        labels = info.get("label", {})
        if not isinstance(labels, dict):
            raise ValueError("Eurostat category labels missing")
        if isinstance(index, dict):
            if any(type(v) is not int for v in index.values()) or set(index.values()) != set(range(size)):
                raise ValueError("Non-dense Eurostat category indices")
            sorted_codes = [code for code, _ in sorted(index.items(), key=lambda kv: kv[1])]
        elif isinstance(index, list):
            if len(index) != size or len(set(index)) != size:
                raise ValueError("Eurostat category index is invalid")
            sorted_codes = index
        else:
            raise ValueError("Eurostat category index is missing")
        if not all(isinstance(code, str) and code for code in sorted_codes):
            raise ValueError("Eurostat original classification identifiers missing")
        order[dim] = sorted_codes
        names[dim] = labels
    for field, allowed in (
        ("freq", {"A"}), ("unit", {"PC_ACT"}),
        ("sex", set(SEXS)), ("age", set(AGES)),
    ):
        if set(order[field]) != allowed:
            raise ValueError("Eurostat requested units or statistical classifications changed")
    values = result.get("value")
    statuses = result.get("status", {})
    if not isinstance(values, (dict, list)) or not isinstance(statuses, (dict, list)):
        raise ValueError("Eurostat observations or source flags invalid")
    if len(values) > total:
        raise ValueError("Eurostat numeric values exceed source shape")
    for code in order["time"]:
        if len(code) != 4 or not code.isdigit() or not 1900 <= int(code) <= 2100:
            raise ValueError("Eurostat annual reference period changed")
    for code in order["geo"]:
        if not 2 <= len(code) <= 24 or not all(c.isupper() or c.isdigit() or c == "_" for c in code):
            raise ValueError("Eurostat geography identifier changed")
        if not names["geo"].get(code):
            raise ValueError("Eurostat geography name missing")
    def source_at(collection, k):
        return collection.get(str(k)) if isinstance(collection, dict) else (
            collection[k] if k < len(collection) else None
        )
    rows = []
    observed = set()
    for key in (values.keys() if isinstance(values, dict) else range(len(values))):
        if not str(key).isdigit() or not 0 <= int(key) < total:
            raise ValueError("Invalid Eurostat JSON-stat observation position")
        pos = int(key)
        value = source_at(values, pos)
        if value is None:
            continue
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("Eurostat source observation is not finite numeric")
        coords = {}
        for dim, size in reversed(list(zip(ids, sizes))):
            coords[dim] = order[dim][pos % size]
            pos //= size
        if coords["freq"] != "A" or coords["unit"] != "PC_ACT":
            raise ValueError("Unexpected Eurostat annual measurement")
        flag = source_at(statuses, int(key)) or ""
        if not isinstance(flag, str) or len(flag) > 25:
            raise ValueError("Eurostat quality/source flag malformed")
        item = [coords["geo"], int(coords["time"]),
                coords["sex"], coords["age"], value, flag]
        identity = tuple(item[:4])
        if identity in observed:
            raise ValueError("Duplicate Eurostat country-year-sex-age observation")
        observed.add(identity)
        rows.append(item)
    if len(rows) < 200 or len({x[0] for x in rows}) < 15:
        raise ValueError("Eurostat source returned insufficient real observations")
    rows.sort(key=lambda r: (r[0], r[2], r[3], r[1]))
    by_series = Counter((r[2], r[3]) for r in rows)
    if len(by_series) != len(SEXS) * len(AGES):
        raise ValueError("Requested Eurostat age/sex breakdown not present in numeric data")
    return {
        "schema_version": 1,
        "public_release": True,
        "provider": "Eurostat (European Commission)",
        "dataset": DATASET,
        "title": "Unemployment rate by sex and age — annual data",
        "original_unit": "PC_ACT",
        "unit_label": "%",
        "source_url": SOURCE_URL,
        "official_api_url": URL,
        "source_raw_sha256": sha256(raw).hexdigest(),
        "downloaded_at_utc": captured or datetime.now(timezone.utc).isoformat(),
        "copyright_url": COPYRIGHT,
        "citation": "Eurostat, Unemployment by sex and age — annual data (une_rt_a), "
                    "https://doi.org/10.2908/une_rt_a, accessed [date]. "
                    "Data subset and chart formatting: World Economic Atlas.",
        "not_harmonized_with_other_providers": True,
        "classification_labels": {"sex": SEXS, "age": AGES},
        "geography_labels": {k: names["geo"][k] for k in sorted({r[0] for r in rows})},
        "numeric_observations": len(rows),
        "series_breakdowns": len(by_series),
        "rows": rows,
    }


def build_source(output: Path) -> dict:
    request = Request(URL, headers={"User-Agent": "WorldEconomicAtlas/1.0 (public statistical data)",
                                    "Accept": "application/json"})
    with urlopen(request, timeout=70) as response:
        raw = response.read(MAX_BYTES + 1)
    release = decode(raw)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(release, ensure_ascii=False,
                                 separators=(",", ":")) + "\n", encoding="utf-8")
    return release


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path("atlas_public/data/eurostat/annual_unemployment.json"))
    args = parser.parse_args()
    release = build_source(args.output)
    print("ATLAS_EUROSTAT_OFFICIAL_UNEMPLOYMENT_VERIFIED",
          release["numeric_observations"], release["series_breakdowns"],
          release["source_raw_sha256"])
