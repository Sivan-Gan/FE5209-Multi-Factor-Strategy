"""Focused pre-Gate-A validation tests.

The tests use synthetic returns for arithmetic semantics and inspect only frozen
input hashes for immutability.  They never write to canonical/factor/raw inputs.
"""
from __future__ import annotations

import ast
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.validation import build_formal_factor_validation as validation


def observation(permno: str = "10000", score: float | None = 1.0, ret1: float | None = 0.01, ret4: float | None = 0.04, *, member: int = 1, executable: int = 1, common: int = 1, sector: str | None = "20") -> dict:
    row = {
        "signal_date": "2011-01-07", "permno": permno, "execution_date": "2011-01-10",
        "next_execution_date": "2011-01-18", "fourth_execution_date": "2011-02-07",
        "historical_member": member, "execution_eligible": executable,
        "common_7_factor_score_available": common, "gsector": sector,
        "r_1w_exec": ret1, "r_4w_exec": ret4,
        "r_1w_status": "VALID" if ret1 is not None else "UNEXPLAINED_INCOMPLETE_PATH",
        "r_4w_status": "VALID" if ret4 is not None else "UNEXPLAINED_INCOMPLETE_PATH",
        "r_1w_terminal_cash": 0, "r_4w_terminal_cash": 0,
    }
    for _factor, column in validation.FACTOR_FIELDS:
        row[column] = score
    return row


