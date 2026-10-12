"""Gate B driver: run Steps 20-21 for every candidate configuration and summarise.

Grid (no dense search, SSOT Step 19 / 4.2):
  J in {13, 26, 52} x lambda in {0.5, 1} x risk adjustment off/on x buffer off/on (24)
  + lambda = 0 static equal factor weights, buffer off/on (2; J and RA have no effect)
  all with min-factors 4
  + baseline (J=26, lambda=0.5, RA off) with min-factors 3, buffer off/on -> robustness

For each config it writes the weekly target holdings and one summary row with:
  - construction diagnostics: names held, sector-cap weeks, cash weeks, target turnover;
  - a GROSS PREVIEW: weekly sum(w_i * r_i) over E_t -> E_{t+1}, no costs, cash at 0%.
    This is only a wiring check.  Official Gate B metrics are M5's after-cost daily NAV
    (SSOT 4.4); do not pick a configuration from the preview.

Inputs (all <= 2020-12-31):
  - handoff/final_factor/data/factor_scores_weekly.parquet
  - handoff/us_downstream/data/security_weekly_execution_returns.parquet
  - outputs/strategy/factor_weights_weekly_2016_2020.csv  (fmom_weights.py)

Usage (run from the repository root; takes a few minutes):
  python -m src.strategy.run_gate_b
"""
from __future__ import annotations

import argparse
import itertools
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.strategy import composite_score as cs
from src.strategy import factor_returns as fr
from src.strategy import fmom_weights as fw
from src.strategy import portfolio as pf

RETURNS_FILE = "handoff/us_downstream/data/security_weekly_execution_returns.parquet"
WEEKS_PER_YEAR = 52


def config_grid() -> list[dict]:
    grid = []
    for J, lam, ra, buf in itertools.product(fw.J_GRID, fw.LAMBDA_GRID, fw.RISK_ADJ_GRID, (False, True)):
        # lambda = 0 is plain equal factor weights: J and risk adjustment do nothing, run it once
        if lam == 0 and (J != fw.BASELINE["J"] or ra):
            continue
        grid.append({"J": J, "lam": lam, "risk_adjusted": ra, "min_factors": fw.K, "buffer": buf})
    for buf in (False, True):
        grid.append({**{"J": fw.BASELINE["J"], "lam": fw.BASELINE["lam"],
                        "risk_adjusted": fw.BASELINE["risk_adjusted"]}, "min_factors": 3, "buffer": buf})
    return grid


def tag_of(c: dict) -> str:
    return pf.config_tag(c["J"], c["lam"], c["risk_adjusted"], c["min_factors"]) + f"_buf{int(c['buffer'])}"


# ------------------------------------------------------------- 1. returns
def load_returns(root: Path, start: str, end: str) -> pd.DataFrame:
    r = pd.read_parquet(root / RETURNS_FILE,
                        columns=["signal_date", "next_execution_date", "PERMNO",
                                 "execution_to_next_execution_total_return", "execution_feasible",
                                 "unavailable_reason"],
                        filters=[("signal_date", ">=", start), ("signal_date", "<=", end)])
    for c in ("signal_date", "next_execution_date", "PERMNO"):
        r[c] = r[c].astype(str)
    status = r["unavailable_reason"].fillna("").replace("", "VALID")
    r["usable"] = ((r["execution_feasible"] == 1) & status.isin(fr.VALID_RETURN_STATUSES)
                   & np.isfinite(r["execution_to_next_execution_total_return"])).astype(int)
    return r[["signal_date", "next_execution_date", "PERMNO",
              "execution_to_next_execution_total_return", "usable"]]


