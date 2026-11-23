"""Snapshot ONLY source-relevant official ILOSTAT English dictionary labels.

Reads the published, immutable Atlas ILOSTAT source ZIP and ILO official
metadata API. Writes separately verifiable, bounded, source-attributed labels.
Never changes observations, original classifications, private SQLite, or
numeric source provenance.
"""
from __future__ import annotations

from collections import defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import zipfile

FIELDS=("ref_area","sex","classif1","classif2","source","indicator")
BASE="https://rplumber.ilo.org/metadata/dic"
EXPECTED_ZIP_SHA256="aa6431d19449b78e99b46450f6a5883243460864b7f7a8f873f4dd4f54152831"
MAX_BYTES=12_000_000
NAME="dictionaries_en.json"


def requested_codes(data:Path)->dict[str,set[str]]:
    root=Path(data)/"ilostat"
    archive=root/"ilostat_sdg_labour_source_native.csv.zip"
    manifest=json.loads((root/"ilostat_sdg_manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("release_type")!="public_ilostat_sdg_annual_source_native"
        or manifest.get("file_sha256")!=EXPECTED_ZIP_SHA256
        or hashlib.sha256(archive.read_bytes()).hexdigest()!=EXPECTED_ZIP_SHA256):
        raise ValueError("Public original ILOSTAT source edition changed")
    codes={f:set() for f in FIELDS}
    row_count=0
    with zipfile.ZipFile(archive) as z:
        with z.open("ilostat_sdg_labour_source_native.csv") as src:
            reader=csv.DictReader(io.TextIOWrapper(src,encoding="utf-8"))
            if "original_dimensions_json" not in (reader.fieldnames or []):
                raise ValueError("ILOSTAT public dimensions missing")
            for row in reader:
                row_count+=1
                dimensions=json.loads(row["original_dimensions_json"])
                if not isinstance(dimensions,dict):
                    raise ValueError("Invalid official original dimensions")
                for field in FIELDS:
                    code=(row["geography_code"] if field=="ref_area" else dimensions.get(field)) or ""
                    if code:
                        if not isinstance(code,str) or len(code)>180:
                            raise ValueError("Unexpected original dictionary code")
                        codes[field].add(code)
    if row_count!=153116:
        raise ValueError("Unexpected original ILOSTAT source row count")
    return codes


def parse_dictionary(field:str,raw:bytes)->dict[str,str]:
    if field not in FIELDS or not raw or len(raw)>MAX_BYTES:
        raise ValueError("Invalid official dictionary source")
    try:
        source=raw.decode("utf-8-sig")
    except UnicodeError as ex:
        raise ValueError("Unreadable official ILOSTAT dictionary") from ex
    reader=csv.DictReader(io.StringIO(source))
    columns=reader.fieldnames or []
    code_col=field if field in columns else ("var_name" if "var_name" in columns else None)
    label_col=(field+".label" if field+".label" in columns else
               "var_label" if "var_label" in columns else None)
    if not code_col or not label_col or len(set(columns))!=len(columns):
        raise ValueError("Unexpected original ILOSTAT English dictionary CSV columns")
    found={}
    count=0
    for row in reader:
        count+=1
        if count>350_000 or None in row:
            raise ValueError("Unbounded or malformed ILOSTAT dictionary")
        code=(row.get(code_col) or "").strip()
        label=(row.get(label_col) or "").strip()
        if not code or not label or len(code)>180 or len(label)>420 or "<script" in label.lower():
            continue
        if code in found and found[code]!=label:
            raise ValueError("Official ILOSTAT code has conflicting English labels")
        found[code]=label
    if not found:
        raise ValueError("Empty official ILOSTAT dictionary")
    return found


def build(data:Path,fetch=None)->dict:
    codes=requested_codes(data)
    results={}
    metadata={}
    for field in FIELDS:
        url=BASE+"?"+urlencode({"var":field,"lang":"en","format":".csv"})
        if fetch:
            raw=fetch(field,url)
        else:
            req=Request(url,headers={"User-Agent":"Mozilla/5.0",
                                     "Accept":"text/csv"})
            with urlopen(req,timeout=90) as response:
                raw=response.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:
            raise ValueError("Official ILOSTAT dictionary response exceeds bound")
        lookup=parse_dictionary(field,raw)
        matched={code:lookup[code] for code in sorted(codes[field]) if code in lookup}
        metadata[field]={
            "official_url":url,
            "official_raw_sha256":hashlib.sha256(raw).hexdigest(),
            "requested_original_codes":len(codes[field]),
            "matched_original_codes":len(matched),
            "unmatched_original_codes":len(codes[field])-len(matched),
        }
        results[field]=matched
    if not results["sex"] or not results["ref_area"] or not any(
        results[f] for f in ("classif1","classif2")
    ):
        raise ValueError("Official sex, geography and classification labels were not resolved")
    return {
        "schema_version":1,"dictionary_language":"English",
        "official_metadata_endpoint":BASE,
        "original_source_zip_sha256":EXPECTED_ZIP_SHA256,
        "captured_utc":datetime.now(timezone.utc).isoformat(),
        "label_provenance":"Official ILOSTAT dictionaries, not inferred descriptions",
        "source_metadata":metadata,
        "codes":results
    }


def main()->None:
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data",type=Path,default=Path("atlas_public/data"))
    parser.add_argument("--output",type=Path,
                        default=Path("atlas_public/data/ilostat")/NAME)
    args=parser.parse_args()
    result=build(args.data)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("ATLAS_OFFICIAL_ILOSTAT_DICTIONARY_LABELS_VERIFIED",
          {k:(v["matched_original_codes"],v["requested_original_codes"])
           for k,v in result["source_metadata"].items()})


if __name__=="__main__":
    main()
