"use strict";
/* Public static WDI adapter. Reuses the existing Atlas dashboard unchanged.
 * Converts its /api requests into queries over approved public JSON files.
 * Only World Bank WDI data published in this site's data/ directory are exposed.
 * Does not contact or reveal the private Atlas SQLite database.
 */
(() => {
  const originalFetch = window.fetch.bind(window);
  let catalogPromise = null;
  const datasets = new Map();
  const LIMIT = 2000;
  const CSV_LIMIT = 10000;
  const SOURCE = "World Bank World Development Indicators";
  const STATUS = "WDI_SOURCE_NATIVE_NUMERIC_VERIFIED_NOT_CROSS_PROVIDER_HARMONIZED";

  function fail(message) {
    throw new Error(message);
  }
  function number(value, fallback) {
    if (value === null || value === undefined || value === "") return fallback;
    const parsed = Number(value);
    return Number.isInteger(parsed) ? parsed : fail("Invalid integer filter");
  }
  function checkCode(value) {
    if (!/^[A-Z0-9_.]{2,80}$/.test(value || "")) fail("Invalid source series code");
    return value;
  }
  function checkFamily(params) {
    if ((params.get("family") || "") !== "wdi" ||
        (params.get("layer") || "source_separated") !== "source_separated") {
      fail("This public edition currently contains the verified World Bank WDI source snapshot only");
    }
  }
  function catalog() {
    if (!catalogPromise) {
      catalogPromise = originalFetch("/data/catalog.json", {cache:"no-store"}).then(async response => {
        if (!response.ok) fail("Public WDI source catalog is not deployed");
        const data = await response.json();
        if (data.public_edition !== true ||
            !data.summary || data.summary.source_separated_numeric_checks_passed !== true ||
            data.summary.observations !== 300240 || data.summary.series !== 32) {
          fail("The published source manifest has not passed Atlas validation");
        }
        return data;
      });
    }
    return catalogPromise;
  }
  async function dataset(code) {
    checkCode(code);
    if (!datasets.has(code)) {
      const promise = catalog().then(async manifest => {
        if (!manifest.series.some(entry => entry.code === code)) {
          fail("Indicator is not in the approved public source release");
        }
        const response = await originalFetch("/data/series/" + code + ".json", {cache:"force-cache"});
        if (!response.ok) fail("Public indicator source file is unavailable");
        const result = await response.json();
        if (result.code !== code || !Array.isArray(result.rows)) {
          fail("Public source file does not match the chosen indicator");
        }
        return result;
      });
      datasets.set(code, promise);
    }
    return datasets.get(code);
  }
  function countByGeo(rows) {
    const counts = new Map();
    for (const point of rows) counts.set(point[0], (counts.get(point[0]) || 0) + 1);
    return counts;
  }
  function safeFilter(manifest, rawSeries, params, maxRows) {
    const selected = (params.get("geos") || "").split(",").filter(Boolean);
    if (selected.length > 8 || selected.some(x => !/^[A-Z0-9_.-]{2,32}$/.test(x))) {
      fail("Choose at most eight valid geographies");
    }
    const chosen = new Set(selected);
    const first = number(params.get("start"), 1900);
    const last = number(params.get("end"), 2026);
    if (first > last || first < -1000 || last > 2200) fail("Invalid year range");
    const series = rawSeries.code;
    const sourceUrl = "https://data.worldbank.org/indicator/" + encodeURIComponent(series);
    const out = [];
    for (const point of rawSeries.rows) {
      const code = point[0], year = point[1], value = point[2];
      if (year < first || year > last || (chosen.size && !chosen.has(code))) continue;
      out.push({
        provider: SOURCE, geography: code, year, period: String(year),
        series, value, raw_value: String(value), unit: "source_defined", frequency: "annual",
        source_status: STATUS, source_url: sourceUrl
      });
    }
    out.sort((a,b) => a.year - b.year || a.geography.localeCompare(b.geography));
    const truncated = out.length > maxRows;
    const rows = out.slice(0, maxRows);
    const warnings = ["Original World Bank values, not harmonized across providers. Check each indicator's source definitions."];
    if (truncated) warnings.unshift("Selection has more rows than this view supports. Narrow filters.");
    return {
      rows, returned: rows.length, truncated,
      units: ["source_defined"], frequencies: ["annual"],
      duplicate_keys: 0, chart_warnings: warnings,
      chart_blockers: truncated ? [warnings[0]] : [],
      layer: "source_separated", family: "wdi", series,
      can_plot: rows.length > 0 && !truncated,
      not_cross_provider_harmonized: true
    };
  }

  async function query(path, params) {
    const manifest = await catalog();
    if (path === "/api/overview") {
      const n = manifest.summary;
      const coverage = manifest.broader_catalog || {};
      return {
        canonical_records: n.observations, canonical_families: 1,
        native_records: 0, native_sources: 0,
        public_edition: true,
        public_geographies: n.geographies, public_series: n.series,
        counts_additive: false, research_ready: false,
        snapshots: [{snapshot_id:"WDI · 2026-11-09", created_utc: manifest.source_downloaded_at_utc, data_harmonized:0}],
        catalog: {
          source_descriptions: coverage.source_descriptions || 125,
          priority_sources: coverage.priority_sources || 71,
          exact_series_audits_verified_by_registry: coverage.exact_series_audits_verified_by_registry || 0,
          note: "Public site includes one approved WDI source edition; other Atlas sources are not publicly released yet."
        },
        note: "These are the selected 300,240 verified WDI source observations, not the entire private canonical warehouse."
      };
    }
    if (path === "/api/families") {
      return [{
        id:"wdi", name:"World Bank · World Development Indicators",
        layer:"source_separated", observations:manifest.summary.observations,
        first_year:Math.min(...manifest.series.map(x=>x.first_year)),
        last_year:Math.max(...manifest.series.map(x=>x.last_year)),
        geographies:manifest.summary.geographies, series:manifest.summary.series
      }];
    }
    checkFamily(params);
    if (path === "/api/series") {
      const q = (params.get("q") || "").toLowerCase().slice(0,90);
      return manifest.series.filter(x => !q ||
        x.code.toLowerCase().includes(q) || x.title.toLowerCase().includes(q) ||
        x.topic.toLowerCase().includes(q))
        .sort((a,b) => b.rows - a.rows)
        .slice(0,250).map(x => ({
          name:x.code, title:x.title, rows:x.rows, first_year:x.first_year,
          last_year:x.last_year, units:1
        }));
    }
    const code = checkCode(params.get("series") || "");
    const values = await dataset(code);
    if (path === "/api/geographies") {
      const q = (params.get("q") || "").toLowerCase();
      const counts = countByGeo(values.rows);
      return [...counts.entries()].filter(([geo]) => {
        const name = manifest.geographies[geo]?.name || geo;
        return !q || geo.toLowerCase().includes(q) || name.toLowerCase().includes(q);
      }).map(([geo, rows]) => ({
        code:geo, rows, name:manifest.geographies[geo]?.name || geo,
        geography_type:manifest.geographies[geo]?.kind || "unresolved"
      })).sort((a,b) => b.rows - a.rows).slice(0,350);
    }
    if (path === "/api/rows") {
      const limit = Math.min(LIMIT, Math.max(1, number(params.get("limit"), LIMIT)));
      return safeFilter(manifest, values, params, limit);
    }
    fail("Unknown public Atlas endpoint");
  }

  window.fetch = async (input, init) => {
    const inputUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const target = new URL(inputUrl, location.href);
    if (target.origin !== location.origin || !target.pathname.startsWith("/api/")) {
      return originalFetch(input, init);
    }
    try {
      const data = await query(target.pathname, target.searchParams);
      return new Response(JSON.stringify(data), {
        status:200, headers:{"Content-Type":"application/json; charset=utf-8",
                             "Cache-Control":"no-store"}
      });
    } catch (error) {
      return new Response(JSON.stringify({error:error?.message || "Public data request failed"}), {
        status:400, headers:{"Content-Type":"application/json; charset=utf-8"}
      });
    }
  };

  function csvCell(value) {
    let text = String(value === null || value === undefined ? "" : value);
    // Prevent spreadsheet cells from interpreting source labels as formulas.
    if (/^[\s]*[=+@-]/.test(text) && !/^-?[0-9]+(\.[0-9]+)?$/.test(text.trim())) text = "'" + text;
    return '"' + text.replace(/"/g,'""') + '"';
  }

  window.ATLAS_PUBLIC_DOWNLOAD = async args => {
    try {
      const params = new URLSearchParams({...args,limit:String(CSV_LIMIT)});
      const manifest = await catalog();
      checkFamily(params);
      const values = await dataset(checkCode(params.get("series") || ""));
      const out = safeFilter(manifest, values, params, CSV_LIMIT);
      if (out.truncated) fail("This CSV would exceed 10,000 rows; narrow your selection.");
      if (!out.rows.length) fail("No observations found for this CSV export.");
      const fields = ["provider","geography","year","period","series","value","raw_value",
                      "unit","frequency","source_status","source_url"];
      const lines = [fields.join(",")];
      for (const row of out.rows) lines.push(fields.map(k=>csvCell(row[k])).join(","));
      const blob = new Blob([lines.join("\n") + "\n"],{type:"text/csv;charset=utf-8"});
      const url = URL.createObjectURL(blob);
      const link=document.createElement("a");
      link.href=url;
      link.download="worldeconatlas_" + values.code + "_wdi_20261109.csv";
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      const notice=document.getElementById("notice");
      if (notice){notice.textContent=error.message;notice.hidden=false;}
    }
  };
})();
