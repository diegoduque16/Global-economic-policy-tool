#!/usr/bin/env python3
"""Official World Bank WDI bulk ZIP transport for unavailable Indicators API.

Uses ONLY https://databank.worldbank.org/data/download/WDI_CSV.zip, a World
Bank primary-source distribution. Preserves source ZIP bytes in the artifact.
Native country/territory AND aggregate codes are kept. No harmonization,
imputation, private-data access, or publishing takes place.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import time
import urllib.error
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

BULK_URL = "https://databank.worldbank.org/data/download/WDI_CSV.zip"
TRANSPORT = "WORLD_BANK_OFFICIAL_WDI_BULK_CSV"
RAW_ZIP = "raw/WDI_CSV.zip"
PANEL = "processed/wdi_panel.csv"
MAX_ZIP_BYTES = 525_000_000
MAX_UNCOMPRESSED_BYTES = 4_000_000_000
CSV_COLUMNS = [
    "country_iso3", "year", "indicator", "value", "unit",
    "source", "source_url", "downloaded_at_utc", "obs_status", "decimal",
]
INDICATOR = re.compile(r"[A-Za-z0-9_.-]{2,100}\Z")
GEO = re.compile(r"[A-Z]{3}\Z")
YEAR = re.compile(r"(\d{4})(?: \[YR(\d{4})\])?\Z")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def official_url(url: str) -> bool:
    p = urlsplit(url)
    return p.scheme == "https" and (p.hostname == "worldbank.org" or
                                     (p.hostname or "").endswith(".worldbank.org"))


def download_official_zip(destination: Path, retries: int = 3,
                          timeout: int = 90, pause=time.sleep) -> dict:
    """Bounded retries and streaming: never buffer a 280MB ZIP in RAM."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not 1 <= retries <= 5:
        raise ValueError("Unsafe World Bank bulk retry count")
    last = None
    for attempt in range(retries):
        tmp = destination.with_name(destination.name + ".partial")
        tmp.unlink(missing_ok=True)
        try:
            req = Request(BULK_URL, headers={
                "User-Agent": "WorldEconomicAtlas/1.0 (source-preserving economic research)",
                "Accept": "application/zip, application/octet-stream",
            })
            with urlopen(req, timeout=timeout) as response:
                if response.status != 200 or not official_url(response.geturl()):
                    raise ValueError("World Bank bulk download redirected to untrusted provider")
                size = 0
                h = hashlib.sha256()
                with tmp.open("wb") as file:
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        size += len(block)
                        if size > MAX_ZIP_BYTES:
                            raise ValueError("Official WDI bulk source exceeds download size cap")
                        file.write(block)
                        h.update(block)
            if size < 100:
                raise ValueError("Empty/truncated official WDI source")
            if not zipfile.is_zipfile(tmp):
                raise ValueError("World Bank bulk response is not a ZIP archive")
            tmp.replace(destination)
            return {"source_url": BULK_URL, "sha256": h.hexdigest(), "bytes": size}
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError,
                ValueError, zipfile.BadZipFile) as exc:
            last = exc
            tmp.unlink(missing_ok=True)
            if isinstance(exc, ValueError) or (isinstance(exc, urllib.error.HTTPError) and
                exc.code not in (408, 429, 500, 502, 503, 504)):
                break
            if attempt + 1 < retries:
                pause(min(20, 3 * 2 ** attempt))
    raise RuntimeError("World Bank official WDI bulk download failed: " +
                       type(last).__name__) from last


def _open_csv(archive: zipfile.ZipFile, member: str):
    if member not in archive.namelist():
        raise ValueError("Missing official World Bank WDI CSV member: " + member)
    return io.TextIOWrapper(archive.open(member), encoding="utf-8-sig", newline="")


