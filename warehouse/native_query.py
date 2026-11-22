#!/usr/bin/env python3
"""Read-only, source-native Atlas SQLite explorer (no harmonization or publication).

Examples:
  python -m warehouse.native_query --db /private/native_source_extension.sqlite sources --match employment --limit 25 --offset 0
  python -m warehouse.native_query --db /private/native_source_extension.sqlite facets --source-id ilostat_EMP_EXAMPLE_abcdef123456
  python -m warehouse.native_query --db /private/native_source_extension.sqlite records --source-id ilostat_EMP_EXAMPLE_abcdef123456 --geo UG --year 2023 --limit 20
All source/geography/series codes are originals, not inferred comparable indicators.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
import sqlite3


def connect_read_only(path: Path) -> sqlite3.Connection:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Expected an existing private SQLite file (not a symlink)")
    db = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA trusted_schema=OFF")
    return db


def browse(db: sqlite3.Connection, *, command: str, limit: int = 20,
           source_id: str | None = None, geo: str | None = None,
           year: str | None = None, series: str | None = None,
           include_raw: bool = False, match: str | None = None,
           source_prefix: str | None = None, offset: int = 0) -> dict:
    if type(limit) is not int or limit < 1 or limit > 500:
        raise ValueError("Limit must be between 1 and 500")
    if command == "sources":
        if any(value is not None for value in (source_id, geo, year, series)) or include_raw:
            raise ValueError("Record filters cannot be used in source catalog")
        if type(offset) is not int or not 0 <= offset <= 100_000:
            raise ValueError("Source catalog offset must be between 0 and 100000")
        for value in (match, source_prefix):
            if value is not None and (not isinstance(value, str) or
                                      not value.strip() or len(value) > 128):
                raise ValueError("Invalid source catalog search")
        terms, params = [], []
        if match is not None:
            terms.append("(instr(lower(source_id), lower(?)) > 0 OR "
                         "instr(lower(source_name), lower(?)) > 0)")
            params.extend((match.strip(), match.strip()))
        if source_prefix is not None:
            terms.append("substr(source_id, 1, length(?)) = ?")
            params.extend((source_prefix, source_prefix))
        where = " WHERE " + " AND ".join(terms) if terms else ""
        total = db.execute("SELECT COUNT(*) FROM native_sources" + where, params).fetchone()[0]
        rows = db.execute(
            "SELECT source_id,source_name,original_records,indexed_records,raw_sha256 "
            "FROM native_sources" + where + " ORDER BY source_id LIMIT ? OFFSET ?",
            [*params, limit, offset]
        ).fetchall()
        return {"mode": "original_sources", "source_native_not_harmonized": True,
                "not_a_public_release": True,
                "query": {"match": match, "source_prefix": source_prefix},
                "total_matching_sources": total, "offset": offset, "limit": limit,
                "has_more": offset + len(rows) < total,
                "rows": [dict(zip(("source_id","source_name","original_records",
                                  "indexed_records","source_sha256"), row)) for row in rows]}
    if any(value is not None for value in (match, source_prefix)) or offset:
        raise ValueError("Catalog search and offset only work with sources")
    if command not in ("records", "facets"):
        raise ValueError("Expected sources, facets or records command")
    if not isinstance(source_id, str) or not source_id.strip() or len(source_id) > 256:
        raise ValueError("Exact --source-id is mandatory for private record queries")
    for name, value in (("geo", geo), ("year", year), ("series", series)):
        if value is not None and (not isinstance(value, str) or not value or len(value) > 128):
            raise ValueError("Invalid original " + name + " filter")
    source = db.execute(
        "SELECT raw_sha256, original_records,indexed_records FROM native_sources WHERE source_id=?",
        (source_id,)
    ).fetchone()
    if source is None:
        raise ValueError("Unknown original source ID: " + source_id)
    filters = ["source_id = ?"]
    values: list = [source_id]
    for col, value in (("geo_code", geo), ("year_text", year), ("series_code", series)):
        if value is not None:
            filters.append(col + " = ?")
            values.append(value)
    if command == "facets":
        if include_raw:
            raise ValueError("Raw rows are not valid for facets")
        # Facets are source-native counts. They are not countries, harmonized
        # indicators, comparable years, or deduplicated statistical observations.
        facets = {}
        for label, column in (("geographies", "geo_code"),
                              ("years", "year_text"),
                              ("series", "series_code")):
            where = " AND ".join(filters)
            params = values + [limit]
            data = db.execute(
                "SELECT " + column + ", COUNT(*) AS rows FROM native_records WHERE " +
                where + " AND " + column + " IS NOT NULL AND " + column + " != '' " +
                "GROUP BY " + column + " ORDER BY rows DESC, " + column +
                " LIMIT ?", params
            ).fetchall()
            facets[label] = [{"code": k, "rows": n} for k, n in data]
        return {"mode": "original_facets", "source_native_not_harmonized": True,
                "not_a_public_release": True, "source_id": source_id,
                "source_sha256": source[0],
                "filters": {"geo_code": geo, "year_text": year, "series_code": series},
                "limit_per_dimension": limit, "facets": facets}
    columns = ["source_id","ordinal","year_text","period_text","geo_code",
               "series_code","industry_code","value_text"]
    if include_raw:
        columns.append("raw_row_json")
    sql = ("SELECT " + ",".join(columns) + " FROM native_records WHERE " +
           " AND ".join(filters) + " ORDER BY ordinal LIMIT ?")
    values.append(limit)
    rows = []
    for row in db.execute(sql, values):
        entry = dict(zip(columns, row))
        if include_raw:
            entry["raw_row"] = json.loads(entry.pop("raw_row_json"))
        rows.append(entry)
    return {
        "mode": "original_records",
        "source_native_not_harmonized": True,
        "not_a_public_release": True,
        "source_id": source_id,
        "source_sha256": source[0],
        "source_original_rows": source[1],
        "source_indexed_rows": source[2],
        "filters": {"geo_code": geo, "year_text": year, "series_code": series},
        "limit": limit,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Locally restored private Atlas SQLite")
    commands = parser.add_subparsers(dest="command", required=True)
    source_cmd = commands.add_parser("sources", help="List original source IDs and provenance")
    source_cmd.add_argument("--limit", type=int, default=20)
    source_cmd.add_argument("--offset", type=int, default=0,
                            help="Page through source IDs in stable alphabetical order")
    source_cmd.add_argument("--match", help="Case-insensitive literal substring in source ID or name")
    source_cmd.add_argument("--source-prefix", help="Exact original source ID prefix (no wildcard matching)")
    facet_cmd = commands.add_parser("facets", help="Discover available original geography, year and series codes")
    facet_cmd.add_argument("--source-id", required=True)
    facet_cmd.add_argument("--geo")
    facet_cmd.add_argument("--year")
    facet_cmd.add_argument("--series")
    facet_cmd.add_argument("--limit", type=int, default=40)
    record_cmd = commands.add_parser("records", help="Inspect exact original rows")
    record_cmd.add_argument("--source-id", required=True)
    record_cmd.add_argument("--geo", help="Exact original geo_code, not an inferred country")
    record_cmd.add_argument("--year", help="Exact original year_text")
    record_cmd.add_argument("--series", help="Exact original series_code")
    record_cmd.add_argument("--limit", type=int, default=20)
    record_cmd.add_argument("--format", choices=("json", "csv"), default="json",
                            help="CSV stdout is source-native, not harmonized")
    record_cmd.add_argument("--include-raw", action="store_true",
                            help="Include original raw-row JSON (PRIVATE; do not publish)")
    args = parser.parse_args()
    with connect_read_only(args.db) as db:
        result = browse(db, command=args.command, limit=args.limit,
                        source_id=getattr(args, "source_id", None),
                        geo=getattr(args, "geo", None),
                        year=getattr(args, "year", None),
                        series=getattr(args, "series", None),
                        include_raw=getattr(args, "include_raw", False),
                        match=getattr(args, "match", None),
                        source_prefix=getattr(args, "source_prefix", None),
                        offset=getattr(args, "offset", 0))
    if getattr(args, "format", "json") == "csv":
        # Protect accidental publication: stdout is a *local* stream, and
        # rows remain bounded. Quoting prevents CSV column shift.
        fields = ["source_id", "ordinal", "year_text", "period_text",
                  "geo_code", "series_code", "industry_code", "value_text"]
        if args.include_raw:
            fields.append("raw_row")
        writer = csv.DictWriter(sys.stdout, fieldnames=fields)
        writer.writeheader()
        for row in result["rows"]:
            out = dict(row)
            if args.include_raw:
                out["raw_row"] = json.dumps(out["raw_row"], ensure_ascii=False)
            writer.writerow(out)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
