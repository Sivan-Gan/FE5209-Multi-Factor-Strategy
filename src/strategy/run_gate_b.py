"""Gate B driver: run Steps 20-21 for every candidate configuration and evaluate them.

Grid (no dense search, SSOT Step 19 / 4.2):
  J in {13, 26, 52} x lambda in {0.5, 1} x risk adjustment off/on x buffer off/on (24)
  + lambda = 0 static equal factor weights, buffer off/on (2; J and RA have no effect)
  all with min-factors 4
  + baseline (J=26, lambda=0.5, RA off) with min-factors 3, buffer off/on -> robustness

For each config (2016-2020, weekly, metrics.py conventions):
  - construction diagnostics: names held, sector-cap weeks, cash weeks;
  - gross and net performance at a BASE one-way cost and a DOUBLE-COST stress
    (SSOT Step 46): annual return, volatility, Sharpe (excess over FF rf), Sortino,
    max drawdown, Calmar, drawdown episodes, weekly hit rate, benchmark-relative metrics;
  - turnover on drifted weights, cost drag, break-even one-way cost;
  - trade metrics (course list): round trips, holding period, trade win rate, profit/trade.
Benchmarks (SSOT Step 23): the S&P 500 total return (net_bench_* / net_active_* columns) and an
extra row BENCH_EQUAL_WEIGHT_UNIVERSE = all eligible stocks equal weight, same rebalance and cost,
which separates the universe / equal-weight effect from the factor-selection effect.

COST STATUS: the base cost is a PROVISIONAL research scenario until the group freezes it
(SSOT 4.4 "Cost-Parameter Status").  Weekly metrics are for Gate B comparison; final
authoritative numbers use daily NAV (SSOT 4.4 Frequency Rule).

Inputs (all <= 2020-12-31):
  - handoff/final_factor/data/factor_scores_weekly.parquet
  - handoff/us_downstream/data/security_weekly_execution_returns.parquet
  - handoff/us_downstream/data/risk_free_weekly.parquet
  - handoff/us_downstream/data/benchmark_weekly_execution_returns.parquet
  - outputs/strategy/factor_weights_weekly_2016_2020.csv  (fmom_weights.py)

Usage (run from the repository root):
  python -m src.strategy.run_gate_b                 # base cost 10 bp one-way, stress 20 bp
  python -m src.strategy.run_gate_b --cost-bps 15
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
from src.strategy import metrics as mt
from src.strategy import portfolio as pf

DATA = "handoff/us_downstream/data/"
RETURNS_FILE = DATA + "security_weekly_execution_returns.parquet"
RF_FILE = DATA + "risk_free_weekly.parquet"
BENCH_FILE = DATA + "benchmark_weekly_execution_returns.parquet"
DEFAULT_COST_BPS = 10.0
STRESS_MULTIPLIER = 2.0
BENCH_EW_TAG = "BENCH_EQUAL_WEIGHT_UNIVERSE"


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


# ------------------------------------------------------------- 1. inputs
def _str(df: pd.DataFrame, cols) -> pd.DataFrame:
    for c in cols:
        df[c] = df[c].astype(str)
    return df


def load_returns(root: Path, start: str, end: str) -> pd.DataFrame:
    r = pd.read_parquet(root / RETURNS_FILE,
                        columns=["signal_date", "next_execution_date", "PERMNO",
                                 "execution_to_next_execution_total_return", "execution_feasible",
                                 "unavailable_reason"],
                        filters=[("signal_date", ">=", start), ("signal_date", "<=", end)])
    r = _str(r, ("signal_date", "next_execution_date", "PERMNO"))
    status = r["unavailable_reason"].fillna("").replace("", "VALID")
    r["usable"] = ((r["execution_feasible"] == 1) & status.isin(fr.VALID_RETURN_STATUSES)
                   & np.isfinite(r["execution_to_next_execution_total_return"])).astype(int)
    return r[["signal_date", "next_execution_date", "PERMNO",
              "execution_to_next_execution_total_return", "usable"]]


def load_weekly_series(root: Path, file: str, value_col: str, start: str, end: str) -> pd.Series:
    """Risk-free or benchmark return per signal_date (E_t -> E_{t+1})."""
    df = pd.read_parquet(root / file, columns=["signal_date", value_col],
                         filters=[("signal_date", ">=", start), ("signal_date", "<=", end)])
    return df.assign(signal_date=df["signal_date"].astype(str)).set_index("signal_date")[value_col].astype(float)


# ------------------------------------------------------- 2. one config
def evaluate(hold: pd.DataFrame, returns: pd.DataFrame, rf: pd.Series | None, bench: pd.Series | None,
             end: str, cost: float) -> tuple[dict, pd.DataFrame]:
    path = mt.weekly_path(hold, returns, end).set_index("signal_date")
    stress = cost * STRESS_MULTIPLIER
    series = {"gross": path["gross_return"], "net": mt.net_returns(path, cost),
              "stress": mt.net_returns(path, stress)}
    row = {}
    for label, r in series.items():
        perf = mt.performance(r, rf, bench if label == "net" else None)
        keep = perf if label == "net" else {k: perf[k] for k in
                                            ("ann_return", "ann_vol", "sharpe", "max_drawdown", "calmar")}
        row.update({f"{label}_{k}": v for k, v in keep.items()})
    row["cost_drag_ann"] = row["gross_ann_return"] - row["net_ann_return"]
    row["avg_weekly_turnover"] = float(path["turnover"].mean())
    row["ann_turnover"] = float(path["turnover"].mean() * mt.WEEKS_PER_YEAR)
    row["break_even_oneway_cost"] = mt.break_even_cost(path)
    row["avg_unpriced_weight"] = float(path["unpriced_weight"].mean())
    row.update(mt.trade_stats(hold, returns, end, cost))
    weekly = path.assign(net_return=series["net"], stress_return=series["stress"]).reset_index()
    return row, weekly


# ------------------------------------------------------------------ 3. run
def run(root: Path, start: str, end: str, out_dir: Path, cost_bps: float) -> pd.DataFrame:
    t0 = time.time()
    cost = cost_bps / 1e4
    weights = pd.read_csv(root / cs.WEIGHTS_FILE)
    rules = weights["window_rule"].unique() if "window_rule" in weights.columns else ["strict_calendar"]
    if len(rules) != 1:
        raise ValueError(f"factor weights file mixes window rules {list(rules)}; rerun fmom_weights.py")
    window_rule = rules[0]
    scores = cs.load_scores(root, start, end)
    returns = load_returns(root, start, end)
    rf = load_weekly_series(root, RF_FILE, "risk_free_return", start, end)
    bench = load_weekly_series(root, BENCH_FILE, "benchmark_total_return", start, end)
    print(f"loaded inputs in {time.time() - t0:.0f}s; base cost {cost_bps:g} bp one-way, "
          f"stress {cost_bps * STRESS_MULTIPLIER:g} bp (PROVISIONAL until frozen by the group); "
          f"FMOM window rule {window_rule}")

    out_dir.mkdir(parents=True, exist_ok=True)
    rows, weekly_all, yearly_all = [], [], []
    grid = config_grid()
    for i, c in enumerate(grid, 1):
        week_w = cs.weights_for_config(weights, c["J"], c["lam"], c["risk_adjusted"])
        scored = cs.compute_scores(scores, week_w, c["min_factors"])
        hold, construction = pf.build_holdings(scored, c["buffer"])
        problems = pf.check_holdings(hold, construction)
        tag = tag_of(c)
        hold.to_csv(out_dir / f"target_holdings_{tag}.csv", index=False)
        perf, weekly = evaluate(hold, returns, rf, bench, end, cost)
        row = {"tag": tag, **c, "cost_bps": cost_bps, "fmom_window_rule": window_rule,
               "avg_names_held": construction["n_held"].mean(),
               "weeks_sector_cap_binding": int((construction["n_skipped_by_sector_cap"] > 0).sum()),
               "weeks_with_cash": int(construction["constraint_infeasible"].sum()),
               **perf, "checks": "PASS" if not problems else "; ".join(problems)}
        rows.append(row)
        weekly_all.append(weekly.assign(tag=tag))
        yr = mt.yearly_returns(weekly["net_return"], weekly["signal_date"])
        yearly_all.append({"tag": tag, **{f"net_{y}": v for y, v in yr.items()}})
        print(f"[{i:2d}/{len(grid)}] {tag:<32} net Sharpe {row['net_sharpe']:.3f}  {row['checks']}")

    # SSOT Step 23 benchmark: every eligible stock, equal weight, same rebalance / cost / execution
    ew = scores[scores["eligible_universe_flag"] == 1][["signal_date", "PERMNO"]].copy()
    ew["weight"] = 1.0 / ew.groupby("signal_date")["PERMNO"].transform("count")
    perf, weekly = evaluate(ew, returns, rf, bench, end, cost)
    tag = BENCH_EW_TAG
    rows.append({"tag": tag, "cost_bps": cost_bps,
                 "avg_names_held": float(ew.groupby("signal_date").size().mean()),
                 **perf, "checks": "BENCHMARK"})
    weekly_all.append(weekly.assign(tag=tag))
    yr = mt.yearly_returns(weekly["net_return"], weekly["signal_date"])
    yearly_all.append({"tag": tag, **{f"net_{y}": v for y, v in yr.items()}})
    print(f"[bench] {tag:<32} net Sharpe {perf['net_sharpe']:.3f}")

    summary = pd.DataFrame(rows)
    summary.to_csv(out_dir / "gate_b_config_summary.csv", index=False)
    pd.concat(weekly_all).to_csv(out_dir / "gate_b_weekly_returns.csv", index=False)
    pd.DataFrame(yearly_all).to_csv(out_dir / "gate_b_yearly_net_returns.csv", index=False)
    print(f"done in {time.time() - t0:.0f}s")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default=fr.GATE_B_LAST_DATE)
    parser.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS,
                        help="base one-way all-in cost in bp; the stress case is 2x")
    args = parser.parse_args()
    if args.end > fr.GATE_B_LAST_DATE:
        raise ValueError(f"Gate B may not go beyond {fr.GATE_B_LAST_DATE}; got end={args.end}")
    root = fr.project_root()
    summary = run(root, args.start, args.end, root / "outputs/strategy/gate_b", args.cost_bps)

    show = summary[["tag", "avg_weekly_turnover", "gross_sharpe", "net_ann_return", "net_sharpe",
                    "net_max_drawdown", "stress_sharpe", "break_even_oneway_cost",
                    "avg_holding_weeks", "trade_win_rate", "checks"]]
    pd.set_option("display.width", 220)
    print("\n" + show.round(4).to_string(index=False))
    print(f"\nNOTE: weekly metrics, 2016-2020 only. Base cost {args.cost_bps:g} bp one-way is PROVISIONAL "
          f"until the group freezes it.\nFull columns: outputs/strategy/gate_b/gate_b_config_summary.csv")
    configs = summary[summary["tag"] != BENCH_EW_TAG]
    print("\nALL CONFIGS PASS" if (configs["checks"] == "PASS").all() else "\nSOME CONFIGS NEED A LOOK")


if __name__ == "__main__":
    main()
