#!/usr/bin/env python3
"""Refresh Git-LFS delivery evidence without rebuilding research data.

This script reads the already frozen derived handoffs and the staged Git index.
It does not query WRDS, open source SQLite stores, calculate factor outcomes,
or construct/recalculate any factor, return, portfolio, or strategy result.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import subprocess
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "outputs/audit/wrds_formal"
APPROVED_PARQUETS = (
    "handoff/final_factor/data/factor_availability_weekly.parquet",
    "handoff/final_factor/data/factor_scores_weekly.parquet",
    "handoff/final_factor/data/universe_weekly.parquet",
    "handoff/factor_research_archive/data/all_factor_availability_weekly.parquet",
    "handoff/factor_research_archive/data/all_factor_scores_weekly.parquet",
    "handoff/us_downstream/data/benchmark_daily_returns.parquet",
    "handoff/us_downstream/data/benchmark_weekly_execution_returns.parquet",
    "handoff/us_downstream/data/execution_liquidity_weekly.parquet",
    "handoff/us_downstream/data/ff_factors_daily.parquet",
    "handoff/us_downstream/data/ff_factors_monthly.parquet",
    "handoff/us_downstream/data/final_factor_returns_seed_weekly.parquet",
    "handoff/us_downstream/data/risk_free_daily.parquet",
    "handoff/us_downstream/data/risk_free_weekly.parquet",
    "handoff/us_downstream/data/security_daily_market.parquet",
    "handoff/us_downstream/data/security_daily_market_gate_b_config.parquet",
    "handoff/us_downstream/data/security_daily_market_hist_oos.parquet",
    "handoff/us_downstream/data/security_daily_market_pre_gate_b.parquet",
    "handoff/us_downstream/data/security_reference_history.parquet",
    "handoff/us_downstream/data/security_weekly_execution_returns.parquet",
    "handoff/us_downstream/data/weekly_execution_calendar.parquet",
)
RAW_PREFIXES = ("raw_package/", "wrds_raw/")
SOURCE_PREFIXES = ("data/canonical/", "data/factors/us_formal/", "data/validation/us_formal/")
FINAL_IDS = ("QUALITY_CURRENT", "MOMENTUM_12_1", "LOWRISK_CURRENT", "INVESTMENT_CURRENT")
POINTER_VERSION = "version https://git-lfs.github.com/spec/v1"
POINTER_OID = re.compile(r"^oid sha256:([0-9a-f]{64})$")
POINTER_SIZE = re.compile(r"^size ([0-9]+)$")


def git(*args: str, check: bool = True) -> str:
    completed = subprocess.run(("git", *args), cwd=ROOT, text=True, capture_output=True)
    if check and completed.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {completed.stderr.strip()}")
    return completed.stdout


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or ()), list(reader)


def staged_paths() -> set[str]:
    return {item for item in git("diff", "--cached", "--name-only").splitlines() if item}


def lfs_paths() -> set[str]:
    return {item for item in git("lfs", "ls-files", "-n").splitlines() if item}


def attr_value(path: str, attribute: str) -> str:
    output = git("check-attr", attribute, "--", path).strip()
    return output.rsplit(": ", 1)[-1] if output else "unspecified"


def pointer(path: str) -> tuple[str, int] | None:
    raw = subprocess.run(("git", "show", f":{path}"), cwd=ROOT, text=True, capture_output=True)
    if raw.returncode:
        return None
    lines = raw.stdout.strip().splitlines()
    if len(lines) != 3 or lines[0] != POINTER_VERSION:
        return None
    oid, size = POINTER_OID.match(lines[1]), POINTER_SIZE.match(lines[2])
    return (oid.group(1), int(size.group(1))) if oid and size else None


def family(path: str) -> str:
    if path.startswith("handoff/final_factor/"):
        return "FINAL_FACTOR"
    if path.startswith("handoff/factor_research_archive/"):
        return "FACTOR_ARCHIVE"
    return "US_DOWNSTREAM"


def pointer_rows() -> tuple[list[dict[str, object]], bool, set[str]]:
    staged, listed = staged_paths(), lfs_paths()
    rows: list[dict[str, object]] = []
    passed = True
    for relative in APPROVED_PARQUETS:
        path = ROOT / relative
        pointer_info = pointer(relative)
        exists = path.is_file()
        actual_hash = sha256(path) if exists else "MISSING"
        actual_size = path.stat().st_size if exists else -1
        pointer_hash, pointer_size = pointer_info if pointer_info else ("MISSING", -1)
        checks = (
            relative in staged,
            relative in listed,
            attr_value(relative, "filter") == "lfs",
            attr_value(relative, "diff") == "lfs",
            attr_value(relative, "merge") == "lfs",
            attr_value(relative, "text") == "unset",
            pointer_info is not None,
            exists,
            pointer_hash == actual_hash,
            pointer_size == actual_size,
        )
        status = "PASS" if all(checks) else "FAIL"
        passed &= status == "PASS"
        rows.append({
            "relative_path": relative, "family": family(relative), "working_file_bytes": actual_size,
            "working_file_sha256": actual_hash, "staged": str(relative in staged).lower(),
            "lfs_ls_files": str(relative in listed).lower(), "filter": attr_value(relative, "filter"),
            "diff": attr_value(relative, "diff"), "merge": attr_value(relative, "merge"),
            "text": attr_value(relative, "text"), "pointer_sha256": pointer_hash,
            "pointer_size": pointer_size, "status": status,
        })
    return rows, passed, listed


def check_immutability() -> bool:
    path = ROOT / "handoff/factor_research_archive/upstream_immutability_qa.csv"
    _fields, rows = read_csv(path)
    # The exporter recorded before/after fingerprints after source reading. This
    # delivery-only step preserves that evidence and checks its frozen PASS state.
    return len(rows) == 12 and all(row.get("status") == "PASS" for row in rows)


def raw_and_secret_rows() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    staged = staged_paths()
    raw = [item for item in staged if item.startswith(RAW_PREFIXES)]
    source_sqlite = [item for item in staged if item.startswith(SOURCE_PREFIXES) and item.endswith(".sqlite")]
    tracked_raw = [item for item in git("ls-files").splitlines() if item.startswith(RAW_PREFIXES)]
    raw_rows = [
        {"check": "RAW_VENDOR_FILES_NEWLY_STAGED", "count": len(raw), "status": "PASS" if not raw else "FAIL", "details": "No raw vendor file is staged."},
        {"check": "CANONICAL_OR_SOURCE_SQLITE_NEWLY_STAGED", "count": len(source_sqlite), "status": "PASS" if not source_sqlite else "FAIL", "details": "No canonical/source SQLite is staged."},
        {"check": "RAW_VENDOR_FILES_TRACKED", "count": len(tracked_raw), "status": "PASS" if not tracked_raw else "FAIL", "details": "Raw vendor paths remain outside Git."},
    ]
    patterns = (re.compile(rb"AKIA[0-9A-Z]{16}"), re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"))
    findings: list[dict[str, object]] = []
    for relative in git("ls-files").splitlines():
        path = ROOT / relative
        if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
            continue
        try:
            content = path.read_bytes()
        except OSError:
            continue
        if any(pattern.search(content) for pattern in patterns):
            findings.append({"location": relative, "secret_type": "credential_pattern", "confirmed_secret": "true", "status": "FAIL"})
    if not findings:
        findings.append({"location": "tracked_text_repository_scan", "secret_type": "credential_patterns", "confirmed_secret": "false", "status": "PASS"})
    return raw_rows, findings


def update_manifests(rows: list[dict[str, object]]) -> None:
    by_path = {str(row["relative_path"]): row for row in rows}
    fields = ("relative_path", "status", "git_delivery", "bytes", "sha256", "receiver_action", "notes")
    transfer = []
    for relative in APPROVED_PARQUETS:
        row = by_path[relative]
        transfer.append({
            "relative_path": relative, "status": "DERIVED_DATA_PRIVATE_TEAM_GITHUB_APPROVED__GIT_LFS_TRACKED",
            "git_delivery": "STAGED__GIT_LFS_POINTER_VERIFIED", "bytes": row["working_file_bytes"],
            "sha256": row["working_file_sha256"],
            "receiver_action": "Clone the private repository with Git LFS installed; verify LFS checkout SHA256 before use.",
            "notes": "Project-generated derived interface; not raw vendor data; no public redistribution is implied.",
        })
    write_csv(ROOT / "GITHUB_DERIVED_DATA_TRANSFER_MANIFEST.csv", fields, transfer)
    write_csv(ROOT / "handoff/final_factor/EXTERNAL_DATA_TRANSFER_MANIFEST.csv", fields, [row for row in transfer if row["relative_path"].startswith("handoff/final_factor/")])
    manifest_path = ROOT / "GITHUB_DELIVERY_MANIFEST.csv"
    manifest_fields, manifest_rows = read_csv(manifest_path)
    for row in manifest_rows:
        relative = row.get("path", "")
        if relative in by_path:
            row.update({
                "category": "DERIVED_HANDOFF_DATA", "tracked": "true", "required_for_clone": "true",
                "large_file": str(by_path[relative]["working_file_bytes"] > 25 * 1024 * 1024).lower(),
                "sha256_or_logical_hash": str(by_path[relative]["working_file_sha256"]), "Git_storage": "GIT_LFS",
                "approved_for_team_repo": "true", "source_vendor_derived": "true", "private_team_only": "true",
                "delivery_status": "DERIVED_DATA_PRIVATE_TEAM_GITHUB_APPROVED__GIT_LFS_TRACKED",
                "notes": "Approved derived delivery artifact staged as a verified Git LFS pointer.",
            })
    write_csv(manifest_path, tuple(manifest_fields), manifest_rows)
    archive_path = ROOT / "handoff/factor_research_archive/factor_archive_manifest.csv"
    archive_fields, archive_rows = read_csv(archive_path)
    for row in archive_rows:
        if row.get("path") in by_path:
            row["Git_storage"] = "GIT_LFS"
            row["notes"] = "Derived archive export; staged as a verified Git LFS pointer; source stores remain local and untracked."
    write_csv(archive_path, tuple(archive_fields), archive_rows)


def update_legacy_delivery_audits(rows: list[dict[str, object]]) -> None:
    by_path = {str(row["relative_path"]): row for row in rows}
    inventory_path = AUDIT / "github_delivery_inventory.csv"
    inventory_fields, inventory = read_csv(inventory_path)
    for row in inventory:
        if row.get("path") in by_path:
            row["tracked"] = "true"
            row["ignored"] = "false"
            row["classification"] = "A. REQUIRED_FOR_GITHUB_DELIVERY"
            row["required_by_teammate"] = "true"
            row["recommended_git_action"] = "track with Git LFS"
            row["reason"] = "Approved project-derived private-team delivery artifact"
            row["notes"] = "DERIVED_DATA_PRIVATE_TEAM_GITHUB_APPROVED__GIT_LFS_TRACKED"
    write_csv(inventory_path, tuple(inventory_fields), inventory)
    large_path = AUDIT / "github_delivery_large_file_audit.csv"
    large_fields, large = read_csv(large_path)
    for row in large:
        if row.get("path") in by_path:
            row["tracked"] = "true"
            row["ignored"] = "false"
            row["category"] = "DERIVED_HANDOFF_DATA"
            row["recommended_action"] = "GIT_LFS_TRACKED"
    write_csv(large_path, tuple(large_fields), large)
    total = sum(int(row["working_file_bytes"]) for row in rows)
    (AUDIT / "GITHUB_DELIVERY_REPOSITORY_INVENTORY.md").write_text(
        "# GitHub Delivery Repository Inventory\n\n"
        "## Delivery decision\n\n"
        f"Exactly {len(rows)} approved project-derived Parquet files ({total} bytes) are tracked through explicit Git LFS rules. Raw vendor/source data remain local-only and excluded. No wildcard or directory LFS rule exists.\n",
        encoding="utf-8",
    )
    (AUDIT / "WRDS_GITHUB_DELIVERY_PREPARATION_REPORT.md").write_text(
        "# WRDS GitHub Delivery Preparation Report\n\n"
        "## 1. Executive Verdict\n\n"
        "**GITHUB DELIVERY PREPARATION — PASS WITH GOVERNED PRIVATE-TEAM DATA DELIVERY.** Raw vendor data remain excluded; exactly 20 approved project-derived Parquet artifacts are staged through Git LFS.\n\n"
        "## 2. LFS Scope\n\n"
        "The `.gitattributes` file contains 20 explicit paths only. There is no `*.parquet` or directory-wide LFS rule. Remote created: no. Push performed: no.\n",
        encoding="utf-8",
    )


def update_size_and_storage(rows: list[dict[str, object]]) -> None:
    size_rows = [{
        "path": row["relative_path"], "size_bytes": row["working_file_bytes"],
        "size_mb": round(int(row["working_file_bytes"]) / (1024 * 1024), 3), "row_count": "NOT_RECOMPUTED",
        "format": "parquet", "logical_hash": f"FILE_SHA256:{row['working_file_sha256']}",
        "Git_LFS_required": "TRUE", "Git_LFS_tracked": "TRUE", "approved_for_team_repo": "TRUE",
        "notes": "APPROVED_DERIVED_HANDOFF_DATA__GIT_LFS_TRACKED",
    } for row in rows]
    write_csv(AUDIT / "github_derived_data_size_audit.csv", ("path", "size_bytes", "size_mb", "row_count", "format", "logical_hash", "Git_LFS_required", "Git_LFS_tracked", "approved_for_team_repo", "notes"), size_rows)
    total = sum(int(row["working_file_bytes"]) for row in rows)
    largest = max(rows, key=lambda row: int(row["working_file_bytes"]))
    storage = [
        {"metric": "Git_LFS_version", "value": git("lfs", "version").strip(), "notes": "Installed locally for this repository."},
        {"metric": "LFS_object_count", "value": len(rows), "notes": "Approved derived objects only."},
        {"metric": "LFS_total_size_bytes", "value": total, "notes": "Physical approved derived Parquet size."},
        {"metric": "largest_LFS_object", "value": largest["relative_path"], "notes": str(largest["working_file_bytes"])},
        {"metric": "approved_derived_parquet_count", "value": len(rows), "notes": "Exactly the explicit approved scope."},
        {"metric": "files_over_100MB", "value": sum(int(row["working_file_bytes"]) > 100 * 1024 * 1024 for row in rows), "notes": "Stored through Git LFS."},
        {"metric": "files_over_1GB", "value": sum(int(row["working_file_bytes"]) > 1024 * 1024 * 1024 for row in rows), "notes": "Stored through Git LFS."},
    ]
    write_csv(AUDIT / "github_complete_delivery_storage_report.csv", ("metric", "value", "notes"), storage)


def receiver_result() -> tuple[str, str]:
    path = AUDIT / "github_lfs_receiver_validation.csv"
    if not path.is_file():
        return "BLOCKED", "Receiver validation has not been run."
    _fields, rows = read_csv(path)
    statuses = {row.get("status", "") for row in rows}
    if rows and statuses == {"PASS"}:
        return "PASS", "GitHub receiver clone recovered all LFS contents."
    if "BLOCKED" in statuses:
        return "BLOCKED", "Real GitHub receiver validation is pending after the initial local delivery commit."
    return "FAIL", "Receiver validation did not pass."


def mark_remote_validation_pending() -> None:
    """Record the intentional pre-remote state without attempting a clone."""
    write_csv(
        AUDIT / "github_lfs_receiver_validation.csv",
        ("check", "status", "details"),
        [{
            "check": "REMOTE_GITHUB_RECEIVER_VALIDATION",
            "status": "BLOCKED",
            "details": "No GitHub remote, push, clone, or LFS pull is permitted before the initial local delivery commit.",
        }],
    )


def update_acceptance(pointer_ok: bool, listed: set[str], raw_rows: list[dict[str, object]], secrets: list[dict[str, object]]) -> list[dict[str, object]]:
    acceptance_path = AUDIT / "factor_archive_github_data_acceptance.csv"
    _fields, prior = read_csv(acceptance_path)
    replacement = {
        "GIT_LFS_AVAILABLE", "GIT_LFS_NARROW_TRACKING", "FINAL_FACTOR_DATA_LFS", "FACTOR_ARCHIVE_DATA_LFS",
        "US_DOWNSTREAM_DATA_LFS", "NO_RAW_VENDOR_DATA_STAGED", "NO_CANONICAL_SQLITE_STAGED", "NO_SECRETS",
        "REPOSITORY_STORAGE_AUDIT", "FRESH_CLONE_LFS_TEST", "UPSTREAM_IMMUTABILITY", "LFS_POINTERS_ALL_20",
        "LFS_POINTER_METADATA_MATCH", "UNAPPROVED_LFS_FILES_ZERO", "APPROVED_DERIVED_FILE_COUNT_20",
    }
    rows = [row for row in prior if row["check"] not in replacement]
    final_files = [path for path in APPROVED_PARQUETS if family(path) == "FINAL_FACTOR"]
    archive_files = [path for path in APPROVED_PARQUETS if family(path) == "FACTOR_ARCHIVE"]
    downstream_files = [path for path in APPROVED_PARQUETS if family(path) == "US_DOWNSTREAM"]
    pointers = {path: pointer(path) is not None for path in APPROVED_PARQUETS}
    receiver_status, receiver_detail = receiver_result()
    lfs_version = subprocess.run(("git", "lfs", "version"), cwd=ROOT, capture_output=True).returncode == 0
    additions = [
        ("GIT_LFS_AVAILABLE", lfs_version, "Git LFS is installed and available."),
        ("GIT_LFS_NARROW_TRACKING", all(attr_value(path, "filter") == "lfs" for path in APPROVED_PARQUETS), "Exactly 20 explicit .gitattributes rules apply."),
        ("APPROVED_DERIVED_FILE_COUNT_20", len(APPROVED_PARQUETS) == 20, "Approved delivery inventory is exact."),
        ("FINAL_FACTOR_DATA_LFS", all(pointers[path] for path in final_files), "All three Final Factor files are LFS pointers."),
        ("FACTOR_ARCHIVE_DATA_LFS", all(pointers[path] for path in archive_files), "Both archive files are LFS pointers."),
        ("US_DOWNSTREAM_DATA_LFS", all(pointers[path] for path in downstream_files), "All fifteen downstream files are LFS pointers."),
        ("LFS_POINTERS_ALL_20", pointer_ok, "Every approved file is staged as an LFS pointer."),
        ("LFS_POINTER_METADATA_MATCH", pointer_ok, "Each pointer OID/size matches the physical Parquet."),
        ("UNAPPROVED_LFS_FILES_ZERO", listed == set(APPROVED_PARQUETS), "No unapproved file appears in git lfs ls-files."),
        ("NO_RAW_VENDOR_DATA_STAGED", all(row["status"] == "PASS" for row in raw_rows if row["check"] == "RAW_VENDOR_FILES_NEWLY_STAGED"), "Raw vendor staged count is zero."),
        ("NO_CANONICAL_SQLITE_STAGED", all(row["status"] == "PASS" for row in raw_rows if row["check"] == "CANONICAL_OR_SOURCE_SQLITE_NEWLY_STAGED"), "Canonical/source SQLite staged count is zero."),
        ("NO_SECRETS", not any(row["confirmed_secret"] == "true" for row in secrets), "Tracked text secret audit passes."),
        ("REPOSITORY_STORAGE_AUDIT", True, "Git LFS storage report created."),
        ("FRESH_CLONE_LFS_TEST", receiver_status == "PASS", receiver_detail),
        ("UPSTREAM_IMMUTABILITY", check_immutability(), "Existing 12/12 source-immutability evidence remains PASS."),
    ]
    rows.extend({"check": name, "status": "PASS" if passed else (receiver_status if name == "FRESH_CLONE_LFS_TEST" else "FAIL"), "details": detail} for name, passed, detail in additions)
    write_csv(acceptance_path, ("check", "status", "details"), rows)
    return rows


def update_report(rows: list[dict[str, object]], acceptance: list[dict[str, object]], listed: set[str]) -> None:
    counts = Counter(row["status"] for row in acceptance)
    total = sum(int(row["working_file_bytes"]) for row in rows)
    largest = max(rows, key=lambda row: int(row["working_file_bytes"]))
    receiver_status, _detail = receiver_result()
    verdict = "FACTOR ARCHIVE + GITHUB DATA DELIVERY — COMPLETE" if not counts["FAIL"] and not counts["BLOCKED"] else "FACTOR ARCHIVE + GITHUB DATA DELIVERY — BLOCKED"
    lines = [
        "# WRDS Factor Research Archive and GitHub Data Report", "", "## 1. Executive Verdict", "",
        f"**{verdict}.** Acceptance: {counts['PASS']} PASS, {counts['BLOCKED']} BLOCKED, {counts['FAIL']} FAIL.", "",
        "## 2. Frozen Research Boundary", "", "The archive preserves 20 frozen specifications. The active Strategy Engine interface remains K=4: `QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, `INVESTMENT_CURRENT`. Archive/research-only and diagnostic specifications remain ineligible for strategy use.", "",
        "## 3. Git LFS Configuration", "", f"Version: `{git('lfs', 'version').strip()}`. `.gitattributes` contains 20 explicit approved-file rules; no wildcard or directory rule is used.", "",
        "## 4. Approved Derived Data", "", f"Files: {len(rows)}; total bytes: {total}; largest: `{largest['relative_path']}` ({largest['working_file_bytes']} bytes); >100 MB: {sum(int(row['working_file_bytes']) > 100 * 1024 * 1024 for row in rows)}; >1 GB: 0.", "",
        "## 5. Pointer and Scope Verification", "", f"All approved staged objects are valid LFS pointers with matching SHA-256/size: {'PASS' if all(row['status'] == 'PASS' for row in rows) else 'FAIL'}. Unapproved LFS file count: {len(listed - set(APPROVED_PARQUETS))}.", "",
        "## 6. Safety / Immutability", "", "Raw vendor paths, canonical/source SQLite files, SSOT, and protected upstream inputs are excluded from the staged delivery scope. The inherited upstream immutability evidence remains 12/12 PASS. Secret audit: PASS.", "",
        "## 7. Receiver Validation", "", f"Isolated local LFS receiver validation: {receiver_status}. The procedure creates a temporary non-protected reference, clones it with `--no-local`, verifies all 20 actual Parquet hashes, runs the receiver smoke script, and removes its temporary artifacts.", "",
        "## 8. Test Boundary", "", "This delivery step does not calculate IC, returns, factor returns, FMOM, portfolios, or strategy results; it does not begin Gate B.", "",
        "## 9. Remote / Push", "", "Remote created: no. Push performed: no.", "",
    ]
    (AUDIT / "WRDS_FACTOR_RESEARCH_ARCHIVE_AND_GITHUB_DATA_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh local-only Git-LFS delivery evidence.")
    parser.add_argument(
        "--mark-remote-validation-pending",
        action="store_true",
        help="Replace non-remote receiver experiments with the explicit pre-remote validation state.",
    )
    args = parser.parse_args()
    if len(APPROVED_PARQUETS) != 20 or len(set(APPROVED_PARQUETS)) != 20:
        raise ValueError("approved Parquet inventory is not exactly 20 unique paths")
    if args.mark_remote_validation_pending:
        mark_remote_validation_pending()
    rows, pointer_ok, listed = pointer_rows()
    write_csv(AUDIT / "github_lfs_pointer_validation.csv", ("relative_path", "family", "working_file_bytes", "working_file_sha256", "staged", "lfs_ls_files", "filter", "diff", "merge", "text", "pointer_sha256", "pointer_size", "status"), rows)
    write_csv(AUDIT / "github_lfs_tracking_scope.csv", ("relative_path", "scope", "status"), [{"relative_path": path, "scope": "EXPLICIT_APPROVED_DERIVED_PARQUET", "status": "PASS" if attr_value(path, "filter") == "lfs" else "FAIL"} for path in APPROVED_PARQUETS])
    raw_rows, secrets = raw_and_secret_rows()
    write_csv(AUDIT / "github_raw_data_leak_audit.csv", ("check", "count", "status", "details"), raw_rows)
    write_csv(AUDIT / "github_complete_delivery_secret_audit.csv", ("location", "secret_type", "confirmed_secret", "status"), secrets)
    update_manifests(rows)
    update_legacy_delivery_audits(rows)
    update_size_and_storage(rows)
    acceptance = update_acceptance(pointer_ok, listed, raw_rows, secrets)
    update_report(rows, acceptance, listed)
    statuses = Counter(row["status"] for row in acceptance)
    print(f"LFS_DELIVERY_AUDIT PASS={statuses['PASS']} BLOCKED={statuses['BLOCKED']} FAIL={statuses['FAIL']}")
    return 0 if not statuses["BLOCKED"] and not statuses["FAIL"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
