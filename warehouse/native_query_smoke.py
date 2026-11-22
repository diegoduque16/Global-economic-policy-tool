#!/usr/bin/env python3
"""Fail-closed, metadata-only smoke checks on an actual SHA-restored private Atlas DB.

Do not print original data, source IDs, country codes or raw row contents.
This is a validation step in the existing PRIVATE ingestion workflow, not a
public source publisher or a statistical harmonization step.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from warehouse.ilostat_private_chain import load_pointer, verify_parent_db
from warehouse.native_query import browse, connect_read_only


def check_private_query(db_path: Path, pointer_path: Path) -> dict:
    pointer = load_pointer(pointer_path)
    # Includes SQLite integrity and exact source/row count reconciliation.
    integrated_ilostat = verify_parent_db(db_path, pointer)
    if not integrated_ilostat:
        raise ValueError("Expected integrated original ILOSTAT tables")
    checked = 0
    checked_original_rows = 0
    found_ilostat = False
    with connect_read_only(db_path) as db:
        # All existing source identities must remain discoverable and preserve
        # their source-native source receipt, never invented series names.
        sources = db.execute(
            "SELECT source_id, original_records, indexed_records FROM native_sources ORDER BY source_id"
        ).fetchall()
        if len(sources) != pointer["native_sources"]:
            raise ValueError("Source catalog changed between read-only checks")
        families = {}
        for source_id, original, indexed in sources:
            if original != indexed or original < 1:
                raise ValueError("Source-native receipt counts do not reconcile")
            family = source_id.split("_", 1)[0]
            families.setdefault(family, source_id)
        # Check a representative source from each original source family plus
        # latest ILOSTAT source, without exposing private source data in logs.
        sample_ids = set(families.values())
        ilo = next((sid for sid, _, _ in reversed(sources)
                    if sid.startswith("ilostat_")), None)
        if ilo:
            sample_ids.add(ilo)
        for source_id in sorted(sample_ids):
            original = db.execute(
                "SELECT ordinal, geo_code, year_text, series_code FROM native_records "
                "WHERE source_id=? ORDER BY ordinal LIMIT 1", (source_id,)
            ).fetchone()
            if original is None:
                raise ValueError("Source has no queryable source-native records")
            ordinal, geo, year, series = original
            filters = {"source_id": source_id}
            if geo:
                filters["geo"] = geo
            if year:
                filters["year"] = year
            if series:
                filters["series"] = series
            rows = browse(db, command="records", limit=10, **filters)
            if not any(row["ordinal"] == ordinal for row in rows["rows"]):
                # Another record with same filtered dimensions may appear first.
                # At minimum all returned records must match supplied filters.
                if not rows["rows"]:
                    raise ValueError("Original filtered records cannot be retrieved")
            for row in rows["rows"]:
                if row["source_id"] != source_id:
                    raise ValueError("Source identity mismatch")
                if geo and row["geo_code"] != geo:
                    raise ValueError("Original geography filter mismatch")
                if year and row["year_text"] != year:
                    raise ValueError("Original year filter mismatch")
                if series and row["series_code"] != series:
                    raise ValueError("Original series filter mismatch")
                if "raw_row" in row:
                    raise ValueError("Private original JSON returned without opt-in")
            facets = browse(db, command="facets", limit=20, **filters)
            for label, key, value in (
                ("geographies", "geo", geo),
                ("years", "year", year),
                ("series", "series", series),
            ):
                if value and not any(facet["code"] == value and facet["rows"] >= 1
                                     for facet in facets["facets"][label]):
                    raise ValueError("Original facet missing queried dimension")
            checked += 1
            checked_original_rows += len(rows["rows"])
            found_ilostat |= source_id.startswith("ilostat_")
    if not found_ilostat or checked < 2:
        raise ValueError("Did not check both original ILOSTAT and other source families")
    return {
        "private_source_native_query_smoke": "PASS",
        "original_source_families_sampled": checked,
        "original_filtered_rows_examined_privately": checked_original_rows,
        "source_native_records_preserved": pointer["native_records"],
        "source_ids_preserved": pointer["native_sources"],
        "ilostat_original_tables_present": len(integrated_ilostat),
        "public_release": False,
        "harmonized": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--pointer", required=True, type=Path)
    args = parser.parse_args()
    result = check_private_query(args.db, args.pointer)
    # Metadata only. No source IDs, original geographic dimensions or values.
    for key, value in result.items():
        print("ATLAS_QUERY_SMOKE", key, value)


if __name__ == "__main__":
    main()
