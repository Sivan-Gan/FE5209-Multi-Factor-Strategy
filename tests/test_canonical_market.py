"""Data-layer unit checks for canonical market identity and weekly timing."""

from __future__ import annotations

import unittest

from src.data import canonical_market, source_audit


ROOT = canonical_market.project_root()


class TestCanonicalMarketUnit(unittest.TestCase):
    def test_canonical_yahoo_mapping_is_authoritative(self) -> None:
        _, by_yahoo = canonical_market.read_security_master(source_audit.package_root(ROOT))
        self.assertEqual(by_yahoo["BF-B"]["ticker"], "BF.B")
        self.assertEqual(by_yahoo["BRK-B"]["ticker"], "BRK.B")
        self.assertEqual(len(by_yahoo), 503)

    def test_weekly_calendar_uses_last_actual_date_and_no_friday_assumption(self) -> None:
        dates = [
            canonical_market._parse_date("2026-01-05"),
            canonical_market._parse_date("2026-01-08"),
            canonical_market._parse_date("2026-01-12"),
            canonical_market._parse_date("2026-01-16"),
        ]
        calendar = canonical_market.build_weekly_calendar_rows(dates)
        self.assertEqual([row["signal_date"] for row in calendar], ["2026-01-08", "2026-01-16"])
        self.assertEqual(calendar[0]["next_trading_date"], "2026-01-12")
        self.assertEqual(calendar[-1]["next_trading_date"], "")

    def test_calendar_month_history_rule_is_calendar_based(self) -> None:
        self.assertEqual(
            canonical_market._add_months(canonical_market._parse_date("2024-02-29"), 24).isoformat(),
            "2026-02-28",
        )


if __name__ == "__main__":
    unittest.main()
