"""Replay the certified Eurostat enterprise AI release into an Atlas native SQLite copy."""
import argparse
import csv
from hashlib import sha256 as bytes_sha256
import io
import json
import math
from pathlib import Path
from zipfile import ZipFile

from warehouse.native_materialize import sha256
from warehouse.native_query import browse, connect_read_only
from warehouse.us_europe_native_extension import extend_sqlite

ARTIFACT_SHA = "3af4d77f88fab54bf7a9c15562ec5e9b018a496965462e6efcb8ec24f5e851b2"
SERIES = "isoc_eb_ai.E_AI_TANY.C10-S951_X_K.GE10"
SCOPE = {"freq": "A", "size_emp": "GE10", "nace_r2": "C10-S951_X_K",
         "indic_is": "E_AI_TANY", "unit": "PC_ENT"}


def replay(artifact):
    if sha256(artifact) != ARTIFACT_SHA:
        raise ValueError("Eurostat AI workflow artifact SHA-256 differs")
    with ZipFile(artifact) as release:
        manifest = json.loads(release.read("manifest.json"))
        raw = release.read("official_response.json")
        panel = release.read("ai_adoption_panel.csv")
    raw_sha = bytes_sha256(raw).hexdigest()
    if raw_sha != manifest["raw_sha256"] or bytes_sha256(panel).hexdigest() != manifest["panel_sha256"]:
        raise ValueError("Original Eurostat response or panel SHA-256 differs")

    original = json.loads(raw)
    dimensions = original["id"]
    categories = original["dimension"]
    codes = {dim: sorted(categories[dim]["category"]["index"],
                         key=categories[dim]["category"]["index"].get) for dim in dimensions}
    if set(dimensions) != {*SCOPE, "geo", "time"} or any(
            codes[dim] != [code] for dim, code in SCOPE.items()):
        raise ValueError("Unexpected original Eurostat dimensions or measurement scope")
    sizes = original["size"]
    if sizes != [len(codes[dim]) for dim in dimensions]:
        raise ValueError("Eurostat source shape differs")

    rows = {}
    for key, value in original["value"].items():
        offset = int(key)
        if not 0 <= offset < math.prod(sizes) or type(value) not in (float, int) or not 0 <= value <= 100:
            raise ValueError("Invalid Eurostat observation")
        coords = {}
        for dim, size in reversed(list(zip(dimensions, sizes))):
            offset, index = divmod(offset, size)
            coords[dim] = codes[dim][index]
        geo, year = coords["geo"], coords["time"]
        rows[geo, year] = {
            **coords, "country_iso3": geo, "period": year, "year": int(year),
            "series": SERIES, "value": value, "unit": SCOPE["unit"],
            "frequency": "annual", "provider": "Eurostat",
            "observation_kind": "survey_estimate",
            "status_flag": original["status"].get(key, ""),
            "source_url": manifest["source_url"], "retrieved_utc": manifest["retrieved_utc"],
            "source_updated": original["updated"],
            "geo_label": categories["geo"]["category"]["label"][geo],
        }

    csv_rows = list(csv.DictReader(io.StringIO(panel.decode("utf-8"))))
    replayed = {(r["geo"], r["year"]): (float(r["value_percent"]), r["status_flag"],
                r["source_url"], r["indicator"], r["dataset"], r["provider"]) for r in csv_rows}
    expected = {key: (row["value"], row["status_flag"], row["source_url"],
                      SCOPE["indic_is"], "isoc_eb_ai", "Eurostat") for key, row in rows.items()}
    if (len(csv_rows) != 139 or len(rows) != 139 or replayed != expected
            or len({geo for geo, _ in rows}) != 35 or rows["EU27_2020", "2025"]["value"] != 19.95):
        raise ValueError("Original Eurostat values, flags or coverage differ from the certified panel")
    return [("europe", rows[key]) for key in sorted(rows)], raw_sha


def integrate(database, artifact, parent_rows):
    rows, raw_sha = replay(artifact)
    report = extend_sqlite(database, rows, {("europe", SERIES): raw_sha},
                           expected_parent_rows=parent_rows)
    source_id = f"eurostat_{SERIES}_{raw_sha[:12]}"
    with connect_read_only(database) as db:
        result = browse(db, command="records", source_id=source_id, geo="EU27_2020",
                        year="2025", include_raw=True)
    if len(result["rows"]) != 1 or float(result["rows"][0]["value_text"]) != 19.95:
        raise ValueError("Eurostat AI source-native query did not reproduce EU 2025")
    return {
        "parent_native_records": report["prior_wid_wdi_rows_preserved"],
        "new_native_records": report["native_records_in_new_attachment"],
        "observations_added": len(rows), "geographies": 35, "source_id": source_id,
        "original_raw_sha256": raw_sha, "original_artifact_sha256": ARTIFACT_SHA,
        "original_run_id": 45155822409, "public_release": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("parent_rows", type=int)
    args = parser.parse_args()
    print(json.dumps(integrate(args.database, args.artifact, args.parent_rows), indent=2))