def gross_preview(hold: pd.DataFrame, returns: pd.DataFrame, end: str) -> pd.DataFrame:
    """Weekly gross return of the target book; weeks whose holding period ends after `end` are dropped."""
    m = hold.merge(returns, on=["signal_date", "PERMNO"], how="left")
    m["usable"] = m["usable"].fillna(0).astype(int)
    m["contrib"] = np.where(m["usable"] == 1, m["weight"] * m["execution_to_next_execution_total_return"], 0.0)
    m["unpriced_w"] = np.where(m["usable"] == 1, 0.0, m["weight"])
    g = m.groupby("signal_date")
    weekly = pd.DataFrame({"gross_return": g["contrib"].sum(), "unpriced_weight": g["unpriced_w"].sum(),
                           "period_end": g["next_execution_date"].max()})
    return weekly[weekly["period_end"].fillna("9999") <= end]


def preview_stats(weekly: pd.DataFrame) -> dict:
    r = weekly["gross_return"]
    vol = r.std(ddof=1) * np.sqrt(WEEKS_PER_YEAR)
    return {
        "preview_weeks": len(r),
        "preview_gross_ann_return": (1 + r).prod() ** (WEEKS_PER_YEAR / len(r)) - 1 if len(r) else np.nan,
        "preview_gross_ann_vol": vol,
        "preview_gross_sharpe_rf0": (r.mean() * WEEKS_PER_YEAR) / vol if vol > 0 else np.nan,
        "preview_unpriced_weight_avg": weekly["unpriced_weight"].mean(),
    }


# ------------------------------------------------------------------ 2. run
def run(root: Path, start: str, end: str, out_dir: Path) -> pd.DataFrame:
    t0 = time.time()
    weights = pd.read_csv(root / cs.WEIGHTS_FILE)
    scores = cs.load_scores(root, start, end)
    returns = load_returns(root, start, end)
    print(f"loaded inputs in {time.time() - t0:.0f}s")

    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    grid = config_grid()
    for i, c in enumerate(grid, 1):
        week_w = cs.weights_for_config(weights, c["J"], c["lam"], c["risk_adjusted"])
        scored = cs.compute_scores(scores, week_w, c["min_factors"])
        hold, weekly = pf.build_holdings(scored, c["buffer"])
        problems = pf.check_holdings(hold, weekly)
        tag = tag_of(c)
        hold.to_csv(out_dir / f"target_holdings_{tag}.csv", index=False)
        row = {**c, "tag": tag,
               "weeks": len(weekly),
               "avg_names_held": weekly["n_held"].mean(),
               "weeks_sector_cap_binding": int((weekly["n_skipped_by_sector_cap"] > 0).sum()),
               "weeks_with_cash": int(weekly["constraint_infeasible"].sum()),
               "avg_one_way_target_turnover": weekly["one_way_turnover"].mean(),
               "checks": "PASS" if not problems else "; ".join(problems)}
        row.update(preview_stats(gross_preview(hold, returns, end)))
        rows.append(row)
        print(f"[{i:2d}/{len(grid)}] {tag:<32} {row['checks']}")
    summary = pd.DataFrame(rows)
    summary.to_csv(out_dir / "gate_b_config_summary.csv", index=False)
    print(f"done in {time.time() - t0:.0f}s")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default=fr.GATE_B_LAST_DATE)
    args = parser.parse_args()
    if args.end > fr.GATE_B_LAST_DATE:
        raise ValueError(f"Gate B may not go beyond {fr.GATE_B_LAST_DATE}; got end={args.end}")
    root = fr.project_root()
    summary = run(root, args.start, args.end, root / "outputs/strategy/gate_b")

    show = summary[["tag", "avg_names_held", "weeks_sector_cap_binding", "weeks_with_cash",
                    "avg_one_way_target_turnover", "preview_gross_ann_return", "preview_gross_ann_vol",
                    "preview_gross_sharpe_rf0", "checks"]]
    pd.set_option("display.width", 200)
    print("\n" + show.round(4).to_string(index=False))
    print("\nNOTE: preview_* columns are gross, weekly, target-weight, no costs - a wiring check only.\n"
          "Official Gate B comparison uses M5's after-cost daily NAV (SSOT 4.4).")
    print("\nALL CONFIGS PASS" if (summary["checks"] == "PASS").all() else "\nSOME CONFIGS NEED A LOOK")


if __name__ == "__main__":
    main()
