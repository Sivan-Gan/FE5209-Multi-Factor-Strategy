"""Focused unit tests for Formal WRDS Gate 0 Phase 2B.2 pre-query controls."""

from __future__ import annotations

import csv
import gzip
import tempfile
import unittest
from pathlib import Path

from src.data import wrds_gate0_phase2b2 as phase2b2


class TestWrdsGate0Phase2B2(unittest.TestCase):
    def test_reconciliation_is_deterministic_and_traces_each_permco(self) -> None:
        source = [
            {
                "classification": "SUPPLEMENTAL_CRSP_EXTRACTION_REQUIRED",
                "group_type": "GVKEY",
                "group_identifier": "000002",
                "permcos": '["20","3"]',
                "relevant_permnos": '["102","11"]',
            },
            {
                "classification": "SUPPLEMENTAL_CRSP_EXTRACTION_REQUIRED",
                "group_type": "PERMCO",
                "group_identifier": "3",
                "permcos": '["3"]',
                "relevant_permnos": '["11"]',
            },
        ]
        query = [
            {"permco": "20", "source_phase2b_groups": '["GVKEY:000002"]', "linked_gvkeys_from_current_raw": "[]", "historical_sp500_member_permnos": "[]", "query_key_role": "PRIMARY"},
            {"permco": "3", "source_phase2b_groups": '["GVKEY:000002","PERMCO:3"]', "linked_gvkeys_from_current_raw": "[]", "historical_sp500_member_permnos": "[]", "query_key_role": "PRIMARY"},
        ]
        rows = phase2b2.build_permco_reconciliation(reversed(source), reversed(query))
        self.assertEqual([(row["permco"], row["phase2b_group_id"]) for row in rows], [("3", "GVKEY:000002"), ("3", "PERMCO:3"), ("20", "GVKEY:000002")])
        self.assertEqual(rows[0]["group_gvkey"], "000002")
        self.assertEqual(rows[1]["group_gvkey"], "")

    def test_reconciliation_rejects_untraced_query_key(self) -> None:
        source = [{"classification": "SUPPLEMENTAL_CRSP_EXTRACTION_REQUIRED", "group_type": "GVKEY", "group_identifier": "1", "permcos": '["3"]', "relevant_permnos": "[]"}]
        query = [
            {"permco": "3", "source_phase2b_groups": '["GVKEY:1"]', "linked_gvkeys_from_current_raw": "[]", "historical_sp500_member_permnos": "[]", "query_key_role": "PRIMARY"},
            {"permco": "4", "source_phase2b_groups": "[]", "linked_gvkeys_from_current_raw": "[]", "historical_sp500_member_permnos": "[]", "query_key_role": "PRIMARY"},
        ]
        with self.assertRaises(ValueError):
            phase2b2.build_permco_reconciliation(source, query)

    def test_interval_overlap_is_inclusive(self) -> None:
        self.assertTrue(phase2b2.interval_overlap("2020-01-01", "2020-01-31", "2020-01-31", "2020-02-01"))
        self.assertFalse(phase2b2.interval_overlap("2020-01-01", "2020-01-30", "2020-01-31", "2020-02-01"))

    def test_coverage_classification_preserves_limitations(self) -> None:
        self.assertEqual(phase2b2.classify_issuer_coverage(history_present=False, relevant_class_count=0, daily_class_count=0, nonmissing_dlycap_count=0, daily_row_count=0, uncovered_share_daily_count=0, adr_flagged_class_count=0), "ISSUER_COVERAGE_UNRESOLVED")
        self.assertEqual(phase2b2.classify_issuer_coverage(history_present=True, relevant_class_count=2, daily_class_count=2, nonmissing_dlycap_count=10, daily_row_count=10, uncovered_share_daily_count=1, adr_flagged_class_count=0), "ISSUER_COVERAGE_COMPLETE_WITH_LIMITATION")
        self.assertEqual(phase2b2.classify_issuer_coverage(history_present=True, relevant_class_count=2, daily_class_count=2, nonmissing_dlycap_count=10, daily_row_count=10, uncovered_share_daily_count=0, adr_flagged_class_count=0), "ISSUER_COVERAGE_COMPLETE")

    def test_security_classification_and_original_vs_supplemental_labels(self) -> None:
        included, reason = phase2b2.security_classification({"issuertype": "CORP", "securitytype": "EQTY", "securitysubtype": "COM", "sharetype": "NS"})
        self.assertEqual((included, reason), ("INCLUDED_COMMON_EQUITY", "PHASE2B1_EQTY_COM_SHARETYPE_NS_CE_SB_ISSUERTYPE_ACOR_CORP_REIT"))
        excluded, excluded_reason = phase2b2.security_classification({"issuertype": "CORP", "securitytype": "EQTY", "securitysubtype": "COM", "sharetype": "AD"})
        self.assertEqual(excluded, "EXCLUDED_NON_COMMON_OR_NONCOMPARABLE")
        self.assertIn("SHARETYPE_AD", excluded_reason)
        self.assertEqual(phase2b2.original_vs_supplemental_flags("1", {"1"}, True), ("true", "false"))
        self.assertEqual(phase2b2.original_vs_supplemental_flags("2", {"1"}, True), ("false", "true"))

    def test_sample_start_share_interval_coverage(self) -> None:
        intervals = phase2b2._share_intervals([{"shrstartdt": "2003-12-01", "shrenddt": "2004-01-15", "permno": "1"}])
        self.assertTrue(phase2b2._active_share_rows(intervals, "2004-01-01"))
        self.assertFalse(phase2b2._active_share_rows(intervals, "2004-02-01"))

    def test_content_based_schema_identification_ignores_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            raw = root / "wrds_raw" / "US_WRDS_2026-10-09" / "crsp"
            raw.mkdir(parents=True)
            values = [str(value) for value in range(1, 46)]
            with (root / "config" / "wrds_supplemental_issuer_permcos.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["permco"])
                writer.writeheader()
                writer.writerows({"permco": value} for value in values)

            def write_gzip(name: str, fields: list[str], records: list[dict[str, str]]) -> None:
                with gzip.open(raw / name, "wt", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(records)

            security_fields = sorted(phase2b2.SECURITY_HISTORY_FINGERPRINT)
            daily_fields = sorted(phase2b2.DAILY_SECURITY_FINGERPRINT)
            share_fields = sorted(phase2b2.SHARE_HISTORY_FINGERPRINT)
            security = [{**{field: "" for field in security_fields}, "permco": value, "permno": value, "secinfostartdt": "2004-01-01", "secinfoenddt": "2025-12-31", "securitybegdt": "2004-01-01", "securityenddt": "2025-12-31", "shareclass": "A", "issuertype": "CORP", "securitytype": "EQTY", "securitysubtype": "COM", "sharetype": "NS", "securityactiveflg": "Y", "tradingstatusflg": "A"} for value in values]
            daily = [{**{field: "" for field in daily_fields}, "permco": value, "permno": value, "dlycaldt": "2004-01-02", "dlyprc": "1", "dlycap": "1", "shrout": "1"} for value in values]
            shares = [{**{field: "" for field in share_fields}, "permco": value, "permno": value, "shrstartdt": "2003-12-01", "shrenddt": "2025-12-31", "shrout": "1", "shrsource": "OBS", "shrfactype": "NA", "shradrflg": "N"} for value in values]
            write_gzip("misnamed_alpha.csv.gz", security_fields, security)
            write_gzip("misnamed_beta.csv.gz", daily_fields, daily)
            write_gzip("misnamed_gamma.csv.gz", share_fields, shares)
            identified = phase2b2.identify_supplemental_raw_files(root)
            self.assertEqual(identified["SECURITY_INFORMATION_HISTORY"].name, "misnamed_alpha.csv.gz")
            self.assertEqual(identified["DAILY_SECURITY_DATA"].name, "misnamed_beta.csv.gz")
            self.assertEqual(identified["SHARE_OUTSTANDING_HISTORY"].name, "misnamed_gamma.csv.gz")

    def test_raw_manifest_addendum_is_stable_and_records_pre_post_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw.csv.gz"
            with gzip.open(raw, "wt", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["PERMNO", "PERMCO", "DlyCalDt", "DlyPrc", "DlyCap", "ShrOut"])
                writer.writeheader()
                writer.writerow({"PERMNO": "1", "PERMCO": "2", "DlyCalDt": "2020-01-02", "DlyPrc": "1", "DlyCap": "1", "ShrOut": "1"})
            columns, rows = phase2b2._gzip_rows(raw)
            digest = phase2b2._sha256(raw)
            output = phase2b2.build_new_raw_manifest({"DAILY_SECURITY_DATA": raw}, {"DAILY_SECURITY_DATA": rows}, {"DAILY_SECURITY_DATA": columns}, before_hashes={"DAILY_SECURITY_DATA": digest}, root=root)
            self.assertEqual(output[0]["pre_post_sha256_match"], "true")
            self.assertEqual(output[0]["relative_path"], "raw.csv.gz")
            self.assertEqual(output[0]["duplicate_extra_rows"], 0)


if __name__ == "__main__":
    unittest.main()
