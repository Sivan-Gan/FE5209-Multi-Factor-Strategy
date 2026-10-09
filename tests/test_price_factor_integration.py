"""End-to-end artifact checks for the DEV canonical price-factor pipeline."""

from __future__ import annotations

import csv
import gzip
import json
import unittest
from collections import defaultdict
from datetime import date

from src.data import canonical_market, run_price_factor_pipeline


ROOT = canonical_market.project_root()


class TestPriceFactorArtifacts(unittest.TestCase):
    """Validate generated inputs without rebuilding the large source package."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.canonical = canonical_market.canonical_paths(ROOT)
        cls.outputs = run_price_factor_pipeline.output_paths(ROOT)
        required = (*cls.canonical.values(), cls.outputs["scores"], cls.outputs["audit_json"])
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise AssertionError(f"Run the DEV price-factor pipeline before these artifact checks: {missing}")
        with cls.outputs["audit_json"].open("r", encoding="utf-8") as handle:
            cls.audit = json.load(handle)

    def test_raw_package_post_build_hash_verification_passed(self) -> None:
        manifest = self.audit["post_build_raw_manifest"]
        self.assertTrue(manifest["passed"], manifest)
        self.assertEqual(manifest["verified_payload_files"], 508)
        self.assertEqual(manifest["manifest_sha256"], "b1cc92f9dc97ad1a5e963136f0b0867c092c4b44bfcbc2e1092abfeedc4cde72")

    def test_canonical_mapping_keys_and_weekly_last_actual_dates(self) -> None:
        with self.canonical["master"].open("r", newline="", encoding="utf-8") as handle:
            master = list(csv.DictReader(handle))
        ticker_to_yahoo = {row["ticker"]: row["yahoo_ticker"] for row in master}
        self.assertEqual(len(master), 503)
        self.assertEqual(ticker_to_yahoo["BF.B"], "BF-B")
        self.assertEqual(ticker_to_yahoo["BRK.B"], "BRK-B")

        previous_date_by_ticker: dict[str, str] = {}
        benchmark_dates: list[date] = []
        daily_rows = 0
        with gzip.open(self.canonical["daily"], "rt", newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                daily_rows += 1
                ticker = row["ticker"]
                previous = previous_date_by_ticker.get(ticker)
                self.assertTrue(previous is None or row["date"] > previous, (ticker, previous, row["date"]))
                previous_date_by_ticker[ticker] = row["date"]
                if row["is_benchmark"] == "true":
                    self.assertEqual(ticker, "SPY")
                    self.assertEqual(row["is_stock_cross_section"], "false")
                    benchmark_dates.append(date.fromisoformat(row["date"]))
        self.assertEqual(daily_rows, 2_392_900)
        self.assertEqual(len(benchmark_dates), 5_210)
        expected_signal_by_week: dict[tuple[int, int], date] = {}
        benchmark_position = {trading_date: index for index, trading_date in enumerate(benchmark_dates)}
        for trading_date in benchmark_dates:
            iso = trading_date.isocalendar()
            expected_signal_by_week[(iso.year, iso.week)] = trading_date
        with self.canonical["calendar"].open("r", newline="", encoding="utf-8") as handle:
            calendar = list(csv.DictReader(handle))
        self.assertEqual(len(calendar), 1_081)
        for row in calendar:
            signal = date.fromisoformat(row["signal_date"])
            iso = signal.isocalendar()
            self.assertEqual(signal, expected_signal_by_week[(iso.year, iso.week)])
            self.assertEqual(row["benchmark_available_on_signal_date"], "true")
            if row["next_trading_date"]:
                self.assertGreater(row["next_trading_date"], row["signal_date"])
                self.assertEqual(
                    benchmark_dates[benchmark_position[signal] + 1].isoformat(), row["next_trading_date"]
                )
        self.assertEqual(calendar[-1]["next_trading_date"], "")

    def test_spy_is_excluded_and_missing_metrics_remain_missing(self) -> None:
        total = 0
        missing_raw = 0
        available_by_metric: defaultdict[str, int] = defaultdict(int)
        with gzip.open(self.outputs["scores"], "rt", newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                total += 1
                self.assertNotEqual(row["ticker"], "SPY")
                self.assertEqual(row["evidence_stage"], "DEV_CURRENT_UNIVERSE")
                if row["raw_metric_available"] == "true":
                    self.assertNotEqual(row["raw_value"], "")
                    available_by_metric[row["metric"]] += 1
                else:
                    missing_raw += 1
                    self.assertEqual(row["raw_value"], "")
                    self.assertEqual(row["winsorized_value"], "")
                    self.assertEqual(row["z_score"], "")
                    self.assertEqual(row["direction_normalized_score"], "")
        self.assertEqual(total, 1_631_229)
        self.assertGreater(missing_raw, 0)
        self.assertEqual(set(available_by_metric), {"MOM12_1", "MOM6_1", "VOL252"})


if __name__ == "__main__":
    unittest.main()
