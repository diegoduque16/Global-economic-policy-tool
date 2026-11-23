"""Compile an already published, source-pinned Eurostat release into Atlas chart files.

Never connect to the API during a public site build. Only the independently
validated public source edition is transformed; no private SQLite is read.
"""
from __future__ import annotations
from collections import Counter, defaultdict
import json
from pathlib import Path
if __package__:
    from .eurostat_unemployment import SEXS, AGES, URL, SOURCE_URL
else:
    from eurostat_unemployment import SEXS, AGES, URL, SOURCE_URL

MIN_NUMERIC = 200


def build(data: Path, output: Path) -> dict | None:
    source_file = Path(data) / "eurostat" / "annual_unemployment.json"
    if not source_file.exists():
        return None
    data_obj = json.loads(source_file.read_text(encoding="utf-8"))
    if (data_obj.get("schema_version") != 1 or data_obj.get("public_release") is not True
        or data_obj.get("dataset") != "une_rt_a"
        or data_obj.get("official_api_url") != URL
        or data_obj.get("source_url") != SOURCE_URL
        or data_obj.get("original_unit") != "PC_ACT"
        or data_obj.get("unit_label") != "%"
        or data_obj.get("not_harmonized_with_other_providers") is not True
        or data_obj.get("classification_labels") != {"sex": SEXS, "age": AGES}
        or not isinstance(data_obj.get("source_raw_sha256"), str)
        or len(data_obj["source_raw_sha256"]) != 64
        or not isinstance(data_obj.get("rows"), list)
        or data_obj.get("numeric_observations") != len(data_obj["rows"])
        or data_obj["numeric_observations"] < MIN_NUMERIC
        or data_obj.get("series_breakdowns") != len(SEXS)*len(AGES)):
        raise ValueError("Eurostat original public source edition failed checks")
    groups = defaultdict(lambda: defaultdict(list))
    checked = set()
    for observation in data_obj["rows"]:
        if not isinstance(observation, list) or len(observation) != 6:
            raise ValueError("Eurostat original observation shape changed")
        geo, year, sex, age, value, status = observation
        if (geo not in data_obj.get("geography_labels", {})
            or type(year) is not int or not 1900 <= year <= 2100
            or sex not in SEXS or age not in AGES
            or type(value) not in (int,float)
            or not -1_000 <= value <= 1_000
            or not isinstance(status,str) or len(status)>25):
            raise ValueError("Eurostat original source dimensions or values invalid")
        key = geo, year, sex, age
        if key in checked:
            raise ValueError("Eurostat original years are not unique per series")
        checked.add(key)
        groups[geo][sex, age].append([year, value, status])
    out = Path(output)/"data"/"eurostat"/"charts"
    out.mkdir(parents=True, exist_ok=True)
    catalog = []
    all_measures = set()
    for n, (geo, codes) in enumerate(sorted(groups.items())):
        series = []
        for (sex, age), rows in sorted(codes.items()):
            rows.sort(key=lambda r:r[0])
            title = f"Unemployment rate (%) · {SEXS[sex]} · {AGES[age]}"
            series.append({
                "id": sex + "|" + age,
                "title": title,
                "original_dimensions": {"dataset":"une_rt_a", "freq":"A",
                                        "unit":"PC_ACT", "sex":sex, "age":age},
                "unit_label": "%",
                "rows": rows,
                "first_year": rows[0][0],
                "last_year": rows[-1][0],
                "plot_safe": True,
            })
            all_measures.add((sex,age))
        fname = f"{n:04d}.json"
        (out/fname).write_text(json.dumps({"code":geo,"series":series},
                                         ensure_ascii=False,separators=(",",":"))+"\n",
                               encoding="utf-8")
        catalog.append({
            "code":geo,
            "name":data_obj["geography_labels"][geo],
            "file":fname,
            "series_count":len(series),
            "numeric_records":sum(len(s["rows"]) for s in series),
            "geography_type":"country_or_aggregate",
        })
    if len(all_measures) != len(SEXS)*len(AGES) or sum(x["numeric_records"] for x in catalog) != data_obj["numeric_observations"]:
        raise ValueError("Eurostat chart generation lost original numeric observations")
    meta = {key:data_obj[key] for key in (
        "provider","dataset","title","original_unit","unit_label","source_url",
        "official_api_url","source_raw_sha256","downloaded_at_utc","copyright_url",
        "citation","numeric_observations","series_breakdowns",
        "not_harmonized_with_other_providers"
    )}
    meta.update({"schema_version":1,"geographies":catalog,"chartable_geographies":len(catalog),
                 "annual_source_defined_not_harmonized":True})
    (out/"catalog.json").write_text(json.dumps(meta,ensure_ascii=False,
                                              separators=(",",":"))+"\n",encoding="utf-8")
    return meta
