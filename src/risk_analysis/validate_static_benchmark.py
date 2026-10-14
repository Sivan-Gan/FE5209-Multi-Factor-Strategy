
"""Member 5: Static Multi-Factor OOS Acceptance Tests.

Run:
    python -m src.risk_analysis.validate_static_benchmark
"""

from pathlib import Path

import numpy as np
import pandas as pd

from src.strategy import composite_score as cs
from src.strategy import portfolio as pf
from src.strategy import run_gate_b as rg
from src.strategy import metrics as mt
from src.strategy import governance as gv

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "outputs/strategy/benchmarks/hist_oos"
DYNAMIC = ROOT / "outputs/strategy/frozen/hist_oos"
REPORT_DIR = ROOT / "outputs/risk_analysis/us_hist_oos"

START = "2021-01-01"
END = "2025-12-31"
HISTORY_START = "2016-01-01"
TOL = 1e-9

checks = []


def check(name, condition, detail=""):
    passed = bool(condition)
    checks.append({
        "check": name,
        "status": "PASS" if passed else "FAIL",
        "detail": str(detail)
    })


def main():
    cfg = gv.load_config(ROOT)
    p = cfg["parameters"]
    cost = cfg["transaction_cost"]["oneway_bps"] / 10000

    path = pd.read_csv(STATIC / "static_weekly_path.csv")
    hold = pd.read_csv(
        STATIC / "static_target_holdings.csv",
        dtype={"PERMNO": str, "sector": str}
    )
    ledger = pd.read_csv(
        STATIC / "static_trade_ledger.csv",
        dtype={"PERMNO": str}
    )
    construction = pd.read_csv(
        STATIC / "static_construction.csv"
    )
    dynamic = pd.read_csv(DYNAMIC / "weekly_path.csv")

    # 1. Configuration
    check("Frozen config", cfg["status"] == "FROZEN")
    check("Factor count K=4", len(p["final_factor_set"]) == 4)
    check("Static buffer enabled", p["buffer"] is True)
    check("Cost = 10bp", abs(cost - 0.001) < 1e-12)

    # 2. Sample and timing
    check("259 completed weeks", len(path) == 259)
    check("Unique signal dates",
          not path["signal_date"].duplicated().any())
    check("OOS signal dates",
          path["signal_date"].between(START, END).all())
    check("OOS period ends",
          path["period_end"].le(END).all())

    aligned = path.merge(
        dynamic[["signal_date", "period_end"]],
        on="signal_date",
        how="outer",
        suffixes=("_static", "_dynamic"),
        indicator=True,
        validate="one_to_one"
    )
    check("Dynamic/Static identical weeks",
          (aligned["_merge"] == "both").all()
          and (aligned["period_end_static"]
               == aligned["period_end_dynamic"]).all())

    # 3. Portfolio constraints
    check("No duplicate holdings",
          not hold.duplicated(["signal_date", "PERMNO"]).any())
    check("Finite nonnegative stock weights",
          np.isfinite(hold["weight"]).all()
          and (hold["weight"] >= 0).all())

    weekly_stock_weight = hold.groupby("signal_date")["weight"].sum()
    c = construction.set_index("signal_date")
    total = weekly_stock_weight.reindex(c.index, fill_value=0) + c["cash_weight"]

    check("Stock + cash = 1",
          np.allclose(total, 1, atol=TOL),
          f"max_error={float(abs(total-1).max()):.3e}")

    check("Sector cap <=30%",
          (hold.groupby(["signal_date", "sector"])["weight"]
           .sum() <= 0.30 + TOL).all())

    check("Holdings <= target count",
          (c["n_held"] <= c["n_target"]).all())
    check("No infeasible weeks",
          (c["constraint_infeasible"] == 0).all())

    # 4. Ledger and transaction cost
    ledger_gtr = (
        ledger.groupby("signal_date")["trade_weight"]
        .apply(lambda x: x.abs().sum())
    )
    actual_gtr = path.set_index("signal_date")["gtr"]

    gtr_error = (
        ledger_gtr.reindex(actual_gtr.index, fill_value=0)
        - actual_gtr
    ).abs().max()

    check("Ledger rebuilds GTR", gtr_error < TOL,
          f"max_error={gtr_error:.3e}")

    cost_error = abs(
        ledger["cost"].sum() - cost * path["gtr"].sum()
    )
    check("Ledger rebuilds cost", cost_error < TOL,
          f"error={cost_error:.3e}")

    turnover_error = (
        path["turnover"] - path["gtr"] / 2
    ).abs().max()
    check("Turnover = GTR/2", turnover_error < TOL)

    net_expected = (
        (1 - cost * path["gtr"])
        * (1 + path["gross_return"]) - 1
    )
    net_error = abs(net_expected - path["net_return"]).max()
    check("Gross-to-net closure", net_error < TOL,
          f"max_error={net_error:.3e}")

    # 5. Rebuild gross return from actual stock returns
    returns = rg.load_returns(ROOT, START, END)
    merged = hold.merge(
        returns,
        on=["signal_date", "PERMNO"],
        how="left",
        validate="one_to_one"
    )

    usable = merged["usable"].fillna(0).eq(1)
    stock_r = np.where(
        usable,
        merged["execution_to_next_execution_total_return"],
        0.0
    )
    merged["contribution"] = merged["weight"] * stock_r

    gross_rebuilt = (
        merged.groupby("signal_date")["contribution"].sum()
        .reindex(path["signal_date"])
    )

    gross_error = abs(
        gross_rebuilt.to_numpy() - path["gross_return"].to_numpy()
    ).max()

    check("Holdings rebuild gross return",
          gross_error < TOL,
          f"max_error={gross_error:.3e}")

    # 6. Independently reconstruct static stock selection.
    # Static factors are exactly equal-weighted.
    scores = cs.load_scores(
        ROOT, HISTORY_START, END, allow_oos=True
    )

    static_weights = pd.DataFrame({
        "signal_date": sorted(scores["signal_date"].unique())
    })
    for col in cs.WEIGHT_COLS:
        static_weights[col] = 0.25

    scored = cs.compute_scores(scores, static_weights, 4)
    full_hold, _ = pf.build_holdings(scored, use_buffer=True)

    expected_hold = full_hold[
        full_hold["signal_date"].between(START, END)
    ].copy()

    keys = ["signal_date", "PERMNO"]
    a = expected_hold[keys + ["weight"]].rename(
        columns={"weight": "expected"}
    )
    b = hold[keys + ["weight"]].rename(
        columns={"weight": "actual"}
    )

    matched = a.merge(b, on=keys, how="outer", indicator=True)
    same_names = (matched["_merge"] == "both").all()
    same_weights = (
        same_names and
        np.allclose(matched["expected"], matched["actual"], atol=TOL)
    )

    check("Static stock selection and weights",
          same_weights,
          f"expected={len(a)}, actual={len(b)}")

    # 7. Continuity: independently rebuild accounting from 2016.
    all_returns = rg.load_returns(ROOT, HISTORY_START, END)
    full_path, _ = mt.weekly_path_and_ledger(
        full_hold, all_returns, END, cost
    )
    expected_path = full_path[
        full_path["signal_date"].between(START, END)
    ]

    first = path.iloc[0]
    expected_first = expected_path.iloc[0]

    check("2021 first-week continuity",
          first["signal_date"] == expected_first["signal_date"]
          and abs(first["gtr"] - expected_first["gtr"]) < TOL,
          f"first_week_GTR={first['gtr']:.6f}")

    # 8. Save report
    report = pd.DataFrame(checks)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report.to_csv(
        REPORT_DIR / "static_acceptance_report.csv",
        index=False
    )

    print("\n=== STATIC BENCHMARK ACCEPTANCE ===")
    print(report.to_string(index=False))

    n_fail = (report["status"] == "FAIL").sum()
    print("\nVERDICT:", "ACCEPTED" if n_fail == 0 else "REJECTED")
    print("Checks:", len(report), "Failures:", n_fail)

    if n_fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
