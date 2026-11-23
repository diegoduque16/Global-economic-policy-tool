"""Tests that the public Pages release includes only approved data assets."""
import hashlib
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
                (series/(item["code"]+".json")).write_text(json.dumps({
                    "code":item["code"],"rows":[["USA",2020,float(1)]]
                }))
            manifest={
                "schema_version":1,"public_edition":True,
                "source_run_id":42581151428,
                "summary":{"observations":300240,"series":32,"geographies":261,
                           "aggregate_codes":44,"country_or_territory_codes":217,
                           "source_separated_numeric_checks_passed":True},
                "series":indicators,
                "geographies":{"USA":{"kind":"country_or_territory","name":"United States"}},
            }
            (data/"catalog.json").write_text(json.dumps(manifest))
            (data/"LICENSE-ATTRIBUTION.txt").write_text("World Bank WDI attribution")
            output=root/"site"
            result=build(data=data,output=output,include_research=False)
            self.assertTrue(result["public_edition"])
            self.assertEqual(result["public_records"],300240)
            for name in ("index.html","app.js","style.css","public_adapter.js",
                         "_headers","data/catalog.json","data/availability.json","data/LICENSE-ATTRIBUTION.txt",
                         "project/index.html","project/brief.json","project/project.css","sources/index.html"):
                self.assertTrue((output/name).is_file(),name)
            html=(output/"index.html").read_text()
            availability=json.loads((output/"data/availability.json").read_text())
            self.assertTrue(availability["public_edition"])
            self.assertEqual(len(availability["by_geography"]["USA"]),32)
            self.assertEqual(availability["by_geography"]["USA"]["TEST0"],[1,2020,2020])
            self.assertIn("World Economic Atlas",html)
            guide=(output/"sources/index.html").read_text()
            self.assertIn("Find and cite economic data",guide)
            self.assertIn("International Labour Organization",guide)
            self.assertIn("U.S. Bureau of Labor Statistics",guide)
            self.assertIn("FRED",guide)
            self.assertIn("third-party",guide.lower())
            self.assertIn('href="/sources/"',html)

            self.assertIn('id="data-access"',html)
            self.assertIn('Compare countries &amp; advanced options',html)
            self.assertIn('Private research database',html)
            self.assertIn('How to access the private explorer',html)
            css=(output/"style.css").read_text()
            self.assertIn('.side .sidebar-filters{display:none}',css)
            self.assertIn('.side.show-filters .sidebar-filters{display:block}',css)
            self.assertNotIn('onedrive:Diego/',html)

            for filename in ("app.js", "public_adapter.js", "style.css"):
                digest=hashlib.sha256((output/filename).read_bytes()).hexdigest()[:16]
                path="/"+filename+"?v="+digest
                self.assertIn(path,html)
                self.assertNotIn('src="/'+filename+'"',html)
                self.assertNotIn('href="/'+filename+'"',html)
            # Every UI source change must produce a different asset reference.
            self.assertIn('/app.js?v=',html)
            self.assertIn('href="/project/"',html)
            self.assertIn("Project brief &amp; decisions",html)
            public_brief = json.loads((output/"project/brief.json").read_text())
            self.assertEqual(public_brief["project"],"World Economic Atlas")
            self.assertEqual(public_brief["schema_version"],1)
            self.assertGreaterEqual(len(public_brief["workstreams"]),5)
            self.assertTrue(public_brief["assistant_start_here"])
            self.assertIn("Completion first",public_brief["direction"][0])
            project_page=(output/"project/index.html").read_text()
            self.assertIn("Latest agreed direction",project_page)
            self.assertIn("Decisions and changes",project_page)
            self.assertIn("Instructions for continuing Atlas",project_page)
            self.assertIn('href="/project/brief.json"',project_page)
            self.assertNotIn("onedrive:Diego/",project_page)
            self.assertNotIn("rclone.conf",project_page)
            self.assertNotIn("warehouse.sqlite",project_page)

            self.assertIn('id="quick-topic-wrap"',html)
            self.assertIn('id="quick-topic"',html)
            self.assertIn("Informal employment",html)
            self.assertIn("Average hourly earnings",html)
            self.assertIn("Unemployment rate",html)
            self.assertIn(".quick-topic-field[hidden]{display:none!important}",
                          (output/"style.css").read_text())
            self.assertIn('id="quick-source"',html)
            self.assertIn('World Bank — economic indicators',html)
            self.assertNotIn('<option value="bls">',html)
            self.assertIn('id="quick-country"',html)
            self.assertIn('id="quick-country-search"',html)
            self.assertIn('id="quick-indicator"',html)
            self.assertIn('id="quick-indicator-search"',html)
            self.assertIn('id="quick-year-start"',html)
            self.assertIn('id="quick-year-end"',html)
            self.assertIn('src="/public_adapter.js?v=',html)
            self.assertIn("World Bank",html)
            client=(output/"app.js").read_text()
            self.assertIn("ATLAS_PUBLIC_DOWNLOAD",client)
            self.assertIn("public_geographies",client)
            self.assertIn("function renderCountryPicker()",client)
            self.assertIn("function renderPublicCitation()",client)
            self.assertIn("navigator.clipboard.writeText(citation)",client)
            self.assertIn("World Development Indicators",client)

            # Source series IDs remain DOM option values for querying only;
            # they must not be exposed as human-visible menu labels.
            self.assertIn('label:publicView()?(x.title||"Indicator label unavailable")',client)
            self.assertIn('publicView()?(geo.name||"Unnamed geographic area")',client)
            self.assertIn('publicView()?indicatorLabel(r.series):r.series',client)
            self.assertIn('publicView()?"Verified original value":r.source_status',client)
            self.assertIn('publicView()?"":" ("+r.geography+")"',client)
            self.assertIn('indicatorLabel(state.lastResponse.series)',client)
            self.assertIn('Search by topic or indicator name',html)
            self.assertIn('Search by country or region name',html)
            self.assertNotIn('Search by series name or code',html)
            self.assertNotIn('Search country name or code',html)
            self.assertIn('api("/api/countries"',client)
            self.assertIn('state.publicCountry',client)
            adapter=(output/"public_adapter.js").read_text()
            self.assertIn('"/api/countries"',adapter)
            self.assertIn('"/data/availability.json"',adapter)
            self.assertIn('$("quick-indicator").addEventListener',client)
            self.assertIn('$("quick-country").addEventListener',client)
            self.assertFalse(list(output.rglob("warehouse.sqlite")))
            self.assertEqual(len(list((output/"data/series").glob("*.json"))),32)

    def test_current_public_release_shows_verified_bls_download(self):
        # Production public data assets are tracked in repository main after
        # the independently verified source-only BLS publisher succeeded.
        from atlas_public.build_site import build
        root=Path(__file__).resolve().parents[1] / "atlas_public" / "data"
        if not (root/"bls"/"bls_jolts_manifest.json").is_file():
            self.skipTest("BLS source archive has not been released in this checkout")
        with tempfile.TemporaryDirectory() as tmp:
            site=Path(tmp)/"site"
            build(data=root,output=site,include_research=False)
            html=(site/"index.html").read_text()
            guide=(site/"sources"/"index.html").read_text()
            self.assertIn("BLS download now available",html)
            self.assertIn("Explore BLS source and citations",html)
            self.assertIn("Download verified Atlas BLS JOLTS research CSV",guide)
            self.assertIn("Source manifest and checksums",guide)

    def test_real_public_bls_chart_source_is_selectable(self):
        root=Path(__file__).resolve().parents[1]/"atlas_public"/"data"
        if not (root/"bls"/"bls_jolts_manifest.json").is_file():
            self.skipTest("Verified BLS archive not yet available")
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/"site"
            build(data=root,output=output,include_research=False)
            html=(output/"index.html").read_text(encoding="utf-8")
            manifest=json.loads((output/"data"/"bls"/"charts"/"catalog.json").read_text())
            self.assertIn('id="quick-source"',html)
            self.assertIn('<option value="bls">',html)
            self.assertEqual(manifest["series_count"],830)
            self.assertEqual(manifest["source_native_records"],252710)
            self.assertEqual(len(list((output/"data"/"bls"/"charts").glob("*.json"))),831)
            self.assertTrue(all(entry["title"] and "bls_jolts_" not in entry["title"]
                                for entry in manifest["series"]))
            self.assertTrue(all(entry["rows"]>0 for entry in manifest["series"]))
            adapter=(output/"public_adapter.js").read_text()
            self.assertIn('async function blsQuery(path, params)',adapter)
            self.assertIn('plot_granularity:"monthly"',adapter)
            app=(output/"app.js").read_text()
            self.assertIn('publicSource.addEventListener("change"',app)
            self.assertIn('isBLS?',app)

    def test_real_ilostat_sdg_source_chart_files(self):
        root=Path(__file__).resolve().parents[1]/"atlas_public"/"data"
        if not (root/"ilostat"/"ilostat_sdg_manifest.json").is_file():
            self.skipTest("Published original ILOSTAT source release not in checkout")
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/"site"
            build(data=root,output=output,include_research=False)
            html=(output/"index.html").read_text(encoding="utf-8")
            index=json.loads((output/"data"/"ilostat"/"charts"/"catalog.json").read_text())
            self.assertEqual(index["source_native_records"],153116)
            self.assertEqual(index["finite_numeric_records"],149148)
            self.assertEqual(index["original_table_count"],3)
            self.assertTrue(index["annual_source_defined_not_harmonized"])
            self.assertGreater(index["chartable_geographies"],100)
            self.assertIn('<option value="ilostat">International Labour Organization',html)
            self.assertIn("International Labour Organization — ILOSTAT",html)
            self.assertIn("Download original ILOSTAT data and citation file",html)
            guide=(output/"sources"/"index.html").read_text()
            self.assertIn("Download ILOSTAT annual source records with citations",guide)
            self.assertIn("Atlas charts + download + original portal",guide)

            total=0
            kinds=set()
            for geo in index["geographies"]:
                content=json.loads((output/"data"/"ilostat"/"charts"/geo["file"]).read_text())
                self.assertEqual(content["code"],geo["code"])
                self.assertEqual(len(content["series"]),geo["series_count"])
                self.assertEqual(sum(len(x["rows"]) for x in content["series"]),
                                 geo["numeric_records"])
                for item in content["series"]:
                    self.assertTrue(item["title"].startswith((
                        "Informal employment", "Average hourly earnings","Unemployment rate"
                    )))
                    self.assertNotIn("SDG_0",item["title"])
                    self.assertEqual(item["rows"],sorted(item["rows"]))
                    kinds.add(item["table_id"])
                total+=geo["numeric_records"]
            self.assertEqual(total,149148)
            self.assertEqual(len(kinds),3)
            dictionary=(root/"ilostat"/"dictionaries_en.json")
            if dictionary.is_file():
                self.assertTrue(index["official_english_dictionary_applied"])
                units=set()
                for geo in index["geographies"]:
                    data=json.loads((output/"data"/"ilostat"/"charts"/geo["file"]).read_text())
                    for item in data["series"]:
                        self.assertTrue(item["official_indicator_title"].startswith(
                            "SDG indicator "))
                        self.assertNotIn("Original statistical breakdown",item["title"])
                        units.add(item["unit_label"])
                        self.assertTrue(item["source_name"])
                self.assertEqual(units,{"%","local currency"})

            adapter=(output/"public_adapter.js").read_text()
            self.assertIn("async function ilostatQuery(path,params)",adapter)
            self.assertIn('unit:entry.unit_label',adapter)
            self.assertIn('entry.unit_label==="local currency"',adapter)

            self.assertIn('data.finite_numeric_records!==149148',adapter)
            client=(output/"app.js").read_text()
            self.assertIn('isILO=currentFamily()?.id==="ilostat"',client)
            self.assertIn("function setIndicatorChoices(previous",client)
            self.assertIn('(x.title||"").startsWith(activeTopic)',client)
            self.assertIn('publicTopic.addEventListener("change"',client)
            self.assertIn('topic.value=f?.id==="ilostat"?"Unemployment rate":""',client)


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
