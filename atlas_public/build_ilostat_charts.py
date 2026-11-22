"""Create source-native, country-scoped charts from the public, SHA-pinned ILOSTAT SDG ZIP.

No private warehouse is read. Source-defined structural dimensions are kept
separate; exact numeric row totals, original table counts and source file SHA
must reconcile. Statistical series with duplicated annual keys are marked
unsafe for plotting rather than silently aggregated.
"""
from __future__ import annotations
from collections import defaultdict,Counter
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import zipfile

TABLES={
    "SDG_0831_SEX_ECO_RT_A":"Informal employment",
    "SDG_0851_SEX_OCU_NB_A":"Average hourly earnings",
    "SDG_0852_SEX_AGE_RT_A":"Unemployment rate",
}
EXPECTED_RECORDS=153116
EXPECTED_NUMERIC=149148
SEX_NAMES={"SEX_T":"Total", "SEX_F":"Women", "SEX_M":"Men"}
FIELDS=["table_id","geography_code","year","original_series_id",
        "original_value_text","source_status","original_dimensions_json"]

def safe_title(table:str, dimensions:dict, position:int)->str:
    sex=SEX_NAMES.get(dimensions.get("sex",""),"")
    age=""
    for val in dimensions.values():
        if not isinstance(val,str):
            continue
        match=re.fullmatch(r"AGE_Y([0-9]{1,2})-([0-9]{1,2})",val)
        if match:
            age="Ages "+match.group(1)+"–"+match.group(2)
            break
    bits=[TABLES[table]]
    if sex:bits.append(sex)
    if age:bits.append(age)
    bits.append("Original statistical breakdown "+str(position))
    return " · ".join(bits)

