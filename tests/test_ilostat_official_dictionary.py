"""Offline tests for bounded, official ILOSTAT dictionary label parsing and chart usage."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from atlas_public.build_ilostat_dictionary import parse_dictionary
from atlas_public.build_ilostat_charts import safe_title, load_public_dictionary


class OfficialILOSTATDictionaryTests(unittest.TestCase):
    def test_exact_dictionary_column_names(self):
        raw=b"classif1,classif1.label,classif1.sort\nAGE_Y15-24,15 to 24 years,1\n"
        parsed=parse_dictionary("classif1",raw)
        self.assertEqual(parsed["AGE_Y15-24"],"15 to 24 years")
        self.assertEqual(len(parsed),1)

    def test_generic_official_column_names(self):
        raw=b"var_name,var_label,var_sort\nSEX_T,Total,1\nSEX_F,Female,2\n"
        self.assertEqual(parse_dictionary("sex",raw)["SEX_F"],"Female")

    def test_malformed_dictionary_fails_not_guess(self):
        with self.assertRaisesRegex(ValueError,"columns"):
            parse_dictionary("classif1",b"code,name\nX,Some random name\n")
        with self.assertRaisesRegex(ValueError,"conflicting"):
            parse_dictionary("sex",
                b"sex,sex.label\nSEX_T,Total\nSEX_T,Women\n")

    def test_chart_title_prefers_verified_label_without_source_codes(self):
        dims={"sex":"SEX_F","classif1":"AGE_Y15-24","source":"UNKNOWN"}
        reference={"codes":{
            "sex":{"SEX_F":"Female"},
            "classif1":{"AGE_Y15-24":"15 to 24 years"},
            "classif2":{}
        }}
        title=safe_title("SDG_0852_SEX_AGE_RT_A",dims,3,reference)
        self.assertEqual(title,"Unemployment rate · Female · 15 to 24 years")
        self.assertNotIn("SEX_F",title)
        self.assertNotIn("Original statistical breakdown",title)
        self.assertIn("Original statistical breakdown",
                      safe_title("SDG_0852_SEX_AGE_RT_A",{},3))

    def test_pinned_snapshot_requires_all_provider_source_shas(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/"ilostat"
            root.mkdir()
            fields=["ref_area","sex","classif1","classif2","source","indicator"]
            source={
                "schema_version":1,"dictionary_language":"English",
                "original_source_zip_sha256":"aa6431d19449b78e99b46450f6a5883243460864b7f7a8f873f4dd4f54152831",
                "label_provenance":"Official ILOSTAT dictionaries, not inferred descriptions",
                "codes":{f:{} for f in fields},
                "source_metadata":{f:{"official_raw_sha256":"a"*64} for f in fields}
            }
            (root/"dictionaries_en.json").write_text(json.dumps(source))
            self.assertIsNotNone(load_public_dictionary(Path(temp)))
            source["source_metadata"]["classif1"]["official_raw_sha256"]="NOT_SHA"
            (root/"dictionaries_en.json").write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError,"SHA missing"):
                load_public_dictionary(Path(temp))


if __name__=="__main__":
    unittest.main()
