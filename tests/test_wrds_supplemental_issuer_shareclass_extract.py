"""Focused tests for Formal Gate 0 Phase 2B.1 extraction preparation."""

from __future__ import annotations

import unittest

from src.data import wrds_supplemental_issuer_shareclass_extract as supplemental


class TestWrdsSupplementalIssuerShareclassExtract(unittest.TestCase):
    def test_common_equity_classification_is_documented_and_excludes_adr_units(self) -> None:
        base = {
            "securitytype": "EQTY",
            "securitysubtype": "COM",
            "issuertype": "CORP",
        }
        self.assertTrue(supplemental.is_eligible_common_equity({**base, "sharetype": "NS"}))
        self.assertTrue(supplemental.is_eligible_common_equity({**base, "sharetype": "CE"}))
        self.assertTrue(supplemental.is_eligible_common_equity({**base, "sharetype": "SB"}))
        self.assertFalse(supplemental.is_eligible_common_equity({**base, "sharetype": "AD"}))
        self.assertFalse(supplemental.is_eligible_common_equity({**base, "sharetype": "UG"}))
        self.assertFalse(supplemental.is_eligible_common_equity({**base, "sharetype": "NS", "securitysubtype": "PFD"}))

    def test_query_universe_prefers_permco_and_is_deterministic(self) -> None:
        sufficiency = [
            {
                "classification": "SUPPLEMENTAL_CRSP_EXTRACTION_REQUIRED",
                "permcos": '["20","3"]',
                "relevant_permnos": '["102","11"]',
                "group_type": "GVKEY",
                "group_identifier": "001234",
            },
            {
                "classification": "SUPPLEMENTAL_CRSP_EXTRACTION_REQUIRED",
                "permcos": '["3"]',
                "relevant_permnos": '["11"]',
                "group_type": "PERMCO",
                "group_identifier": "3",
            },
        ]
        names = [
            {"permco": "3", "permno": "11", "ticker": "B", "issuernm": "Beta"},
            {"permco": "3", "permno": "12", "ticker": "B2", "issuernm": "Beta"},
            {"permco": "20", "permno": "102", "ticker": "A", "issuernm": "Alpha"},
        ]
        ccm = [
            {"LPERMCO": "3", "GVKEY": "000003"},
            {"LPERMCO": "20", "GVKEY": "000020"},
        ]
        rows = supplemental.derive_query_universe(sufficiency, names, ccm)
        self.assertEqual([row["permco"] for row in rows], ["3", "20"])
        self.assertEqual(rows[0]["historical_sp500_member_permnos"], '["11"]')
        self.assertEqual(rows[0]["known_sibling_permnos_in_current_raw"], '["11","12"]')
        self.assertIn("000003", rows[0]["linked_gvkeys_from_current_raw"])
        self.assertEqual(rows[0]["query_key_role"], "PRIMARY_PERMCO_QUERY_KEY__TICKER_DIAGNOSTIC_ONLY")

    def test_deterministic_batches_and_invalid_size(self) -> None:
        self.assertEqual(supplemental.permco_batches(["20", "3", "11", "3"], 2), [("3", "11"), ("20",)])
        with self.assertRaises(ValueError):
            supplemental.permco_batches(["3"], 0)

    def test_history_query_uses_documented_table_filter_and_period(self) -> None:
        sql, params = supplemental.security_history_query(("3", "20"))
        self.assertIn("crsp.stksecurityinfohist", sql)
        self.assertIn("s.sharetype IN ('NS', 'CE', 'SB')", sql)
        self.assertIn("s.securitysubtype = 'COM'", sql)
        self.assertEqual(params["start_date"], "2004-01-01")
        self.assertEqual(params["end_date"], "2025-12-31")
        self.assertEqual({params["permco_0"], params["permco_1"]}, {"3", "20"})

    def test_daily_and_shares_queries_are_time_valid_and_no_issuer_aggregation(self) -> None:
        daily_sql, _ = supplemental.daily_security_query(("3",))
        shares_sql, _ = supplemental.shares_query(("3",))
        self.assertIn("crsp.stkdlysecuritydata", daily_sql)
        self.assertIn("d.dlycaldt >= s.secinfostartdt", daily_sql)
        self.assertIn("crsp.stkshares", shares_sql)
        self.assertIn("h.shrstartdt <=", shares_sql)
        self.assertNotIn("sum(", daily_sql.lower())

    def test_schema_validation_is_case_insensitive(self) -> None:
        self.assertEqual(
            supplemental.validate_extraction_schema(["PERMNO", "DlyCalDt", "DlyCap"], ["permno", "dlycaldt"]),
            [],
        )
        self.assertEqual(
            supplemental.validate_extraction_schema(["PERMNO"], ["permno", "dlycaldt"]),
            ["dlycaldt"],
        )


if __name__ == "__main__":
    unittest.main()
