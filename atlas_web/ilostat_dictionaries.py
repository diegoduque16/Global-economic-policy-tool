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

    def unavailable_dictionary_ids(self):
        return [kind for kind in KINDS if kind not in self.maps]

    def public_safe_provenance(self):
        # Only kind + original SHA + original ILO URL; no private paths.
        return [
            {"dictionary_id": kind, "source_sha256": data["sha256"],
             "source_url": data["source_url"],
             "dictionary_currency_verified": False}
            for kind, data in sorted(self.receipts.items())
        ]


def load_code_lists(checkpoint_file: Path, dictionary_dir: Path,
                    *, required: tuple[str, ...] | None = None) -> VerifiedILOCodeLists:
    """Fail closed for absent, tampered or malformed SHA-pinned private code lists.

    The ILO bulk guide describes a code, label and sort column, but archived
    original code lists may include optional metadata columns. Find a unique
    explicit label column or the second of two fields; preserve the original
    code in the first column. Strict original source SHA and original CSV row
    counts still guard the file and ambiguous structures fail closed.
    """
    checkpoint_file = Path(checkpoint_file)
    dictionary_dir = Path(dictionary_dir)
    state = json.loads(checkpoint_file.read_text(encoding="utf-8"))
    dictionary_state = state.get("dictionaries")
    if state.get("schema_version") != 1 or not isinstance(dictionary_state, dict):
        raise ValueError("Missing verified private ILOSTAT dictionary checkpoint")
    # Original ILOSTAT provider does not guarantee every optional code-list
    # endpoint is archived. Only use names actually present in the immutable
    # private checkpoint; explicitly requested missing lists still fail closed.
    if required is None:
        required = tuple(kind for kind in KINDS if kind in dictionary_state)
    if (not required or not set(required).issubset(KINDS)
        or len(set(required)) != len(required)):
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
        seen_codes = set()
        with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as inp:
            reader = csv.reader(inp)
            columns = next(reader, [])
            # ILO's published documentation describes three columns, while
            # its live original code lists can contain optional extra
            # metadata fields. Detect the named .label/var_label column,
            # retaining the original code in column 0 and ignoring other
            # verified-but-not-mapped metadata. A two-column code/label list
            # is valid too, but unlabeled ambiguous multi-column files are not.
            if (not 2 <= len(columns) <= 12 or len(set(columns)) != len(columns)
                or not all(x.strip() for x in columns)):
                raise ValueError("Unknown official ILOSTAT dictionary CSV columns: " + kind)
            label_columns = [
                index for index, name in enumerate(columns)
                if name.strip().lower() in ("var_label","label","label_en","name_en","description")
                   or name.strip().lower().endswith(".label")
                   or name.strip().lower().endswith("_label")
            ]
            if len(label_columns) == 1 and label_columns[0] != 0:
                label_index = label_columns[0]
            elif len(columns) == 2 and len(label_columns) == 0:
                label_index = 1
            else:
                raise ValueError("Ambiguous original ILOSTAT dictionary label column: " + kind)
            total = 0
            for row in reader:
                if len(row) != len(columns):
                    raise ValueError("Ragged official ILOSTAT code list: " + kind)
                code, label = row[0], row[label_index]
                if len(code) > 256 or len(label) > 4096:
                    raise ValueError("Invalid original ILOSTAT dictionary code or label: " + kind)
                # Count every SHA-verified original CSV record, including
                # source rows whose code is blank (e.g. an unflagged status).
                # Such rows have no valid lookup key; never invent a code or
                # attach their labels to numeric observations.
                total += 1
                if total > MAX_ROWS:
                    raise ValueError("Oversized official ILOSTAT code list")
                if not code.strip():
                    continue
                if code in seen_codes:
                    raise ValueError("Duplicate official ILOSTAT dictionary code: " + kind)
                seen_codes.add(code)
                # Official original files can contain a source code with no
                # English label. Keep the code unknown, never invent a label.
                if label.strip():
                    labels[code] = label.strip()
        if total != rows:
            raise ValueError("Original ILOSTAT dictionary receipt row mismatch: " + kind)
        maps[kind] = labels
        receipts[kind] = {"sha256": sha, "source_url": source_url}
    return VerifiedILOCodeLists(maps, receipts)
