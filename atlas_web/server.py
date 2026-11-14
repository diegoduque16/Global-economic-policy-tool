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


class AtlasStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f"Atlas SQLite snapshot not found: {self.path}")
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
        return {
            "canonical_records": sum(int(x["observations"] or 0) for x in canonical),
            "canonical_families": len(canonical),
            "native_records": sum(int(x["observations"] or 0) for x in native),
            "native_sources": len(native),
            "snapshots": snapshots,
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
            return [dict(r) for r in db.execute(sql, (fid, series, series, q, "%" + q + "%"))]

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
        if truncated: blocked.append("Selection exceeds the displayed row limit; narrow the filters.")
        if len(units)>1: blocked.append("Multiple source units; do not plot on one scale.")
        if len(periods)>1: blocked.append("Mixed frequencies; narrow the selection.")
        if duplicate_keys: blocked.append("Multiple observations share a geography, period and series; use the table to inspect disaggregations.")
        if layer=="source_native": blocked.append("Source-native units/definitions have not been independently harmonized.")
        if any(r["value"] is None for r in items): blocked.append("Some source-native values are suppressed or nonnumeric; these are retained in the table.")
        return {"rows":items, "returned":len(items), "truncated":truncated,
                "units":units,"frequencies":periods,
                "duplicate_keys":duplicate_keys, "chart_warnings":blocked,
                "layer":layer, "family":fid, "series":series,
                "can_plot":not blocked and bool(items),
                "not_cross_provider_harmonized":True}

    def export_csv(self, *args):
        payload=self.rows(*args,limit=CSV_LIMIT)
        if not payload["rows"]:
            raise ValueError("No records to export")
        cols=["provider","geography","year","period","series","value","raw_value",
              "unit","frequency","source_status","source_url"]
        output=io.StringIO()
        writer=csv.DictWriter(output,fieldnames=cols,extrasaction="ignore")
        writer.writeheader();writer.writerows(payload["rows"])
        return output.getvalue()


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
    ap.add_argument("--db",required=True,help="Existing verified Atlas warehouse.sqlite snapshot")
    ap.add_argument("--host",default="127.0.0.1",
                    help="Bind locally by default; a remote host MUST add authentication")
    ap.add_argument("--port",type=int,default=8765)
    args=ap.parse_args()
    if args.host not in ("127.0.0.1","localhost","::1"):
        ap.error("External hosting requires an authenticated reverse proxy; local-only by default")
    AtlasHandler.store=AtlasStore(args.db)
    server=ThreadingHTTPServer((args.host,args.port),AtlasHandler)
    print(f"Atlas Data Explorer: http://{args.host}:{args.port}/")
    print("Read-only source-separated warehouse; no data is uploaded to GitHub")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__=="__main__":
    main()
