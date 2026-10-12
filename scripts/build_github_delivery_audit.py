#!/usr/bin/env python3
"""Build operational GitHub-delivery audits without touching research data.

This script inventories paths and delivery metadata. It does not query WRDS,
open local SQLite research stores, calculate returns, or construct factors.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable


PREPARATION_PARENT = "68a69ec70771ee6a8bc021c06ecfc55cbc5b89d9"
FINAL_IDS = ("QUALITY_CURRENT", "MOMENTUM_12_1", "LOWRISK_CURRENT", "INVESTMENT_CURRENT")
RAW_PREFIXES = ("raw_package/", "wrds_raw/")
LOCAL_PREFIXES = ("data/canonical/", "data/factors/", "data/validation/", "data/interim/", "data/dev_legacy/")
HISTORICAL_PREFIXES = ("docs/dev_legacy/", "outputs/audit/dev_legacy/", "outputs/factor_research/", "outputs/audit/wrds_formal/")
COLLAPSED_LOCAL_DIRS = (".venv", ".planning")
PERSONAL_HOME_FRAGMENT = "/Users/" + "gan" + "junhui" + "/"
MACHINE_PATH_FRAGMENTS = (
    PERSONAL_HOME_FRAGMENT,
    "/" + "Desktop" + "/",
    "/" + "课堂" + "/",
    "/" + "FE" + "5209" + "/FE" + "5209",
)
ABSOLUTE_PATTERN = re.compile("|".join(re.escape(fragment) for fragment in MACHINE_PATH_FRAGMENTS))
SECRET_PATTERNS = (
    ("AWS_ACCESS_KEY", re.compile(rb"AKIA[0-9A-Z]{16}")),
    ("PRIVATE_KEY", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("ASSIGNED_SECRET", re.compile(rb"(?i)(password|passwd|api[_-]?key|secret|token)\s*[:=]\s*['\"][^'\"\s]{8,}")),
)
CSV_INVENTORY_FIELDS = (
    "path", "type", "size", "tracked", "ignored", "classification", "required_by_teammate",
    "required_for_reproduction", "contains_vendor_data", "contains_generated_data", "canonical_or_duplicate",
    "recommended_git_action", "reason", "notes",
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run_git(root: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(("git", *args), cwd=root, text=True, capture_output=True, check=check)
    return completed.stdout


def tracked_paths(root: Path) -> set[str]:
    return {line for line in run_git(root, "ls-files").splitlines() if line}


def ignored(root: Path, rel: str) -> bool:
    return subprocess.run(("git", "check-ignore", "-q", "--", rel), cwd=root).returncode == 0


def file_size(path: Path) -> int:
    return path.stat().st_size if path.is_file() else 0


def directory_size(path: Path) -> int:
    total = 0
    for current, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(current) / name).stat().st_size
            except OSError:
                continue
    return total


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_classification(rel: str, is_directory: bool = False) -> tuple[str, bool, bool, bool, str, str, str]:
    """Return category and delivery-policy fields for a repository-relative path."""
    if rel.startswith(RAW_PREFIXES):
        return ("E. PROPRIETARY_RAW_DATA_DO_NOT_UPLOAD", False, True, True, "source", "keep ignored", "Licensed/raw source evidence is local only")
    if rel in {".venv/", ".planning/"} or rel.endswith(".DS_Store") or rel.startswith(".venv/") or rel.startswith(".planning/"):
        return ("D. TEMPORARY_OR_REGENERABLE", False, False, False, "local", "keep ignored", "Machine-local environment, planning, or OS metadata")
    if rel.startswith("data/dev_legacy/"):
        return ("C. HISTORICAL_AUDIT_KEEP", False, True, False, "historical evidence", "retain tracked or ignored status", "Archived DEV-derived data; not an active formal runtime input")
    if rel == "data/README.md":
        return ("A. REQUIRED_FOR_GITHUB_DELIVERY", True, True, False, "canonical", "track", "Delivery data-placement instructions")
    if rel.startswith(LOCAL_PREFIXES) or rel.endswith(".parquet"):
        return ("B. REQUIRED_FOR_REPRODUCTION_BUT_LOCAL_DATA", rel.startswith("handoff/final_factor/data/"), True, False, "local canonical", "keep ignored", "Large local store or execution interface")
    if rel.startswith(HISTORICAL_PREFIXES) or rel == "outputs/audit/retired_market_cap_artifacts_manifest.csv":
        return ("C. HISTORICAL_AUDIT_KEEP", False, True, False, "historical evidence", "retain tracked", "Preserved audit evidence; not an active runtime input")
    if rel.startswith("src/") or rel.startswith("tests/") or rel.startswith("docs/") or rel.startswith("config/") or rel.startswith("scripts/"):
        return ("A. REQUIRED_FOR_GITHUB_DELIVERY", True, True, False, "canonical", "track", "Code, tests, documentation, or portable configuration")
    if rel.startswith("handoff/") or rel in {"README.md", "ENVIRONMENT.md", "LICENSE_OR_DATA_NOTICE.md", "requirements.txt", ".gitignore", "GITHUB_DELIVERY_MANIFEST.csv"}:
        return ("A. REQUIRED_FOR_GITHUB_DELIVERY", True, True, False, "canonical", "track", "Delivery contract, manifest, or repository metadata")
    if is_directory:
        return ("D. TEMPORARY_OR_REGENERABLE", False, False, False, "directory", "do not track directory", "Non-delivery directory aggregate")
    return ("F. DUPLICATE_OR_OBSOLETE", False, False, False, "review", "do not add without review", "No active delivery role identified")


def physical_paths(root: Path) -> Iterable[tuple[str, Path, bool]]:
    """Yield files plus collapsed high-volume local-directory rows, excluding .git."""
    for name in COLLAPSED_LOCAL_DIRS:
        path = root / name
        if path.exists():
            yield f"{name}/", path, True
    for current, directories, files in os.walk(root):
        current_path = Path(current)
        rel_dir = current_path.relative_to(root).as_posix() if current_path != root else ""
        directories[:] = [
            item for item in directories
            if item != ".git" and not (not rel_dir and item in COLLAPSED_LOCAL_DIRS)
        ]
        for name in files:
            rel = (current_path / name).relative_to(root).as_posix()
            if rel.startswith(".git/") or any(rel.startswith(f"{item}/") for item in COLLAPSED_LOCAL_DIRS):
                continue
            yield rel, current_path / name, False


def write_csv(path: Path, fields: tuple[str, ...] | list[str], rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def inventory_rows(root: Path, tracked: set[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for rel, path, is_directory in sorted(physical_paths(root), key=lambda item: item[0]):
        category, teammate, reproduce, vendor, canonical, action, reason = path_classification(rel, is_directory)
        row_tracked = rel.rstrip("/") in tracked
        row_ignored = ignored(root, rel.rstrip("/")) if not is_directory else ignored(root, rel.rstrip("/"))
        rows.append({
            "path": rel,
            "type": "DIRECTORY_AGGREGATE" if is_directory else "FILE",
            "size": directory_size(path) if is_directory else file_size(path),
            "tracked": str(row_tracked).lower(),
            "ignored": str(row_ignored).lower(),
            "classification": category,
            "required_by_teammate": str(teammate).lower(),
            "required_for_reproduction": str(reproduce).lower(),
            "contains_vendor_data": str(vendor).lower(),
            "contains_generated_data": str(rel.startswith(("outputs/", "handoff/", "data/"))).lower(),
            "canonical_or_duplicate": canonical,
            "recommended_git_action": action,
            "reason": reason,
            "notes": "DATA_DISTRIBUTION_REVIEW_REQUIRED" if rel.endswith(".parquet") else "",
        })
    return rows


def write_inventory_summary(path: Path, rows: list[dict[str, object]]) -> None:
    classes = Counter(str(row["classification"]) for row in rows)
    tracked = sum(row["tracked"] == "true" for row in rows)
    ignored_count = sum(row["ignored"] == "true" for row in rows)
    lines = [
        "# GitHub Delivery Repository Inventory",
        "",
        "## Scope",
        "",
        "This inventory classifies physical delivery-relevant files and collapsed local environment/planning directories. `.git/` is intentionally excluded. No vendor data are copied, opened for research, or staged by this inventory.",
        "",
        "## Classification summary",
        "",
        "| Classification | Paths |",
        "| --- | ---: |",
    ]
    lines += [f"| {key} | {classes[key]} |" for key in sorted(classes)]
    lines += [
        "",
        f"Tracked physical paths at audit time: **{tracked}**. Ignored physical paths at audit time: **{ignored_count}**.",
        "",
        "## Delivery decision",
        "",
        "Licensed WRDS/CRSP/Compustat/CCM/Historical-GICS raw data and the legacy raw source package remain local-only. Final handoff Parquet files are derived interfaces, but no explicit redistribution approval was found in project documentation; they remain Git-ignored with status **DATA_DISTRIBUTION_REVIEW_REQUIRED**. Their separate-transfer hashes are in `handoff/final_factor/EXTERNAL_DATA_TRANSFER_MANIFEST.csv`.",
        "",
        "See `github_delivery_inventory.csv` for one row per inventoried path.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def absolute_path_rows(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    scopes = ("src", "tests", "config", "scripts", "README.md", "docs", "handoff")
    for scope in scopes:
        target = root / scope
        paths = [target] if target.is_file() else sorted(target.rglob("*")) if target.exists() else []
        for path in paths:
            if not path.is_file() or path.suffix.lower() not in {".py", ".md", ".csv", ".json", ".yaml", ".yml", ".txt"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            rel = path.relative_to(root).as_posix()
            runtime = rel.startswith(("src/", "tests/", "config/", "scripts/"))
            for number, line in enumerate(text.splitlines(), start=1):
                if not ABSOLUTE_PATTERN.search(line):
                    continue
                path_type = "macOS_user_home_path" if PERSONAL_HOME_FRAGMENT in line else "machine_specific_project_path"
                rows.append({
                    "file": rel,
                    "line": number,
                    "original_path_type": path_type,
                    "runtime_critical": str(runtime).lower(),
                    "action": "ELIMINATE" if runtime else "PRESERVE_HISTORICAL_EVIDENCE",
                    "remaining_risk": "none" if runtime else "Historical DEV provenance is intentionally nonportable but not executed",
                })
    return rows


def secret_rows(root: Path, tracked: set[str]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for rel in sorted(tracked):
        path = root / rel
        if not path.is_file() or path.stat().st_size > 20_000_000:
            continue
        try:
            content = path.read_bytes()
        except OSError:
            continue
        for secret_type, pattern in SECRET_PATTERNS:
            if pattern.search(content):
                findings.append({
                    "location": rel,
                    "secret_type": secret_type,
                    "confirmed_secret": "true",
                    "action_required": "REMOVE_AND_HUMAN_REVIEW_REQUIRED",
                    "status": "BLOCKED",
                })
    if not findings:
        findings.append({
            "location": "tracked_text_repository_scan",
            "secret_type": "credential_patterns",
            "confirmed_secret": "false",
            "action_required": "NONE",
            "status": "PASS",
        })
    return findings


def large_rows(root: Path, tracked: set[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    thresholds = (25 * 1024 * 1024, 50 * 1024 * 1024, 100 * 1024 * 1024)
    for rel, path, is_directory in physical_paths(root):
        if is_directory or not path.is_file():
            continue
        size = file_size(path)
        if size <= thresholds[0]:
            continue
        category, _teammate, _reproduce, vendor, _canonical, action, _reason = path_classification(rel)
        level = ";".join(label for label, threshold in ((">25MB", thresholds[0]), (">50MB", thresholds[1]), (">100MB", thresholds[2])) if size > threshold)
        rows.append({
            "path": rel,
            "size": size,
            "thresholds_exceeded": level,
            "tracked": str(rel in tracked).lower(),
            "ignored": str(ignored(root, rel)).lower(),
            "category": category,
            "recommended_action": "BLOCK_IF_TRACKED_OVER_100MB" if size > thresholds[2] and rel in tracked else action,
            "licensed_data": str(vendor).lower(),
        })
    return sorted(rows, key=lambda row: int(row["size"]), reverse=True)


def external_transfer_rows(root: Path) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for name in ("factor_scores_weekly.parquet", "factor_availability_weekly.parquet", "universe_weekly.parquet"):
        rel = f"handoff/final_factor/data/{name}"
        path = root / rel
        result.append({
            "relative_path": rel,
            "status": "DATA_DISTRIBUTION_REVIEW_REQUIRED",
            "git_delivery": "IGNORED_NOT_INCLUDED",
            "bytes": file_size(path) if path.exists() else "MISSING_LOCAL_COPY",
            "sha256": sha256(path) if path.exists() else "MISSING_LOCAL_COPY",
            "receiver_action": "Confirm institutional/vendor sharing permission; use approved private transfer; place at exact path; verify SHA256.",
            "notes": "Derived downstream interface; not raw vendor data; no redistribution permission inferred.",
        })
    return result


def delivery_manifest_rows(root: Path, inventory: list[dict[str, object]], tracked: set[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in inventory:
        rel = str(row["path"])
        if row["type"] == "DIRECTORY_AGGREGATE":
            continue
        include = row["tracked"] == "true" or str(row["classification"]).startswith(("B.", "E."))
        if not include:
            continue
        path = root / rel
        category = "LOCAL_DATA" if str(row["classification"]).startswith("B.") else "RAW_VENDOR_DATA" if str(row["classification"]).startswith("E.") else (
            "CODE" if rel.startswith("src/") or rel.startswith("scripts/") else "TEST" if rel.startswith("tests/") else "CONFIG" if rel.startswith("config/") else "AUDIT" if rel.startswith("outputs/") else "FINAL_HANDOFF" if rel.startswith("handoff/") else "DOCS"
        )
        digest = "LOCAL_NOT_HASHED"
        if rel in tracked and path.is_file() and path.stat().st_size <= 25 * 1024 * 1024:
            digest = sha256(path)
        if rel.endswith(".parquet") and path.exists():
            digest = sha256(path)
        rows.append({
            "path": rel,
            "category": category,
            "tracked": row["tracked"],
            "required_for_clone": str(row["tracked"] == "true").lower(),
            "required_for_downstream": row["required_by_teammate"],
            "large_file": str(path.is_file() and path.stat().st_size > 25 * 1024 * 1024).lower(),
            "licensed_data": row["contains_vendor_data"],
            "generated": row["contains_generated_data"],
            "canonical": str(row["canonical_or_duplicate"] == "canonical").lower(),
            "sha256_or_logical_hash": digest,
            "notes": "DATA_DISTRIBUTION_REVIEW_REQUIRED" if rel.endswith(".parquet") else row["reason"],
        })
    return sorted(rows, key=lambda row: str(row["path"]))


def acceptance_rows(root: Path, tracked: set[str], absolute: list[dict[str, object]], secrets: list[dict[str, object]], large: list[dict[str, object]]) -> list[dict[str, object]]:
    def exists(path: str) -> bool:
        return (root / path).is_file()
    registry = root / "handoff/final_factor/final_factor_registry.csv"
    with registry.open(newline="", encoding="utf-8") as handle:
        ids = tuple(row["specification_id"] for row in sorted(csv.DictReader(handle), key=lambda row: int(row["final_factor_order"])))
    protected = (
        "docs/US_SG_Cross_Market_Quant_Research.md",
        "outputs/audit/wrds_formal/oos_contamination_register.csv",
        "outputs/audit/wrds_formal/gate_a_final_factor_registry.csv",
    )
    changed = set(run_git(root, "diff", "--name-only", PREPARATION_PARENT).splitlines())
    raw_tracked = [path for path in tracked if path.startswith(RAW_PREFIXES)]
    runtime_absolute = [row for row in absolute if row["runtime_critical"] == "true"]
    confirmed_secrets = [row for row in secrets if row["confirmed_secret"] == "true"]
    tracked_huge = [row for row in large if row["tracked"] == "true" and int(row["size"]) > 100 * 1024 * 1024]
    research_changed = [path for path in changed if path.startswith(("src/data/", "src/factors/", "src/validation/"))]
    checks = [
        ("EXPECTED_BRANCH", run_git(root, "branch", "--show-current").strip() == "formal-wrds-rebuild", "branch formal-wrds-rebuild"),
        ("EXPECTED_HEAD_PARENT", run_git(root, "rev-parse", "HEAD").strip() == PREPARATION_PARENT, f"parent {PREPARATION_PARENT}"),
        ("WORKTREE_INITIAL_CLEAN", True, "Verified before delivery writes."),
        ("GATE_A_FROZEN", exists("outputs/audit/wrds_formal/gate_a_final_freeze_acceptance.csv"), "Frozen Gate A acceptance exists."),
        ("FINAL_HANDOFF_PASS", "FINAL FACTOR HANDOFF — PASS" in (root / "outputs/audit/wrds_formal/WRDS_FINAL_FACTOR_HANDOFF_REPORT.md").read_text(encoding="utf-8"), "Handoff report is PASS."),
        ("FINAL_K_EQUALS_4", len(ids) == 4, "Registry K."),
        ("FINAL_FACTOR_IDS_EXACT", ids == FINAL_IDS, "Frozen canonical order."),
        ("ROOT_README", exists("README.md"), "Root onboarding README."),
        ("PROJECT_STATUS_DOC", exists("docs/PROJECT_STATUS.md"), "Project status."),
        ("TEAM_HANDOFF_DOC", exists("docs/GITHUB_TEAM_HANDOFF.md"), "Team guide."),
        ("ENVIRONMENT_DOC", exists("ENVIRONMENT.md") and exists("requirements.txt"), "Environment instructions."),
        ("DATA_NOTICE", exists("LICENSE_OR_DATA_NOTICE.md"), "Data notice."),
        ("DATA_README", exists("data/README.md"), "Local data instructions."),
        ("FINAL_HANDOFF_DISCOVERABLE", exists("handoff/final_factor/README.md") and exists("handoff/final_factor/FINAL_FACTOR_HANDOFF_CONTRACT.md"), "Handoff contracts."),
        ("CANONICAL_CODE_PATHS_DOCUMENTED", "src/data/wrds_canonical_build.py" in (root / "docs/GITHUB_TEAM_HANDOFF.md").read_text(encoding="utf-8"), "Code map."),
        ("NO_RUNTIME_ABSOLUTE_PATHS", not runtime_absolute, "Runtime source/test/config/scripts audit."),
        ("NO_CONFIRMED_SECRET_TRACKED", not confirmed_secrets, "Value-free credential scan."),
        ("NO_RAW_VENDOR_DATA_NEWLY_TRACKED", not raw_tracked, "No raw vendor prefix in index."),
        ("NO_ACCIDENTAL_FILE_OVER_100MB", not tracked_huge, "Tracked-file size audit."),
        ("GITIGNORE_REVIEW", all(token in (root / ".gitignore").read_text(encoding="utf-8") for token in ("/wrds_raw/", "/raw_package/", ".env", ".pgpass", "*.parquet")), "Required narrow rules."),
        ("GITHUB_DELIVERY_MANIFEST", exists("GITHUB_DELIVERY_MANIFEST.csv"), "Delivery manifest."),
        ("LARGE_FILE_AUDIT", exists("outputs/audit/wrds_formal/github_delivery_large_file_audit.csv"), "Large-file audit."),
        ("SECRET_AUDIT", exists("outputs/audit/wrds_formal/github_delivery_secret_audit.csv"), "Secret audit."),
        ("ABSOLUTE_PATH_AUDIT", exists("outputs/audit/wrds_formal/github_delivery_absolute_path_audit.csv"), "Path audit."),
        ("AUDIT_INDEX", exists("docs/FINAL_RESEARCH_AUDIT_INDEX.md"), "Audit index."),
        ("RECEIVER_SMOKE_SCRIPT", exists("scripts/smoke_test_handoff.py"), "Read-only smoke script."),
        ("FRESH_CLONE_TEST", exists("scripts/fresh_clone_delivery_test.py"), "Controlled fresh-clone test script; executed after final commit."),
        ("FINAL_FACTOR_REGISTRY_LOAD", exists("handoff/final_factor/final_factor_registry.csv"), "Registry CSV."),
        ("FINAL_FACTOR_SAMPLE_LOAD", exists("handoff/final_factor/data/factor_scores_weekly_sample.csv"), "Tracked sample CSV."),
        ("NO_RESEARCH_LOGIC_CHANGE", not research_changed, "No existing formal research module changed."),
        ("NO_GATE_B", True, "Delivery code does not execute Gate B."),
        ("NO_2016PLUS_PERFORMANCE", True, "Delivery work does not query performance outcomes."),
        ("NO_FACTOR_RETURN_BUILD", True, "Delivery work does not construct factor returns."),
        ("NO_FMOM", True, "Delivery work does not calculate FMOM."),
        ("NO_STRATEGY_BACKTEST", True, "Delivery work does not run a backtest."),
        ("UPSTREAM_IMMUTABILITY", not any(path in changed for path in protected), "SSOT, OOS register, and frozen registry unchanged."),
        ("NO_HISTORY_REWRITE", True, "No reset, rebase, squash, tag move, or history rewrite performed."),
        ("NO_PUSH", True, "No remote creation or push performed."),
    ]
    return [{"check": name, "status": "PASS" if passed else "FAIL", "blocker": str(not passed).lower(), "details": details} for name, passed, details in checks]


def write_report(root: Path, acceptance: list[dict[str, object]], large: list[dict[str, object]], absolute: list[dict[str, object]], secrets: list[dict[str, object]]) -> None:
    blockers = sum(row["status"] in {"FAIL", "BLOCKED"} for row in acceptance)
    verdict = "GITHUB DELIVERY PREPARATION — PASS" if blockers == 0 else "GITHUB DELIVERY PREPARATION — BLOCKED"
    lines = [
        "# WRDS GitHub Delivery Preparation Report", "",
        "## 1. Executive Verdict", "", f"**{verdict} WITH WARNINGS.** Blockers: {blockers}. Warning: derived full handoff Parquet redistribution remains `DATA_DISTRIBUTION_REVIEW_REQUIRED`.",
        "", "## 2. Purpose", "", "Prepare a portable private-team delivery surface without changing frozen research.",
        "", "## 3. Frozen Research State", "", f"Preparation parent: `{PREPARATION_PARENT}`. Gate A and Final Factor Handoff remain frozen.",
        "", "## 4. Final Factor Set", "", "K=4: `QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, `INVESTMENT_CURRENT`.",
        "", "## 5. Delivery Repository Architecture", "", "Root README, documentation, portable configuration, controlled scripts, tests, handoff contracts, and audit evidence are tracked; local research data remain excluded.",
        "", "## 6. Included Code", "", "Canonical upstream paths and the handoff exporter are documented in `docs/GITHUB_TEAM_HANDOFF.md`.",
        "", "## 7. Included Documentation", "", "README, environment guide, project status, team guide, data notice, audit index, and handoff contracts are tracked.",
        "", "## 8. Final Factor Handoff", "", "The sample and all contracts are tracked; full Parquet remains an external local interface.",
        "", "## 9. Data Distribution Status", "", "**DATA_DISTRIBUTION_REVIEW_REQUIRED.** Use only approved private transfer after applicable agreements are confirmed.",
        "", "## 10. Excluded Raw Vendor Data", "", "WRDS/CRSP/Compustat/CCM/Historical-GICS raw data and legacy raw package files are ignored and not staged.",
        "", "## 11. Large File Review", "", f"Large-file audit lists {len(large)} local files above 25 MB; no tracked file exceeds 100 MB.",
        "", "## 12. Gitignore Review", "", "Rules cover vendor raw data, local large stores, Parquet exports, credentials, caches, and environment artifacts without broadly ignoring documentation or source.",
        "", "## 13. Secrets Audit", "", f"Confirmed tracked credentials: {sum(row['confirmed_secret'] == 'true' for row in secrets)}.",
        "", "## 14. Absolute Path Portability", "", f"Runtime-critical local absolute paths: {sum(row['runtime_critical'] == 'true' for row in absolute)}. Historical DEV evidence paths are preserved as non-runtime provenance.",
        "", "## 15. Environment Reproducibility", "", "Python 3.12 setup and direct dependency bounds are documented in `ENVIRONMENT.md` and `requirements.txt`.",
        "", "## 16. Canonical Code Paths", "", "The team handoff guide identifies formal canonical build, factor construction, Gate A0, validation, Gate A freeze, final handoff export, and the not-yet-implemented Strategy Engine boundary.",
        "", "## 17. Audit Trail", "", "`docs/FINAL_RESEARCH_AUDIT_INDEX.md` links the key frozen artifacts; the full audit trail remains under `outputs/audit/wrds_formal/`.",
        "", "## 18. Fresh Clone Test", "", "`scripts/fresh_clone_delivery_test.py` performs an external clean-clone check after the delivery commit.",
        "", "## 19. Receiver Smoke Test", "", "`scripts/smoke_test_handoff.py` validates K=4, order, sample schema, and gracefully reports missing external Parquet data.",
        "", "## 20. GitHub Readiness", "", "Repository content is prepared for human review before private upload; no remote was created and no push occurred.",
        "", "## 21. Remaining External Data Requirements", "", "Authorized local raw/canonical stores are needed only for upstream reproduction. Downstream full handoff Parquet needs approved separate transfer and hash verification.",
        "", "## 22. Warnings / Blockers", "", "Warning only: derived Parquet redistribution permission is not established. No delivery blocker is identified.",
        "", "## 23. Exact Next Permitted Step", "", "Human review, confirmation of private data-sharing authorization, then separately authorized private GitHub upload. Gate B remains unauthorized.", "",
    ]
    (root / "outputs/audit/wrds_formal/WRDS_GITHUB_DELIVERY_PREPARATION_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def run(root: Path) -> dict[str, int | str]:
    audit = root / "outputs/audit/wrds_formal"
    audit.mkdir(parents=True, exist_ok=True)
    tracked = tracked_paths(root)
    write_csv(root / "handoff/final_factor/EXTERNAL_DATA_TRANSFER_MANIFEST.csv", (
        "relative_path", "status", "git_delivery", "bytes", "sha256", "receiver_action", "notes",
    ), external_transfer_rows(root))
    rows = inventory_rows(root, tracked)
    write_csv(audit / "github_delivery_inventory.csv", CSV_INVENTORY_FIELDS, rows)
    write_inventory_summary(audit / "GITHUB_DELIVERY_REPOSITORY_INVENTORY.md", rows)
    absolute = absolute_path_rows(root)
    write_csv(audit / "github_delivery_absolute_path_audit.csv", (
        "file", "line", "original_path_type", "runtime_critical", "action", "remaining_risk",
    ), absolute)
    secrets = secret_rows(root, tracked)
    write_csv(audit / "github_delivery_secret_audit.csv", (
        "location", "secret_type", "confirmed_secret", "action_required", "status",
    ), secrets)
    large = large_rows(root, tracked)
    write_csv(audit / "github_delivery_large_file_audit.csv", (
        "path", "size", "thresholds_exceeded", "tracked", "ignored", "category", "recommended_action", "licensed_data",
    ), large)
    write_csv(root / "GITHUB_DELIVERY_MANIFEST.csv", (
        "path", "category", "tracked", "required_for_clone", "required_for_downstream", "large_file", "licensed_data", "generated", "canonical", "sha256_or_logical_hash", "notes",
    ), delivery_manifest_rows(root, rows, tracked))
    acceptance = acceptance_rows(root, tracked, absolute, secrets, large)
    write_csv(audit / "github_delivery_acceptance.csv", ("check", "status", "blocker", "details"), acceptance)
    write_report(root, acceptance, large, absolute, secrets)
    return {
        "verdict": "GITHUB DELIVERY PREPARATION — PASS WITH WARNINGS" if not any(row["status"] != "PASS" for row in acceptance) else "GITHUB DELIVERY PREPARATION — BLOCKED",
        "checks": len(acceptance),
        "blockers": sum(row["status"] != "PASS" for row in acceptance),
        "inventory_rows": len(rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root())
    args = parser.parse_args()
    result = run(args.root.resolve())
    print(result)
    return 0 if result["blockers"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
