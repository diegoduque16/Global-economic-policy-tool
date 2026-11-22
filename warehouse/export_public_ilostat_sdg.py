"""Release three verified official ILOSTAT annual SDG datasets from the private Atlas SQLite.

Only CC BY 4.0 public-source annual indicator rows, source identifiers and
original non-value classification metadata are exported. No raw_row_json,
private SQLite, credentials or unrelated-provider data may be published.
"""
from __future__ import annotations
from collections import Counter
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import zipfile

from warehouse.ilostat_private_chain import load_pointer, verify_parent_db

TABLES = {
    "SDG_0831_SEX_ECO_RT_A": ("Informal employment (SDG 8.3.1)", 57_488),
    "SDG_0851_SEX_OCU_NB_A": ("Average hourly earnings (SDG 8.5.1)", 47_322),
    "SDG_0852_SEX_AGE_RT_A": ("Unemployment rate (SDG 8.5.2)", 48_306),
}
TOTAL = sum(x[1] for x in TABLES.values())
EXPECTED_FINITE = 149_148
HEADER = ["table_id","geography_code","year","original_series_id",
          "original_value_text","source_status","original_dimensions_json"]
RELEASE_NAME = "ilostat_sdg_labour_source_native.csv.zip"

def sha256(path:Path)->str:
    h=hashlib.sha256()
    with Path(path).open("rb") as src:
        for chunk in iter(lambda:src.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()


def export(db_path:Path, pointer_path:Path, output:Path)->dict:
    db_path, pointer_path, output=Path(db_path),Path(pointer_path),Path(output)
    pointer=load_pointer(pointer_path)
    verified=verify_parent_db(db_path,pointer)
    if not set(TABLES).issubset(verified):
        raise ValueError("Missing verified original ILOSTAT SDG tables")
    output.mkdir(parents=True,exist_ok=True)
    archive=output/RELEASE_NAME
    by_table=Counter()
    finite=0
    sources=[]
    con=sqlite3.connect("file:"+str(db_path.resolve())+"?mode=ro",uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
        for table,(label,expected) in TABLES.items():
            rows=con.execute(
                "SELECT source_id,raw_sha256,original_records,indexed_records "
                "FROM native_sources WHERE source_id GLOB ?",("ilostat_"+table+"_*",)
            ).fetchall()
            if len(rows)!=1:
                raise ValueError("ILOSTAT table source identity not unique")
            sid,digest,original,indexed=rows[0]
            if (not re.fullmatch(r"ilostat_"+re.escape(table)+r"_[0-9a-f]{12}",sid)
                or not re.fullmatch(r"[0-9a-f]{64}",digest)
                or digest[:12]!=sid[-12:]
                or original!=expected or indexed!=expected):
                raise ValueError("Invalid ILOSTAT original source receipt")
            sources.append({"table_id":table,"label":label,"source_id":sid,
                            "source_original_sha256":digest,"original_rows":expected})
        with zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED,compresslevel=7) as z:
            with z.open("ilostat_sdg_labour_source_native.csv","w") as stream:
                with io.TextIOWrapper(stream,encoding="utf-8",newline="",write_through=True) as out:
                    writer=csv.writer(out)
                    writer.writerow(HEADER)
                    for source in sources:
                        table=source["table_id"]
                        query="""SELECT r.geo_code,r.year_text,r.series_code,r.value_text,
                                   s.original_dimensions_json
                            FROM native_records r JOIN native_ilostat_series s
                            ON r.source_id=s.source_id AND r.series_code=s.series_code
                            WHERE r.source_id=? ORDER BY r.ordinal"""
                        for geo,year,series,value,dims in con.execute(query,(source["source_id"],)):
                            if (not isinstance(geo,str) or not re.fullmatch(r"[A-Z0-9_]{2,20}",geo)
                                or not isinstance(year,str) or not re.fullmatch(r"[12][0-9]{3}",year)
                                or not isinstance(series,str) or not series.startswith(table+"_")
                                or not isinstance(dims,str) or not isinstance(json.loads(dims),dict)):
                                raise ValueError("Unexpected original ILOSTAT source dimensions")
                            value=value or ""
                            if value:
                                try:
                                    x=float(value)
                                except (ValueError,OverflowError):
                                    x=float("nan")
                                if math.isfinite(x):
                                    finite+=1
                            writer.writerow([table,geo,year,series,value,
                                             "finite_numeric" if value and math.isfinite(float(value)) else "source_missing_or_nonnumeric",
                                             dims])
                            by_table[table]+=1
            z.writestr("ORIGINAL_SOURCES.json",json.dumps(sources,ensure_ascii=False,indent=2))
            z.writestr("CITATION_AND_USAGE.txt",
                "International Labour Organization (ILO), ILOSTAT, annual SDG "
                "labour indicator tables 8.3.1, 8.5.1 and 8.5.2, original public "
                "source-native data. Source: https://ilostat.ilo.org/data/bulk/\n"
                "Reuse: CC BY 4.0 for ILO datasets published on or after 3 May 2023 "
                "(restricted microdata excluded). https://www.ilo.org/rights-and-permissions\n"
                "Citation: International Labour Organization (ILO). ILOSTAT, "
                "[table and original dimensions], [country, year], retrieved via "
                "World Economic Atlas source edition "+pointer["edition_id"]+
                ", accessed [date]. Include original table ID and Atlas edition.\n"
                "Numerical values are not harmonized; blank/non-numeric source "
                "values remain flagged, never converted to zero. Original "
                "classification dimensions are retained as metadata without "
                "invented definitions or official unit claims.\n")
        if any(by_table[t]!=expected for t,(_,expected) in TABLES.items()):
            raise ValueError("Source-native table counts diverged during export")
        if finite!=EXPECTED_FINITE:
            raise ValueError("Original verified finite numeric count changed")
        manifest={
            "schema_version":1,"release_type":"public_ilostat_sdg_annual_source_native",
            "producer":"International Labour Organization (ILO)","dataset":"ILOSTAT SDG labour tables",
            "official_source_url":"https://ilostat.ilo.org/data/bulk/",
            "license":"CC BY 4.0 for data published after 3 May 2023; restricted microdata excluded",
            "license_url":"https://www.ilo.org/rights-and-permissions",
            "source_edition_id":pointer["edition_id"],
            "source_archive_sha256":pointer["archive_sha256"],
            "source_database_sha256":sha256(db_path),
            "original_table_count":len(TABLES),"source_native_records":TOTAL,
            "finite_numeric_records":finite,"source_native_not_harmonized":True,
            "original_tables":[{"table_id":t,"title":TABLES[t][0],
                                "records":by_table[t]} for t in TABLES],
            "file":RELEASE_NAME,"file_sha256":sha256(archive)
        }
        (output/"ilostat_sdg_manifest.json").write_text(
            json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        return manifest
    finally:
        con.close()


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",required=True,type=Path)
    parser.add_argument("--pointer",required=True,type=Path)
    parser.add_argument("--out",required=True,type=Path)
    a=parser.parse_args()
    m=export(a.db,a.pointer,a.out)
    print("ATLAS_PUBLIC_ILOSTAT_SDG_VALIDATED",m["source_native_records"],
          m["finite_numeric_records"],m["file_sha256"])

if __name__=="__main__":
    main()
