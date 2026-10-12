"""Delivery-only checks: no factor research or performance calculation occurs here."""
from __future__ import annotations

import csv
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "outputs/audit/wrds_formal"
EXPECTED = ("QUALITY_CURRENT", "MOMENTUM_12_1", "LOWRISK_CURRENT", "INVESTMENT_CURRENT")


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class GitHubDeliveryTests(unittest.TestCase):
    def test_01_final_k_and_exact_order(self) -> None:
        registry = sorted(rows(ROOT / "handoff/final_factor/final_factor_registry.csv"), key=lambda row: int(row["final_factor_order"]))
        self.assertEqual(tuple(row["specification_id"] for row in registry), EXPECTED)
        self.assertEqual(len(registry), 4)

    def test_02_old_momentum_value_dividend_and_size_are_not_active(self) -> None:
        text = (ROOT / "handoff/final_factor/final_factor_registry.csv").read_text(encoding="utf-8")
        for forbidden in ("MOMENTUM_CURRENT", "VALUE_CURRENT", "DIVIDEND_CURRENT", "SIZE_CURRENT"):
            self.assertNotIn(forbidden, text)

    def test_03_root_readme_has_required_delivery_sections(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for section in ("Project Overview", "Quick Start", "Final Factor Handoff", "Data Layers", "Testing", "Team Handoff Checklist"):
            self.assertIn(section, text)

    def test_04_required_delivery_documents_exist(self) -> None:
        required = (
            "ENVIRONMENT.md", "LICENSE_OR_DATA_NOTICE.md", "data/README.md", "docs/PROJECT_STATUS.md",
            "docs/GITHUB_TEAM_HANDOFF.md", "docs/FINAL_RESEARCH_AUDIT_INDEX.md", "config/paths.example.yaml",
        )
        self.assertTrue(all((ROOT / path).is_file() for path in required))

    def test_05_delivery_and_source_manifests_parse(self) -> None:
        for path in (
            ROOT / "GITHUB_DELIVERY_MANIFEST.csv",
            ROOT / "handoff/final_factor/source_manifest.csv",
            ROOT / "handoff/final_factor/code_manifest.csv",
            ROOT / "handoff/final_factor/EXTERNAL_DATA_TRANSFER_MANIFEST.csv",
        ):
            self.assertTrue(rows(path), path)

    def test_06_canonical_code_manifest_paths_exist(self) -> None:
        for row in rows(ROOT / "handoff/final_factor/code_manifest.csv"):
            self.assertTrue((ROOT / row["relative_path"]).is_file(), row["relative_path"])

    def test_07_no_runtime_machine_absolute_path(self) -> None:
        audit = rows(AUDIT / "github_delivery_absolute_path_audit.csv")
        self.assertFalse([row for row in audit if row["runtime_critical"] == "true"])

    def test_08_no_confirmed_tracked_secret(self) -> None:
        audit = rows(AUDIT / "github_delivery_secret_audit.csv")
        self.assertFalse([row for row in audit if row["confirmed_secret"] == "true"])

    def test_09_no_tracked_raw_vendor_data_or_over_100mb_file(self) -> None:
        manifest = rows(ROOT / "GITHUB_DELIVERY_MANIFEST.csv")
        self.assertFalse([row for row in manifest if row["tracked"] == "true" and row["licensed_data"] == "true"])
        large = rows(AUDIT / "github_delivery_large_file_audit.csv")
        self.assertFalse([row for row in large if row["tracked"] == "true" and ">100MB" in row["thresholds_exceeded"]])

    def test_10_sample_loads_with_four_score_columns(self) -> None:
        sample = rows(ROOT / "handoff/final_factor/data/factor_scores_weekly_sample.csv")
        self.assertTrue(sample)
        self.assertTrue({"signal_date", "PERMNO", "quality_score", "momentum_score", "lowrisk_score", "investment_score"}.issubset(sample[0]))

    def test_11_receiver_smoke_script_passes(self) -> None:
        completed = subprocess.run((sys.executable, "-B", "scripts/smoke_test_handoff.py"), cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("SMOKE_TEST_HANDOFF PASS", completed.stdout)

    def test_12_full_parquet_is_not_tracked(self) -> None:
        tracked = subprocess.run(("git", "ls-files", "--", "handoff/final_factor/data"), cwd=ROOT, text=True, capture_output=True, check=True).stdout.splitlines()
        self.assertFalse([path for path in tracked if path.endswith(".parquet")])

    def test_13_acceptance_matrix_is_all_pass(self) -> None:
        acceptance = rows(AUDIT / "github_delivery_acceptance.csv")
        self.assertGreaterEqual(len(acceptance), 38)
        self.assertTrue(all(row["status"] == "PASS" for row in acceptance))

    def test_14_delivery_code_does_not_execute_research_or_strategy(self) -> None:
        text = (ROOT / "scripts/smoke_test_handoff.py").read_text(encoding="utf-8")
        for forbidden in ("src.validation", "src.factors", "src.strategy", "sqlite3"):
            self.assertNotIn(forbidden, text)

    def test_15_frozen_upstream_files_are_unchanged_since_delivery_parent(self) -> None:
        protected = (
            "docs/US_SG_Cross_Market_Quant_Research.md",
            "outputs/audit/wrds_formal/gate_a_final_factor_registry.csv",
        )
        changed = subprocess.run(("git", "diff", "--name-only", "68a69ec70771ee6a8bc021c06ecfc55cbc5b89d9", "--", *protected), cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
        self.assertEqual(changed, "")

    def test_16_oos_register_is_a_living_governance_record(self) -> None:
        register = rows(AUDIT / "oos_contamination_register.csv")
        record = next(row for row in register if row["stage"] == "US_DOWNSTREAM_DATA_MECHANICAL_MATERIALIZATION")
        self.assertEqual(record["performance_evidence_accessed"], "MECHANICAL_DATA_MATERIALIZATION_ONLY")
        self.assertEqual(record["post2015_returns_used_for_factor_choice"], "FALSE")
        self.assertEqual(record["status"], "NONDECISIONAL_OPERATIONAL_ACCESS_DOCUMENTED")


if __name__ == "__main__":
    unittest.main()
