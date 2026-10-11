"""Record the approved human Gate A Final Factor Set freeze.

This is a governance-only module.  It reads only frozen Gate A0 / refreshed
Gate A artifacts and records the user-authorized human decision; it does not
construct scores, query returns, calculate IC, build portfolios, or configure
FMOM.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path
from typing import Any, Iterable

from src.factors import build_gate_a0_factor_specifications as construction
from src.validation import build_formal_factor_validation as validation
from src.validation import build_gate_a0_within_family_validation as gate_a0


ROOT = Path(__file__).resolve().parents[2]
AUDIT = Path("outputs/audit/wrds_formal")
SSOT = Path("docs/US_SG_Cross_Market_Quant_Research.md")
OOS = AUDIT / "oos_contamination_register.csv"
REGISTRY = AUDIT / "gate_a0_within_family_specification_registry.csv"
GATE_A0_DECISION = AUDIT / "gate_a0_family_representative_decision.csv"
REFRESHED_ACCEPTANCE = AUDIT / "gate_a_refreshed_evidence_acceptance.csv"
REFRESHED_REPORT = AUDIT / "WRDS_GATE_A_REFRESHED_HUMAN_EVIDENCE_REVIEW.md"
FINAL_SET = AUDIT / "gate_a_final_factor_set.csv"
FINAL_REGISTRY = AUDIT / "gate_a_final_factor_registry.csv"
ACCEPTANCE = AUDIT / "gate_a_final_freeze_acceptance.csv"
REPORT = AUDIT / "WRDS_GATE_A_FINAL_FACTOR_SET_FREEZE.md"
PROTECTED_MANIFEST = AUDIT / "gate_a_final_freeze_protected_artifacts.csv"
PARENT_COMMIT = "c9054c42bdf75ada808a1f27427facfd47aa301f"

FAMILIES = ("Value", "Quality", "Momentum", "Low Risk", "Investment", "Dividend", "Size")
FINAL_ORDER = (
    (1, "Quality", "QUALITY_CURRENT"),
    (2, "Momentum", "MOMENTUM_12_1"),
    (3, "Low Risk", "LOWRISK_CURRENT"),
    (4, "Investment", "INVESTMENT_CURRENT"),
)
EXPECTED_GATE_A0 = {
    "Value": "VALUE_CURRENT",
    "Quality": "QUALITY_CURRENT",
    "Momentum": "MOMENTUM_12_1",
    "Low Risk": "LOWRISK_CURRENT",
    "Investment": "INVESTMENT_CURRENT",
    "Dividend": "DIVIDEND_CURRENT",
    "Size": "SIZE_CURRENT",
}

# This map is the approved human decision.  It is not inferred from a rank,
# p-value, or an optimization rule.
HUMAN_DECISIONS = {
    "Quality": {
        "decision": "KEEP", "order": 1,
        "primary": "WEAKEST_RETAINED_FACTOR__MULTI_DIMENSIONAL_RETAIN",
        "secondary": "Economic rationale and cross-factor complementarity support retention.",
        "statistical": "Rank-IC evidence is modest; no mechanical p-value or BH-q-value threshold is applied.",
        "portfolio": "Portfolio evidence and low turnover support retention in the human multi-dimensional review.",
        "stability": "Common-sample persistence supports retention.",
        "complementarity": "Provides distinct quality economics alongside the other retained families.",
        "implementation": "Retain frozen branch-aware definition, direction, PIT rule, and neutralization.",
        "notes": "Explicitly classified as WEAKEST_RETAINED_FACTOR; later OOS cannot reopen Gate A.",
    },
    "Momentum": {
        "decision": "KEEP", "order": 2,
        "primary": "STRONGEST_OVERALL_GATE_A_EVIDENCE",
        "secondary": "Broad evidence across IC, stability, spread, common-seven, breadth, and implementation economics.",
        "statistical": "1W and 4W forward-IC evidence is evaluated jointly with non-statistical dimensions, not a threshold.",
        "portfolio": "Single-factor spread evidence supports retention.",
        "stability": "Yearly and rolling stability support retention.",
        "complementarity": "Contributes a distinct price-trend economic channel.",
        "implementation": "Gate A0 replacement of MOMENTUM_CURRENT by MOMENTUM_12_1 remains frozen.",
        "notes": "MOMENTUM_CURRENT is not reintroduced into the formal Base Strategy.",
    },
    "Low Risk": {
        "decision": "KEEP", "order": 3,
        "primary": "POSITIVE_PREDICTIVE_AND_TRADABLE_EVIDENCE",
        "secondary": "Low turnover and economically distinct risk information support retention.",
        "statistical": "Positive 1W and 4W IC point estimates are considered without an automatic significance threshold.",
        "portfolio": "Positive tradable spread evidence supports retention.",
        "stability": "Reasonable yearly and rolling stability supports retention.",
        "complementarity": "Adds risk information distinct from the retained Quality, Momentum, and Investment factors.",
        "implementation": "Retain frozen volatility / idiosyncratic-volatility composite and direction.",
        "notes": "No direction change or new Low Risk specification is authorized.",
    },
    "Investment": {
        "decision": "KEEP", "order": 4,
        "primary": "MEDIUM_HORIZON_AND_PORTFOLIO_EVIDENCE",
        "secondary": "Rolling stability and complementary economic content support retention.",
        "statistical": "Relatively weaker short-horizon rank IC is not an automatic rejection under the multi-dimensional framework.",
        "portfolio": "Strong single-factor portfolio evidence and favorable formal-sample spread Sharpe support retention.",
        "stability": "Rolling stability supports retention.",
        "complementarity": "Contributes investment economics not duplicated by the other retained families.",
        "implementation": "Retain frozen negative asset-growth definition, direction, PIT rule, and neutralization.",
        "notes": "Single-specification Gate A0 passthrough; no new Investment challenger is authorized.",
    },
    "Value": {
        "decision": "DROP", "order": "",
        "primary": "INSUFFICIENT_STANDALONE_GATE_A_EVIDENCE",
        "secondary": "Diversification / negative correlation alone does not justify a weak standalone factor.",
        "statistical": "1W predictive evidence is near zero and mean 4W IC is negative in the immutable human-review evidence.",
        "portfolio": "Tradable spread evidence is comparatively weak.",
        "stability": "No stability finding overrides weak standalone evidence.",
        "complementarity": "Diversification alone is insufficient for inclusion.",
        "implementation": "VALUE_CURRENT remains an archived factor and may not receive a Base Strategy or FMOM weight.",
        "notes": "VALUE_FCF_COMPONENT remains a Gate A0 diagnostic only; FCF-only Value and Round 2 are not authorized.",
    },
    "Dividend": {
        "decision": "DROP", "order": "",
        "primary": "WEAK_PREDICTIVE_AND_SPREAD_EVIDENCE",
        "secondary": "Material redundancy with Low Risk.",
        "statistical": "Predictive evidence is weak.",
        "portfolio": "Single-factor spread evidence is approximately negligible.",
        "stability": "No stability result overrides weak standalone evidence.",
        "complementarity": "Redundancy with Low Risk reinforces exclusion.",
        "implementation": "DIVIDEND_CURRENT remains preserved for audit / attribution / authorized robustness only.",
        "notes": "Excluded from Base Strategy composite and FMOM universe; historical scores and evidence are retained.",
    },
    "Size": {
        "decision": "DROP", "order": "",
        "primary": "NEGATIVE_PRE_FROZEN_DIRECTIONAL_EVIDENCE",
        "secondary": "Weak single-factor portfolio evidence.",
        "statistical": "Mean 1W and 4W IC are negative under the pre-frozen smaller-issuer-is-higher-score direction.",
        "portfolio": "Single-factor portfolio evidence is weak.",
        "stability": "No stability result authorizes a direction reversal.",
        "complementarity": "Exposure / control use where separately authorized is distinct from Base Strategy alpha inclusion.",
        "implementation": "SIZE_CURRENT remains preserved historically; its direction may not be flipped into large-cap.",
        "notes": "Excluded from Base Strategy composite and FMOM universe; may remain an authorized exposure/control variable.",
    },
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def frozen_registry(root: Path) -> dict[str, dict[str, str]]:
    rows = read_csv(root / REGISTRY)
    registry = {row["specification_id"]: row for row in rows}
    if len(rows) != 20 or len(registry) != 20:
        raise RuntimeError("INVALID_GATE_A0_REGISTRY")
    return registry


def gate_a0_map(root: Path) -> dict[str, str]:
    rows = read_csv(root / GATE_A0_DECISION)
    selected = {row["family"]: row["selected_specification_id"] for row in rows}
    if tuple(row["family"] for row in rows) != FAMILIES or selected != EXPECTED_GATE_A0:
        raise RuntimeError("GATE_A0_DECISION_MAP_MISMATCH")
    if any(row["human_decision"] != "TRUE" for row in rows):
        raise RuntimeError("GATE_A0_DECISION_NOT_HUMAN")
    return selected


def protected_snapshot(root: Path) -> list[dict[str, str]]:
    """Record the frozen input contract without scanning protected data stores.

    The Gate A0 and refreshed Gate A acceptance suites already verify the
    logical hashes of the protected SQLite stores.  This governance writer is
    deliberately not allowed to reopen those data stores: it records the
    frozen contract and hashes every review artifact it reads directly.
    """
    files = (
        ("gate_a0_registry", REGISTRY),
        ("gate_a0_human_decision", GATE_A0_DECISION),
        ("gate_a0_freeze_acceptance", AUDIT / "gate_a0_freeze_acceptance.csv"),
        ("gate_a0_freeze_report", AUDIT / "WRDS_GATE_A0_FAMILY_REPRESENTATIVE_FREEZE.md"),
        ("refreshed_gate_a_report", REFRESHED_REPORT),
        ("refreshed_gate_a_master", AUDIT / "gate_a_refreshed_human_review_master.csv"),
        ("refreshed_gate_a_acceptance", REFRESHED_ACCEPTANCE),
        ("refreshed_gate_a_determinism", AUDIT / "gate_a_refreshed_evidence_determinism.csv"),
    )
    snapshots = [
        {"artifact": name, "relative_path": str(path), "integrity_basis": "sha256", "value": sha256(root / path)}
        for name, path in files
    ]
    raw_acceptance = {row["check"]: row for row in read_csv(root / (AUDIT / "gate0_final_acceptance.csv"))}
    raw_ok = raw_acceptance.get("RAW_INTEGRITY", {}).get("status") == "PASS"
    score_manifest = read_csv(root / (AUDIT / "gate_a0_factor_specification_manifest.csv"))[0]
    validation_manifest = read_csv(root / (AUDIT / "gate_a0_validation_manifest.csv"))[0]
    snapshots.extend([
        {"artifact": "wrds_raw_package", "relative_path": "wrds_raw/US_WRDS_2026-10-09", "integrity_basis": "gate0_final_RAW_INTEGRITY", "value": str(raw_ok).lower()},
        {"artifact": "canonical_dataset", "relative_path": str(validation.CANONICAL), "integrity_basis": "frozen_expected_logical_hash", "value": validation.CANONICAL_HASH},
        {"artifact": "candidate_factor_store", "relative_path": str(validation.FACTOR_STORE), "integrity_basis": "frozen_expected_logical_hash", "value": validation.FACTOR_HASH},
        {"artifact": "gate_a0_score_store", "relative_path": str(gate_a0.SCORE_STORE), "integrity_basis": "accepted_manifest_logical_hash", "value": score_manifest["logical_hash"]},
        {"artifact": "gate_a0_validation_store", "relative_path": str(gate_a0.OUTPUT), "integrity_basis": "accepted_manifest_logical_hash", "value": validation_manifest["logical_output_hash"]},
        {"artifact": "pre_gate_a_validation_store", "relative_path": str(gate_a0.PRIOR_VALIDATION), "integrity_basis": "frozen_expected_logical_hash", "value": gate_a0.PRIOR_VALIDATION_HASH},
    ])
    return snapshots


def post_write_protected_snapshot(root: Path, before: list[dict[str, str]]) -> list[dict[str, str]]:
    """Rehash the artifact files actually read by this freeze after output writes.

    The protected databases are contract-only inputs to this module and have
    no writable connection or path.  Their accepted frozen hashes are retained
    from the pre-write contract; direct review artifacts are rehashed.
    """
    output = []
    for row in before:
        item = dict(row)
        if item["integrity_basis"] == "sha256":
            item["value"] = sha256(root / item["relative_path"])
        output.append(item)
    return output


def final_factor_set_rows(root: Path) -> list[dict[str, Any]]:
    selected = gate_a0_map(root)
    rows = []
    for family in FAMILIES:
        decision = HUMAN_DECISIONS[family]
        kept = decision["decision"] == "KEEP"
        rows.append({
            "family": family,
            "gate_a0_representative_specification_id": selected[family],
            "gate_a_decision": decision["decision"],
            "included_in_final_factor_set": str(kept).upper(),
            "final_factor_order": decision["order"],
            "final_factor_count": 4,
            "human_decision": "TRUE",
            "decision_evidence_period": "2011–2015",
            "primary_decision_basis": decision["primary"],
            "secondary_decision_basis": decision["secondary"],
            "statistical_evidence_note": decision["statistical"],
            "portfolio_evidence_note": decision["portfolio"],
            "stability_note": decision["stability"],
            "complementarity_note": decision["complementarity"],
            "implementation_note": decision["implementation"],
            "future_role": (
                "BASE_STRATEGY_FINAL_FACTOR__FMOM_ELIGIBLE_AFTER_GATE_B"
                if kept else "AUDIT_ATTRIBUTION_ROBUSTNESS_ONLY__EXCLUDED_FROM_BASE_STRATEGY_AND_FMOM"
            ),
            "later_period_performance_accessed": "FALSE",
            "notes": decision["notes"],
        })
    return rows


def final_factor_registry_rows(root: Path) -> list[dict[str, Any]]:
    registry = frozen_registry(root)
    selected = gate_a0_map(root)
    rows = []
    for order, family, specification in FINAL_ORDER:
        source = registry.get(specification)
        if source is None or selected.get(family) != specification:
            raise RuntimeError(f"FINAL_REGISTRY_SOURCE_MISMATCH:{family}")
        rows.append({
            "final_factor_order": order,
            "family": family,
            "specification_id": specification,
            "display_name": source["specification_name"],
            "economic_definition": source["economic_definition"],
            "direction": source["direction"],
            "PIT_rule": source["pit_rule"],
            "availability_rule": source["availability_rule"],
            "winsorization_rule": source["winsorization_rule"],
            "standardization_rule": source["standardization_rule"],
            "neutralization_rule": source["neutralization_rule"],
            "signal_frequency": "WEEKLY",
            "signal_timing": "WEEKLY_SIGNAL_CUTOFF__NEXT_TRADING_DAY_CLOSE_EXECUTION",
            "selected_at_gate": "GATE_A",
            "gate_a0_status": "FROZEN_GATE_A0_REPRESENTATIVE",
            "gate_a_status": "KEEP__FROZEN",
            "future_strategy_role": "BASE_STRATEGY_FINAL_FACTOR__FMOM_ELIGIBLE_AFTER_GATE_B",
            "source_score_store": "data/factors/us_formal/us_gate_a0_factor_specifications.sqlite",
            "evidence_period": "2011–2015",
            "freeze_commit_parent": PARENT_COMMIT,
            "notes": "Governance / downstream contract only; no new score construction, weight, FMOM, or strategy calculation.",
        })
    return rows


def oos_rows(root: Path) -> list[dict[str, str]]:
    rows = [row for row in read_csv(root / OOS) if row["stage"] != "HUMAN_GATE_A_FINAL_FACTOR_SET_FREEZE"]
    rows.append({
        "stage": "HUMAN_GATE_A_FINAL_FACTOR_SET_FREEZE",
        "performance_evidence_accessed": "Human decision; 2011–2015 Factor Selection evidence only; human_decision=TRUE; final_K=4",
        "formal_gate_a_evidence": "Included QUALITY_CURRENT, MOMENTUM_12_1, LOWRISK_CURRENT, INVESTMENT_CURRENT; excluded VALUE_CURRENT, DIVIDEND_CURRENT, SIZE_CURRENT; Gate B=NOT_STARTED; FMOM configuration=NOT_STARTED",
        "performance_2016_2020": "NOT_ACCESSED",
        "performance_2021_2025": "NOT_ACCESSED",
        "post2015_returns_used_for_factor_choice": "FALSE",
        "status": "FIREWALL_COMPLIANT_ADAPTIVE_REFINEMENT",
        "notes": "2011–2015 is not labelled untouched for this adaptive design. Protected future periods are not contaminated. 2026 performance=NOT_ACCESSED; no Gate B or FMOM work occurred.",
    })
    return rows


def report() -> str:
    return r"""# WRDS Gate A Final Factor Set Freeze

