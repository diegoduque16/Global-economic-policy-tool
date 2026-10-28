"""Offline, side-effect-free tests for official catalog schema and safety gates."""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from research import atlas2_discover_provider_catalogs as m


def wb(page=1, pages=1, total=1, entries=None):
    entries = entries if entries is not None else [{"id":"NY.GDP.MKTP.CD","name":"GDP (current US$)",
                 "source":{"id":"2","value":"World Development Indicators"},"unit":"US$"}]
    return json.dumps([{"page":page,"pages":pages,"total":total}, entries]).encode()


def euro(body=''):
    return ('"title"\t"code"\t"type"\t"last update of data"\t"last table structure change"\t"data start"\t"data end"\t"values"\n' + body).encode()


class DiscoveryTests(unittest.TestCase):
    def test_world_bank_metadata_preserved(self):
        pages,total,rows=m.parse_wb_page(wb(),1)
        self.assertEqual((pages,total), (1,1))
        self.assertEqual(rows[0]['source_key'], '2:NY.GDP.MKTP.CD')
        self.assertEqual(rows[0]['evidence_status'],'OFFICIAL_CATALOG_METADATA_NOT_INGESTED')

    def test_world_bank_rejects_pagination_shift(self):
        with self.assertRaises(ValueError):m.parse_wb_page(wb(page=2, pages=2, total=2),1)

    def test_world_bank_rejects_api_error(self):
        with self.assertRaises(ValueError):m.parse_wb_page(json.dumps({'message':'No data'}).encode(),1)

    def test_world_bank_rejects_missing_database(self):
        with self.assertRaises(ValueError):m.parse_wb_page(wb(entries=[{'id':'A','name':'test'}]),1)

    def test_eurostat_parses_and_deduplicates_toc_placement(self):
        body='"    Real GDP"\t"nama_10_gdp"\t"dataset"\t"27.10.2026"\t"27.10.2026"\t"1950"\t"2026"\t1234\n'
        body += '"        Real GDP"\t"nama_10_gdp"\t"dataset"\t"27.10.2026"\t"27.10.2026"\t"1950"\t"2026"\t1234\n'
        body += '"       Inflation"\t"prc_hicp_midx"\t"table"\t"27.10.2026"\t"27.10.2026"\t"2000-01"\t"2026-09"\t3456\n'
        rows, dup=m.parse_eurostat_toc(euro(body))
        self.assertEqual((len(rows),dup),(2,1))
        self.assertEqual(rows[0]['source_key'],'dataset:nama_10_gdp')
        self.assertEqual(rows[0]['observation_values_reported_source'],'1234')

    def test_eurostat_rejects_schema_changes(self):
        with self.assertRaises(ValueError):m.parse_eurostat_toc(b'code\ttitle\n')

    def test_eurostat_rejects_bad_advertised_counts(self):
        with self.assertRaises(ValueError):
            m.parse_eurostat_toc(euro('"Title"\t"a_b"\t"dataset"\t""\t""\t""\t""\t"one million"\n'))

    def test_output_is_metadata_only_and_hashes_verify(self):
        row={'provider':'worldbank','provider_catalog_type':'indicator','source_key':'2:X','source_code':'X',
             'title':'GDP','database_id':'2','database_name':'WDI','unit_source':'',
             'description_source':'','start_period_source':'','end_period_source':'',
             'last_update_source':'','observation_values_reported_source':'',
             'evidence_status':'OFFICIAL_CATALOG_METADATA_NOT_INGESTED'}
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'catalog'
            with patch.object(m,'discover_worldbank',return_value=([row],{'catalog_entries':1})):
                summary=m.export(target, selected=('worldbank',))
            self.assertEqual(summary['economic_observations_ingested'],0)
            self.assertFalse(summary['research_ready'])
            manifest=json.loads((target/'sha256_manifest.json').read_text())
            for name,item in manifest.items():
                p=target/name
                self.assertEqual(m.digest(p.read_bytes()),item['sha256'])
            with self.assertRaises(FileExistsError):
                m.export(target, selected=('worldbank',))

if __name__=='__main__':unittest.main()
