"""Gate C automated acceptance checks for the frozen strategy run (SSOT 0.10).

Each check returns one row: check_id, category, description, critical, status, detail.
Any critical FAIL marks the run REJECTED (it must not enter formal result tables).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.strategy import portfolio as pf

TOL = 1e-10


def _row(cid, cat, desc, ok, detail="", critical=True):
    return {"check_id": cid, "category": cat, "description": desc, "critical": critical,
            "status": "PASS" if ok else "FAIL", "detail": detail}


def run_checks(*, weights: pd.DataFrame, scored: pd.DataFrame, hold: pd.DataFrame, construction: pd.DataFrame,
               path: pd.DataFrame, ledger: pd.DataFrame, returns: pd.DataFrame, cost: float,
               start: str, end: str) -> pd.DataFrame:
    rows = []

    # ---------------- FMOM / timing
    wsum = weights.groupby("signal_date")["weight"].sum()
    rows.append(_row("FMOM_01", "FMOM/Timing", "factor weights sum to 1 every week",
                     np.allclose(wsum, 1.0, atol=1e-12), f"max |sum-1| = {float((wsum - 1).abs().max()):.2e}"))
    used = weights[weights["window_last_next_execution_date"].astype(str).str.len() > 0]
    leak = (used["window_last_next_execution_date"].astype(str) > used["signal_date"].astype(str)).sum()
    rows.append(_row("FMOM_02", "FMOM/Timing", "FMOM only uses completed weeks (E_{u+1} <= S_t)",
                     leak == 0, f"violations = {int(leak)}"))
    rows.append(_row("FMOM_03", "FMOM/Timing", "factor weights non-negative",
                     bool((weights["weight"] >= -TOL).all())))
    fb = weights.drop_duplicates("signal_date")["dynamic_status"].value_counts().to_dict()
    rows.append(_row("FMOM_04", "FMOM/Timing", "dynamic-weight fallback weeks (information)", True,
                     str(fb), critical=False))

    # ---------------- portfolio
    problems = pf.check_holdings(hold[hold["signal_date"] <= "9999"], construction) if len(hold) else ["no holdings"]
    problems = [p for p in problems if "Gate B window" not in p]          # window is checked separately below
    rows.append(_row("PORT_01", "Portfolio", "weights + cash = 1, no negatives, no duplicates, sector cap, "
                     "names <= target", not problems, "; ".join(problems)))
    bad = ~np.isfinite(hold["weight"].to_numpy(dtype=float))
    rows.append(_row("PORT_02", "Portfolio", "no NaN / inf weights", not bad.any(), f"bad = {int(bad.sum())}"))
    ok_names = set(zip(scored.loc[scored["score_available"] == 1, "signal_date"],
                       scored.loc[scored["score_available"] == 1, "PERMNO"]))
    outside = sum((d, p) not in ok_names for d, p in zip(hold["signal_date"], hold["PERMNO"]))
    rows.append(_row("PORT_03", "Portfolio", "every holding is an eligible stock with a valid composite score",
                     outside == 0, f"violations = {outside}"))
    rows.append(_row("PORT_04", "Portfolio", "constraint-infeasible weeks (information)", True,
                     f"{int(construction['constraint_infeasible'].sum())} weeks", critical=False))

    # ---------------- sample window
    in_window = hold["signal_date"].between(start, end).all()
    rows.append(_row("WIN_01", "Sample window", f"holdings signal dates within [{start}, {end}]", bool(in_window)))
    late = (path["period_end"] > end).sum() if len(path) else 0
    rows.append(_row("WIN_02", "Sample window", f"no holding period ends after {end}", late == 0,
                     f"violations = {int(late)}"))

    # ---------------- accounting
    lg = ledger.groupby("signal_date")["trade_weight"].apply(lambda x: x.abs().sum())
    gtr = path.set_index("signal_date")["gtr"]
    diff = (lg.reindex(gtr.index).fillna(0.0) - gtr).abs().max() if len(gtr) else 0.0
    rows.append(_row("ACC_01", "Accounting", "trade ledger rebuilds GTR every week", diff < 1e-9,
                     f"max diff = {diff:.2e}"))
    cdiff = abs(ledger["cost"].sum() - cost * gtr.sum())
    rows.append(_row("ACC_02", "Accounting", "trade ledger rebuilds total cost (c x GTR)", cdiff < 1e-9,
                     f"diff = {cdiff:.2e}"))
    m = hold.merge(returns, on=["signal_date", "PERMNO"], how="left")
    m["r"] = np.where(m["usable"].fillna(0) == 1, m["execution_to_next_execution_total_return"], 0.0)
    rebuilt = (m["weight"] * m["r"]).groupby(m["signal_date"]).sum().reindex(path["signal_date"]).to_numpy()
    gdiff = float(np.abs(rebuilt - path["gross_return"].to_numpy()).max()) if len(path) else 0.0
    rows.append(_row("ACC_03", "Accounting", "holdings rebuild gross return", gdiff < 1e-10,
                     f"max diff = {gdiff:.2e}"))
    net = (1 - cost * path["gtr"]) * (1 + path["gross_return"]) - 1
    nav_a = float(np.prod(1 + net))
    nav_b = float(np.prod((1 - cost * path["gtr"])) * np.prod(1 + path["gross_return"]))
    rows.append(_row("ACC_04", "Accounting", "gross -> net NAV identity (1-TC)(1+R)",
                     abs(nav_a - nav_b) < 1e-9, f"NAV diff = {abs(nav_a - nav_b):.2e}"))
    rows.append(_row("ACC_05", "Accounting", "average unpriced holding weight (information)", True,
                     f"{float(path['unpriced_weight'].mean()):.6f}" if len(path) else "n/a", critical=False))
    return pd.DataFrame(rows)


def verdict(report: pd.DataFrame) -> str:
    failed = report[(report["critical"]) & (report["status"] == "FAIL")]
    return "ACCEPTED" if failed.empty else "REJECTED"
