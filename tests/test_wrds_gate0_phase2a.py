"""Focused unit tests for Formal WRDS Gate 0 Phase 2A helpers."""

from __future__ import annotations

import unittest
from datetime import date

from src.data import wrds_gate0_phase2a as phase2a


class TestWrdsGate0Phase2A(unittest.TestCase):
    """Keep interval and reconciliation rules deterministic and auditable."""

    def test_inclusive_interval_boundaries_and_open_end(self) -> None:
        start = date(2020, 1, 2)
        end = date(2020, 1, 31)
        self.assertTrue(phase2a.interval_contains(start, start, end))
        self.assertTrue(phase2a.interval_contains(end, start, end))
        self.assertFalse(phase2a.interval_contains(date(2020, 2, 1), start, end))
        self.assertTrue(phase2a.interval_contains(date(2026, 1, 1), start, None))

    def test_overlap_logic_includes_exact_boundary_and_open_end(self) -> None:
        self.assertTrue(
            phase2a.intervals_overlap(
                date(2020, 1, 1), date(2020, 1, 31), date(2020, 1, 31), date(2020, 2, 29)
            )
        )
        self.assertFalse(
            phase2a.intervals_overlap(
                date(2020, 1, 1), date(2020, 1, 30), date(2020, 1, 31), date(2020, 2, 29)
            )
        )
        self.assertTrue(phase2a.intervals_overlap(date(2020, 1, 1), None, date(2030, 1, 1), None))

    def test_ccm_open_end_is_explicit_not_a_raw_date_substitution(self) -> None:
        parsed, status = phase2a._ccm_end("E")
        self.assertIsNone(parsed)
        self.assertEqual(status, "OPEN_ENDED_E")
        parsed, status = phase2a._ccm_end("2025-12-31")
        self.assertEqual(parsed, date(2025, 12, 31))
        self.assertEqual(status, "ORDINARY_DATE")

    def test_unmatched_classification_is_deterministic(self) -> None:
        self.assertEqual(phase2a.classify_unmatched(False), "CCM_UNLINKED")
        self.assertEqual(phase2a.classify_unmatched(True), "CCM_BOUNDARY_GAP")
        self.assertEqual(phase2a.classify_unmatched(True, True), "OTHER_UNRESOLVED")

    def test_row_count_reconciliation_detects_header_definition(self) -> None:
        result = phase2a.reconcile_batch_counts(
            [
                {"physical_lines": 4, "header_lines": 1, "data_rows": 3},
                {"physical_lines": 3, "header_lines": 1, "data_rows": 2},
            ]
        )
        self.assertEqual(result["physical_lines"], 7)
        self.assertEqual(result["header_lines"], 2)
        self.assertEqual(result["data_rows"], 5)
        self.assertTrue(result["all_rows_reconcile"])

    def test_gics_interval_matching_is_inclusive_and_keeps_blank_end_open(self) -> None:
        parsed, status = phase2a._gics_end("")
        self.assertIsNone(parsed)
        self.assertEqual(status, "OPEN_ENDED_RAW_BLANK")
        self.assertTrue(phase2a.interval_contains(date(2025, 12, 31), date(1999, 6, 30), parsed))

    def test_deterministic_summary_ordering(self) -> None:
        rows = [
            {"section": "z", "metric": "b", "category": "b"},
            {"section": "a", "metric": "b", "category": "a"},
            {"section": "a", "metric": "a", "category": "z"},
        ]
        ordered = sorted(rows, key=lambda row: (row["section"], row["metric"], row["category"]))
        self.assertEqual([row["section"] + row["metric"] for row in ordered], ["aa", "ab", "zb"])


if __name__ == "__main__":
    unittest.main()