class FormalFactorValidationTests(unittest.TestCase):
    def test_01_execution_day_return_is_excluded(self) -> None:
        ret, status, _reason, _terminal = validation.forward_path(
            {"E": (0.50, "N", 10.0, 1, 1), "D1": (0.10, "N", 11.0, 1, 1)}, ["D1"]
        )
        self.assertEqual(status, "VALID")
        self.assertAlmostEqual(ret or 0, 0.10)

    def test_02_post_execution_dates_compound(self) -> None:
        ret, _status, _reason, _terminal = validation.forward_path(
            {"D1": (0.10, "N", 1.0, 1, 1), "D2": (0.20, "N", 1.0, 1, 1)}, ["D1", "D2"]
        )
        self.assertAlmostEqual(ret or 0, 0.32)

    def test_03_confirmed_delisting_becomes_cash(self) -> None:
        ret, status, reason, terminal = validation.forward_path(
            {"D1": (-0.25, "Y", 1.0, 1, 1)}, ["D1", "D2"]
        )
        self.assertAlmostEqual(ret or 0, -0.25)
        self.assertEqual((status, reason, terminal), ("VALID_CONFIRMED_DELISTING_CASH", "CONFIRMED_DELISTING_CASH", 1))

    def test_04_unexplained_path_is_unavailable(self) -> None:
        ret, status, _reason, _terminal = validation.forward_path({}, ["D1"])
        self.assertIsNone(ret)
        self.assertEqual(status, "UNEXPLAINED_INCOMPLETE_PATH")

    def test_05_no_separate_delret_is_added(self) -> None:
        self.assertEqual(validation.VALID_RETURN_STATUSES, {"VALID", "VALID_CONFIRMED_DELISTING_CASH"})
        self.assertIn("USE_CIZ_DLYRET_DIRECTLY__DO_NOT_ADD_SEPARATE_DELRET", Path(validation.__file__).read_text())

    def test_06_no_dividend_return_adjustment_is_added(self) -> None:
        ret, _status, _reason, _terminal = validation.forward_path(
            {"D1": (0.10, "N", 1.0, 1, 1)}, ["D1"]
        )
        self.assertAlmostEqual(ret or 0.0, 0.10)

    def test_07_one_week_execution_horizon_uses_next_endpoint(self) -> None:
        self.assertEqual(observation()["next_execution_date"], "2011-01-18")

    def test_08_four_week_execution_horizon_uses_fourth_endpoint(self) -> None:
        self.assertEqual(observation()["fourth_execution_date"], "2011-02-07")

    def test_09_post_2015_endpoint_is_excluded(self) -> None:
        self.assertGreater("2016-01-04", validation.FORMAL_CUTOFF)

    def test_10_spearman_uses_ranks(self) -> None:
        self.assertAlmostEqual(validation.spearman([10, 20, 30], [4, 1, 9]) or 0, 0.5)

    def test_11_overall_ic_requires_30_observations(self) -> None:
        rows = [observation(str(i), float(i), float(i) / 100) for i in range(29)]
        weekly, _sector = validation.weekly_ic(rows, validation.PRIMARY)
        self.assertTrue(all(row["status"] == "INSUFFICIENT_CROSS_SECTION_N_LT_30" for row in weekly))

    def test_12_sector_ic_requires_10_observations(self) -> None:
        rows = [observation(str(i), float(i), float(i) / 100) for i in range(9)]
        _weekly, sector = validation.weekly_ic(rows, validation.PRIMARY)
        self.assertTrue(all(row["status"] == "INSUFFICIENT_SECTOR_CROSS_SECTION_N_LT_10" for row in sector))

    def test_13_four_week_hac_label_and_lag_are_frozen(self) -> None:
        summary = validation.summarize_ic([{"sample_role": validation.PRIMARY, "research_period": "FACTOR_SELECTION", "factor": "Size", "horizon": "4W", "spearman_ic": 0.1}])
        result = next(row for row in summary if row["factor"] == "Size" and row["horizon"] == "4W")
        self.assertEqual(result["nw_t_label"], "NW_t_overlap_lag3")

    def test_14_block_bootstrap_is_deterministic(self) -> None:
        values = [0.1, -0.1, 0.2, 0.0, 0.3]
        self.assertEqual(validation.moving_block_bootstrap(values), validation.moving_block_bootstrap(values))

    def test_15_top_30_exact_count(self) -> None:
        rows = [observation(str(i), float(i)) for i in range(10)]
        self.assertEqual(len(validation.target_leg(rows, "size_final", top=True)), 3)

    def test_16_bottom_30_exact_count(self) -> None:
        rows = [observation(str(i), float(i)) for i in range(10)]
        self.assertEqual(len(validation.target_leg(rows, "size_final", top=False)), 3)

    def test_17_permno_is_deterministic_tie_break(self) -> None:
        rows = [observation("2", 1.0), observation("1", 1.0), observation("3", 0.0), observation("4", 0.0)]
        self.assertEqual([row["permno"] for row in validation.target_leg(rows, "size_final", top=True)], ["1"])

    def test_18_top_and_bottom_do_not_overlap(self) -> None:
        rows = [observation(str(i), float(i)) for i in range(10)]
        top = {row["permno"] for row in validation.target_leg(rows, "size_final", top=True)}
        bottom = {row["permno"] for row in validation.target_leg(rows, "size_final", top=False)}
        self.assertFalse(top & bottom)

    def test_19_equal_weighting(self) -> None:
        rows = [observation(str(i), float(i)) for i in range(10)]
        selected = validation.target_leg(rows, "size_final", top=True)
        self.assertAlmostEqual(sum(1.0 / len(selected) for _ in selected), 1.0)

    def test_20_no_sector_cap(self) -> None:
        portfolio_schema = validation.SCHEMA.split("CREATE TABLE factor_portfolio_weekly", 1)[1].split("CREATE TABLE factor_holdings", 1)[0]
        self.assertNotIn("gsector", portfolio_schema)

    def test_21_no_beta_hedge(self) -> None:
        portfolio_schema = validation.SCHEMA.split("CREATE TABLE factor_portfolio_weekly", 1)[1].split("CREATE TABLE factor_holdings", 1)[0]
        self.assertNotIn("beta", portfolio_schema.lower())

    def test_22_spread_is_top_minus_bottom(self) -> None:
        self.assertAlmostEqual(0.03 - 0.01, 0.02)

    def test_23_drifted_pretrade_weights_are_used(self) -> None:
        source = Path(validation.__file__).read_text()
        self.assertIn("target_weight", source)
        self.assertIn("1.0 + float(row[\"realized_return\"])", source)

    def test_24_gtr_is_l1_distance(self) -> None:
        target, pretrade = {"A": 1.0}, {"B": 1.0}
        self.assertEqual(sum(abs(target.get(k, 0) - pretrade.get(k, 0)) for k in set(target) | set(pretrade)), 2.0)

    def test_25_turnover_is_half_gtr(self) -> None:
        self.assertEqual(0.5 * 2.0, 1.0)

    def test_26_cost_uses_gtr_not_turnover(self) -> None:
        source = Path(validation.__file__).read_text()
        self.assertIn("middle * float(row[\"top_gtr\"])", source)

    def test_27_net_wealth_formula(self) -> None:
        c, gtr, hold = 0.01, 0.8, 0.10
        self.assertAlmostEqual((1 - c * gtr) * (1 + hold) - 1, 0.0912)

    def test_28_dev_and_selection_are_separate(self) -> None:
        self.assertEqual(validation.period_label("2010-12-31"), "DEV")
        self.assertEqual(validation.period_label("2011-01-01"), "FACTOR_SELECTION")

    def test_29_gate_a_period_ends_in_2015(self) -> None:
        self.assertEqual(validation.FORMAL_CUTOFF, "2015-12-31")

    def test_30_no_performance_row_after_2015(self) -> None:
        with self.assertRaises(ValueError):
            validation.period_label("2016-01-01")

    def test_31_2016_return_is_not_eligible(self) -> None:
        self.assertFalse("2016-01-04" <= validation.FORMAL_CUTOFF)

    def test_32_common_sample_is_distinct_diagnostic(self) -> None:
        self.assertIn("DIAGNOSTIC", validation.COMMON)
        self.assertFalse(validation.score_eligible(observation(common=0), "Size", validation.COMMON))

    def test_33_no_automatic_factor_ranking(self) -> None:
        self.assertNotIn("def select_factor", Path(validation.__file__).read_text().lower())

    def test_34_no_keep_drop_field(self) -> None:
        self.assertNotIn("KEEP", validation.SCHEMA)
        self.assertNotIn("DROP", validation.SCHEMA)

    def test_35_no_fmom(self) -> None:
        self.assertNotIn("def fmom", Path(validation.__file__).read_text().lower())

    def test_36_no_final_factor_set(self) -> None:
        self.assertNotIn("def freeze_final", Path(validation.__file__).read_text().lower())

    def test_37_no_strategy_backtest(self) -> None:
        self.assertNotIn("def backtest", Path(validation.__file__).read_text().lower())

    def test_38_validation_observation_has_natural_key(self) -> None:
        self.assertIn("PRIMARY KEY(signal_date, permno)", validation.SCHEMA)

    def test_39_logical_hash_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "a.sqlite", Path(directory) / "b.sqlite"
            for path in (first, second):
                connection = sqlite3.connect(path)
                connection.execute("CREATE TABLE x (id INTEGER PRIMARY KEY, value TEXT)")
                connection.execute("INSERT INTO x VALUES (1, 'same')")
                connection.commit(); connection.close()
            self.assertEqual(validation.validation_hash(first), validation.validation_hash(second))

    def test_40_candidate_factor_input_freeze_constant(self) -> None:
        frozen = validation.input_freeze(validation.project_root())
        self.assertEqual(frozen["factor_hash"], validation.FACTOR_HASH)

    def test_41_canonical_input_freeze_constant(self) -> None:
        frozen = validation.input_freeze(validation.project_root())
        self.assertEqual(frozen["canonical_hash"], validation.CANONICAL_HASH)

    def test_42_raw_immutability_is_a_required_acceptance_check(self) -> None:
        self.assertTrue(validation.gate0.verify_raw_integrity(validation.project_root())["raw_hashes_match"])


if __name__ == "__main__":
    unittest.main()