def scan_bulk(path: Path, indicators: list[str], start: int, end: int) -> dict:
    """Read original CSV cells and produce a source-exact key/value map.

    Reading it again during validation independently verifies output values
    against the ORIGINAL ZIP, not a transformed intermediate file.
    """
    if not (1950 <= start <= end <= 2100):
        raise ValueError("Unsafe WDI bulk requested year scope")
    requested = list(dict.fromkeys(indicators))
    if not requested or len(requested) != len(indicators) or any(
            not isinstance(code, str) or not INDICATOR.fullmatch(code)
            for code in requested):
        raise ValueError("Duplicate or invalid WDI indicator list")

    values: dict[tuple[str, int, str], float] = {}
    raw_rows = 0
    skipped_blank = 0
    raw_indicator_rows = set()
    observed_indicators = set()
    geographies = set()
    min_year, max_year = None, None
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) < 2 or len(members) > 40:
            raise ValueError("Unexpected official WDI ZIP member inventory")
        for member in members:
            if (member.filename.startswith("/") or ".." in Path(member.filename).parts or
                    member.file_size > MAX_UNCOMPRESSED_BYTES or
                    member.compress_size > MAX_ZIP_BYTES or member.flag_bits & 0x1):
                raise ValueError("Unsafe or unexpectedly large WDI ZIP source member")
        if sum(m.file_size for m in members) > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("WDI official source uncompressed size exceeds safety cap")
        # World Bank has distributed both WDIData.csv and WDICSV.csv,
        # sometimes with a single archive folder prefix. Identify the
        # source member by its original filename, never inventing a file.
        names = archive.namelist()
        raw_data = [n for n in names if
                    Path(n).name in ("WDIData.csv", "WDICSV.csv")]
        raw_series = [n for n in names if Path(n).name == "WDISeries.csv"]
        if len(raw_data) != 1 or len(raw_series) != 1:
            raise ValueError("Official WDI ZIP has unrecognized CSV member layout: " +
                             ",".join(sorted(names)[:15]))

        official_series = set()
        with _open_csv(archive, raw_series[0]) as stream:
            meta = csv.DictReader(stream)
            if "Series Code" not in (meta.fieldnames or []):
                raise ValueError("Unknown official WDI series metadata schema")
            for row in meta:
                code = (row.get("Series Code") or "").strip()
                if code in requested:
                    official_series.add(code)
        if official_series != set(requested):
            raise ValueError("Official bulk ZIP series metadata missing requested codes: " +
                             ",".join(sorted(set(requested) - official_series)))

        with _open_csv(archive, raw_data[0]) as stream:
            reader = csv.DictReader(stream)
            fields = reader.fieldnames or []
            if not {"Country Code", "Indicator Code"}.issubset(fields):
                raise ValueError("Unknown official World Bank WDIData header")
            years = {}
            for column in fields:
                match = YEAR.fullmatch(column)
                if match and start <= int(match.group(1)) <= end:
                    if match.group(2) and match.group(2) != match.group(1):
                        raise ValueError("Inconsistent World Bank WDI year column")
                    year = int(match.group(1))
                    if year in years:
                        raise ValueError("Duplicate WDI year column")
                    years[year] = column
            if len(years) < min(40, end - start + 1) or start not in years:
                raise ValueError("World Bank bulk WDI archive has incomplete historical year columns")
            for row in reader:
                code = (row.get("Indicator Code") or "").strip()
                if code not in requested:
                    continue
                raw_rows += 1
                geo = (row.get("Country Code") or "").strip().upper()
                if not GEO.fullmatch(geo):
                    raise ValueError("Unknown source geography in selected WDI row")
                if None in row or any(v is None for v in row.values()):
                    raise ValueError("Malformed WDI raw source CSV row")
                raw_indicator_rows.add(code)
                for year, field in years.items():
                    cell = (row[field] or "").strip()
                    if cell == "":
                        skipped_blank += 1
                        continue
                    try:
                        numeric = float(cell)
                    except (TypeError, ValueError, OverflowError) as exc:
                        raise ValueError("Nonnumeric WDI source observation") from exc
                    if not math.isfinite(numeric):
                        raise ValueError("Nonfinite WDI source observation")
                    key = (geo, year, code)
                    if key in values:
                        raise ValueError("Duplicate official WDI geography-year-indicator key")
                    values[key] = numeric
                    observed_indicators.add(code)
                    geographies.add(geo)
                    min_year = year if min_year is None else min(year, min_year)
                    max_year = year if max_year is None else max(year, max_year)
        if observed_indicators != set(requested) or raw_indicator_rows != set(requested):
            raise ValueError("Some requested WDI indicators have no actual numeric values")
        # ZIP read validates CRC of the two source CSVs as well as metadata.
    return {
        "values": values, "raw_source_indicator_geography_rows": raw_rows,
        "blank_source_cells_in_selected_rows": skipped_blank,
        "year_columns": sorted(years), "verified_indicator_codes": sorted(observed_indicators),
        "geographies": sorted(geographies), "first_observed_year": min_year,
        "last_observed_year": max_year,
    }


