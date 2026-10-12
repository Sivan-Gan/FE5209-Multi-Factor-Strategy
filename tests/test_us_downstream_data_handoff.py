"""Focused governance and integrity checks for the US downstream data handoff."""
from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.handoff.load_us_downstream_data import GovernanceError, load_us_downstream_data
from src.handoff import prepare_us_downstream_data as handoff


ROOT = Path(__file__).resolve().parents[1]


class DownstreamDataHandoffTests(unittest.TestCase):
    def test_01_final_registry_is_exact_frozen_k4(self) -> None:
        handoff.validate_final_factor_registry(ROOT)

    def test_02_return_semantics_are_direct_ciz_only(self) -> None:
        source = (ROOT / "src/handoff/prepare_us_downstream_data.py").read_text(encoding="utf-8")
        self.assertIn("USE_CIZ_DLYRET_DIRECTLY__DO_NOT_ADD_SEPARATE_DELRET", source)
        self.assertIn("CRSP_CIZ_DLYRET_DIRECT", source)
        self.assertNotIn("dlydelret", source.lower())

    def test_03_no_seed_date_can_be_after_2015(self) -> None:
        self.assertEqual(handoff.SEED_CUTOFF, "2015-12-31")
        self.assertLess(handoff.SEED_CUTOFF, "2016-01-01")

    def test_04_calendar_sample_role_mapping_is_frozen(self) -> None:
        self.assertEqual(handoff.sample_role("2004-01-02"), "INITIALIZATION")
        self.assertEqual(handoff.sample_role("2006-01-06"), "DEV_WARMUP")
        self.assertEqual(handoff.sample_role("2011-01-07"), "FACTOR_SELECTION")
        self.assertEqual(handoff.sample_role("2016-01-08"), "CONFIGURATION_AND_STRATEGY_FREEZE")
        self.assertEqual(handoff.sample_role("2021-01-08"), "HIST_OOS_BASE")

    def test_05_factor_seed_rounding_and_permno_tie_break_are_deterministic(self) -> None:
        rows = [
            {"PERMNO": "3", "score": 1.0}, {"PERMNO": "1", "score": 2.0},
            {"PERMNO": "2", "score": 2.0}, {"PERMNO": "4", "score": 0.0},
        ]
        self.assertEqual([row["PERMNO"] for row in handoff.ranked_leg(rows, "score", top=True)], ["1"])
        self.assertEqual([row["PERMNO"] for row in handoff.ranked_leg(rows, "score", top=False)], ["4"])

    def test_06_standard_and_reverse_compounding_are_mechanical(self) -> None:
        self.assertAlmostEqual(handoff.compound([0.10, -0.10]) or 0.0, -0.01)
        self.assertIsNone(handoff.compound([-1.0]))

    def test_07_missing_paths_have_deterministic_non_imputed_reasons(self) -> None:
        self.assertEqual(
            handoff.forward_path_for_security({}, ["2023-03-14"])[1],
            "NO_POST_EXECUTION_DAILY_ROWS",
        )
        self.assertEqual(
            handoff.forward_path_for_security(
                {"2023-03-15": (0.01, 0, 1, 1)},
                ["2023-03-14", "2023-03-15"],
            )[1],
            "MISSING_FIRST_POST_EXECUTION_TRADING_DAY",
        )

    def test_08_hist_oos_is_blocked_without_future_freezes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(GovernanceError, "HIST_OOS_BLOCKED"):
                load_us_downstream_data("HIST_OOS", Path(directory))

    def test_09_unknown_stage_has_no_bypass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(GovernanceError, "UNKNOWN_OR_UNAUTHORIZED"):
                load_us_downstream_data("FORCE", Path(directory))

    def test_10_hist_oos_requires_both_named_pass_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit = root / "outputs/audit/wrds_formal"
            audit.mkdir(parents=True)
            for name, check in (
                ("gate_b_fmom_configuration_freeze_acceptance.csv", "GATE_B_FROZEN"),
                ("gate_c_us_base_strategy_freeze_acceptance.csv", "GATE_C_FROZEN"),
            ):
                with (audit / name).open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["check", "status"])
                    writer.writeheader()
                    writer.writerow({"check": check, "status": "FAIL"})
            with self.assertRaises(GovernanceError):
                load_us_downstream_data("HIST_OOS", root)

    def test_11_stage_loader_returns_only_the_authorized_date_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "handoff/us_downstream/data"
            data.mkdir(parents=True)
            signal_datasets = {
                "weekly_execution_calendar", "security_weekly_execution_returns",
                "execution_liquidity_weekly", "benchmark_weekly_execution_returns",
                "risk_free_weekly",
            }
            daily_datasets = {
                "benchmark_daily_returns", "risk_free_daily", "ff_factors_daily", "ff_factors_monthly",
            }
            all_names = signal_datasets | daily_datasets | {
                "security_daily_market_pre_gate_b", "security_daily_market_gate_b_config",
                "security_daily_market_hist_oos", "final_factor_returns_seed_weekly",
                "security_reference_history",
            }
            for name in all_names:
                key = "signal_date" if name in signal_datasets else "effective_start_date" if name == "security_reference_history" else "date"
                values = ["2015-12-31", "2016-01-04", "2021-01-04"]
                if name == "final_factor_returns_seed_weekly":
                    values = ["2015-12-31"]
                pq.write_table(pa.table({key: values}), data / f"{name}.parquet")
            gate_b = load_us_downstream_data("GATE_B", root)
            self.assertNotIn("security_daily_market_hist_oos", gate_b)
            for name, table in gate_b.items():
                key = "signal_date" if "signal_date" in table.column_names else "effective_start_date" if "effective_start_date" in table.column_names else "date"
                self.assertTrue(all(value <= "2020-12-31" for value in table.column(key).to_pylist()), name)

    def test_12_local_parquet_policy_is_narrow_and_present(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("/handoff/us_downstream/data/*.parquet", gitignore)
        self.assertNotIn("/handoff/us_downstream/", [line.strip() for line in gitignore.splitlines()])

    def test_13_data_dictionary_uses_required_order(self) -> None:
        rows = handoff.data_dictionary_rows()
        self.assertTrue(rows)
        required = [
            "dataset", "column", "dtype", "unit", "description", "natural_key",
            "PIT_or_execution_status", "source_table", "source_field", "transformation",
            "future_information_risk", "downstream_use", "notes",
        ]
        self.assertEqual(required, [
            "dataset", "column", "dtype", "unit", "description", "natural_key",
            "PIT_or_execution_status", "source_table", "source_field", "transformation",
            "future_information_risk", "downstream_use", "notes",
        ])

    def test_14_market_data_has_no_2026_permitted_boundary(self) -> None:
        self.assertEqual(handoff.FORMAL_CUTOFF, "2025-12-31")
        with self.assertRaisesRegex(ValueError, "DATE_OUTSIDE_FORMAL_RANGE"):
            handoff.sample_role("2026-01-02")


@unittest.skipUnless((ROOT / "handoff/us_downstream/data/security_daily_market.parquet").is_file(), "local downstream Parquet not materialized")
class MaterializedDownstreamDataTests(unittest.TestCase):
    """Checks local generated interfaces without calculating any performance statistic."""

    PACKAGE = ROOT / "handoff/us_downstream"

    @staticmethod
    def csv_rows(name: str) -> list[dict[str, str]]:
        with (MaterializedDownstreamDataTests.PACKAGE / name).open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def test_15_acceptance_has_zero_blockers(self) -> None:
        self.assertFalse(any(row["status"] in {"FAIL", "BLOCKED"} for row in self.csv_rows("downstream_acceptance.csv")))

    def test_16_root_acceptance_matches_package_acceptance(self) -> None:
        package_rows = self.csv_rows("downstream_acceptance.csv")
        with (ROOT / "outputs/audit/wrds_formal/us_downstream_data_acceptance.csv").open(newline="", encoding="utf-8") as handle:
            root_rows = list(csv.DictReader(handle))
        self.assertEqual(package_rows, root_rows)

    def test_17_all_deterministic_dataset_hashes_match(self) -> None:
        self.assertTrue(all(row["status"] == "PASS" and row["equal"] == "true" for row in self.csv_rows("downstream_determinism.csv")))

    def test_18_all_immutable_upstream_inputs_match(self) -> None:
        self.assertTrue(all(row["status"] == "PASS" for row in self.csv_rows("downstream_upstream_immutability_qa.csv")))

    def test_19_manifest_has_exactly_the_expected_local_datasets(self) -> None:
        rows = self.csv_rows("downstream_source_manifest.csv")
        expected = {
            "security_daily_market", "security_daily_market_pre_gate_b", "security_daily_market_gate_b_config",
            "security_daily_market_hist_oos", "weekly_execution_calendar", "security_weekly_execution_returns",
            "execution_liquidity_weekly", "benchmark_daily_returns", "benchmark_weekly_execution_returns",
            "risk_free_daily", "risk_free_weekly", "ff_factors_daily", "ff_factors_monthly",
            "security_reference_history", "final_factor_returns_seed_weekly",
        }
        self.assertEqual({row["dataset"] for row in rows}, expected)
        self.assertTrue(all(row["contains_strategy_returns"] == "FALSE" for row in rows))

    def test_20_daily_partitions_reconcile_to_full_daily_interface(self) -> None:
        rows = {row["dataset"]: int(row["row_count"]) for row in self.csv_rows("downstream_source_manifest.csv")}
        self.assertEqual(
            rows["security_daily_market"],
            rows["security_daily_market_pre_gate_b"] + rows["security_daily_market_gate_b_config"] + rows["security_daily_market_hist_oos"],
        )

    def test_21_manifest_has_no_2026_date_range(self) -> None:
        self.assertTrue(all("2026" not in row["date_range"] for row in self.csv_rows("downstream_source_manifest.csv")))

    def test_22_execution_calendar_schema_and_terminal_status_are_explicit(self) -> None:
        path = self.PACKAGE / "data/weekly_execution_calendar.parquet"
        schema = pq.ParquetFile(path).schema_arrow
        self.assertEqual(schema.names, handoff.CALENDAR_SCHEMA.names)
        table = pq.read_table(path, columns=["calendar_status", "return_period_complete"])
        self.assertIn("NO_NEXT_SIGNAL_WITHIN_FORMAL_CUTOFF", set(table.column("calendar_status").to_pylist()))
        self.assertIn(0, set(table.column("return_period_complete").to_pylist()))

    def test_23_weekly_return_schema_preserves_direct_ciz_provenance(self) -> None:
        path = self.PACKAGE / "data/security_weekly_execution_returns.parquet"
        self.assertEqual(pq.ParquetFile(path).schema_arrow.names, handoff.WEEKLY_RETURN_SCHEMA.names)
        table = pq.read_table(path, columns=["return_source", "unavailable_reason"])
        self.assertEqual(set(table.column("return_source").to_pylist()), {"CRSP_CIZ_DLYRET_DIRECT"})
        self.assertNotIn("UNEXPLAINED_INCOMPLETE_PATH", set(table.column("unavailable_reason").to_pylist()))

    def test_24_liquidity_schema_contains_backward_looking_adv_fields(self) -> None:
        schema = pq.ParquetFile(self.PACKAGE / "data/execution_liquidity_weekly.parquet").schema_arrow
        self.assertEqual(schema.names, handoff.LIQUIDITY_SCHEMA.names)
        self.assertTrue({"ADV20", "ADV60", "valid_trading_days_20", "valid_trading_days_60"}.issubset(schema.names))

    def test_25_benchmark_schema_is_total_return_interface(self) -> None:
        schema = pq.ParquetFile(self.PACKAGE / "data/benchmark_daily_returns.parquet").schema_arrow
        self.assertEqual(schema.names, handoff.BENCHMARK_DAILY_SCHEMA.names)
        self.assertIn("total_return", schema.names)

    def test_26_ff_and_rf_schemas_expose_decimal_unit_provenance(self) -> None:
        ff_schema = pq.ParquetFile(self.PACKAGE / "data/ff_factors_daily.parquet").schema_arrow
        rf_schema = pq.ParquetFile(self.PACKAGE / "data/risk_free_daily.parquet").schema_arrow
        self.assertEqual(ff_schema.names, handoff.FF_DAILY_SCHEMA.names)
        self.assertEqual(rf_schema.names, handoff.RF_DAILY_SCHEMA.names)
        self.assertIn("unit", ff_schema.names)
        self.assertIn("unit", rf_schema.names)

    def test_27_seed_schema_is_limited_to_frozen_k4_interface(self) -> None:
        schema = pq.ParquetFile(self.PACKAGE / "data/final_factor_returns_seed_weekly.parquet").schema_arrow
        self.assertEqual(schema.names, handoff.SEED_SCHEMA.names)
        table = pq.read_table(self.PACKAGE / "data/final_factor_returns_seed_weekly.parquet", columns=["specification_id", "signal_date", "factor_return_available", "next_execution_date"])
        self.assertEqual(set(table.column("specification_id").to_pylist()), {spec for _family, spec, _score, _flag in handoff.FINAL_FACTORS})
        self.assertTrue(all(value <= handoff.SEED_CUTOFF for value in table.column("signal_date").to_pylist()))
        self.assertFalse(any(available and endpoint > handoff.SEED_CUTOFF for available, endpoint in zip(table.column("factor_return_available").to_pylist(), table.column("next_execution_date").to_pylist()) if endpoint))

    def test_28_seed_reproduction_qa_has_only_pass_status(self) -> None:
        self.assertTrue(all(row["status"] == "PASS" for row in self.csv_rows("final_factor_return_seed_qa.csv")))

    def test_29_join_qa_has_no_orphans_or_conflicts(self) -> None:
        row = self.csv_rows("downstream_join_qa.csv")[0]
        keys = [
            "orphan_factor_score_rows", "orphan_return_rows", "orphan_universe_rows",
            "orphan_liquidity_rows", "duplicate_keys", "identifier_conflicts", "calendar_conflicts",
        ]
        self.assertTrue(all(int(row[key]) == 0 for key in keys), row)

    def test_30_no_research_decision_qa_passes(self) -> None:
        self.assertTrue(all(row["status"] == "PASS" for row in self.csv_rows("downstream_no_research_decision_qa.csv")))

    def test_31_contract_manifests_and_samples_are_present(self) -> None:
        required = {
            "US_DOWNSTREAM_DATA_CONTRACT.md", "README.md", "US_DOWNSTREAM_DATA_REQUIREMENT_MATRIX.csv",
            "downstream_data_dictionary.csv", "downstream_source_manifest.csv", "downstream_partition_manifest.csv",
            "downstream_access_contract.csv", "downstream_non_data_decisions.csv",
            "US_DOWNSTREAM_CAPABILITY_MATRIX.csv", "EXTERNAL_DOWNSTREAM_DATA_TRANSFER_MANIFEST.csv",
        }
        self.assertTrue(all((self.PACKAGE / name).is_file() for name in required))
        self.assertTrue(any((self.PACKAGE / "data/samples").glob("*_sample_pre2016.csv")))

    def test_32_dictionary_header_is_the_governed_order(self) -> None:
        with (self.PACKAGE / "downstream_data_dictionary.csv").open(newline="", encoding="utf-8") as handle:
            self.assertEqual(next(csv.reader(handle)), [
                "dataset", "column", "dtype", "unit", "description", "natural_key",
                "PIT_or_execution_status", "source_table", "source_field", "transformation",
                "future_information_risk", "downstream_use", "notes",
            ])

    def test_33_stage_contract_has_no_force_override(self) -> None:
        rows = self.csv_rows("downstream_access_contract.csv")
        self.assertEqual({row["stage"] for row in rows}, {"GATE_B", "HIST_OOS"})
        self.assertTrue(all("force" not in row["loader_behavior"].lower() or "no force override" in row["loader_behavior"].lower() for row in rows))


if __name__ == "__main__":
    unittest.main()
