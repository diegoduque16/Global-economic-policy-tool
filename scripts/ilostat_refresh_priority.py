#!/usr/bin/env python3
"""Prioritize verified missing ILOSTAT source files and current Atlas research needs.

Uses the existing collector and immutable private checkpoint. This only orders
official table IDs; it never changes source identity, retries, rates, units,
original byte preservation, or the SHA-verified no-loss guard.
"""
from __future__ import annotations

import re

PRIORITY_LABOUR_TABLES = (
    "SDG_0831_SEX_ECO_RT_A",  # informal employment, physical source
    "SDG_0851_SEX_OCU_NB_A",  # local-currency hourly earnings, physical source
    "SDG_0852_SEX_AGE_RT_A",  # unemployment rate, physical source
)
_SOURCE_ID = re.compile(r"[A-Za-z0-9_]{4,100}\Z")


def prioritize_official_tables(toc: dict, downloaded: dict) -> list[str]:
    """Order official TOC IDs, retaining the collector's exact freshness checks.

    Never creates a second download list or treats source files as distinct
    causal economic observations. The main workflow is responsible for
    skipping files whose official last_update + row count remain unchanged.
    """
    if not isinstance(toc, dict) or not isinstance(downloaded, dict):
        raise ValueError("Invalid official ILOSTAT TOC/checkpoint types")
    if any(not isinstance(k, str) or not _SOURCE_ID.fullmatch(k) for k in toc):
        raise ValueError("Invalid official ILOSTAT table ID")
    return sorted(toc, key=lambda code: (
        code in downloaded,  # truly missing official files must come first
        0 if "_GAI_" in code else (1 if code in PRIORITY_LABOUR_TABLES else 2),
        code,
    ))
