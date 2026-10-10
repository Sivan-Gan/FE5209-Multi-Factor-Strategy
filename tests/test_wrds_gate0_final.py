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

    def test_issue_ids_are_unique_and_have_frozen_primary_categories(self) -> None:
        issues, _, _, _, _, _ = _acceptance_inputs()
        self.assertEqual(len(issues), len({row["issue_id"] for row in issues}))
        self.assertTrue(all(row["severity"] in final.PRIMARY_CATEGORIES for row in issues))

    def test_complete_contract_fails_closed_and_preserves_the_blocker(self) -> None:
        issues, acceptance, units, evidence, alignment, assessment = _acceptance_inputs()
        final.validate_final_outputs(issues, acceptance, final._canonical_contract_rows(), units, evidence, alignment, assessment)
        self.assertEqual(assessment["status"], "BLOCKED")
        self.assertEqual(final.derive_verdict(acceptance), "FORMAL GATE 0 — BLOCKED")

    def test_every_delivered_factor_relevant_field_has_one_frequency_specific_row(self) -> None:
        units, _, _, _ = _unit_contracts()
        annual = {row["raw_field"] for row in units if row["frequency"] == "ANNUAL"}
        quarterly = {row["raw_field"] for row in units if row["frequency"] == "QUARTERLY"}
        self.assertTrue({"ni", "ceq", "oancf", "capx", "at", "gp", "dvc", "dvt", "csho"}.issubset(annual))
        self.assertTrue({"niq", "ceqq", "oancfy", "capxy", "atq", "dlcq", "cshoq"}.issubset(quarterly))
        self.assertTrue(all(row["status"] == "UNRESOLVED" for row in units))

    def test_cross_source_ratio_alignment_covers_value_inputs_and_keeps_crsp_scale(self) -> None:
        _, evidence, alignment, _ = _unit_contracts()
        self.assertEqual({row["ratio_name"] for row in alignment}, {"E/P", "B/P", "FCF Yield"})
        self.assertTrue(all(row["denominator_native_scale"] == "thousand USD" for row in alignment))
        self.assertTrue(all(row["status"] == "BLOCKED" for row in alignment))
        crsp = next(row for row in evidence if row["evidence_id"] == "CRSP_CIZ_DLYCAP_THOUSANDS")
        self.assertIn("thousands", crsp["unit_statement"])

    def test_authoritative_evidence_does_not_overstate_generic_millions_language(self) -> None:
        _, evidence, _, _ = _unit_contracts()
        generic = next(row for row in evidence if row["evidence_id"] == "WRDS_COMPUSTAT_GENERIC_MILLIONS")
        protected = next(row for row in evidence if row["evidence_id"] == "WRDS_COMPUSTAT_DICTIONARY_ACCESS_BOUNDARY")
        self.assertEqual(generic["confidence"], "OFFICIAL_BUT_INSUFFICIENT_SCOPE")
        self.assertEqual(generic["applies_to_quarterly"], "NOT_ESTABLISHED")
        self.assertEqual(protected["applies_to_annual"], "NOT_INSPECTED")

    def test_annual_quarterly_scope_and_currency_scale_are_separate(self) -> None:
        units, _, _, _ = _unit_contracts()
        by_key = {(row["raw_field"], row["frequency"]): row for row in units}
        self.assertIn(("ni", "ANNUAL"), by_key)
        self.assertIn(("niq", "QUARTERLY"), by_key)
        self.assertIn("curcd=USD", by_key[("ni", "ANNUAL")]["currency_basis"])
        self.assertIn("curcdq=USD", by_key[("niq", "QUARTERLY")]["currency_basis"])
        self.assertTrue(all("numeric scale" in row["currency_basis"] for row in units))

    def test_fcf_alignment_does_not_silently_assume_scale_or_sign(self) -> None:
        _, _, alignment, _ = _unit_contracts()
        fcf = next(row for row in alignment if row["ratio_name"] == "FCF Yield")
        self.assertIn("oancfy", fcf["numerator_fields"])
        self.assertIn("capxy", fcf["numerator_fields"])
        self.assertIn("sign", fcf["required_conversion"])
        self.assertTrue(fcf["formula_in_common_units"].startswith("UNAVAILABLE"))

    def test_unresolved_factor_relevant_field_cannot_produce_numeric_unit_pass(self) -> None:
        issues, acceptance, units, evidence, alignment, assessment = _acceptance_inputs()
        bad_acceptance = [dict(row) for row in acceptance]
        numeric = next(row for row in bad_acceptance if row["check"] == "NUMERIC_UNIT_ALIGNMENT")
        numeric["status"] = "PASS"
        numeric["blocking"] = "false"
        with self.assertRaises(ValueError):
            final.validate_final_outputs(issues, bad_acceptance, final._canonical_contract_rows(), units, evidence, alignment, assessment)

    def test_deterministic_conversion_contract_does_not_invent_a_multiplier(self) -> None:
        _, _, first, _ = _unit_contracts()
        _, _, second, _ = _unit_contracts()
        self.assertEqual(first, second)
        self.assertTrue(all("UNAVAILABLE" in row["required_conversion"] for row in first))
        self.assertTrue(all("k_" in row["formula_in_common_units"] for row in first))

    def test_no_2026_leakage_contract_remains_frozen(self) -> None:
        extension = next(row for row in final._canonical_contract_rows() if row["component"] == "2026 extension separation")
        self.assertEqual(extension["formal_period"], "2026-01-01 onward")
        self.assertNotIn("historical", extension["status"].lower())

    def test_raw_integrity_contract_still_covers_the_original_and_supplemental_files(self) -> None:
        raw = final.verify_raw_integrity(final.project_root())
        self.assertEqual(raw["expected_file_count"], 19)
        self.assertTrue(raw["raw_hashes_match"], raw["raw_failures"])

    def test_verdict_ordering_remains_fail_closed(self) -> None:
        self.assertEqual(final.derive_verdict([{"status": "PASS", "blocking": "false"}]), "FORMAL GATE 0 — PASS")
        self.assertEqual(final.derive_verdict([{"status": "PASS_WITH_WARNING", "blocking": "false"}]), "FORMAL GATE 0 — PASS WITH WARNINGS")
        self.assertEqual(final.derive_verdict([{"status": "FAIL", "blocking": "true"}]), "FORMAL GATE 0 — FAIL")


if __name__ == "__main__":
    unittest.main()
