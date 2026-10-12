#!/usr/bin/env python3
"""Run an isolated pre-commit receiver validation for the staged LFS delivery.

The protected branch is never committed, moved, reset, or rebased by this
script. It creates a temporary local reference from the current staged tree,
pushes that reference and its LFS objects to a temporary bare local source,
clones it with ``--no-local``, validates actual LFS-smudged Parquet content,
runs the receiver smoke test, and removes every temporary artifact afterwards.
"""
from __future__ import annotations

import csv
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "outputs/audit/wrds_formal"
APPROVED_MANIFEST = ROOT / "GITHUB_DERIVED_DATA_TRANSFER_MANIFEST.csv"


def git(*args: str, cwd: Path = ROOT, check: bool = True, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(("git", *args), cwd=cwd, text=True, capture_output=True, env=env)
    if check and completed.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {completed.stderr.strip()}")
    return completed.stdout


def manifest() -> list[dict[str, str]]:
    with APPROVED_MANIFEST.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 20 or len({row["relative_path"] for row in rows}) != 20:
        raise ValueError("approved delivery manifest is not exactly 20 unique files")
    return rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_report(rows: list[dict[str, str]]) -> None:
    path = AUDIT / "github_lfs_receiver_validation.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("check", "status", "details"), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def temporary_commit() -> tuple[str, str]:
    tree = git("write-tree").strip()
    parent = git("rev-parse", "HEAD").strip()
    author_name = git("show", "-s", "--format=%an", "HEAD").strip()
    author_email = git("show", "-s", "--format=%ae", "HEAD").strip()
    environment = os.environ.copy()
    environment.update({
        "GIT_AUTHOR_NAME": author_name, "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": author_name, "GIT_COMMITTER_EMAIL": author_email,
    })
    commit = git("commit-tree", tree, "-p", parent, "-m", "Temporary local LFS receiver validation", env=environment).strip()
    ref = f"refs/heads/lfs-receiver-validation-{uuid.uuid4().hex}"
    git("update-ref", ref, commit)
    return ref, commit


def main() -> int:
    checks: list[dict[str, str]] = []
    temporary_ref: str | None = None
    clone_dir: Path | None = None
    try:
        git("lfs", "version")
        staged = set(git("diff", "--cached", "--name-only").splitlines())
        expected = manifest()
        expected_paths = {row["relative_path"] for row in expected}
        checks.append({"check": "STAGED_APPROVED_FILES", "status": "PASS" if expected_paths <= staged else "FAIL", "details": "All manifest paths are staged before isolated validation."})
        if checks[-1]["status"] != "PASS":
            raise ValueError("not all approved LFS paths are staged")
        temporary_ref, temporary_commit_id = temporary_commit()
        with tempfile.TemporaryDirectory(prefix="fe5209-lfs-receiver-") as directory:
            temporary_root = Path(directory)
            bare_dir = temporary_root / "source.git"
            clone_dir = temporary_root / "receiver"
            branch = temporary_ref.removeprefix("refs/heads/")
            bare_url = bare_dir.as_uri()
            subprocess.run(("git", "init", "--bare", str(bare_dir)), text=True, capture_output=True, check=True)
            subprocess.run(("git", "push", "--no-verify", str(bare_dir), f"{temporary_ref}:refs/heads/{branch}"), cwd=ROOT, text=True, capture_output=True, check=True)
            git("-c", f"remote.lfs-validation.url={bare_url}", "lfs", "push", "lfs-validation", branch)
            subprocess.run(("git", "clone", "--no-local", "--branch", branch, "--single-branch", bare_url, str(clone_dir)), text=True, capture_output=True, check=True)
            subprocess.run(("git", "lfs", "pull"), cwd=clone_dir, text=True, capture_output=True, check=True)
            received_lfs = set(git("lfs", "ls-files", "-n", cwd=clone_dir).splitlines())
            checks.append({"check": "RECEIVER_LFS_SCOPE", "status": "PASS" if received_lfs == expected_paths else "FAIL", "details": "Receiver sees exactly the 20 approved LFS paths."})
            exact = all((clone_dir / row["relative_path"]).is_file() and (clone_dir / row["relative_path"]).stat().st_size == int(row["bytes"]) and sha256(clone_dir / row["relative_path"]) == row["sha256"] for row in expected)
            checks.append({"check": "RECEIVER_REAL_PARQUET_HASHES", "status": "PASS" if exact else "FAIL", "details": "Receiver working tree contains actual Parquet contents with manifest hashes."})
            smoke = subprocess.run((sys.executable, "-B", "scripts/smoke_test_complete_delivery.py"), cwd=clone_dir, text=True, capture_output=True)
            checks.append({"check": "RECEIVER_COMPLETE_SMOKE", "status": "PASS" if smoke.returncode == 0 and "SMOKE_TEST_COMPLETE_DELIVERY PASS" in smoke.stdout else "FAIL", "details": "Receiver smoke validates K=4, 20-spec archive, and required downstream files."})
            checks.append({"check": "TEMPORARY_VALIDATION_COMMIT", "status": "PASS", "details": f"Temporary non-protected commit {temporary_commit_id} was transferred through a temporary local bare source and will be dereferenced."})
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        checks.append({"check": "RECEIVER_VALIDATION_EXECUTION", "status": "FAIL", "details": str(exc).replace(str(ROOT), "<repository>")})
    finally:
        if temporary_ref:
            git("update-ref", "-d", temporary_ref, check=False)
        if clone_dir and clone_dir.exists():
            shutil.rmtree(clone_dir, ignore_errors=True)
    write_report(checks)
    failures = [row for row in checks if row["status"] != "PASS"]
    print(f"FRESH_CLONE_LFS_TEST {'PASS' if not failures else 'FAIL'} checks={len(checks)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
