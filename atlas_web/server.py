#!/usr/bin/env python3
"""Private, read-only Atlas economic-data explorer.

Uses the *existing* warehouse.sqlite. It exposes source-separated canonical
observations and staged source-native records without combining their totals,
silently casting suppressed values, or creating another database.
Standard-library-only backend. Bind to loopback unless deployed behind auth.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import mimetypes
import re
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
LIMIT = 2000
CSV_LIMIT = 10000


def numeric(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if math.isfinite(n) else None


def geography_reference():
    """Reuse the already verified WDI public-release geography codes, never infer all are countries.

    A public catalog has authoritative original country-vs-aggregate labels for
    261 WDI codes. Any newer/private code absent there stays unclassified
    unless also independently present in Atlas's ISO3 country reference.
    This does not alter, publish or relabel observations in either database.
    """
    names = {"WLD": "World aggregate"}
    kinds = {"WLD": "aggregate"}
    iso_path = HERE.parent / "research" / "iso3_country_codes.csv"
    if iso_path.is_file():
        with iso_path.open(encoding="utf-8-sig", newline="") as source:
            for row in csv.DictReader(source):
                code, name = row.get("iso3", ""), row.get("name", "")
                if code and name and code != "WLD":
                    names[code] = name
                    kinds[code] = "country_or_territory"
    catalog_path = HERE.parent / "atlas_public" / "data" / "catalog.json"
    if catalog_path.is_file():
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        geos = catalog.get("geographies", {})
        summary = catalog.get("summary", {})
        if (catalog.get("public_edition") is not True
            or not isinstance(geos, dict)
            or len(geos) != summary.get("geographies")
            or sum(v.get("kind") == "aggregate" for v in geos.values()) != summary.get("aggregate_codes")
            or sum(v.get("kind") == "country_or_territory" for v in geos.values()) != summary.get("country_or_territory_codes")):
            raise ValueError("Atlas verified public WDI geography catalog changed")
        for code, entry in geos.items():
            if entry.get("kind") not in ("aggregate", "country_or_territory"):
                raise ValueError("Invalid World Bank geographic classification")
            names[code] = entry["name"]
            kinds[code] = entry["kind"]
    return names, kinds


YEAR_PERIOD = re.compile(r"[0-9]{4}\Z")
MONTH_PERIOD = re.compile(r"([0-9]{4})-(0[1-9]|1[0-2])\Z")
ISO_DATE_PERIOD = re.compile(r"([0-9]{4})-(0[1-9]|1[0-2])-([0-2][0-9]|3[01])\Z")
QUARTER_PERIOD = re.compile(r"[0-9]{4}-Q[1-4]\Z")


def plot_period_granularity(rows):
    """Classify one source-native time axis without inferring an annual series.

    Charting is allowed only when original source frequencies and period
    encodings agree. Mixed monthly YYYY-MM and YYYY-MM-DD representations
    are deliberately not silently treated as identical points.
    """
    if not rows:
        return "unsupported"
    periods = [str(r.get("period") or "") for r in rows]
    frequencies = {
        str(r.get("frequency") or "").strip().lower() for r in rows
    }
    names = {
        "m": "monthly", "monthly": "monthly",
        "q": "quarterly", "quarterly": "quarterly",
        "a": "annual", "annual": "annual", "yearly": "annual",
    }
    modes = {names.get(freq, freq) for freq in frequencies}
    if modes == {"monthly"}:
        if all(MONTH_PERIOD.fullmatch(p) for p in periods):
            return "monthly"
        if all(ISO_DATE_PERIOD.fullmatch(p) for p in periods):
            # Reject invalid calendar dates (e.g. February 31).
            from datetime import date
            try:
                for period in periods:
                    date.fromisoformat(period)
            except ValueError:
                return "unsupported"
            return "monthly"
    if modes == {"quarterly"} and all(QUARTER_PERIOD.fullmatch(p) for p in periods):
        return "quarterly"
    if all(YEAR_PERIOD.fullmatch(p) for p in periods) and not modes.intersection(
        {"monthly", "quarterly"}
    ):
        return "annual"
    return "unsupported"


class AtlasStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f"Atlas SQLite snapshot not found: {self.path}")
        # Reuse the verified public WDI source geography classifications.
        self.geography_names, self.geography_types = geography_reference()
        with self.connect() as db:
            required = {"observations", "source_files"}
            if not required.issubset(self.tables(db)):
                raise ValueError("Expected Atlas canonical observations/source_files tables")

    def connect(self):
        # SQLite read-only URI, not a writeable copy or shared public dataset.
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @staticmethod
    def tables(db):
        return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def families(self):
        with closing(self.connect()) as db:
            rows = [dict(r) for r in db.execute(
                """SELECT family AS id, family AS name, 'source_separated' AS layer,
                          csv_rows AS observations, first_year, last_year,
                          country_codes AS geographies, series_count AS series
                   FROM source_files ORDER BY family""")]
            if {"native_sources", "native_records"}.issubset(self.tables(db)):
                rows.extend(dict(r) for r in db.execute(
                    """SELECT source_id AS id, source_name AS name, 'source_native' AS layer,
                              indexed_records AS observations, NULL AS first_year,
                              NULL AS last_year, NULL AS geographies, NULL AS series
                       FROM native_sources ORDER BY source_id"""))
            return rows

    def family(self, fid, layer=None):
        match = next((x for x in self.families() if x["id"] == fid and
                      (layer is None or x["layer"] == layer)), None)
        if match is None:
            raise ValueError("Choose a source family from the available inventory")
        return match

    def overview(self):
        families = self.families()
        canonical = [x for x in families if x["layer"] == "source_separated"]
        native = [x for x in families if x["layer"] == "source_native"]
        with closing(self.connect()) as db:
            snapshots = []
            if "snapshots" in self.tables(db):
                snapshots = [dict(r) for r in db.execute(
                    "SELECT snapshot_id, created_utc, data_harmonized FROM snapshots ORDER BY created_utc DESC LIMIT 5")]
        # Read the established Atlas registry summary; never build another
        # catalogue or reclassify stale LIVE labels as verified datasets.
        registry_path = HERE.parent / "data" / "registry" / "registry_summary.json"
        catalog = None
        if registry_path.is_file():
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            evidence = registry.get("priority_p0_evidence_summary") or {}
            catalog = {
                "source_descriptions": int(registry.get("source_count", 0)),
                "priority_sources": int((registry.get("by_priority") or {}).get("P0", 0)),
                "domains": registry.get("by_domain") or {},
                "priority_evidence": evidence.get("by_evidence_basis") or {},
                "exact_series_audits_verified_by_registry": int(evidence.get("exact_series_key_audits_completed", 0)),
                "note": "Registry statuses are historic evidence mappings, not proof of complete ingested datasets.",
            }
        return {
            "canonical_records": sum(int(x["observations"] or 0) for x in canonical),
            "canonical_families": len(canonical),
            "native_records": sum(int(x["observations"] or 0) for x in native),
            "native_sources": len(native),
            "snapshots": snapshots,
            "catalog": catalog,
            "counts_additive": False,
            "research_ready": False,
            "note": ("Canonical observations and additional source-native rows can overlap. "
                     "They are displayed separately, are not statistically harmonized, "
                     "and must not be summed into a unique-observation total."),
        }

    def series(self, fid, layer, q="", max_results=250):
        self.family(fid, layer)
        q = q.strip()[:80]
        with closing(self.connect()) as db:
            if layer == "source_separated":
                rows = db.execute(
                    """SELECT series AS name, COUNT(*) AS rows,
                              MIN(year) AS first_year, MAX(year) AS last_year,
                              COUNT(DISTINCT unit) AS units
                       FROM observations WHERE family=?
                         AND (?='' OR series LIKE ?)
                       GROUP BY series ORDER BY rows DESC LIMIT ?""",
                    (fid, q, "%" + q + "%", max_results)).fetchall()
            else:
                rows = db.execute(
                    """SELECT series_code AS name, COUNT(*) AS rows,
                              NULL AS first_year, NULL AS last_year, NULL AS units
                       FROM native_records WHERE source_id=? AND series_code != ''
                         AND (?='' OR series_code LIKE ?)
                       GROUP BY series_code ORDER BY rows DESC LIMIT ?""",
                    (fid, q, "%" + q + "%", max_results)).fetchall()
            return [dict(x) for x in rows]

    def geographies(self, fid, layer, series="", q=""):
        self.family(fid, layer)
        with closing(self.connect()) as db:
            if layer == "source_separated":
                sql = """SELECT country_or_polity_code AS code, COUNT(*) AS rows
                         FROM observations WHERE family=?
                         AND (?='' OR series=?)
                         AND (?='' OR country_or_polity_code LIKE ?)
                         GROUP BY country_or_polity_code ORDER BY rows DESC LIMIT 350"""
            else:
                sql = """SELECT geo_code AS code, COUNT(*) AS rows
                         FROM native_records WHERE source_id=?
                         AND (?='' OR series_code=?)
                         AND (?='' OR geo_code LIKE ?)
                         GROUP BY geo_code ORDER BY rows DESC LIMIT 350"""
            geographies = [dict(r) for r in db.execute(sql, (fid, series, series, q, "%" + q + "%"))]
            for row in geographies:
                code = row.get("code") or ""
                row["name"] = self.geography_names.get(code, code)
                row["geography_type"] = self.geography_types.get(code, "unclassified")
            return geographies

    def rows(self, fid, layer, series, geos, first, last, limit=LIMIT):
        self.family(fid, layer)
        if not series or len(series)>160:
            raise ValueError("Choose an indicator/series first")
        if len(geos)>8 or any(len(x)>32 for x in geos):
            raise ValueError("Choose up to eight geographic areas")
        if first>last or first< -1000 or last>2200:
            raise ValueError("Invalid year range")
        limit=max(1, min(CSV_LIMIT, int(limit)))
        with closing(self.connect()) as db:
            args = [fid, series, first, last]
            if layer == "source_separated":
                sql = """SELECT provider, country_or_polity_code AS geography, year,
                                period, series, value, unit, frequency, source_status,
                                source_url
                         FROM observations WHERE family=? AND series=?
                           AND year BETWEEN ? AND ?"""
                if geos:
                    sql += " AND country_or_polity_code IN (" + ",".join("?" for _ in geos) + ")"
                    args += geos
                sql += " ORDER BY year, country_or_polity_code, period LIMIT ?"
            else:
                sql = """SELECT source_id AS provider, geo_code AS geography, year_text AS year,
                                period_text AS period, series_code AS series,
                                value_text AS value
                         FROM native_records WHERE source_id=? AND series_code=?
                           AND year_text BETWEEN ? AND ?"""
                args[2], args[3] = str(first).zfill(4), str(last).zfill(4)
                if geos:
                    sql += " AND geo_code IN (" + ",".join("?" for _ in geos) + ")"
                    args += geos
                sql += " ORDER BY year_text, geo_code, period_text LIMIT ?"
            args.append(limit+1)
            raw = [dict(x) for x in db.execute(sql, args)]
        truncated = len(raw)>limit
        raw = raw[:limit]
        items=[]
        for r in raw:
            v=numeric(r["value"])
            if layer == "source_native":
                item = {**r, "year": int(r["year"]) if str(r["year"]).isdigit() else None,
                        "value":v, "raw_value":r["value"], "unit":"source-defined / unverified",
                        "frequency":"source-native", "source_status":"SOURCE_NATIVE_UNHARMONIZED",
                        "source_url":""}
            else:
                item = {**r, "value":v, "raw_value":str(r["value"])}
            items.append(item)
        units = sorted({r["unit"] for r in items})
        periods = sorted({r["frequency"] for r in items})
        keys = [(r["geography"],r["series"],r["period"]) for r in items]
        duplicate_keys = len(keys)-len(set(keys))
        blocked = []
        warnings = []
        if truncated:
            blocked.append("Selection exceeds the displayed row limit; narrow the filters.")
        if len(units)>1:
            blocked.append("Multiple source units; do not plot on one scale.")
        if len(periods)>1:
            blocked.append("Mixed source frequencies; narrow the selection.")
        if duplicate_keys:
            blocked.append("Repeated geography/period/series keys; inspect source disaggregations in Table.")
        granularity = plot_period_granularity(items)
        if granularity == "unsupported":
            blocked.append("Unknown or inconsistent source periods; use Table and inspect original frequency.")
        if granularity == "annual":
            year_keys = [(r["geography"], r["year"]) for r in items if r["year"] is not None]
            if len(year_keys) != len(set(year_keys)):
                blocked.append("Multiple observations within a year; inspect source disaggregations in Table.")
        if layer == "source_native":
            warnings.append("Exploratory only: source-native units and definitions have not been independently harmonized.")
        if any(r["value"] is None for r in items):
            warnings.append("Suppressed or nonnumeric source values are retained in Table and excluded from charts.")
        return {"rows":items, "returned":len(items), "truncated":truncated,
                "units":units,"frequencies":periods,
                "duplicate_keys":duplicate_keys, "chart_warnings":blocked+warnings,
                "chart_blockers":blocked,
                "layer":layer, "family":fid, "series":series,
                "plot_granularity":granularity,
                "can_plot":not blocked and any(r["value"] is not None for r in items),
                "not_cross_provider_harmonized":True}

    def export_csv(self, *args):
        payload=self.rows(*args,limit=CSV_LIMIT)
        if not payload["rows"]:
            raise ValueError("No records to export")
        if payload["truncated"]:
            raise ValueError("CSV export exceeds 10,000 rows; narrow the years or geographies")
        cols=["provider","geography","year","period","series","value","raw_value",
              "unit","frequency","source_status","source_url"]
        output=io.StringIO()
        writer=csv.DictWriter(output,fieldnames=cols,extrasaction="ignore")
        writer.writeheader()
        for row in payload["rows"]:
            safe = {}
            for key in cols:
                value = row.get(key, "")
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                    # Prevent spreadsheet-formula injection when exported CSV is opened.
                    value = "'" + value
                safe[key] = value
            writer.writerow(safe)
        return output.getvalue()



class NativeAttachmentStore(AtlasStore):
    """Open a SHA-verified private native SQLite attachment without the >10GB parent.

    Reuses the existing Atlas HTTP explorer, never publishes or harmonizes data.
    Provider groups are presentation filters, not pooled comparable series.
    """

    PROVIDERS = (
        ("wdi", "World Bank WDI (private native)", "world_bank_wdi_"),
        ("wid", "World Inequality Database (private native)", "wid_world_"),
        ("fred", "FRED U.S. economic series (private native)", "fred_"),
        ("eurostat", "Eurostat (private native)", "eurostat_"),
        ("bls_jolts", "BLS JOLTS jobs and labor turnover (private native)", "bls_jolts_"),
    )
    BLS_LABELS = {"JO": "Job openings", "HI": "Hires", "TS": "Total separations",
                  "QU": "Quits", "LD": "Layoffs and discharges"}

    def __init__(self, path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f"Atlas source-native attachment not found: {self.path}")
        self.geography_names, self.geography_types = geography_reference()
        self.groups = {}
        self.source_details = {}
        with closing(self.connect()) as db:
            if not {"native_sources", "native_records"}.issubset(self.tables(db)):
                raise ValueError("Expected Atlas private source-native attachment tables")
            columns = {r[1] for r in db.execute("PRAGMA table_info(native_records)")}
            if not {"source_id", "ordinal", "year_text", "period_text", "geo_code",
                    "series_code", "value_text", "raw_row_json"}.issubset(columns):
                raise ValueError("Incompatible Atlas source-native attachment schema")
            sources = [dict(x) for x in db.execute(
                "SELECT source_id, indexed_records, original_scope FROM native_sources")]
            for group_id, label, prefix in self.PROVIDERS:
                matches = [r for r in sources if r["source_id"].startswith(prefix)]
                if matches:
                    self.groups[group_id] = {
                        "id": group_id, "name": label, "layer": "source_native",
                        "observations": sum(int(r["indexed_records"]) for r in matches),
                        "series": len(matches), "prefix": prefix,
                    }
                    for record in matches:
                        try:
                            detail = json.loads(record["original_scope"])
                        except (ValueError, TypeError):
                            detail = {}
                        self.source_details[record["source_id"]] = detail if isinstance(detail, dict) else {}
            if not self.groups or sum(x["observations"] for x in self.groups.values()) != db.execute(
                    "SELECT COUNT(*) FROM native_records").fetchone()[0]:
                raise ValueError("Unrecognized or incomplete provider sources in private Atlas attachment")

    def families(self):
        return [{k: v for k, v in group.items() if k != "prefix"}
                for group in self.groups.values()]

    def overview(self):
        result = super().overview()
        # The UI groups 976 original series/source identities into 5 providers.
        # Do not report 5 provider groups as 5 original indexed native sources.
        result["native_provider_groups"] = len(self.groups)
        result["native_sources"] = sum(group["series"] for group in self.groups.values())
        return result

    def _group(self, fid, layer):
        if layer != "source_native" or fid not in self.groups:
            raise ValueError("Choose a private source-native provider")
        return self.groups[fid]

    def _title(self, fid, code):
        if fid != "bls_jolts":
            return code
        details = next((v for key, v in self.source_details.items()
                        if key.startswith("bls_jolts_") and v.get("original_provider_series_id") == code), {})
        measure = self.BLS_LABELS.get(code[-3:-1], code[-3:-1])
        state = details.get("state_code", "?")
        industry = details.get("industry_code", "?")
        rate = "rate" if code.endswith("R") else "level"
        return f"{measure} · {rate} · state {state} · industry {industry} · seasonally adjusted"

    def series(self, fid, layer, q="", max_results=250):
        group = self._group(fid, layer)
        with closing(self.connect()) as db:
            rows = [dict(r) for r in db.execute(
                """SELECT series_code AS name, COUNT(*) AS rows,
                          MIN(substr(period_text,1,4)) AS first_year,
                          MAX(substr(period_text,1,4)) AS last_year,
                          NULL AS units
                   FROM native_records
                   WHERE source_id GLOB ? AND series_code IS NOT NULL AND series_code != ''
                   GROUP BY series_code ORDER BY rows DESC, series_code""",
                (group["prefix"] + "*",))]
        needle = q.strip().lower()[:80]
        out = []
        for item in rows:
            item["title"] = self._title(fid, item["name"])
            if not needle or needle in item["name"].lower() or needle in item["title"].lower():
                out.append(item)
                if len(out) >= max_results:
                    break
        return out

    def geographies(self, fid, layer, series="", q=""):
        group = self._group(fid, layer)
        with closing(self.connect()) as db:
            rows = [dict(r) for r in db.execute(
                """SELECT geo_code AS code, COUNT(*) AS rows FROM native_records
                   WHERE source_id GLOB ? AND (?='' OR series_code=?)
                         AND (?='' OR geo_code LIKE ?)
                         AND geo_code IS NOT NULL AND geo_code!=''
                   GROUP BY geo_code ORDER BY rows DESC LIMIT 350""",
                (group["prefix"] + "*", series, series, q, "%" + q + "%"))]
        for row in rows:
            row["name"] = self.geography_names.get(row["code"], row["code"])
            row["geography_type"] = self.geography_types.get(row["code"], "unclassified")
        return rows

    def rows(self, fid, layer, series, geos, first, last, limit=LIMIT):
        group = self._group(fid, layer)
        if not series or len(series) > 160:
            raise ValueError("Choose an indicator/series first")
        if len(geos) > 8 or any(len(x) > 32 for x in geos):
            raise ValueError("Choose up to eight geographic areas")
        if first > last or first < -1000 or last > 2200:
            raise ValueError("Invalid year range")
        limit = max(1, min(CSV_LIMIT, int(limit)))
        args = [group["prefix"] + "*", series, f"{first:04d}", f"{last:04d}"]
        sql = """SELECT source_id, ordinal, geo_code AS geography,
                        year_text, period_text AS period, series_code AS series,
                        value_text AS raw_value, raw_row_json
                 FROM native_records
                 WHERE source_id GLOB ? AND series_code=?
                   AND substr(period_text,1,4) BETWEEN ? AND ?"""
        if geos:
            sql += " AND geo_code IN (" + ",".join("?" for _ in geos) + ")"
            args.extend(geos)
        sql += " ORDER BY period_text, geo_code, source_id, ordinal LIMIT ?"
        args.append(limit + 1)
        with closing(self.connect()) as db:
            raw = [dict(r) for r in db.execute(sql, args)]
        truncated = len(raw) > limit
        items = []
        for r in raw[:limit]:
            try:
                source = json.loads(r.pop("raw_row_json"))
            except (ValueError, TypeError) as exc:
                raise ValueError("Invalid original source-native row metadata") from exc
            if not isinstance(source, dict):
                raise ValueError("Original Atlas native row is not a source object")
            dims = source.get("disaggregation", {})
            if isinstance(dims, str):
                try:
                    dims = json.loads(dims)
                except ValueError as exc:
                    raise ValueError("Invalid original dimension record") from exc
            if not isinstance(dims, dict):
                dims = {}
            period = str(r["period"] or r["year_text"] or "")
            year = int(period[:4]) if len(period) >= 4 and period[:4].isdigit() else None
            source_detail = self.source_details.get(r["source_id"], {})
            unit = str(source.get("unit") or source_detail.get("unit") or "source_defined_unverified")
            frequency = str(source.get("frequency") or ("annual" if len(period) == 4 else "source_native"))
            flag = str(source.get("status_flag") or source.get("obs_status") or source.get("data_status") or "")
            item = {
                "provider": str(source.get("provider") or source_detail.get("provider") or group["name"]),
                "geography": r["geography"], "year": year, "period": period,
                "series": r["series"], "value": numeric(r["raw_value"]),
                "raw_value": r["raw_value"], "unit": unit,
                "frequency": frequency,
                "source_status": ("SOURCE_NATIVE_UNHARMONIZED" + (" | " + flag if flag else "")),
                "source_url": str(source.get("source_url") or source_detail.get("original_source_url")
                                  or source_detail.get("source_url") or ""),
                "source_id": r["source_id"], "state_code": dims.get("state_code", ""),
                "industry_code": dims.get("industry_code", ""),
                "disaggregation": json.dumps(dims, ensure_ascii=False, sort_keys=True),
            }
            items.append(item)
        units = sorted({r["unit"] for r in items})
        freqs = sorted({r["frequency"] for r in items})
        keys = [(r["geography"], r["series"], r["period"]) for r in items]
        duplicate_keys = len(keys) - len(set(keys))
        blockers = []
        if truncated:
            blockers.append("Selection exceeds displayed row limit; narrow the filters.")
        if len(units) > 1:
            blockers.append("Multiple source units; do not plot on one scale.")
        if len(freqs) > 1:
            blockers.append("Mixed frequencies; narrow the selection.")
        if duplicate_keys:
            blockers.append("Repeated geography/period/series keys; inspect original dimensions.")
        granularity = plot_period_granularity(items)
        if granularity == "unsupported":
            blockers.append("Unknown or inconsistent original source periods; inspect Table or CSV.")
        if granularity == "annual":
            yearly = [(r["geography"], r["year"]) for r in items if r["year"] is not None]
            if len(yearly) != len(set(yearly)):
                blockers.append("Repeated original annual periods; inspect source dimensions.")
        warnings = ["Private original source-native data; units, vintages and providers are not harmonized."]
        if any(r["value"] is None for r in items):
            warnings.append("Suppressed/nonnumeric values are retained without zero filling.")
        return {"rows": items, "returned": len(items), "truncated": truncated,
                "units": units, "frequencies": freqs, "duplicate_keys": duplicate_keys,
                "chart_warnings": blockers + warnings, "chart_blockers": blockers,
                "layer": layer, "family": fid, "series": series,
                "plot_granularity": granularity,
                "can_plot": not blockers and any(r["value"] is not None for r in items),
                "not_cross_provider_harmonized": True}

    def export_csv(self, *args):
        result = self.rows(*args, limit=CSV_LIMIT)
        if not result["rows"]:
            raise ValueError("No source-native records to export")
        if result["truncated"]:
            raise ValueError("CSV export exceeds 10,000 rows; narrow the filters")
        fields = ("provider", "geography", "year", "period", "series", "value",
                  "raw_value", "unit", "frequency", "source_status", "source_url",
                  "source_id", "state_code", "industry_code", "disaggregation")
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in result["rows"]:
            safe = {}
            for key in fields:
                value = row.get(key, "")
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                    value = "'" + value
                safe[key] = value
            writer.writerow(safe)
        return stream.getvalue()


class AtlasHandler(BaseHTTPRequestHandler):
    store=None

    def send_data(self, data, status=200, mime="application/json; charset=utf-8", download=False):
        payload=(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
                 if not isinstance(data,bytes) else data)
        self.send_response(status)
        self.send_header("Content-Type",mime)
        self.send_header("Content-Length",str(len(payload)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Security-Policy","default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'")
        if download:
            self.send_header("Content-Disposition",'attachment; filename="atlas-export.csv"')
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        url=urlsplit(self.path)
        if not url.path.startswith("/api/"):
            files={"/":"index.html","/index.html":"index.html",
                   "/style.css":"style.css","/app.js":"app.js"}
            name=files.get(url.path)
            if not name:
                return self.send_data({"error":"Not found"},404)
            path=STATIC/name
            if not path.is_file():
                return self.send_data({"error":"Website asset missing"},500)
            mime=mimetypes.guess_type(name)[0] or "text/plain"
            return self.send_data(path.read_bytes(),mime=mime+"; charset=utf-8")
        query={k:v[-1] for k,v in parse_qs(url.query).items()}
        try:
            if url.path=="/api/overview": out=self.store.overview()
            elif url.path=="/api/families": out=self.store.families()
            else:
                fid=query.get("family","")
                layer=query.get("layer","source_separated")
                if layer not in ("source_separated","source_native"):
                    raise ValueError("Unsupported source layer")
                if url.path=="/api/series":
                    out=self.store.series(fid,layer,query.get("q",""))
                elif url.path=="/api/geographies":
                    out=self.store.geographies(fid,layer,query.get("series",""),query.get("q",""))
                elif url.path in ("/api/rows","/api/export"):
                    geos=[x.strip() for x in query.get("geos","").split(",") if x.strip()]
                    start=int(query.get("start","1900"))
                    end=int(query.get("end","2026"))
                    args=(fid,layer,query.get("series",""),geos,start,end)
                    if url.path=="/api/rows":
                        out=self.store.rows(*args,limit=min(LIMIT,max(1,int(query.get("limit",LIMIT)))))
                    else:
                        return self.send_data(self.store.export_csv(*args).encode("utf-8"),
                                              mime="text/csv; charset=utf-8",download=True)
                else:
                    return self.send_data({"error":"Unknown endpoint"},404)
            return self.send_data(out)
        except (ValueError,TypeError) as exc:
            return self.send_data({"error":str(exc)},400)
        except Exception:
            self.log_error("Query failed")
            return self.send_data({"error":"Source query could not be completed"},500)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db",required=True,help="Verified Atlas canonical SQLite or private source-native attachment")
    ap.add_argument("--host",default="127.0.0.1",
                    help="Bind locally by default; a remote host MUST add authentication")
    ap.add_argument("--port",type=int,default=8765)
    args=ap.parse_args()
    if args.host not in ("127.0.0.1","localhost","::1"):
        ap.error("External hosting requires an authenticated reverse proxy; local-only by default")
    db_path = Path(args.db).resolve()
    if not db_path.is_file():
        ap.error("The selected Atlas SQLite file does not exist")
    # The 883,118-row private attachment is a compact *separate* SQLite,
    # while the older canonical warehouse has observations/source_files.
    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as db:
        kinds = AtlasStore.tables(db)
    if {"observations", "source_files"}.issubset(kinds):
        AtlasHandler.store = AtlasStore(db_path)
    elif {"native_sources", "native_records"}.issubset(kinds):
        AtlasHandler.store = NativeAttachmentStore(db_path)
    else:
        ap.error("Unrecognized Atlas SQLite release: neither canonical nor native attachment")
    server=ThreadingHTTPServer((args.host,args.port),AtlasHandler)
    print(f"Atlas Data Explorer: http://{args.host}:{args.port}/")
    print("Read-only Atlas data; this localhost explorer does not publish private source records")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__=="__main__":
    main()
