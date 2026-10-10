"""Focused tests for the Formal WRDS Gate 0 integrated final sign-off."""

from __future__ import annotations

import unittest

from src.data import wrds_gate0_final as final


def _unit_contracts() -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], dict[str, str]]:
    units = final._numeric_units_rows(final.project_root())
    evidence = final._unit_evidence_rows()
    alignment = final._cross_source_alignment_rows()
    assessment = final._unit_alignment_assessment(units, alignment)
    return units, evidence, alignment, assessment


def _acceptance_inputs() -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], dict[str, str]]:
    units, evidence, alignment, assessment = _unit_contracts()
    issues = final._issue_rows(assessment)
    acceptance = final._acceptance_rows(
        {"raw_hashes_match": True, "expected_file_count": 19, "raw_failures": []},
        {"ANNUAL": {"duplicate_groups": 25437}, "QUARTERLY": {"duplicate_groups": 451}},
        issues,
        assessment,
    )
    return issues, acceptance, units, evidence, alignment, assessment


class TestWrdsGate0Final(unittest.TestCase):
    def test_acceptance_matrix_has_every_mandatory_check_once(self) -> None:
        _, acceptance, _, _, _, _ = _acceptance_inputs()
        self.assertEqual({row["check"] for row in acceptance}, set(final.MANDATORY_CHECKS))
        self.assertEqual(len(acceptance), len(final.MANDATORY_CHECKS))

    def test_exact_raw_field_identity_matches_frozen_contract_and_raw_schemas(self) -> None:
        identity = final.verify_field_level_unit_identity(final.project_root())
        self.assertTrue(identity["matches"], identity)
        self.assertEqual(
            set(identity["required_fields"]),
            {"ni[ANNUAL]", "niq[QUARTERLY]", "ceq[ANNUAL]", "ceqq[QUARTERLY]", "oancf[ANNUAL]", "oancfy[QUARTERLY]", "capx[ANNUAL]", "capxy[QUARTERLY]"},
        )

    def test_ni_and_niq_field_level_evidence_is_millions(self) -> None:
        _, evidence, _, _ = _unit_contracts()
        by_id = {row["evidence_id"]: row for row in evidence}
        for evidence_id, annual, quarterly in (("WRDS_COMPUSTAT_NI_ANNUAL_MILLIONS", "YES", "NO"), ("WRDS_COMPUSTAT_NIQ_QUARTERLY_MILLIONS", "NO", "YES")):
            row = by_id[evidence_id]
            self.assertIn("Millions", row["unit_statement"])
            self.assertEqual(row["applies_to_annual"], annual)
            self.assertEqual(row["applies_to_quarterly"], quarterly)

    def test_ceq_and_ceqq_field_level_evidence_is_millions(self) -> None:
        _, evidence, _, _ = _unit_contracts()
        by_id = {row["evidence_id"]: row for row in evidence}
        for evidence_id in ("WRDS_COMPUSTAT_CEQ_ANNUAL_MILLIONS", "WRDS_COMPUSTAT_CEQQ_QUARTERLY_MILLIONS"):
            self.assertIn("Millions", by_id[evidence_id]["unit_statement"])
            self.assertEqual(by_id[evidence_id]["confidence"], "RESOLVED_AUTHORITATIVELY")

    def test_oancf_and_oancfy_field_level_evidence_is_millions(self) -> None:
        _, evidence, _, _ = _unit_contracts()
        by_id = {row["evidence_id"]: row for row in evidence}
        for evidence_id in ("WRDS_COMPUSTAT_OANCF_ANNUAL_MILLIONS", "WRDS_COMPUSTAT_OANCFY_YTD_MILLIONS"):
            self.assertIn("Millions", by_id[evidence_id]["unit_statement"])
            self.assertIn("comp_na_daily_all", by_id[evidence_id]["relevant_section"])

    def test_capx_and_capxy_field_level_evidence_is_millions(self) -> None:
        _, evidence, _, _ = _unit_contracts()
        by_id = {row["evidence_id"]: row for row in evidence}
        for evidence_id in ("WRDS_COMPUSTAT_CAPX_ANNUAL_MILLIONS", "WRDS_COMPUSTAT_CAPXY_YTD_MILLIONS"):
            self.assertIn("Millions", by_id[evidence_id]["unit_statement"])
            self.assertIn("funda", by_id[evidence_id]["relevant_section"])

    def test_required_unit_contract_rows_resolve_to_millions_with_x1000_conversion(self) -> None:
        units, _, _, _ = _unit_contracts()
        by_key = {(row["raw_field"], row["frequency"]): row for row in units}
        self.assertEqual(set(final.FIELD_LEVEL_UNIT_SPECS), set(final.REQUIRED_CROSS_SOURCE_UNIT_FIELDS))
        for key, spec in final.FIELD_LEVEL_UNIT_SPECS.items():
            row = by_key[key]
            self.assertEqual(row["status"], "RESOLVED_AUTHORITATIVELY")
            self.assertEqual(row["authoritative_evidence_id"], spec["evidence_id"])
            self.assertIn("Millions", row["reported_unit_or_scale"])
            self.assertIn("× 1000", row["conversion_to_canonical_usd_scale"])
            self.assertEqual(row["gate0_blocking"], "false")

    def test_currency_denomination_and_numeric_scale_remain_separate(self) -> None:
        units, _, _, _ = _unit_contracts()
        by_key = {(row["raw_field"], row["frequency"]): row for row in units}
        self.assertIn("curcd=USD", by_key[("ni", "ANNUAL")]["currency_basis"])
        self.assertIn("curcdq=USD", by_key[("niq", "QUARTERLY")]["currency_basis"])
        self.assertTrue(all("numeric scale" in row["currency_basis"] for row in units))

    def test_crsp_dlycap_thousands_and_cross_source_x1000_rule_are_frozen(self) -> None:
        _, evidence, alignment, _ = _unit_contracts()
        crsp = next(row for row in evidence if row["evidence_id"] == "CRSP_CIZ_DLYCAP_THOUSANDS")
        self.assertIn("thousands", crsp["unit_statement"])
        self.assertTrue(all(row["denominator_native_scale"] == "thousand USD" for row in alignment))
        self.assertTrue(all("1000" in row["required_conversion"] for row in alignment))

    def test_ep_scale_alignment_is_resolved(self) -> None:
        _, _, alignment, _ = _unit_contracts()
        ep = next(row for row in alignment if row["ratio_name"] == "E/P")
        self.assertEqual(ep["status"], "RESOLVED_AUTHORITATIVELY")
        self.assertIn("(NI or valid PIT earnings measure) × 1000", ep["formula_in_common_units"])

    def test_bp_scale_alignment_is_resolved(self) -> None:
        _, _, alignment, _ = _unit_contracts()
        bp = next(row for row in alignment if row["ratio_name"] == "B/P")
        self.assertEqual(bp["status"], "RESOLVED_AUTHORITATIVELY")
        self.assertIn("(CEQ × 1000)", bp["formula_in_common_units"])

    def test_fcf_yield_scale_alignment_is_resolved_and_preserves_ytd_ttm_scale(self) -> None:
        _, _, alignment, _ = _unit_contracts()
        fcf = next(row for row in alignment if row["ratio_name"] == "FCF Yield")
        self.assertEqual(fcf["status"], "RESOLVED_AUTHORITATIVELY")
        self.assertIn("OANCF - CAPX", fcf["formula_in_common_units"])
        self.assertIn("OANCFY", fcf["formula_in_common_units"])
        self.assertIn("CAPXY", fcf["formula_in_common_units"])
        self.assertIn("preserve the Millions scale", fcf["notes"])

    def test_complete_contract_passes_with_warnings_after_unit_resolution(self) -> None:
        issues, acceptance, units, evidence, alignment, assessment = _acceptance_inputs()
        final.validate_final_outputs(issues, acceptance, final._canonical_contract_rows(), units, evidence, alignment, assessment)
        self.assertEqual(assessment["status"], "PASS")
        self.assertEqual(assessment["gate0_blocking"], "false")
        self.assertEqual(final.derive_verdict(acceptance), "FORMAL GATE 0 — PASS WITH WARNINGS")

    def test_no_unresolved_gate0_blocker_can_yield_pass(self) -> None:
        issues, acceptance, units, evidence, alignment, assessment = _acceptance_inputs()
        bad_units = [dict(row) for row in units]
        unit = next(row for row in bad_units if (row["raw_field"], row["frequency"]) == ("ni", "ANNUAL"))
        unit["status"] = "UNRESOLVED"
        with self.assertRaises(ValueError):
            final.validate_final_outputs(issues, acceptance, final._canonical_contract_rows(), bad_units, evidence, alignment, assessment)

    def test_assessment_blocker_cannot_leave_numeric_acceptance_as_pass(self) -> None:
        issues, acceptance, units, evidence, alignment, assessment = _acceptance_inputs()
        blocked_assessment = dict(assessment, gate0_blocking="true", status="BLOCKED")
        with self.assertRaises(ValueError):
            final.validate_final_outputs(issues, acceptance, final._canonical_contract_rows(), units, evidence, alignment, blocked_assessment)

    def test_historical_generic_millions_evidence_is_preserved_but_not_used_for_resolution(self) -> None:
        _, evidence, _, _ = _unit_contracts()
        generic = next(row for row in evidence if row["evidence_id"] == "WRDS_COMPUSTAT_GENERIC_MILLIONS")
        self.assertEqual(generic["confidence"], "OFFICIAL_BUT_INSUFFICIENT_SCOPE")
        self.assertEqual(generic["applies_to_quarterly"], "NOT_ESTABLISHED")

    def test_raw_integrity_contract_still_covers_the_original_and_supplemental_files(self) -> None:
        raw = final.verify_raw_integrity(final.project_root())
        self.assertEqual(raw["expected_file_count"], 19)
        self.assertTrue(raw["raw_hashes_match"], raw["raw_failures"])

    def test_no_2026_leakage_contract_remains_frozen(self) -> None:
        extension = next(row for row in final._canonical_contract_rows() if row["component"] == "2026 extension separation")
        self.assertEqual(extension["formal_period"], "2026-01-01 onward")
        self.assertNotIn("historical", extension["status"].lower())

    def test_verdict_ordering_remains_fail_closed(self) -> None:
        self.assertEqual(final.derive_verdict([{"status": "PASS", "blocking": "false"}]), "FORMAL GATE 0 — PASS")
        self.assertEqual(final.derive_verdict([{"status": "PASS_WITH_WARNING", "blocking": "false"}]), "FORMAL GATE 0 — PASS WITH WARNINGS")
        self.assertEqual(final.derive_verdict([{"status": "FAIL", "blocking": "true"}]), "FORMAL GATE 0 — FAIL")


if __name__ == "__main__":
    unittest.main()
