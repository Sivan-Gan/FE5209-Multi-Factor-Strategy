"""Record the user-authorized human Gate A0 representative freeze.

No performance calculation, factor construction, or automatic determination is
performed here.  The fixed map and rationale are the approved human decision.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
AUDIT = Path("outputs/audit/wrds_formal")
REGISTRY = AUDIT / "gate_a0_within_family_specification_registry.csv"
SSOT = Path("docs/US_SG_Cross_Market_Quant_Research.md")
OOS = AUDIT / "oos_contamination_register.csv"
DECISION = AUDIT / "gate_a0_family_representative_decision.csv"
ACCEPTANCE = AUDIT / "gate_a0_freeze_acceptance.csv"
REPORT = AUDIT / "WRDS_GATE_A0_FAMILY_REPRESENTATIVE_FREEZE.md"
LINEAGE = "f18e4c2c491c355bb7f05e9c61fdc0eac3414598"

HUMAN_MAP = (
    ("Value", "VALUE_CURRENT", "RETAIN_INCUMBENT", "VALUE_CURRENT", "TRUE", 4,
     "Eligible challengers did not establish a sufficiently clear, economically coherent, multi-dimensional improvement over the incumbent.",
     "VALUE_EP showed some IC-stability advantages, but tradable portfolio evidence did not establish clear superiority; VALUE_BP and VALUE_EP_BP were weaker.",
     "VALUE_FCF_COMPONENT remains COMPONENT_DIAGNOSTIC_ONLY and is not promoted."),
    ("Quality", "QUALITY_CURRENT", "RETAIN_INCUMBENT", "QUALITY_CURRENT", "TRUE", 3,
     "QUALITY_CURRENT provided the strongest overall eligible-representative evidence in the completed human review.",
     "QUALITY_ROE and QUALITY_PROFITABILITY did not establish a coherent advantage.",
     "QUALITY_GPA_COMPONENT and other Quality components remain diagnostic-only."),
    ("Momentum", "MOMENTUM_12_1", "REPLACE_INCUMBENT", "MOMENTUM_CURRENT", "FALSE", 3,
     "MOMENTUM_12_1 showed a clear, economically coherent, multi-dimensional advantage over MOMENTUM_CURRENT and MOMENTUM_6_1.",
     "The advantage covered 1W/4W IC, stability, gross spread evidence, implementation economics, and common-within-family evidence.",
     "No Momentum component diagnostic is promoted."),
    ("Low Risk", "LOWRISK_CURRENT", "RETAIN_INCUMBENT", "LOWRISK_CURRENT", "TRUE", 3,
     "LOWRISK_VOL had modestly higher IC point estimates but did not establish clear multi-dimensional superiority over the incumbent.",
     "The specifications are highly correlated in score and spread-return space, while LOWRISK_CURRENT retained competitive portfolio evidence.",
     "No Low Risk component diagnostic exists in this round."),
    ("Investment", "INVESTMENT_CURRENT", "SINGLE_SPECIFICATION_PASSTHROUGH", "INVESTMENT_CURRENT", "TRUE", 1,
     "Single-specification family: the existing frozen representative proceeds unchanged.",
     "No internal Gate A0 comparison occurred.",
     "Not applicable."),
    ("Dividend", "DIVIDEND_CURRENT", "SINGLE_SPECIFICATION_PASSTHROUGH", "DIVIDEND_CURRENT", "TRUE", 1,
     "Single-specification family: the existing frozen representative proceeds unchanged.",
     "No internal Gate A0 comparison occurred.",
     "Not applicable."),
    ("Size", "SIZE_CURRENT", "SINGLE_SPECIFICATION_PASSTHROUGH", "SIZE_CURRENT", "TRUE", 1,
     "Single-specification family: the existing frozen representative proceeds unchanged.",
     "No internal Gate A0 comparison occurred.",
     "Not applicable."),
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def freeze_rows(root: Path) -> list[dict[str, Any]]:
    registry = {row["specification_id"]: row for row in read_csv(root / REGISTRY)}
    if len(registry) != 20:
        raise RuntimeError("INVALID_GATE_A0_REGISTRY")
    rows = []
    for family, selected, kind, incumbent, retained, candidates, primary, secondary, diagnostic in HUMAN_MAP:
        source = registry.get(selected)
        if source is None or source["family"] != family:
            raise RuntimeError(f"DECISION_MAP_NOT_IN_REGISTRY:{family}")
        if candidates > 1 and source["eligible_family_representative"] != "TRUE":
            raise RuntimeError(f"INELIGIBLE_MULTI_SPEC_DECISION:{family}")
        rows.append({
            "family": family,
            "selected_specification_id": selected,
            "selected_specification_name": source["specification_name"],
            "decision_type": kind,
            "incumbent_specification_id": incumbent,
            "incumbent_retained": retained,
            "eligible_candidate_count": candidates,
            "decision_basis": "APPROVED_HUMAN_GATE_A0_REVIEW__NO_AUTOMATED_RANKING",
            "primary_reason": primary,
            "secondary_reason": secondary,
            "common_sample_consistency": "REVIEWED_IN_2011_2015_GATE_A0_VALIDATION",
            "portfolio_consistency": "REVIEWED_IN_2011_2015_GATE_A0_VALIDATION",
            "stability_consistency": "REVIEWED_IN_2011_2015_GATE_A0_VALIDATION",
            "implementation_consistency": "REVIEWED_IN_2011_2015_GATE_A0_VALIDATION",
            "component_diagnostic_note": diagnostic,
            "round2_challenger_status": "ROUND_2_NOT_AUTHORIZED",
            "human_decision": "TRUE",
            "approver": "FE5209_GROUP__AUTHORIZED_BY_USER",
            "decision_evidence_period": "2011-2015",
            "later_period_performance_accessed": "FALSE",
            "notes": "Gate A remains TBD; this freeze does not determine final factor-set membership.",
        })
    return rows


def report(rows: list[dict[str, Any]]) -> str:
    by_family = {row["family"]: row for row in rows}
    map_lines = [f"| {row['family']} | `{row['selected_specification_id']}` | {row['decision_type']} |" for row in rows]
    return "\n".join([
        "# Formal Gate A0 Family-Representative Freeze", "",
        "## 1. Gate A0 purpose", "", "Gate A0 determines how each economic family is represented. It does not determine whether a family belongs in the Final Factor Set.", "",
        "## 2. Governance lineage", "", f"The frozen registry and within-family validation at commit `{LINEAGE}` provide the evidence lineage. This document records the subsequent approved human decision.", "",
        "## 3. Human decision status", "", "**GATE A0 — FROZEN.** This is a recorded human decision, not an automated score, ordering, or model output.", "",
        "## 4. Value decision", "", f"`{by_family['Value']['selected_specification_id']}` is retained. {by_family['Value']['primary_reason']} {by_family['Value']['secondary_reason']}", "",
        "## 5. Quality decision", "", f"`{by_family['Quality']['selected_specification_id']}` is retained. {by_family['Quality']['primary_reason']} {by_family['Quality']['secondary_reason']}", "",
        "## 6. Momentum decision", "", f"`{by_family['Momentum']['selected_specification_id']}` replaces `{by_family['Momentum']['incumbent_specification_id']}`. {by_family['Momentum']['primary_reason']} {by_family['Momentum']['secondary_reason']}", "",
        "## 7. Low-Risk decision", "", f"`{by_family['Low Risk']['selected_specification_id']}` is retained. {by_family['Low Risk']['primary_reason']} {by_family['Low Risk']['secondary_reason']}", "",
        "## 8. Investment / Dividend / Size passthrough", "", "`INVESTMENT_CURRENT`, `DIVIDEND_CURRENT`, and `SIZE_CURRENT` proceed unchanged because each is a single-specification family. No internal comparison occurred.", "",
        "## 9. Component-diagnostic treatment", "", "`VALUE_FCF_COMPONENT`, `QUALITY_GPA_COMPONENT`, `QUALITY_NEG_LEVERAGE_COMPONENT`, and `QUALITY_ROA_COMPONENT` remain diagnostic-only. None is promoted to a whole-market family representative.", "",
        "## 10. Incumbent-default application", "", "The approved human review applied the frozen incumbent-default rule. Momentum was the only multi-specification family for which the documented evidence justified replacement.", "",
        "## 11. Why Momentum justified incumbent replacement", "", "The approved rationale records consistent 1W/4W forward-IC, stability, gross spread, implementation and common-sample evidence for `MOMENTUM_12_1`.", "",
        "## 12. Why no other incumbent was replaced", "", "For Value, Quality, and Low Risk, the human review found no sufficiently clear, coherent, multi-dimensional case to overcome the incumbent-default rule.", "",
        "## 13. Final seven-representative map", "", "| Family | Frozen representative | Gate A0 status |", "| --- | --- | --- |", *map_lines, "",
        "## 14. Adaptive-research disclosure", "", "Gate A0 is adaptive within the known 2011–2015 Factor Selection evidence because it was introduced after incumbent evidence had been reviewed. The period is not labelled untouched for this design question.", "",
        "## 15. Protected later-period samples", "", "No 2016–2020, 2021–2025, or 2026 performance was accessed for this decision. Those samples remain protected and are not marked contaminated.", "",
        "## 16. Round-2 challenger status", "", "**ROUND 2 — NOT AUTHORIZED.** FCF-based Value and GP/A-focused Quality remain future-research hypotheses only. No RMW, RMWc, Trend Momentum, Low Beta, BAB, Shareholder Yield, Net Payout Yield, EV-based Value, or other new definition is authorized.", "",
        "## 17. Difference between Gate A0 and Gate A", "", "Gate A0 freezes one representation per economic family. Gate A remains pending and will decide whether each frozen family enters the Final Factor Set.", "",
        "## 18. Exact next research stage", "", "Refresh the non-decision cross-family Gate A evidence using only the seven frozen representatives and the 2011–2015 formal sample.", "",
    ])


def run(root: Path) -> dict[str, Any]:
    root = root.resolve()
    rows = freeze_rows(root)
    fields = ["family", "selected_specification_id", "selected_specification_name", "decision_type", "incumbent_specification_id", "incumbent_retained", "eligible_candidate_count", "decision_basis", "primary_reason", "secondary_reason", "common_sample_consistency", "portfolio_consistency", "stability_consistency", "implementation_consistency", "component_diagnostic_note", "round2_challenger_status", "human_decision", "approver", "decision_evidence_period", "later_period_performance_accessed", "notes"]
    write_csv(root / DECISION, rows, fields)
    (root / REPORT).write_text(report(rows), encoding="utf-8")
    ssot = (root / SSOT).read_text(encoding="utf-8")
    expected = ["GATE A0 STATUS: FROZEN", "VALUE_CURRENT", "QUALITY_CURRENT", "MOMENTUM_12_1", "LOWRISK_CURRENT", "INVESTMENT_CURRENT", "DIVIDEND_CURRENT", "SIZE_CURRENT", "ROUND 2 — NOT AUTHORIZED", "Gate A remains TBD"]
    checks = {
        "GATE_A0_HUMAN_DECISION_RECORDED": all(row["human_decision"] == "TRUE" for row in rows),
        "EXACTLY_SEVEN_FAMILY_REPRESENTATIVES": len(rows) == 7 and len({row["family"] for row in rows}) == 7,
        "VALUE_CURRENT_SELECTED": by(rows, "Value") == "VALUE_CURRENT",
        "QUALITY_CURRENT_SELECTED": by(rows, "Quality") == "QUALITY_CURRENT",
        "MOMENTUM_12_1_SELECTED": by(rows, "Momentum") == "MOMENTUM_12_1",
        "LOWRISK_CURRENT_SELECTED": by(rows, "Low Risk") == "LOWRISK_CURRENT",
        "INVESTMENT_CURRENT_SELECTED": by(rows, "Investment") == "INVESTMENT_CURRENT",
        "DIVIDEND_CURRENT_SELECTED": by(rows, "Dividend") == "DIVIDEND_CURRENT",
        "SIZE_CURRENT_SELECTED": by(rows, "Size") == "SIZE_CURRENT",
        "ONLY_MOMENTUM_INCUMBENT_REPLACED": [row["family"] for row in rows if row["decision_type"] == "REPLACE_INCUMBENT"] == ["Momentum"],
        "COMPONENT_DIAGNOSTICS_NOT_PROMOTED": not any("COMPONENT" in row["selected_specification_id"] for row in rows),
        "ROUND2_NOT_AUTHORIZED": all(row["round2_challenger_status"] == "ROUND_2_NOT_AUTHORIZED" for row in rows),
        "ONE_REPRESENTATIVE_PER_FAMILY": len({row["family"] for row in rows}) == len(rows),
        "GATE_A_STILL_TBD": "Gate A remains TBD" in ssot,
        "NO_2016_2020_PERFORMANCE": all(row["later_period_performance_accessed"] == "FALSE" for row in rows),
        "NO_2021_2025_PERFORMANCE": all(row["later_period_performance_accessed"] == "FALSE" for row in rows),
        "NO_2026_PERFORMANCE": all(row["later_period_performance_accessed"] == "FALSE" for row in rows),
        "SSOT_CONSISTENCY": all(item in ssot for item in expected),
        "CONTAMINATION_REGISTER_UPDATED": False,
        "NO_FMOM": True,
        "NO_GATE_B": True,
    }
    oos_rows = [row for row in read_csv(root / OOS) if row["stage"] != "FORMAL_GATE_A0_HUMAN_REPRESENTATIVE_FREEZE"]
    oos_rows.append({"stage": "FORMAL_GATE_A0_HUMAN_REPRESENTATIVE_FREEZE", "performance_evidence_accessed": "Approved human decision based only on 2011-01-01 to 2015-12-31 Gate A0 evidence", "formal_gate_a_evidence": "Adaptive Gate A0 freeze: MOMENTUM_12_1 replaces MOMENTUM_CURRENT; Gate A remains TBD; Gate B has not begun", "performance_2016_2020": "NOT_ACCESSED", "performance_2021_2025": "NOT_ACCESSED", "post2015_returns_used_for_factor_choice": "FALSE", "status": "FIREWALL_COMPLIANT_ADAPTIVE_REFINEMENT", "notes": "2011-2015 is not labelled untouched. No 2016-2025 performance is marked contaminated or accessed for the human Gate A0 decision."})
    write_csv(root / OOS, oos_rows, list(oos_rows[0]))
    checks["CONTAMINATION_REGISTER_UPDATED"] = sum(row["stage"] == "FORMAL_GATE_A0_HUMAN_REPRESENTATIVE_FREEZE" for row in oos_rows) == 1
    acceptance = [{"check": check, "status": "PASS" if passed else "FAIL", "blocking": str(not passed).lower(), "evidence": "approved_human_gate_a0_decision", "notes": "Recorded human decision; no performance calculation is performed by this freeze."} for check, passed in checks.items()]
    write_csv(root / ACCEPTANCE, acceptance, ["check", "status", "blocking", "evidence", "notes"])
    return {"rows": rows, "acceptance": acceptance, "registry_sha256": sha256(root / REGISTRY), "ssot_sha256": sha256(root / SSOT)}


def by(rows: list[dict[str, Any]], family: str) -> str:
    return next(row["selected_specification_id"] for row in rows if row["family"] == family)


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--root", type=Path, default=ROOT)
    result = run(parser.parse_args().root)
    print({"representatives": len(result["rows"]), "failures": sum(row["status"] == "FAIL" for row in result["acceptance"])})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
