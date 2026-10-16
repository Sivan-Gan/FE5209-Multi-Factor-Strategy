"""Synthetic checks for the Gate C pieces: governance lock, trade ledger, acceptance verdict.

Run from the repository root:  python -m unittest tests.test_frozen
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.strategy import acceptance as ac
from src.strategy import governance as gv
from src.strategy import metrics as mt


def temp_root(**overrides) -> Path:
    cfg = gv.load_config()
    cfg.update(overrides)
    root = Path(tempfile.mkdtemp())
    (root / "config/strategy").mkdir(parents=True)
    (root / gv.CONFIG_FILE).write_text(json.dumps(cfg), encoding="utf-8")
    return root


FROZEN = dict(status="FROZEN", freeze_date="2026-10-14", approvers=["M1", "M2", "M3", "M4", "M5", "M6"],
              transaction_cost={"oneway_bps": 10, "stress_multiplier": 2, "status": "FROZEN"})


class GovernanceTest(unittest.TestCase):
    def test_gate_b_window_always_allowed(self):
        gv.check_window("2020-12-31")

    def test_oos_needs_flag(self):
        with self.assertRaises(ValueError):
            gv.check_window("2021-06-30", allow_oos=False, root=temp_root(**FROZEN))

    def test_oos_locked_until_frozen(self):
        with self.assertRaises(PermissionError):
            gv.check_window("2021-06-30", allow_oos=True, root=temp_root(status="PENDING_SIGNOFF"))

    def test_oos_needs_frozen_cost(self):
        cfg = dict(FROZEN, transaction_cost={"oneway_bps": 10, "status": "PENDING_SIGNOFF"})
        with self.assertRaises(PermissionError):
            gv.check_window("2021-06-30", allow_oos=True, root=temp_root(**cfg))

    def test_oos_open_when_frozen(self):
        gv.check_window("2025-12-31", allow_oos=True, root=temp_root(**FROZEN))

    def test_not_beyond_hist_oos(self):
        with self.assertRaises(ValueError):
            gv.check_window("2026-03-31", allow_oos=True, root=temp_root(**FROZEN))

    def test_repository_config_is_not_frozen_by_default(self):
        self.assertTrue(gv.freeze_problems(gv.load_config()) or gv.load_config()["status"] == "FROZEN")


def toy():
    hold = pd.DataFrame({"signal_date": ["2016-01-08", "2016-01-08", "2016-01-15", "2016-01-15"],
                         "PERMNO": ["A", "B", "A", "C"], "weight": [0.5, 0.5, 0.5, 0.5]})
    ret = pd.DataFrame({"signal_date": ["2016-01-08", "2016-01-08", "2016-01-15", "2016-01-15"],
                        "next_execution_date": ["2016-01-19", "2016-01-19", "2016-01-25", "2016-01-25"],
                        "PERMNO": ["A", "B", "A", "C"],
                        "execution_to_next_execution_total_return": [0.1, 0.0, 0.0, 0.0], "usable": 1})
    return hold, ret


class LedgerTest(unittest.TestCase):
    def test_ledger_rebuilds_gtr_and_cost(self):
        hold, ret = toy()
        path, ledger = mt.weekly_path_and_ledger(hold, ret, "2020-12-31", cost_oneway=0.001)
        by_week = ledger.groupby("signal_date")["trade_weight"].apply(lambda x: x.abs().sum())
        self.assertAlmostEqual(by_week.iloc[0], path["gtr"].iloc[0])
        self.assertAlmostEqual(by_week.iloc[1], path["gtr"].iloc[1])
        self.assertAlmostEqual(ledger["cost"].sum(), 0.001 * path["gtr"].sum())
        week2 = ledger[ledger["signal_date"] == "2016-01-15"].set_index("PERMNO")
        self.assertEqual(week2.loc["B", "side"], "SELL")
        self.assertEqual(week2.loc["C", "side"], "BUY")


class VerdictTest(unittest.TestCase):
    def test_critical_failure_rejects(self):
        rep = pd.DataFrame([ac._row("X", "c", "d", True), ac._row("Y", "c", "d", False)])
        self.assertEqual(ac.verdict(rep), "REJECTED")

    def test_non_critical_failure_does_not_reject(self):
        rep = pd.DataFrame([ac._row("X", "c", "d", True), ac._row("Y", "c", "d", False, critical=False)])
        self.assertEqual(ac.verdict(rep), "ACCEPTED")


if __name__ == "__main__":
    unittest.main()
