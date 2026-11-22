"use strict";
/* Public static WDI adapter. Reuses the existing Atlas dashboard unchanged.
 * Converts its /api requests into queries over approved public JSON files.
 * Only World Bank WDI data published in this site's data/ directory are exposed.
 * Does not contact or reveal the private Atlas SQLite database.
 */
(() => {
  const originalFetch = window.fetch.bind(window);
  let catalogPromise = null;
  let availabilityPromise = null;
  let blsCatalogPromise = null;
  const blsSeriesCache = new Map();
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
  async function availability() {
    if (!availabilityPromise) {
      availabilityPromise = Promise.all([
        catalog(),
        originalFetch("/data/availability.json", {cache:"no-store"}).then(async response => {
          if (!response.ok) fail("Public country coverage index is unavailable");
          return response.json();
        })
      ]).then(([manifest, index]) => {
        if (index.public_edition !== true || index.schema_version !== 1 ||
            index.source_run_id !== manifest.source_run_id ||
            !index.by_geography || typeof index.by_geography !== "object") {
          fail("Public availability index does not match the approved WDI edition");
        }
        return index.by_geography;
      });
    }
    return availabilityPromise;
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
  function blsCatalog() {
    if (!blsCatalogPromise) {
      blsCatalogPromise = originalFetch("/data/bls/charts/catalog.json",
          {cache:"force-cache"}).then(async response => {
        if (!response.ok) fail("BLS chart edition has not been published");
        const data = await response.json();
        if (data.schema_version !== 1 || data.series_count !== 830 ||
            data.source_native_records !== 252710 ||
            data.frequency !== "monthly" || !Array.isArray(data.series) ||
            data.series.length !== 830) {
          fail("BLS chart metadata failed public release checks");
        }
        return data;
      });
    }
    return blsCatalogPromise;
  }
  async function blsDataset(code) {
    const catalog = await blsCatalog();
    const entry = catalog.series.find(s => s.id === code);
    if (!entry) fail("Series not present in verified BLS chart release");
    if (!blsSeriesCache.has(code)) {
      blsSeriesCache.set(code, originalFetch("/data/bls/charts/" + entry.file,
          {cache:"force-cache"}).then(async response => {
        if (!response.ok) fail("BLS monthly source file missing");
        const data = await response.json();
        if (data.source_id !== code || !Array.isArray(data.rows) ||
            data.rows.length !== entry.rows) {
          fail("BLS monthly source counts do not match the verified release");
        }
        return data;
      }));
    }
    return blsSeriesCache.get(code);
  }
  async function blsQuery(path, params) {
    const manifest = await blsCatalog();
    if (path === "/api/countries") {
      return [{code:"USA",name:"United States",
        geography_type:"country_or_territory",
        available_indicators:manifest.series_count}];
    }
    if (path === "/api/series") {
      const q = (params.get("q") || "").toLowerCase().trim().slice(0,90);
      const geo = params.get("geo") || "";
      if (geo && geo !== "USA") return [];
      return manifest.series.filter(s => !q || s.title.toLowerCase().includes(q))
        .slice(0,250).map(s => ({
          name:s.id,title:s.title,rows:s.rows,
          first_year:s.first_year,last_year:s.last_year,units:1
        }));
    }
    const code = params.get("series") || "";
    const entry = manifest.series.find(s => s.id === code);
    if (!entry) fail("Unknown original BLS series");
    if (path === "/api/geographies") {
      return [{code:"USA",name:"United States",rows:entry.rows,
        geography_type:"country_or_territory"}];
    }
    if (path === "/api/rows") {
      const first = number(params.get("start"), 2000);
      const last = number(params.get("end"), 2026);
      if (first > last || first < 2000 || last > 2100) fail("Invalid BLS date range");
      const selected = (params.get("geos") || "USA").split(",").filter(Boolean);
      if (selected.length > 1 || selected.some(g => g !== "USA")) {
        fail("This BLS JOLTS edition contains United States source records only");
      }
      const data = await blsDataset(code);
      const values = data.rows.filter(([period]) =>
        Number(period.slice(0,4)) >= first && Number(period.slice(0,4)) <= last);
      const cap = Math.min(LIMIT, Math.max(1, number(params.get("limit"), LIMIT)));
      const truncated = values.length > cap;
      const rows = values.slice(0,cap).map(([period,value])=>({
        provider:"U.S. Bureau of Labor Statistics",
        geography:"USA",year:Number(period.slice(0,4)),period,
        series:code,value,raw_value:String(value),unit:"source_defined",
        frequency:"monthly",source_status:"ORIGINAL_BLS_JOLTS_VERIFIED",
        source_url:"https://www.bls.gov/jlt/"
      }));
      return {rows,returned:rows.length,truncated,
        units:["source_defined"],frequencies:["monthly"],
        duplicate_keys:0,plot_granularity:"monthly",
        chart_warnings:["Monthly source-native BLS JOLTS values. Original measurement units and industry classifications must be verified before comparisons."],
        chart_blockers:truncated?["Narrow the requested years to chart this series."]:[],
        layer:"source_native",family:"bls",series:code,
        can_plot:rows.length > 0 && !truncated,
        not_cross_provider_harmonized:true};
    }
    fail("Unknown BLS public Atlas endpoint");
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
      const families=[{
        id:"wdi", name:"World Bank — World Development Indicators",
        layer:"source_separated", observations:manifest.summary.observations,
        first_year:Math.min(...manifest.series.map(x=>x.first_year)),
        last_year:Math.max(...manifest.series.map(x=>x.last_year)),
        geographies:manifest.summary.geographies, series:manifest.summary.series
      }];
      // Verified BLS archive is a second REAL chartable public source.
      const bls = await blsCatalog();
      families.push({id:"bls",name:"U.S. Bureau of Labor Statistics — JOLTS",
        layer:"source_native",observations:bls.source_native_records,
        first_year:2000,last_year:2026,geographies:1,series:bls.series_count});
      return families;
    }
    if (params.get("family")==="bls") {
      if (params.get("layer")!=="source_native") fail("Invalid BLS public data layer");
      return blsQuery(path,params);
    }
    checkFamily(params);
    if (path === "/api/countries") {
      const coverage = await availability();
      return Object.entries(manifest.geographies).filter(([code]) => coverage[code]).map(([code, geo]) => ({
        code, name:geo.name || code,
        geography_type:geo.kind || "unresolved",
        available_indicators:Object.keys(coverage[code]).length
      })).sort((a,b) => a.name.localeCompare(b.name));
    }
    if (path === "/api/series") {
      const q = (params.get("q") || "").toLowerCase().slice(0,90);
      const geo = params.get("geo") || "";
      const coverage = geo ? (await availability())[geo] : null;
      if (geo && !coverage) fail("Country is not in the approved public source coverage");
      return manifest.series.filter(x => (!geo || coverage[x.code]) && (!q ||
        x.code.toLowerCase().includes(q) || x.title.toLowerCase().includes(q) ||
        x.topic.toLowerCase().includes(q)))
        .sort((a,b) => b.rows - a.rows)
        .slice(0,250).map(x => ({
          name:x.code, title:x.title, rows:geo?coverage[x.code][0]:x.rows,
          first_year:geo?coverage[x.code][1]:x.first_year,
          last_year:geo?coverage[x.code][2]:x.last_year, units:1
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
      let values, out;
      if (params.get("family")==="bls") {
        const bls = await blsCatalog();
        values = bls.series.find(s => s.id===params.get("series"));
        if (!values) fail("BLS series not found");
        out = await blsQuery("/api/rows",params);
      } else {
        checkFamily(params);
        values = await dataset(checkCode(params.get("series") || ""));
        out = safeFilter(manifest, values, params, CSV_LIMIT);
      }
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
      link.download=params.get("family")==="bls"?
        "worldeconatlas_bls_jolts_monthly.csv":
        "worldeconatlas_" + values.code + "_wdi_20261109.csv";
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
