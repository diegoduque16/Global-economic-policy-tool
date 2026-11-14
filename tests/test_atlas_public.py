"""Tests that the public Pages release includes only approved data assets."""
import json
from pathlib import Path
import tempfile
import unittest
from atlas_public.build_site import build


class PublicWebsiteTests(unittest.TestCase):
    def test_cloudflare_build_from_checked_public_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            data=root/"data"
            series=data/"series"
            series.mkdir(parents=True)
            indicators=[{"code":"TEST"+str(i),"title":"Test indicator","rows":1,
                         "topic":"test","first_year":2020,"last_year":2020}
                        for i in range(32)]
            for item in indicators:
                (series/(item["code"]+".json")).write_text("{}")
            manifest={
                "schema_version":1,"public_edition":True,
                "source_run_id":42581151428,
                "summary":{"observations":300240,"series":32,"geographies":261,
                           "aggregate_codes":44,"country_or_territory_codes":217,
                           "source_separated_numeric_checks_passed":True},
                "series":indicators,
            }
            (data/"catalog.json").write_text(json.dumps(manifest))
            (data/"LICENSE-ATTRIBUTION.txt").write_text("World Bank WDI attribution")
            output=root/"site"
            result=build(data=data,output=output)
            self.assertTrue(result["public_edition"])
            self.assertEqual(result["public_records"],300240)
            for name in ("index.html","app.js","style.css","public_adapter.js",
                         "_headers","data/catalog.json","data/LICENSE-ATTRIBUTION.txt"):
                self.assertTrue((output/name).is_file(),name)
            html=(output/"index.html").read_text()
            self.assertIn("World Economic Atlas",html)
            self.assertIn('src="/public_adapter.js"',html)
            self.assertIn("World Bank",html)
            client=(output/"app.js").read_text()
            self.assertIn("ATLAS_PUBLIC_DOWNLOAD",client)
            self.assertIn("public_geographies",client)
            self.assertFalse(list(output.rglob("warehouse.sqlite")))
            self.assertEqual(len(list((output/"data/series").glob("*.json"))),32)

    def test_reject_missing_or_changed_source_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            data=root/"data";data.mkdir()
            (data/"catalog.json").write_text(json.dumps({
                "public_edition":True,"source_run_id":42581151428,
                "summary":{"observations":299999,"series":32,"geographies":261,
                           "aggregate_codes":44,"country_or_territory_codes":217,
                           "source_separated_numeric_checks_passed":True},
                "series":[]
            }))
            with self.assertRaisesRegex(ValueError,"unexpected provenance"):
                build(data=data,output=root/"out")


if __name__ == "__main__":
    unittest.main()
