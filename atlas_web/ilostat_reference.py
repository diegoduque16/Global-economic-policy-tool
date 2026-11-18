"""Public ILO indicator-reference descriptions, never a row-level unit override.

The same public ILOSTAT SDG listing names the measurement types of three
official ILOSTAT tables now indexed privately in Atlas. These references are
helpful for reading source-native data, but are NOT substitutes for original
table-row code lists, source vintages, national currency units, footnotes or
comparability/revision audits. No numeric observations are manufactured.
"""
from __future__ import annotations

import re

OFFICIAL_REFERENCE = "https://ilostat.ilo.org/databases/ilosdg/"
SOURCE_REFERENCE_STAGE = "PUBLIC_ILO_INDICATOR_DESCRIPTION_NOT_SOURCE_ROW_UNIT_VERIFICATION"

TABLES = {
    "SDG_0831_SEX_ECO_RT_A": {
        "indicator": "SDG 8.3.1",
        "title": "Proportion of informal employment in total employment by sex and economic activity",
        "reported_measurement": "Percentage (%)",
        "meaning": "Share of employment classified as informal by sex and economic activity",
        "caution": "Employment definitions, source coverage and classification revisions must be reconciled",
    },
    "SDG_0851_SEX_OCU_NB_A": {
        "indicator": "SDG 8.5.1",
        "title": "Average hourly earnings of employees by sex and occupation",
        "reported_measurement": "Local currency per hour",
        "meaning": "Hourly earnings of employees by sex and occupation",
        "caution": "Country-specific currency, price year, gross/net coverage, occupation and survey source are not reconciled",
    },
    "SDG_0852_SEX_AGE_RT_A": {
        "indicator": "SDG 8.5.2",
        "title": "Unemployment rate by sex and age",
        "reported_measurement": "Percentage (%)",
        "meaning": "Share of the labour force that is unemployed by sex and age",
        "caution": "The original ILO unemployment standard, national survey source and population definitions are not reconciled",
    },
}
_HASH = re.compile(r"[a-f0-9]{16}\Z")


def source_indicator_reference(series_code: str) -> dict | None:
    """Return annotation only for exact original ILOSTAT table + SHA key.

    Source-original units remain 'source_defined_unverified' in the warehouse.
    International comparability is deliberately never auto-approved.
    """
    if not isinstance(series_code, str):
        return None
    for table_id, info in TABLES.items():
        prefix = table_id + "_"
        if series_code.startswith(prefix) and _HASH.fullmatch(series_code[len(prefix):]):
            return {
                "table_id": table_id,
                **info,
                "reference_url": OFFICIAL_REFERENCE,
                "reference_stage": SOURCE_REFERENCE_STAGE,
                "source_row_unit_verified": False,
                "cross_country_comparison_approved": False,
                "period": "annual",
            }
    return None
