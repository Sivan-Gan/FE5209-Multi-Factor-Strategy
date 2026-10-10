"""Focused tests for Formal WRDS Gate 0 Phase 2C audit logic."""

from __future__ import annotations

import unittest
from datetime import date

from src.data import wrds_gate0_phase2c as phase2c


def observation(
    *,
    datadate: date = date(2024, 3, 31),
    rdq: date | None = date(2024, 5, 1),
    oancfy: float | None = 10.0,
    capxy: float | None = 5.0,
    raw_count: int = 1,
    conflict: bool = False,
) -> phase2c.QuarterObservation:
    return phase2c.QuarterObservation(
        datadate=datadate,
        rdq=rdq,
        values={"oancfy": oancfy, "capxy": capxy},
        raw_count=raw_count,
        conflict=conflict,
    )


class TestWrdsGate0Phase2C(unittest.TestCase):
    def setUp(self) -> None:
        self.trading_dates = [date(2024, 2, 2), date(2024, 5, 2), date(2024, 5, 3), date(2024, 8, 2), date(2024, 11, 2), date(2025, 2, 3)]

    def test_rdq_availability_is_next_trading_day_not_same_day(self) -> None:
        item = observation(rdq=date(2024, 5, 1))
        status, available = phase2c.quarterly_availability_status(item, self.trading_dates)
        self.assertEqual(status, "PIT_AVAILABLE_NEXT_LOCAL_TRADING_DAY")
        self.assertEqual(available, date(2024, 5, 2))
        self.assertFalse(phase2c.usable_at_signal(available, date(2024, 5, 1)))
        self.assertTrue(phase2c.usable_at_signal(available, date(2024, 5, 2)))

    def test_missing_or_future_rdq_is_not_pit_available(self) -> None:
        missing_status, _ = phase2c.quarterly_availability_status(observation(rdq=None), self.trading_dates)
        invalid_status, _ = phase2c.quarterly_availability_status(observation(datadate=date(2024, 5, 1), rdq=date(2024, 4, 30)), self.trading_dates)
        self.assertEqual(missing_status, "RDQ_MISSING")
        self.assertEqual(invalid_status, "RDQ_BEFORE_DATADATE")
        self.assertFalse(phase2c.usable_at_signal(date(2026, 1, 2), date(2025, 12, 31)))

    def test_ytd_differencing_uses_same_fiscal_year_predecessors(self) -> None:
        items = {
            1: observation(rdq=date(2024, 5, 1), oancfy=10.0),
            2: observation(datadate=date(2024, 6, 30), rdq=date(2024, 8, 1), oancfy=27.0),
            3: observation(datadate=date(2024, 9, 30), rdq=date(2024, 11, 1), oancfy=42.0),
            4: observation(datadate=date(2024, 12, 31), rdq=date(2025, 2, 1), oancfy=60.0),
        }
        output = phase2c.derive_ytd_single_quarter(items, "oancfy", self.trading_dates)
        self.assertEqual(output[1].value, 10.0)
        self.assertEqual(output[2].value, 17.0)
        self.assertEqual(output[3].value, 15.0)
        self.assertEqual(output[4].value, 18.0)
        self.assertEqual(output[4].method, "Q4_YTD_MINUS_Q3")

    def test_fiscal_year_reset_never_subtracts_prior_year_q4(self) -> None:
        reset = phase2c.derive_ytd_single_quarter({1: observation(oancfy=7.0)}, "oancfy", self.trading_dates)
        self.assertEqual(reset[1].status, "VALID")
        self.assertEqual(reset[1].value, 7.0)
        missing_predecessor = phase2c.derive_ytd_single_quarter({2: observation(oancfy=16.0)}, "oancfy", self.trading_dates)
        self.assertEqual(missing_predecessor[2].status, "MISSING_PREDECESSOR")
        self.assertIsNone(missing_predecessor[2].value)

    def test_duplicate_conflict_is_never_resolved_by_order(self) -> None:
        self.assertEqual(phase2c.duplicate_classification(["a"]), "UNIQUE")
        self.assertEqual(phase2c.duplicate_classification(["a", "a"]), "EXACT_DUPLICATE")
        self.assertEqual(phase2c.duplicate_classification(["a", "b"]), "CONFLICT")
        status, available = phase2c.quarterly_availability_status(observation(raw_count=2, conflict=True), self.trading_dates)
        self.assertEqual(status, "DUPLICATE_OR_CONFLICT")
        self.assertIsNone(available)

    def test_ttm_requires_four_consecutive_pit_available_quarters(self) -> None:
        flow = phase2c.FlowResult("VALID", 1.0, date(2024, 5, 2), "Q1_YTD")
        missing = phase2c.FlowResult("MISSING_PREDECESSOR", None, None, "NO_FILL")
        valid, incomplete = phase2c.ttm_four_quarter_windows({(2024, 1): flow, (2024, 2): flow, (2024, 3): flow, (2024, 4): flow, (2025, 1): flow})
        self.assertEqual((valid, incomplete), (2, 3))
        valid, incomplete = phase2c.ttm_four_quarter_windows({(2024, 1): flow, (2024, 2): flow, (2024, 3): missing, (2024, 4): flow})
        self.assertEqual((valid, incomplete), (0, 3))

    def test_annual_matching_never_uses_annual_datadate_as_publication_date(self) -> None:
        q4_map = {("1", "2023", "2023-12-31"): {"raw_rows": 1, "rdq_values": {date(2024, 2, 1)}, "missing_rdq": 0, "invalid_rdq": 0}}
        raw_row = {"gvkey": "1", "fyear": "2023", "datadate": "2023-12-31"}
        status, available, lag = phase2c._annual_status(raw_row, q4_map, set(), self.trading_dates)
        self.assertEqual(status, "ANNUAL_AVAILABILITY_RESOLVED_WITH_Q4_RDQ")
        self.assertEqual(available, date(2024, 2, 2))
        self.assertEqual(lag, 32)
        blocked, _, _ = phase2c._annual_status({"gvkey": "1", "fyear": "2023", "datadate": "2023-12-31"}, {}, set(), self.trading_dates)
        self.assertEqual(blocked, "ANNUAL_AVAILABILITY_BLOCKED_NO_CORRESPONDING_Q4_RECORD")

    def test_deterministic_output_ordering_helpers(self) -> None:
        q4_map = {("1", "2023", "2023-12-31"): {"raw_rows": 2, "rdq_values": {date(2024, 2, 1), date(2024, 2, 2)}, "missing_rdq": 0, "invalid_rdq": 0}}
        status, _, _ = phase2c._annual_status({"gvkey": "1", "fyear": "2023", "datadate": "2023-12-31"}, q4_map, set(), self.trading_dates)
        self.assertEqual(status, "ANNUAL_AVAILABILITY_BLOCKED_AMBIGUOUS_Q4_RDQ")
        annual = ("at", "ceq")
        quarterly = ("atq", "ceqq")
        self.assertEqual(phase2c._field_contract_rows(annual, quarterly), phase2c._field_contract_rows(annual, quarterly))


if __name__ == "__main__":
    unittest.main()
