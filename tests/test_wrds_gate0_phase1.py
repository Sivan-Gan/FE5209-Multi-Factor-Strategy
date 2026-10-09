"""Focused unit tests for the formal WRDS Gate 0 Phase-1 raw audit."""

from __future__ import annotations

import gzip
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from src.data import wrds_gate0_phase1


class TestWrdsGate0Phase1(unittest.TestCase):
    def _write_gzip_csv(self, path: Path, text: str) -> None:
        with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
            handle.write(text)

    def test_sha256_helper_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.txt"
            path.write_text("formal gate zero\n", encoding="utf-8")
            self.assertEqual(
                wrds_gate0_phase1.sha256_file(path),
                "0c39784859e5bd6cb5e4df5b605709ace62f1381bdb2d8ada0bc274dbad36034",
            )

    def test_gzip_schema_streams_rows_dates_and_unambiguous_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.csv.gz"
            rows = ["PERMNO,DlyCalDt,value"]
            rows.extend(f"{1000 + index},2004-01-01,{index}" for index in range(10_001))
            rows.append("20000,2025-12-31,10001")
            self._write_gzip_csv(path, "\n".join(rows) + "\n")
            schema, coverage = wrds_gate0_phase1.inspect_gzip_csv(path, "crsp/sample.csv.gz", ("permno", "dlycaldt"))
            self.assertEqual(schema["row_count"], 10_002)
            self.assertEqual(schema["column_count"], 3)
            self.assertEqual(schema["observed_min_date"], "2004-01-01")
            self.assertEqual(schema["observed_max_date"], "2025-12-31")
            self.assertEqual(coverage["basic_duplicate_count_if_unambiguous"], 0)
            self.assertEqual(coverage["missing_key_count_if_unambiguous"], 0)

    def test_tar_member_inspection_reads_gzip_batch_without_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tar_path = Path(directory) / "daily.tar"
            csv_bytes = io.BytesIO()
            with gzip.GzipFile(fileobj=csv_bytes, mode="wb") as compressed:
                compressed.write(b"PERMNO,DlyCalDt,DlyRet\n10001,2004-01-02,0.01\n10002,2004-01-05,0.02\n")
            summary_bytes = b"batch_count\n1\n"
            with tarfile.open(tar_path, "w") as archive:
                batch_info = tarfile.TarInfo("daily/crsp_daily_stock_batch_01.csv.gz")
                batch_info.size = len(csv_bytes.getvalue())
                archive.addfile(batch_info, io.BytesIO(csv_bytes.getvalue()))
                summary_info = tarfile.TarInfo("daily/summary.csv")
                summary_info.size = len(summary_bytes)
                archive.addfile(summary_info, io.BytesIO(summary_bytes))
            schemas, members, coverage, status = wrds_gate0_phase1.inspect_tar(tar_path, "crsp/daily.tar")
            self.assertEqual(status, "PASS")
            self.assertEqual(len(schemas), 2)
            self.assertEqual(len(members), 2)
            self.assertEqual(coverage["row_count"], 2)
            self.assertEqual(coverage["notes"], 'natural_key=["permno","dlycaldt"]; gzip_batch_count=1')
            self.assertEqual(coverage["basic_duplicate_count_if_unambiguous"], 0)
            batch = next(row for row in members if row["member_name"].endswith(".csv.gz"))
            self.assertEqual(batch["permno_count"], 2)

    def test_expected_and_unexpected_path_classification(self) -> None:
        actual = {
            wrds_gate0_phase1.REQUIRED_FILES[0],
            wrds_gate0_phase1.OPTIONAL_FILES[0],
            "unexpected/extra.csv.gz",
        }
        classification = wrds_gate0_phase1.classify_expected_paths(actual)
        self.assertEqual(classification["present_required"], [wrds_gate0_phase1.REQUIRED_FILES[0]])
        self.assertEqual(classification["optional_present"], [wrds_gate0_phase1.OPTIONAL_FILES[0]])
        self.assertIn("unexpected/extra.csv.gz", classification["unexpected_files"])
        self.assertEqual(len(classification["missing_required"]), len(wrds_gate0_phase1.REQUIRED_FILES) - 1)

    def test_snapshot_order_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "z").mkdir()
            (root / "a").mkdir()
            (root / "z" / "two.txt").write_text("2", encoding="utf-8")
            (root / "a" / "one.txt").write_text("1", encoding="utf-8")
            first = wrds_gate0_phase1.snapshot_raw_files(root)
            second = wrds_gate0_phase1.snapshot_raw_files(root)
            self.assertEqual(first, second)
            self.assertEqual([item.relative_path for item in first], ["a/one.txt", "z/two.txt"])


if __name__ == "__main__":
    unittest.main()
