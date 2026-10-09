"""Integration checks for the immutable Gate 0 DEV source package."""

from __future__ import annotations

import unittest
from pathlib import Path

from src.data import source_audit


ROOT = Path(__file__).resolve().parents[1]


class TestSourcePackageGate0Audit(unittest.TestCase):
    """Validate delivered evidence without writing to the raw package."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.package = source_audit.package_root(ROOT)
        cls.manifest = source_audit.audit_manifest(cls.package)
        cls.price = source_audit.audit_price_file(cls.package / source_audit.PRICE_FILE)
        cls.price_tickers = source_audit._read_price_tickers(cls.package / source_audit.PRICE_FILE)
        cls.identity = source_audit.audit_identity_layer(cls.package, cls.price_tickers)
        _, map_rows = source_audit._read_csv_rows(cls.package / source_audit.SEC_CIK_MAP_FILE)
        mapped_ciks = {
            source_audit._as_int(row.get("cik"))
            for row in map_rows
            if source_audit._as_int(row.get("cik")) is not None
        }
        cls.sec = source_audit.audit_sec_companyfacts(cls.package, mapped_ciks)

    def test_raw_package_manifest_hash_integrity(self) -> None:
        self.assertTrue(self.manifest["passed"], self.manifest)
        self.assertEqual(self.manifest["verified_payload_files"], self.manifest["declared_payload_files"])

    def test_expected_files_are_readable(self) -> None:
        expected = (
            source_audit.PRICE_FILE,
            source_audit.SECURITY_MASTER_FILE,
            source_audit.PRICE_QUALITY_FILE,
            source_audit.SEC_CIK_MAP_FILE,
            source_audit.SEC_STATUS_FILE,
            source_audit.SEC_MANIFEST_FILE,
            source_audit.YAHOO_MANIFEST_FILE,
        )
        self.assertTrue(all((self.package / relative).is_file() for relative in expected))
        self.assertFalse(self.price["missing_required_columns"], self.price["missing_required_columns"])
        self.assertFalse(self.identity["security_master"]["missing_required_columns"])
        self.assertFalse(self.identity["security_cik_map"]["missing_required_columns"])

    def test_price_primary_key_and_date_validity(self) -> None:
        self.assertTrue(self.price["key_uniqueness_proven_by_strict_order"])
        self.assertEqual(self.price["invalid_date_rows"], 0)
        self.assertEqual(self.price["blank_ticker_rows"], 0)
        self.assertTrue(self.price["benchmark_present"])

    def test_identifier_consistency(self) -> None:
        coverage = self.identity["price_to_master_coverage"]
        ciks = self.identity["cross_layer_cik_coverage"]
        self.assertTrue(coverage["price_tickers_not_in_master_yahoo_mapping_are_expected_benchmark_only"])
        self.assertFalse(coverage["master_yahoo_tickers_without_price_rows"])
        self.assertFalse(ciks["sec_map_ciks_not_in_security_master"])
        self.assertFalse(ciks["sec_status_ciks_not_in_sec_map"])
        self.assertFalse(ciks["sec_map_ciks_not_in_sec_status"])

    def test_sec_archives_are_readable_and_mapped(self) -> None:
        self.assertEqual(self.sec["readable_archive_count"], self.sec["archive_file_count"])
        self.assertFalse(self.sec["filename_cik_mismatches"])
        self.assertFalse(self.sec["archive_ciks_not_in_sec_map"])
        self.assertFalse(self.sec["sec_map_ciks_without_archive"])
        self.assertFalse(self.sec["missing_root_metadata"])


if __name__ == "__main__":
    unittest.main()
