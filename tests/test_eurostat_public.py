"""Source-faithful synthetic JSON-stat tests for Eurostat Atlas chart publication."""
import json
from pathlib import Path
import tempfile
import unittest

from atlas_public.eurostat_unemployment import decode, SEXS, AGES
from atlas_public.build_eurostat_charts import build


def sample_jsonstat():
    ids=["freq","unit","sex","age","geo","time"]
    categories={
        "freq":(["A"],{"A":"Annual"}),
        "unit":(["PC_ACT"],{"PC_ACT":"Percentage of active population"}),
        "sex":(["T","F","M"],{"T":"Total","F":"Females","M":"Males"}),
        "age":(["Y15-74","Y15-24"],{"Y15-74":"15–74","Y15-24":"15–24"}),
        "geo":(["G"+str(n).zfill(2) for n in range(20)],
               {"G"+str(n).zfill(2):"Country "+str(n) for n in range(20)}),
        "time":([str(n) for n in range(2018,2023)],
                {str(n):str(n) for n in range(2018,2023)}),
    }
    dimensions={k:{"category":{"index":{v:i for i,v in enumerate(values)},
                                "label":labels}}
                for k,(values,labels) in categories.items()}
    cells=1
    for key in ids:cells*=len(categories[key][0])
    return {"version":"2.0","class":"dataset","id":ids,
            "size":[len(categories[k][0]) for k in ids],
            "dimension":dimensions,"value":{str(i):float(i % 25)+0.5
                                           for i in range(cells)},
            "status":{"12":"p"}}


class EurostatUnemploymentTests(unittest.TestCase):
    def test_original_jsonstat_coords_and_provenance(self):
        raw=json.dumps(sample_jsonstat()).encode()
        published=decode(raw,captured="2026-11-22T00:00:00+00:00")
        self.assertEqual(published["numeric_observations"],600)
        self.assertEqual(published["series_breakdowns"],6)
        self.assertEqual(len(published["geography_labels"]),20)
        self.assertEqual(published["rows"][0][:4],["G00",2018,"F","Y15-24"])
        self.assertEqual(published["downloaded_at_utc"],"2026-11-22T00:00:00+00:00")
        self.assertTrue(published["citation"].startswith("Eurostat"))

    def test_all_released_records_become_chartable(self):
        published=decode(json.dumps(sample_jsonstat()).encode())
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp)/"data"
            root=data/"eurostat"
            root.mkdir(parents=True)
            (root/"annual_unemployment.json").write_text(json.dumps(published))
            out=Path(temp)/"site"
            result=build(data,out)
            self.assertEqual(result["numeric_observations"],600)
            self.assertEqual(result["chartable_geographies"],20)
            self.assertEqual(result["series_breakdowns"],6)
            seen=0
            for area in result["geographies"]:
                file=out/"data"/"eurostat"/"charts"/area["file"]
                source=json.loads(file.read_text())
                self.assertEqual(source["code"],area["code"])
                seen+=sum(len(s["rows"]) for s in source["series"])
                self.assertTrue(all(s["unit_label"]=="%" for s in source["series"]))
                self.assertTrue(all("Unemployment rate" in s["title"] for s in source["series"]))
            self.assertEqual(seen,600)

    def test_invalid_unit_or_missing_categories_fails_closed(self):
        data=sample_jsonstat()
        data["dimension"]["unit"]["category"]["index"]={"CLV":0}
        with self.assertRaisesRegex(ValueError,"requested units"):
            decode(json.dumps(data).encode())
        data=sample_jsonstat()
        data["dimension"]["geo"]["category"]["label"].pop("G00")
        with self.assertRaisesRegex(ValueError,"geography name"):
            decode(json.dumps(data).encode())

    def test_wrong_source_flags_or_values_fail(self):
        data=sample_jsonstat()
        data["status"]["12"]=["bad"]
        with self.assertRaisesRegex(ValueError,"flag"):
            decode(json.dumps(data).encode())
        data=sample_jsonstat()
        data["value"]["0"]="not-a-number"
        with self.assertRaisesRegex(ValueError,"finite numeric"):
            decode(json.dumps(data).encode())


if __name__=="__main__":
    unittest.main()
