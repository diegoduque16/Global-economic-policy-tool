"""Private, SHA-bound ILOSTAT edition pointer and original source identities.

The pointer contains ONLY source metadata (not original records or credentials).
An old pointer is accepted only if its archived bytes independently match SHA.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3

BOOTSTRAP = {
    "schema_version": 1,
    "edition_id": "atlas-native-ilostat-next-20261120-44658021471",
    "archive_sha256": "373f851d5efbc404f30efc98b7734633cd93fc1aff3b99a25f03241730cf90e1",
    "native_records": 1_231_472,
    "native_sources": 988,
    "public_release": False,
}
EDITION_RE = re.compile(r"atlas-native-ilostat-next-[0-9]{8}-[0-9]{8,15}\Z")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_RE = re.compile(r"ilostat_(.+)_([0-9a-f]{12})\Z")


def validate_pointer(pointer: dict) -> dict:
    if (not isinstance(pointer, dict) or pointer.get("schema_version") != 1
        or not isinstance(pointer.get("edition_id"), str)
        or not EDITION_RE.fullmatch(pointer["edition_id"])
        or not isinstance(pointer.get("archive_sha256"), str)
        or not SHA_RE.fullmatch(pointer["archive_sha256"])
        or type(pointer.get("native_records")) is not int
        or pointer["native_records"] < BOOTSTRAP["native_records"]
        or type(pointer.get("native_sources")) is not int
        or pointer["native_sources"] < BOOTSTRAP["native_sources"]
        or pointer.get("public_release") is not False):
        raise ValueError("Invalid private Atlas latest-edition pointer")
    return pointer


def load_pointer(path: Path) -> dict:
    return validate_pointer(json.loads(Path(path).read_text(encoding="utf-8")))


def verify_parent_db(path: Path, pointer: dict) -> frozenset[str]:
    """Read only authenticated parent SQLite metadata; no guessing IDs."""
    pointer = validate_pointer(pointer)
    with sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True) as db:
        rows = db.execute("SELECT COUNT(*) FROM native_records").fetchone()[0]
        identities = db.execute("SELECT source_id FROM native_sources").fetchall()
        if (rows != pointer["native_records"] or
            len(identities) != pointer["native_sources"] or
            db.execute("PRAGMA quick_check").fetchone()[0] != "ok"):
            raise ValueError("Private parent record or source count mismatch")
    return frozenset(match.group(1) for (source_id,) in identities
                     if (match := SOURCE_RE.fullmatch(source_id)))


def successor_pointer(parent: dict, *, edition_id: str, archive_sha256: str,
                      native_records: int, native_sources: int) -> dict:
    validate_pointer(parent)
    result = {
        "schema_version": 1,
        "edition_id": edition_id,
        "archive_sha256": archive_sha256,
        "native_records": native_records,
        "native_sources": native_sources,
        "public_release": False,
    }
    validate_pointer(result)
    if (result["edition_id"] == parent["edition_id"]
        or result["archive_sha256"] == parent["archive_sha256"]
        or result["native_records"] <= parent["native_records"]
        or result["native_sources"] <= parent["native_sources"]):
        raise ValueError("Private Atlas edition must strictly advance")
    return result
