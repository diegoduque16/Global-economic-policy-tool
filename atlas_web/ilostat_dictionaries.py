"""Verify and use *already archived* original ILOSTAT code lists for PRIVATE labels.

Original codes and units are never rewritten. This module does not download
source data, define harmonized units, or classify official ref_area codes
as sovereign countries. Dictionary snapshot currency is not asserted.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import re

SOURCE_ROOT = "onedrive:Diego/economic research database/atlas/ilostat-bulk-v1/dictionaries/"
MAX_DICTIONARY_BYTES = 20_000_000
MAX_ROWS = 500_000
KINDS = (
    "cl_indicator", "cl_ref_area", "cl_source", "cl_sex", "cl_age",
    "cl_classif1", "cl_classif2", "cl_obs_status",
)
FIELD_TO_DICTIONARY = {
    "indicator": "cl_indicator",
    "ref_area": "cl_ref_area",
    "source": "cl_source",
    "sex": "cl_sex",
    "classif1": "cl_classif1",
    "classif2": "cl_classif2",
    "obs_status": "cl_obs_status",
}
SHA = re.compile(r"[a-f0-9]{64}\Z")


class VerifiedILOCodeLists:
    """Read-only label lookups attached to an existing Atlas source store."""

    def __init__(self, maps: dict, receipts: dict):
        self.maps = maps
        self.receipts = receipts

    def lookup(self, field: str, code: str) -> str | None:
        kind = FIELD_TO_DICTIONARY.get(field)
        if not kind or not code:
            return None
        return self.maps.get(kind, {}).get(str(code))

    def labels_for(self, codes: dict) -> dict:
        return {
            field: label
            for field, code in codes.items()
            if (label := self.lookup(field, code)) is not None
        }

    def public_safe_provenance(self):
        # Only kind + original SHA + original ILO URL; no private paths.
        return [
            {"dictionary_id": kind, "source_sha256": data["sha256"],
             "source_url": data["source_url"],
             "dictionary_currency_verified": False}
            for kind, data in sorted(self.receipts.items())
        ]


def load_code_lists(checkpoint_file: Path, dictionary_dir: Path,
                    *, required: tuple[str, ...] = KINDS) -> VerifiedILOCodeLists:
    """Fail closed for absent, tampered or malformed SHA-pinned private code lists.

    Code lists officially use 3 CSV fields (code, label, order), but actual
    header words vary between bulk and older descriptions. The *position*
    and distinct nonempty code+label are validated; exact original columns
    are never replaced or altered.
    """
    checkpoint_file = Path(checkpoint_file)
    dictionary_dir = Path(dictionary_dir)
    state = json.loads(checkpoint_file.read_text(encoding="utf-8"))
    dictionary_state = state.get("dictionaries")
    if state.get("schema_version") != 1 or not isinstance(dictionary_state, dict):
        raise ValueError("Missing verified private ILOSTAT dictionary checkpoint")
    if not set(required).issubset(KINDS) or len(set(required)) != len(required):
        raise ValueError("Unknown or repeated ILOSTAT dictionary requested")
    maps, receipts = {}, {}
    for kind in required:
        proof = dictionary_state.get(kind)
        if not isinstance(proof, dict):
            raise ValueError("Missing original ILOSTAT dictionary receipt: " + kind)
        sha, rows, remote, source_url = (
            proof.get("sha256"), proof.get("rows"),
            proof.get("remote"), proof.get("source_url"))
        if (not isinstance(sha, str) or not SHA.fullmatch(sha)
            or type(rows) is not int or rows < 1 or rows > MAX_ROWS
            or remote != SOURCE_ROOT + kind + "-" + sha[:16] + ".csv"
            or not isinstance(source_url, str)
            or not source_url.startswith("https://rplumber.ilo.org/metadata/dic?")):
            raise ValueError("Unverified original dictionary metadata: " + kind)
        path = dictionary_dir / (kind + ".csv")
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing original ILOSTAT dictionary bytes: " + kind)
        if path.stat().st_size < 10 or path.stat().st_size > MAX_DICTIONARY_BYTES:
            raise ValueError("Original ILOSTAT dictionary exceeds read-only bounded size")
        h = hashlib.sha256()
        with path.open("rb") as inp:
            for b in iter(lambda: inp.read(1024 * 1024), b""):
                h.update(b)
        if h.hexdigest() != sha:
            raise ValueError("Original ILOSTAT code list failed SHA verification: " + kind)
        labels = {}
        with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as inp:
            reader = csv.reader(inp)
            columns = next(reader, [])
            if (len(columns) != 3 or len(set(columns)) != 3 or
                not all(x.strip() for x in columns)):
                raise ValueError("Unknown official ILOSTAT dictionary CSV columns: " + kind)
            total = 0
            for row in reader:
                if len(row) != 3:
                    raise ValueError("Ragged official ILOSTAT code list: " + kind)
                code, label, _sort = row
                if not code or not label.strip() or len(code) > 256 or len(label) > 4096:
                    raise ValueError("Invalid original ILOSTAT dictionary code or label: " + kind)
                if code in labels:
                    raise ValueError("Duplicate official ILOSTAT dictionary code: " + kind)
                labels[code] = label.strip()
                total += 1
                if total > MAX_ROWS:
                    raise ValueError("Oversized official ILOSTAT code list")
        if total != rows:
            raise ValueError("Original ILOSTAT dictionary receipt row mismatch: " + kind)
        maps[kind] = labels
        receipts[kind] = {"sha256": sha, "source_url": source_url}
    return VerifiedILOCodeLists(maps, receipts)