def build(data:Path,output:Path)->dict|None:
    root=Path(data)/"ilostat"
    manifest_file=root/"ilostat_sdg_manifest.json"
    archive=root/"ilostat_sdg_labour_source_native.csv.zip"
    if not manifest_file.exists() and not archive.exists():
        return None
    if not manifest_file.is_file() or not archive.is_file():
        raise ValueError("Incomplete published ILOSTAT SDG source edition")
    manifest=json.loads(manifest_file.read_text(encoding="utf-8"))
    if (manifest.get("release_type")!="public_ilostat_sdg_annual_source_native"
        or manifest.get("original_table_count")!=3
        or manifest.get("source_native_records")!=EXPECTED_RECORDS
        or manifest.get("finite_numeric_records")!=EXPECTED_NUMERIC
        or manifest.get("source_native_not_harmonized") is not True
        or hashlib.sha256(archive.read_bytes()).hexdigest()!=manifest.get("file_sha256")):
        raise ValueError("ILOSTAT published original-source manifest mismatch")
    expected={x["table_id"]:x["records"] for x in manifest["original_tables"]}
    if set(expected)!=set(TABLES):
        raise ValueError("Unknown ILOSTAT original SDG table")
    countries=json.loads((Path(data)/"catalog.json").read_text(encoding="utf-8"))["geographies"]
    if not isinstance(countries,dict):
        raise ValueError("Approved public geography reference missing")
    groups=defaultdict(dict)
    counts=Counter()
    finite=0
    with zipfile.ZipFile(archive) as z:
        if set(z.namelist())!={"ilostat_sdg_labour_source_native.csv",
                               "ORIGINAL_SOURCES.json","CITATION_AND_USAGE.txt"}:
            raise ValueError("Unexpected ILOSTAT source release members")
        original_sources=json.loads(z.read("ORIGINAL_SOURCES.json"))
        if {x["table_id"] for x in original_sources}!=set(TABLES):
            raise ValueError("Original ILOSTAT source file receipts missing")
        with z.open("ilostat_sdg_labour_source_native.csv") as stream:
            reader=csv.DictReader(io.TextIOWrapper(stream,encoding="utf-8"))
            if reader.fieldnames!=FIELDS:
                raise ValueError("Unexpected ILOSTAT original source columns")
            for item in reader:
                table=item["table_id"]
                geo=item["geography_code"]
                period=item["year"]
                series=item["original_series_id"]
                status=item["source_status"]
                if (table not in TABLES or not re.fullmatch(r"[A-Z0-9_]{2,20}",geo)
                    or not re.fullmatch(r"[12][0-9]{3}",period)
                    or not re.fullmatch(re.escape(table)+r"_[0-9a-f]{16}",series)
                    or status not in {"finite_numeric","source_missing_or_nonnumeric"}):
                    raise ValueError("Unexpected ILOSTAT source identity or period")
                dims=json.loads(item["original_dimensions_json"])
                if not isinstance(dims,dict) or any(
                    not isinstance(k,str) or not isinstance(v,str) for k,v in dims.items()
                ):
                    raise ValueError("Malformed ILOSTAT original classifications")
                counts[table]+=1
                if status!="finite_numeric":
                    continue
                try:value=float(item["original_value_text"])
                except (ValueError,OverflowError) as exc:
                    raise ValueError("ILOSTAT numeric flag and value disagree") from exc
                if not math.isfinite(value):
                    raise ValueError("Nonfinite source row marked numeric")
                finite+=1
                key=(table,series)
                record=groups[geo].get(key)
                if record is None:
                    record={"id":series,"table_id":table,"dimensions":dims,"rows":[]}
                    groups[geo][key]=record
                if record["dimensions"]!=dims:
                    raise ValueError("ILOSTAT original series key dimension mismatch")
                record["rows"].append([int(period),value])
    if any(counts[table]!=expected[table] for table in TABLES):
        raise ValueError("ILOSTAT original table counts changed")
    if sum(counts.values())!=EXPECTED_RECORDS or finite!=EXPECTED_NUMERIC:
        raise ValueError("ILOSTAT finite original numeric rows mismatch")
    destination=Path(output)/"data"/"ilostat"/"charts"
    destination.mkdir(parents=True,exist_ok=True)
    catalog=[]
    for i, (geo,entries) in enumerate(sorted(groups.items())):
        sorted_series=sorted(entries.values(),key=lambda r:(r["table_id"],r["id"]))
        for j,series in enumerate(sorted_series,1):
            series["rows"].sort()
            series["title"]=safe_title(series["table_id"],series["dimensions"],j)
            years=[row[0] for row in series["rows"]]
            series["plot_safe"]=len(set(years))==len(years)
            series["first_year"]=min(years)
            series["last_year"]=max(years)
        name=countries.get(geo,{}).get("name")
        if not name:
            name="ILOSTAT area "+str(i+1)+" (official name not yet verified)"
        filename=f"{i:04d}.json"
        (destination/filename).write_text(
            json.dumps({"code":geo,"series":sorted_series},
                       ensure_ascii=False,separators=(",",":"))+"\n",encoding="utf-8")
        catalog.append({"code":geo,"name":name,"file":filename,
                        "series_count":len(sorted_series),
                        "numeric_records":sum(len(s["rows"]) for s in sorted_series),
                        "geography_type":countries.get(geo,{}).get("kind","unclassified")})
    result={
        "schema_version":1,"source":"International Labour Organization — ILOSTAT",
        "original_source_url":"https://ilostat.ilo.org/data/bulk/",
        "license_url":"https://www.ilo.org/rights-and-permissions",
        "source_archive_sha256":manifest["source_archive_sha256"],
        "public_release_file_sha256":manifest["file_sha256"],
        "source_edition_id":manifest["source_edition_id"],
        "source_native_records":EXPECTED_RECORDS,
        "finite_numeric_records":finite,
        "chartable_geographies":len(catalog),
        "original_table_count":3,
        "annual_source_defined_not_harmonized":True,
        "geographies":catalog,
    }
    (destination/"catalog.json").write_text(
        json.dumps(result,ensure_ascii=False,separators=(",",":"))+"\n",
        encoding="utf-8")
    return result
