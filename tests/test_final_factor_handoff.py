"""Acceptance checks for the governed M2 / M3 -> M4 final-factor handoff."""
from __future__ import annotations

import csv
import subprocess
import unittest
from pathlib import Path

import pyarrow.parquet as pq

from src.handoff import prepare_final_factor_handoff as handoff


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / handoff.PACKAGE
DATA = PACKAGE / "data"


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class FinalFactorHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = rows(PACKAGE / "final_factor_registry.csv")
        cls.decision = rows(ROOT / handoff.GATE_A_SET)
        cls.acceptance = {row["check"]: row for row in rows(PACKAGE / "handoff_acceptance.csv")}
        cls.reproduction = rows(PACKAGE / "factor_score_reproduction_qa.csv")
        cls.join = rows(PACKAGE / "handoff_join_qa.csv")[0]
        cls.score_schema = pq.ParquetFile(DATA / "factor_scores_weekly.parquet").schema_arrow
        cls.availability_schema = pq.ParquetFile(DATA / "factor_availability_weekly.parquet").schema_arrow
        cls.universe_schema = pq.ParquetFile(DATA / "universe_weekly.parquet").schema_arrow

    def test_01_gate_a_is_frozen_and_final_k_is_four(self) -> None:
        self.assertEqual(self.acceptance["GATE_A_FROZEN"]["status"], "PASS")
        self.assertEqual(self.acceptance["FINAL_K_EQUALS_4"]["status"], "PASS")
        self.assertEqual(len(self.registry), 4)

    def test_02_final_factor_ids_and_order_are_exact(self) -> None:
        self.assertEqual(
            [(row["family"], row["specification_id"]) for row in self.registry],
            [(family, specification) for _order, family, specification, _short in handoff.FINAL],
        )
        self.assertEqual([row["final_factor_order"] for row in self.registry], ["1", "2", "3", "4"])

    def test_03_momentum_is_12_1_and_dropped_factors_are_not_active(self) -> None:
        identifiers = {row["specification_id"] for row in self.registry}
        self.assertIn("MOMENTUM_12_1", identifiers)
        self.assertNotIn("MOMENTUM_CURRENT", identifiers)
        self.assertTrue({"VALUE_CURRENT", "DIVIDEND_CURRENT", "SIZE_CURRENT"}.isdisjoint(identifiers))

    def test_04_score_schema_has_only_the_four_active_score_fields(self) -> None:
        names = set(self.score_schema.names)
        self.assertTrue({"quality_score", "momentum_score", "lowrisk_score", "investment_score"}.issubset(names))
        self.assertFalse(any(name in names for name in ("value_score", "dividend_score", "size_score", "momentum_current_score")))

    def test_05_score_schema_contains_no_future_or_performance_columns(self) -> None:
        names = set(self.score_schema.names)
        forbidden = {"future_return", "factor_return", "portfolio_return", "benchmark_return", "ic", "sharpe", "fmom", "factor_weight", "strategy_weight"}
        self.assertFalse(names & forbidden)
        self.assertEqual(self.acceptance["NO_FUTURE_RETURN_COLUMN"]["status"], "PASS")
        self.assertEqual(self.acceptance["NO_FACTOR_RETURN_COLUMN"]["status"], "PASS")
        self.assertEqual(self.acceptance["NO_PERFORMANCE_EVIDENCE"]["status"], "PASS")

    def test_06_score_history_is_complete_existing_history_without_2026(self) -> None:
        manifest = rows(DATA / "factor_score_manifest.csv")[0]
        self.assertEqual((manifest["min_signal_date"], manifest["max_signal_date"], manifest["signal_date_count"]), ("2006-01-06", "2025-12-26", "1043"))
        self.assertEqual(manifest["row_count"], "523909")
        self.assertEqual(manifest["contains_future_returns"], "FALSE")
        self.assertEqual(manifest["contains_performance_evidence"], "FALSE")

    def test_07_score_reproduction_is_exact_for_each_final_factor(self) -> None:
        self.assertEqual([row["specification_id"] for row in self.reproduction], [specification for _o, _f, specification, _short in handoff.FINAL])
        for row in self.reproduction:
            self.assertEqual((row["max_abs_difference"], row["mean_abs_difference"], row["mismatch_count"], row["availability_mismatch_count"], row["status"]), ("0.0", "0.0", "0", "0", "PASS"))

    def test_08_availability_schema_and_all_four_contract_are_present(self) -> None:
        names = set(self.availability_schema.names)
        required = {"signal_date", "PERMNO", "quality_available", "momentum_available", "lowrisk_available", "investment_available", "factor_count_available", "all_four_available"}
        self.assertTrue(required.issubset(names))
        self.assertEqual(self.acceptance["FACTOR_AVAILABILITY_EXPORT"]["status"], "PASS")

    def test_09_availability_flags_match_scores_and_all_four_is_correct(self) -> None:
        scores = pq.ParquetFile(DATA / "factor_scores_weekly.parquet")
        checked = 0
        for batch in scores.iter_batches(batch_size=50_000, columns=["quality_score", "momentum_score", "lowrisk_score", "investment_score", "quality_available", "momentum_available", "lowrisk_available", "investment_available", "factor_count_available", "all_four_available"]):
            for row in batch.to_pylist():
                flags = [row[f"{name}_available"] for name in ("quality", "momentum", "lowrisk", "investment")]
                values = [row[f"{name}_score"] for name in ("quality", "momentum", "lowrisk", "investment")]
                self.assertEqual(flags, [int(value is not None) for value in values])
                self.assertEqual(row["factor_count_available"], sum(flags))
                self.assertEqual(row["all_four_available"], int(sum(flags) == 4))
                checked += 1
        self.assertEqual(checked, 523909)

    def test_10_natural_keys_are_unique_and_universe_join_is_clean(self) -> None:
        self.assertEqual(self.join["status"], "PASS")
        self.assertEqual(tuple(self.join[key] for key in ("orphan_score_rows", "orphan_availability_rows", "duplicate_score_keys", "duplicate_availability_keys", "duplicate_universe_keys", "identifier_conflicts")), ("0", "0", "0", "0", "0", "0"))
        self.assertEqual(self.acceptance["KEY_UNIQUENESS"]["status"], "PASS")
        self.assertEqual(self.acceptance["JOIN_INTEGRITY"]["status"], "PASS")

    def test_11_universe_schema_is_a_minimum_upstream_support_layer(self) -> None:
        names = set(self.universe_schema.names)
        self.assertTrue({"signal_date", "PERMNO", "PERMCO", "GVKEY", "historical_gics_sector", "historical_sp500_member_flag", "issuer_market_cap", "execution_date"}.issubset(names))
        self.assertFalse(any("return" in name for name in names))

    def test_12_formula_dictionary_preserves_the_frozen_definitions(self) -> None:
        formulas = {row["specification_id"]: row for row in rows(PACKAGE / "factor_formula_dictionary.csv")}
        self.assertIn("branch-aware", formulas["QUALITY_CURRENT"]["frozen_definition"])
        self.assertIn("252", formulas["MOMENTUM_12_1"]["frozen_definition"])
        self.assertIn("21", formulas["MOMENTUM_12_1"]["frozen_definition"])
        self.assertIn("-Volatility252", formulas["LOWRISK_CURRENT"]["frozen_definition"])
        self.assertIn("Negative Asset Growth", formulas["INVESTMENT_CURRENT"]["frozen_definition"])

    def test_13_source_and_code_manifest_paths_resolve(self) -> None:
        for row in rows(PACKAGE / "code_manifest.csv"):
            self.assertTrue((ROOT / row["relative_path"]).exists(), row["relative_path"])
        sources = rows(PACKAGE / "source_manifest.csv")
        for row in sources:
            if row["source_layer"] != "WRDS raw":
                self.assertTrue((ROOT / row["relative_path"]).exists(), row["relative_path"])

    def test_14_reproduction_and_audit_lineage_are_complete(self) -> None:
        lineage = rows(PACKAGE / "reproduction_manifest.csv")
        self.assertEqual(len(lineage), 15)
        self.assertEqual(lineage[-1]["stage"], "Handoff export")
        self.assertTrue(all(row["exists"] == "TRUE" for row in rows(PACKAGE / "audit_manifest.csv")))

    def test_15_determinism_is_recorded_for_data_and_tracked_artifacts(self) -> None:
        deterministic = rows(PACKAGE / "handoff_determinism.csv")
        self.assertGreaterEqual(len(deterministic), 3)
        self.assertEqual({row["equal"] for row in deterministic}, {"true"})
        self.assertTrue(all(row["key_difference_count"] == row["value_difference_count"] == "0" for row in deterministic))

    def test_16_no_performance_access_and_no_gate_b_work_are_recorded(self) -> None:
        counters = rows(PACKAGE / "handoff_no_performance_access_qa.csv")[0]
        self.assertTrue(all(value == "0" for value in counters.values()))
        for check in ("NO_2016_2020_PERFORMANCE", "NO_2021_2025_PERFORMANCE", "NO_2026_PERFORMANCE", "NO_GATE_B", "NO_FMOM", "NO_STRATEGY_BACKTEST"):
            self.assertEqual(self.acceptance[check]["status"], "PASS")

    def test_17_receiver_load_test_and_small_sample_are_usable(self) -> None:
        self.assertTrue(handoff.receiver_load_test(PACKAGE))
        sample = rows(DATA / "factor_scores_weekly_sample.csv")
        self.assertEqual(len(sample), 30)
        self.assertEqual(len({row["signal_date"] for row in sample}), 3)

    def test_18_large_parquet_files_are_ignored_and_metadata_is_trackable(self) -> None:
        ignored = subprocess.check_output(["git", "check-ignore", "-q", "handoff/final_factor/data/factor_scores_weekly.parquet"], cwd=ROOT)
        self.assertEqual(ignored, b"")
        self.assertTrue((DATA / "factor_score_manifest.csv").exists())

    def test_19_immutability_and_acceptance_are_clean(self) -> None:
        self.assertEqual(rows(PACKAGE / "upstream_immutability_qa.csv")[0]["status"], "PASS")
        self.assertFalse(any(row["status"] in {"FAIL", "BLOCKED"} for row in self.acceptance.values()))
        self.assertEqual(len(self.acceptance), 40)


if __name__ == "__main__":
    unittest.main()