def collect_bulk(out: Path, indicators: list[str], start: int = 1960,
                 end: int = 2026, *, source_zip: Path | None = None) -> dict:
    """Preserve actual provider ZIP and source-native annual values, not metadata."""
    out = Path(out)
    raw = out / RAW_ZIP
    raw.parent.mkdir(parents=True, exist_ok=True)
    (out / "processed").mkdir(parents=True, exist_ok=True)
    if source_zip is not None:
        shutil.copy2(source_zip, raw)
        info = {"source_url": BULK_URL, "sha256": digest(raw), "bytes": raw.stat().st_size}
    else:
        info = download_official_zip(raw)
    scan = scan_bulk(raw, indicators, start, end)
    stamp = datetime.now(timezone.utc).isoformat()
    panel = out / PANEL
    with panel.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(CSV_COLUMNS)
        for (geo, year, indicator), value in sorted(scan["values"].items()):
            writer.writerow([geo, year, indicator, value, "source_defined",
                             "World Bank WDI official bulk CSV", BULK_URL, stamp, "", ""])
    report = {
        "transport": TRANSPORT, "source_url": BULK_URL,
        "bulk_zip_sha256": info["sha256"], "bulk_zip_bytes": info["bytes"],
        "bulk_file": RAW_ZIP,
        "panel_sha256": digest(panel),
        "downloaded_at_utc": stamp, "country_query": "all",
        "start": start, "end": end, "indicator_count": len(indicators),
        "selected_indicator_codes": sorted(indicators),
        "observations": len(scan["values"]), "raw_pages": 0,
        "manifest": [], "errors": [], "conflicts": [],
        "raw_source_indicator_geography_rows": scan["raw_source_indicator_geography_rows"],
        "blank_source_cells_in_selected_rows": scan["blank_source_cells_in_selected_rows"],
        "source_year_columns": scan["year_columns"],
        "validation_state": "COLLECTED_FROM_OFFICIAL_BULK_NOT_YET_INDEPENDENTLY_VERIFIED",
        "canonical_integration_verified": False, "causal_claims_supported": False,
    }
    (out / "run_report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def validate_bulk_download(folder: Path) -> dict:
    """Re-parse source original ZIP and require exact match with normalized panel."""
    folder = Path(folder)
    report_path = folder / "run_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (report.get("transport") != TRANSPORT or report.get("source_url") != BULK_URL or
            report.get("country_query") != "all" or report.get("start") != 1960 or
            type(report.get("end")) is not int or report["end"] < 2026 or
            report.get("errors") != [] or report.get("conflicts") != [] or
            report.get("canonical_integration_verified") is not False):
        raise ValueError("WDI official bulk source scope/report mismatch")
    selected = report.get("selected_indicator_codes")
    if (not isinstance(selected, list) or len(selected) != report.get("indicator_count")
            or len(selected) > 200 or len(set(selected)) != len(selected)):
        raise ValueError("Missing WDI official bulk indicator scope")
    raw = folder / RAW_ZIP
    panel = folder / PANEL
    if not raw.is_file() or raw.is_symlink() or not panel.is_file() or panel.is_symlink():
        raise ValueError("Missing official WDI source ZIP or numeric panel")
    if (digest(raw) != report.get("bulk_zip_sha256") or
            raw.stat().st_size != report.get("bulk_zip_bytes") or
            digest(panel) != report.get("panel_sha256")):
        raise ValueError("Official WDI source ZIP/panel SHA256 mismatch")
    scanned = scan_bulk(raw, selected, report["start"], report["end"])
    expected = scanned["values"]
    actual = set()
    geos = set()
    indicator_counts = {code: 0 for code in selected}
    with panel.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != CSV_COLUMNS:
            raise ValueError("Unexpected WDI bulk normalized panel schema")
        for row in reader:
            geo, code = row["country_iso3"], row["indicator"]
            if not GEO.fullmatch(geo) or code not in indicator_counts:
                raise ValueError("Unapproved WDI source geography or series")
            try:
                year = int(row["year"])
                value = float(row["value"])
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("Invalid WDI official bulk processed numeric value") from exc
            key = (geo, year, code)
            if (key in actual or not math.isfinite(value) or
                    not report["start"] <= year <= report["end"] or
                    key not in expected or value != expected[key]):
                raise ValueError("WDI normalized numeric key/value differs from original official ZIP")
            if (row["source"] != "World Bank WDI official bulk CSV" or
                    row["source_url"] != BULK_URL or row["unit"] != "source_defined" or
                    row["downloaded_at_utc"] != report["downloaded_at_utc"]):
                raise ValueError("WDI bulk numeric source provenance altered")
            actual.add(key)
            geos.add(geo)
            indicator_counts[code] += 1
    if (len(actual) != len(expected) or len(actual) != report.get("observations") or
            any(n < 1 for n in indicator_counts.values()) or
            scanned["raw_source_indicator_geography_rows"] != report.get(
                "raw_source_indicator_geography_rows") or
            scanned["blank_source_cells_in_selected_rows"] != report.get(
                "blank_source_cells_in_selected_rows")):
        raise ValueError("Incomplete World Bank official ZIP-to-panel verification")
    regular = {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()}
    if (regular != {"run_report.json", RAW_ZIP, PANEL} or
            any(p.is_symlink() for p in folder.rglob("*"))):
        raise ValueError("Untracked or unsafe files in WDI bulk source artifact")
    return {
        "status": "WDI_OFFICIAL_BULK_ORIGINAL_ZIP_NUMERICALLY_VERIFIED_NOT_CANONICAL",
        "transport": TRANSPORT,
        "observations": len(actual),
        "raw_pages": 0,
        "indicators_with_raw_pages": 0,
        "verified_indicator_codes": sorted(indicator_counts),
        "observations_by_indicator": dict(sorted(indicator_counts.items())),
        "geography_codes": len(geos),
        "first_observed_year": scanned["first_observed_year"],
        "last_observed_year": scanned["last_observed_year"],
        "raw_source_rows_examined": scanned["raw_source_indicator_geography_rows"],
        "raw_rows_missing_or_excluded_by_native_normalizer":
            scanned["blank_source_cells_in_selected_rows"],
        "all_output_numeric_keys_and_values_matched_original_raw": True,
        "world_aggregate_present": "WLD" in geos,
        "country_only_geographies_verified": False,
        "geography_type_audit": "OFFICIAL_COUNTRY_VS_AGGREGATE_METADATA_STILL_REQUIRED",
        "report_sha256": digest(report_path), "panel_sha256": digest(panel),
        "official_bulk_zip_sha256": digest(raw),
        "canonical_integration_verified": False,
    }
