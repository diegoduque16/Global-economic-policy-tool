"""Offline synthetic safety checks for private Atlas ILOSTAT edition chaining."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from warehouse.ilostat_private_chain import (
    BOOTSTRAP, load_pointer, successor_pointer, validate_pointer, verify_parent_db,
)


class PrivateChainTests(unittest.TestCase):
    def test_latest_verified_november_20_bootstrap(self):
        pointer = validate_pointer(dict(BOOTSTRAP))
        self.assertEqual(pointer["native_records"], 1_231_472)
        self.assertEqual(pointer["native_sources"], 988)
        self.assertFalse(pointer["public_release"])

    def test_rejects_unsafe_pointer_or_stale_rewind(self):
        for change in (
            {"archive_sha256": "x" * 64},
            {"edition_id": "../../malicious"},
            {"native_records": 10},
            {"native_sources": 12},
            {"public_release": True},
        ):
            with self.assertRaises(ValueError):
                validate_pointer(dict(BOOTSTRAP, **change))

    def test_new_edition_must_advance_records_and_sources(self):
        with self.assertRaisesRegex(ValueError, "strictly advance"):
            successor_pointer(BOOTSTRAP,
                edition_id="atlas-native-ilostat-next-20261120-44658021471",
                archive_sha256="a"*64, native_records=1_231_472, native_sources=988)
        advanced=successor_pointer(
            BOOTSTRAP,edition_id="atlas-native-ilostat-next-20261121-44699999999",
            archive_sha256="a"*64,native_records=1_250_000,native_sources=991)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"pointer.json"
            path.write_text(json.dumps(advanced))
            self.assertEqual(load_pointer(path),advanced)

    def test_source_id_discovery_from_immutable_parent_not_hardcoded_list(self):
        with tempfile.TemporaryDirectory() as folder:
            dbpath=Path(folder)/"parent.sqlite"
            with sqlite3.connect(dbpath) as db:
                db.executescript("""
                    CREATE TABLE native_records (value TEXT);
                    CREATE TABLE native_sources (source_id TEXT);
                    INSERT INTO native_records VALUES ('1');
                    INSERT INTO native_records VALUES ('2');
                    INSERT INTO native_sources VALUES ('wdi_one');
                    INSERT INTO native_sources VALUES ('ilostat_EMP_TEST_SEX_OCU_NB_A_abcdef123456');
                """)
            miniature=dict(BOOTSTRAP,native_records=2,native_sources=2)
            # The public validator correctly rejects a forged tiny edition.
            with self.assertRaises(ValueError):
                verify_parent_db(dbpath,miniature)
            # Test metadata discovery at an accepted baseline using a mock
            # validator; the source IDs themselves must come from SQLite.
            from unittest.mock import patch
            with patch("warehouse.ilostat_private_chain.validate_pointer",
                       side_effect=lambda p:p):
                found=verify_parent_db(dbpath,miniature)
                self.assertEqual(found,{"EMP_TEST_SEX_OCU_NB_A"})
                with sqlite3.connect(dbpath) as db:
                    db.execute("INSERT INTO native_sources VALUES ('ilostat_UNE_NEW_SEX_AGE_RT_A_123456abcdef')")
                with self.assertRaisesRegex(ValueError,"count mismatch"):
                    verify_parent_db(dbpath,miniature)


if __name__=="__main__":
    unittest.main()