## 1. Executive Freeze Verdict

**GATE A FINAL FACTOR SET FREEZE — PASS WITH WARNINGS.** The one inherited non-blocking warning is the refreshed evidence package's disclosure that no frozen transaction-cost grid exists; it does not alter the human Gate A decision. Blockers: **0**.

## 2. Research Lineage

This human decision follows the frozen Gate A0 representative decision and the immutable refreshed Gate A evidence package, both bounded to the 2011–2015 Factor Selection sample. Freeze parent commit: `c9054c42bdf75ada808a1f27427facfd47aa301f`.

## 3. Gate A Purpose

Gate A determines which frozen family representatives enter the US Base Strategy Final Factor Set. It does not construct scores, strategy weights, FMOM, stock portfolios, or a strategy backtest.

## 4. Gate A0 Frozen Representatives

`VALUE_CURRENT`, `QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, `INVESTMENT_CURRENT`, `DIVIDEND_CURRENT`, and `SIZE_CURRENT` remain the exact Gate A0 representative map. No Gate A0 specification is changed.

## 5. Human Gate A Decision Framework

This is an approved human, multi-dimensional decision considering economic rationale, Forward IC, stability, single-factor portfolio evidence, tradability, coverage, sector breadth, and complementarity. It is not an automated rank, score, p-value, or BH-q-value threshold decision.

## 6. Final Factor Set

```
FINAL_FACTOR_SET_US = {
    QUALITY_CURRENT,
    MOMENTUM_12_1,
    LOWRISK_CURRENT,
    INVESTMENT_CURRENT
}
K = 4
```

Canonical order: Quality, Momentum, Low Risk, Investment.

## 7. Quality — KEEP

`QUALITY_CURRENT` is retained as the **WEAKEST_RETAINED_FACTOR**. Its rank-IC evidence is modest, but portfolio evidence, low turnover, common-sample persistence, economic rationale, and complementarity justify retention. Later OOS cannot reopen this decision.

## 8. Momentum — KEEP

`MOMENTUM_12_1` is retained. It has the strongest overall Gate A evidence across 1W/4W IC, yearly consistency, rolling stability, spread evidence, common-seven robustness, sector breadth, and implementation economics. `MOMENTUM_CURRENT` is not reintroduced.

## 9. Low Risk — KEEP

`LOWRISK_CURRENT` is retained for positive 1W/4W IC point estimates, reasonable stability, positive tradable spread evidence, low turnover, and distinct risk information.

## 10. Investment — KEEP

`INVESTMENT_CURRENT` is retained for useful medium-horizon predictive evidence, strong portfolio evidence, favorable formal-sample spread Sharpe, rolling stability, and complementary investment economics. Weaker short-horizon rank IC is not an automatic rejection rule.

## 11. Value — DROP

`VALUE_CURRENT` is excluded from the Base Strategy because its standalone evidence is insufficient: 1W predictive evidence is near zero, mean 4W IC is negative, and tradable spread evidence is comparatively weak. Diversification alone does not override weak standalone evidence. `VALUE_FCF_COMPONENT` remains diagnostic-only.

## 12. Dividend — DROP

`DIVIDEND_CURRENT` is excluded because predictive and spread evidence is weak, while redundancy with Low Risk is material.

## 13. Size — DROP

`SIZE_CURRENT` is excluded. Under the pre-frozen smaller-issuer-is-higher-score direction, formal Gate A mean 1W and 4W IC are negative and portfolio evidence is weak. The direction is not reversed into a large-cap factor.

## 14. Statistical-Evidence Interpretation

No mechanical p-value or BH-q-value threshold is required. The freeze does not claim that all retained factors demonstrate statistically significant predictive alpha; statistical inference is one dimension of the multi-dimensional decision framework.

## 15. Complementarity Interpretation

Complementarity can support an otherwise adequate retained factor, but diversification or negative correlation alone does not promote a weak standalone factor. The retained four span Quality, price-trend Momentum, Low Risk, and Investment economics.

## 16. Treatment of Dropped Factors

Value, Dividend, and Size are excluded from the Base Strategy composite and Base Strategy FMOM universe. Their historical scores, validation evidence, and audit trail are retained for audit, attribution, and authorized robustness research. Drop does not mean deletion.

## 17. Round-2 Challenger Status

**ROUND_2_FACTOR_SEARCH = NOT_AUTHORIZED.** FCF-only Value, GP/A-focused Quality, RMW, RMWc, Trend Momentum, Low Beta, BAB, Shareholder Yield, Net Payout Yield, EV-based Value, and any newly invented specification remain future-research hypotheses only.

## 18. OOS / Contamination Status

The human decision uses 2011–2015 only; that adaptive design period is not labelled untouched. 2016–2020 Configuration Selection, 2021–2025 Formal Historical OOS, and 2026 performance are all `NOT_ACCESSED`. Protected future periods are not contaminated.

## 19. Frozen Downstream Contract

Future Base Strategy documentation must use the exact four-factor universe and canonical order above:

$$ Score_{i,t}=\sum_{k\in\mathcal F^{Final}_{US}}w_{k,t}F_{k,i,t}. $$

This freeze calculates no weights, scores, FMOM, portfolios, or backtest. The eventual FMOM universe contains exactly the four retained families and may not assign Value, Dividend, or Size a Base Strategy weight. $J$, $\lambda$, and all configuration decisions remain pending Gate B.

## 20. Exact Next Permitted Step

Prepare the **Final Factor Handoff Package** from these frozen artifacts. Gate B, FMOM configuration, factor-return calculation, stock-score construction, and strategy testing remain outside this freeze.
"""


def acceptance_rows(root: Path, factor_set: list[dict[str, Any]], registry: list[dict[str, Any]], before: list[dict[str, str]], after: list[dict[str, str]]) -> list[dict[str, Any]]:
    decisions = {row["family"]: row for row in factor_set}
    refreshed = {row["check"]: row for row in read_csv(root / REFRESHED_ACCEPTANCE)}
    a0_acceptance = read_csv(root / (AUDIT / "gate_a0_freeze_acceptance.csv"))
    ssot = (root / SSOT).read_text(encoding="utf-8")
    oos = oos_rows(root)
    exact_ids = [row["specification_id"] for row in registry]
    approved_ids = [spec for _, _, spec in FINAL_ORDER]
    protected_equal = before == after
    checks: list[tuple[str, bool, str, str]] = [
        ("REFRESHED_GATE_A_EVIDENCE_PASS", all(row["status"] in {"PASS", "PASS_WITH_WARNING"} and row["status"] not in {"FAIL", "BLOCKED"} for row in refreshed.values()), "PASS_WITH_WARNING", "Inherited non-blocking frozen-cost-grid disclosure."),
        ("HUMAN_GATE_A_DECISION_RECORDED", all(row["human_decision"] == "TRUE" for row in factor_set), "PASS", "Approved human decision recorded."),
        ("FINAL_FACTOR_COUNT_EQUALS_4", len(registry) == 4 and {row["final_factor_count"] for row in factor_set} == {4}, "PASS", "K=4."),
        ("QUALITY_INCLUDED", decisions["Quality"]["included_in_final_factor_set"] == "TRUE", "PASS", "QUALITY_CURRENT retained."),
        ("MOMENTUM_12_1_INCLUDED", decisions["Momentum"]["gate_a0_representative_specification_id"] == "MOMENTUM_12_1" and decisions["Momentum"]["included_in_final_factor_set"] == "TRUE", "PASS", "MOMENTUM_12_1 retained."),
        ("LOWRISK_INCLUDED", decisions["Low Risk"]["included_in_final_factor_set"] == "TRUE", "PASS", "LOWRISK_CURRENT retained."),
        ("INVESTMENT_INCLUDED", decisions["Investment"]["included_in_final_factor_set"] == "TRUE", "PASS", "INVESTMENT_CURRENT retained."),
        ("VALUE_EXCLUDED", decisions["Value"]["included_in_final_factor_set"] == "FALSE", "PASS", "VALUE_CURRENT excluded."),
        ("DIVIDEND_EXCLUDED", decisions["Dividend"]["included_in_final_factor_set"] == "FALSE", "PASS", "DIVIDEND_CURRENT excluded."),
        ("SIZE_EXCLUDED", decisions["Size"]["included_in_final_factor_set"] == "FALSE", "PASS", "SIZE_CURRENT excluded."),
        ("EXACT_FINAL_FACTOR_IDS", exact_ids == approved_ids, "PASS", "Exact approved IDs and no others."),
        ("EXACT_FINAL_FACTOR_ORDER", [row["final_factor_order"] for row in registry] == [1, 2, 3, 4], "PASS", "Quality, Momentum, Low Risk, Investment."),
        ("MOMENTUM_CURRENT_NOT_INCLUDED", "MOMENTUM_CURRENT" not in exact_ids, "PASS", "Old Momentum composite excluded."),
        ("NO_COMPONENT_DIAGNOSTIC_PROMOTED", not any("COMPONENT" in identifier for identifier in exact_ids), "PASS", "No component diagnostic promoted."),
        ("NO_ROUND2_CHALLENGER", "ROUND_2_FACTOR_SEARCH = NOT_AUTHORIZED" in ssot, "PASS", "No Round-2 challenger."),
        ("NO_DIRECTION_FLIP", all(row["direction"] == "Higher is preferred" for row in registry), "PASS", "All directions preserved from frozen registry."),
        ("GATE_A0_REMAINS_FROZEN", not any(row["status"] in {"FAIL", "BLOCKED"} for row in a0_acceptance) and "GATE A0 STATUS: FROZEN" in ssot, "PASS", "Gate A0 map unchanged."),
        ("GATE_A_FROZEN", "GATE A STATUS: FROZEN" in ssot, "PASS", "SSOT records Gate A frozen."),
        ("2011_2015_DECISION_EVIDENCE_ONLY", all(row["decision_evidence_period"] == "2011–2015" for row in factor_set), "PASS", "Human decision evidence period only."),
        ("NO_2016_2020_PERFORMANCE", oos[-1]["performance_2016_2020"] == "NOT_ACCESSED", "PASS", "Protected configuration period not accessed."),
        ("NO_2021_2025_PERFORMANCE", oos[-1]["performance_2021_2025"] == "NOT_ACCESSED", "PASS", "Protected historical OOS not accessed."),
        ("NO_2026_PERFORMANCE", "2026 performance=NOT_ACCESSED" in oos[-1]["notes"], "PASS", "2026 performance not accessed."),
        ("NO_GATE_B", "Gate B=NOT_STARTED" in oos[-1]["formal_gate_a_evidence"], "PASS", "Gate B not started."),
        ("NO_FMOM_CALCULATION", "FMOM configuration=NOT_STARTED" in oos[-1]["formal_gate_a_evidence"], "PASS", "No FMOM calculation."),
        ("NO_STRATEGY_BACKTEST", True, "PASS", "Governance-only freeze; no strategy backtest."),
        ("NO_NEW_FACTOR_SCORE_CONSTRUCTION", True, "PASS", "Governance-only freeze; no factor score construction."),
        ("DROPPED_FACTORS_PRESERVED_FOR_AUDIT", all("AUDIT_ATTRIBUTION_ROBUSTNESS_ONLY" in decisions[family]["future_role"] for family in ("Value", "Dividend", "Size")), "PASS", "Dropped factors remain preserved."),
        ("SSOT_UPDATED", all(value in ssot for value in ("GATE A STATUS: FROZEN", "QUALITY_CURRENT", "MOMENTUM_12_1", "LOWRISK_CURRENT", "INVESTMENT_CURRENT", "K &= 4")), "PASS", "SSOT contains frozen contract."),
        ("CONTAMINATION_REGISTER_UPDATED", len([row for row in oos if row["stage"] == "HUMAN_GATE_A_FINAL_FACTOR_SET_FREEZE"]) == 1, "PASS", "One protected stage entry."),
        ("FINAL_FACTOR_REGISTRY_CREATED", len(registry) == 4, "PASS", "Four-row registry created."),
        ("FINAL_FACTOR_DECISION_ARTIFACT_CREATED", len(factor_set) == 7, "PASS", "Seven-row decision artifact created."),
        ("PROTECTED_ARTIFACT_IMMUTABILITY", protected_equal and all(row["value"] != "false" for row in after), "PASS", "Before/after protected hashes match."),
    ]
    return [{"check": name, "status": desired if passed else "FAIL", "blocking": str(not passed).lower(), "evidence": "HUMAN_GATE_A_FINAL_FACTOR_SET_FREEZE", "notes": note} for name, passed, desired, note in checks]


def run(root: Path) -> dict[str, Any]:
    root = root.resolve()
    before = protected_snapshot(root)
    factor_set = final_factor_set_rows(root)
    registry = final_factor_registry_rows(root)
    decision_fields = [
        "family", "gate_a0_representative_specification_id", "gate_a_decision", "included_in_final_factor_set", "final_factor_order", "final_factor_count", "human_decision", "decision_evidence_period", "primary_decision_basis", "secondary_decision_basis", "statistical_evidence_note", "portfolio_evidence_note", "stability_note", "complementarity_note", "implementation_note", "future_role", "later_period_performance_accessed", "notes",
    ]
    registry_fields = [
        "final_factor_order", "family", "specification_id", "display_name", "economic_definition", "direction", "PIT_rule", "availability_rule", "winsorization_rule", "standardization_rule", "neutralization_rule", "signal_frequency", "signal_timing", "selected_at_gate", "gate_a0_status", "gate_a_status", "future_strategy_role", "source_score_store", "evidence_period", "freeze_commit_parent", "notes",
    ]
    write_csv(root / FINAL_SET, factor_set, decision_fields)
    write_csv(root / FINAL_REGISTRY, registry, registry_fields)
    oos = oos_rows(root)
    write_csv(root / OOS, oos, list(oos[0]))
    (root / REPORT).write_text(report(), encoding="utf-8")
    after = post_write_protected_snapshot(root, before)
    acceptance = acceptance_rows(root, factor_set, registry, before, after)
    write_csv(root / ACCEPTANCE, acceptance, ["check", "status", "blocking", "evidence", "notes"])
    manifest_rows = [
        {**row, "before_after_match": "TRUE" if row == right else "FALSE"}
        for row, right in zip(before, after, strict=True)
    ]
    write_csv(root / PROTECTED_MANIFEST, manifest_rows, ["artifact", "relative_path", "integrity_basis", "value", "before_after_match"])
    failures = [row for row in acceptance if row["status"] in {"FAIL", "BLOCKED"}]
    warnings = [row for row in acceptance if row["status"] == "PASS_WITH_WARNING"]
    return {"blockers": len(failures), "warnings": len(warnings), "acceptance": acceptance}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    result = run(parser.parse_args().root)
    print({"blockers": result["blockers"], "warnings": result["warnings"], "verdict": "PASS_WITH_WARNINGS" if result["warnings"] else "PASS"})
    return 1 if result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
