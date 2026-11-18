"""Offline tests: only exact original SHA-pinned ILOSTAT dictionaries may label values."""
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from atlas_web.ilostat_dictionaries import (
    SOURCE_ROOT, KINDS, load_code_lists,
)


class VerifiedILOCodeListTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.code_dir=self.root/"dictionaries"
        self.code_dir.mkdir()
        self.state={"schema_version":1,"dictionaries":{}}
        self.data={
            "cl_ref_area":[("GHA","Ghana","1"),("X01","World aggregate","2")],
            "cl_source":[("ILOEST","ILO modeled estimates","1")],
            "cl_sex":[("SEX_F","Female","1"),("SEX_M","Male","2")],
            "cl_classif1":[("AGE_Y15-24","Age 15 to 24","1")],
            "cl_obs_status":[("E","Estimated","1")],
        }
        self.populate()

    def populate(self):
        for kind,rows in self.data.items():
            path=self.code_dir/(kind+".csv")
            with path.open("w",encoding="utf-8",newline="") as target:
                writer=csv.writer(target)
                writer.writerow(["var_name","var_label","var_sort"])
                writer.writerows(rows)
            sha=hashlib.sha256(path.read_bytes()).hexdigest()
            self.state["dictionaries"][kind]={
                "sha256":sha, "rows":len(rows),
                "remote":SOURCE_ROOT+kind+"-"+sha[:16]+".csv",
                "source_url":"https://rplumber.ilo.org/metadata/dic?format=.csv&lang=en&var="+kind,
            }
        self.checkpoint=self.root/"state.json"
        self.checkpoint.write_text(json.dumps(self.state),encoding="utf-8")

    def load(self):
        return load_code_lists(self.checkpoint,self.code_dir,
                               required=tuple(self.data))

    def test_exact_original_labels_and_provenance_without_rewriting_source_codes(self):
        labels=self.load()
        self.assertEqual(labels.lookup("sex","SEX_F"),"Female")
        self.assertEqual(labels.lookup("classif1","AGE_Y15-24"),"Age 15 to 24")
        self.assertEqual(labels.lookup("ref_area","X01"),"World aggregate")
        self.assertIsNone(labels.lookup("classif2","AGE_Y15-24"))
        self.assertEqual(labels.labels_for({
            "sex":"SEX_F", "source":"ILOEST","classif1":"AGE_Y15-24",
            "obs_status":"E","classif2":"UNKNOWN"}),
            {"sex":"Female","source":"ILO modeled estimates",
             "classif1":"Age 15 to 24","obs_status":"Estimated"})
        receipts=labels.public_safe_provenance()
        self.assertEqual(len(receipts),5)
        self.assertFalse(any(item["dictionary_currency_verified"] for item in receipts))
        self.assertNotIn("onedrive:",json.dumps(receipts))

    def test_modified_original_bytes_rejected(self):
        file=self.code_dir/"cl_sex.csv"
        file.write_bytes(file.read_bytes()+b"\n")
        with self.assertRaisesRegex(ValueError,"failed SHA"):
            self.load()

    def test_partial_archive_uses_only_verified_original_dictionaries(self):
        self.state["dictionaries"].pop("cl_ref_area")
        self.checkpoint.write_text(json.dumps(self.state),encoding="utf-8")
        result=load_code_lists(self.checkpoint,self.code_dir)
        self.assertEqual(result.lookup("sex","SEX_F"),"Female")
        self.assertIsNone(result.lookup("ref_area","GHA"))
        self.assertIn("cl_ref_area",result.unavailable_dictionary_ids())
        self.assertEqual(len(result.public_safe_provenance()),4)
        with self.assertRaisesRegex(ValueError,"Missing original"):
            load_code_lists(self.checkpoint,self.code_dir,required=tuple(self.data))

    def test_missing_checkpoint_entry_and_untrusted_remote_rejected(self):
        self.state["dictionaries"].pop("cl_sex")
        self.checkpoint.write_text(json.dumps(self.state),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"Missing original"):
            self.load()
        self.populate()
        self.state["dictionaries"]["cl_sex"]["remote"]="private:wrong.csv"
        self.checkpoint.write_text(json.dumps(self.state),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"Unverified original"):
            self.load()

    def test_duplicate_code_or_ragged_file_rejected_even_with_matching_sha(self):
        self.data["cl_sex"].append(("SEX_F","Conflicting Female","3"))
        self.populate()
        with self.assertRaisesRegex(ValueError,"Duplicate official"):
            self.load()
        self.data["cl_sex"].pop()
        self.populate()
        file=self.code_dir/"cl_sex.csv"
        file.write_bytes(file.read_bytes()+b"TOO,FEW\n")
        sha=hashlib.sha256(file.read_bytes()).hexdigest()
        self.state["dictionaries"]["cl_sex"]["sha256"]=sha
        self.state["dictionaries"]["cl_sex"]["remote"]=SOURCE_ROOT+"cl_sex-"+sha[:16]+".csv"
        self.state["dictionaries"]["cl_sex"]["rows"]=3
        self.checkpoint.write_text(json.dumps(self.state),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"Ragged"):
            self.load()

    def test_unknown_required_kind_not_silently_loaded(self):
        self.assertTrue("cl_classif1" in KINDS)
        with self.assertRaisesRegex(ValueError,"Unknown or repeated"):
            load_code_lists(self.checkpoint,self.code_dir,
                            required=("cl_sex","cl_sex"))
        with self.assertRaisesRegex(ValueError,"Unknown or repeated"):
            load_code_lists(self.checkpoint,self.code_dir,required=("cl_salary",))


if __name__ == "__main__":
    unittest.main()
