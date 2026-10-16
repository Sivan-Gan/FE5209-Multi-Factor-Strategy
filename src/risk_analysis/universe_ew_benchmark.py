
"""Member 5: US Eligible-Universe Equal-Weight Benchmark.

Uses the frozen eligible universe, no factor scores.
Weekly rebalancing with shared execution and accounting rules.

Run:
    python -m src.risk_analysis.universe_ew_benchmark
"""

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from src.strategy import composite_score as cs
from src.strategy import run_gate_b as rg
from src.strategy import metrics as mt
from src.strategy import governance as gv


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/risk_analysis/us_hist_oos"

START_HISTORY = "2016-01-01"
OOS_START = "2021-01-01"
OOS_END = "2025-12-31"

DYNAMIC = ROOT / "outputs/strategy/frozen/hist_oos/weekly_path.csv"
STATIC = (
    ROOT / "outputs/strategy/benchmarks/hist_oos/"
    "static_weekly_path.csv"
)

COST_BPS = 10.0
COST = COST_BPS / 10000


def load_universe():
    """Use the frozen eligible universe, without factor filters."""

    scores = cs.load_scores(
        ROOT, START_HISTORY, OOS_END, allow_oos=True
    )

    eligible = scores[
        scores["eligible_universe_flag"].eq(1)
    ].copy()

    eligible["PERMNO"] = eligible["PERMNO"].astype(str)

    if eligible.duplicated(["signal_date", "PERMNO"]).any():
        raise ValueError("Duplicate eligible stock-week")

    return eligible


def build_holdings(eligible):
    """Equal-weight all eligible stocks each signal week."""

    counts = eligible.groupby("signal_date")["PERMNO"].transform(
        "size"
    )

    holdings = eligible[
        ["signal_date", "execution_date", "PERMNO"]
    ].copy()

    holdings["weight"] = 1.0 / counts

    totals = holdings.groupby("signal_date")["weight"].sum()

    if not np.allclose(totals, 1.0, atol=1e-12):
        raise ValueError("Universe EW weights do not sum to one")

    return holdings


def build_path(holdings):
    """Run continuously from 2016, then extract OOS."""

    returns = rg.load_returns(
        ROOT, START_HISTORY, OOS_END
    )

    # Check each eligible security has a return record.
    keys = ["signal_date", "PERMNO"]

    coverage = holdings[keys].merge(
        returns[keys + ["usable"]],
        on=keys,
        how="left",
        validate="one_to_one"
    )

    missing_records = coverage["usable"].isna().sum()
    if missing_records:
        raise ValueError(
            f"{missing_records} eligible stock-weeks have "
            "no execution-return record"
        )

    path_all, ledger_all = mt.weekly_path_and_ledger(
        holdings,
        returns,
        OOS_END,
        COST
    )

    path_all["net_return"] = mt.net_returns(
        path_all, COST
    )

    def oos(df):
        return df[
            df["signal_date"].between(OOS_START, OOS_END)
        ].copy().reset_index(drop=True)

    return oos(path_all), oos(ledger_all), oos(holdings)


def check_alignment(path):
    dynamic = pd.read_csv(DYNAMIC)
    static = pd.read_csv(STATIC)

    for name, df in [
        ("Universe EW", path),
        ("Dynamic", dynamic),
        ("Static", static)
    ]:
        if df["signal_date"].duplicated().any():
            raise ValueError(f"{name}: duplicate dates")

    a = path[["signal_date", "period_end"]].copy()

    for name, other in [
        ("Dynamic", dynamic),
        ("Static", static)
    ]:
        merged = a.merge(
            other[["signal_date", "period_end"]],
            on="signal_date",
            how="outer",
            suffixes=("_ew", "_other"),
            indicator=True,
            validate="one_to_one"
        )

        if not merged["_merge"].eq("both").all():
            raise ValueError(f"{name}: missing weeks")

        if not merged["period_end_ew"].eq(
            merged["period_end_other"]
        ).all():
            raise ValueError(f"{name}: period mismatch")

    if len(path) != 259:
        raise ValueError(f"Expected 259 weeks, got {len(path)}")

    return dynamic, static


def acceptance(path, ledger, holdings):
    """Core accounting and portfolio invariants."""

    checks = []

    def add(name, passed, detail=""):
        checks.append({
            "check": name,
            "status": "PASS" if passed else "FAIL",
            "detail": detail
        })

    add("259 OOS weeks", len(path) == 259)

    add(
        "No duplicate holdings",
        not holdings.duplicated(
            ["signal_date", "PERMNO"]
        ).any()
    )

    sums = holdings.groupby("signal_date")["weight"].sum()
    add(
        "Weights sum to one",
        np.allclose(sums, 1, atol=1e-12)
    )

    gtr = (
        ledger.assign(abs_trade=ledger["trade_weight"].abs())
        .groupby("signal_date")["abs_trade"].sum()
    )

    actual = path.set_index("signal_date")["gtr"]
    err_gtr = (
        gtr.reindex(actual.index, fill_value=0) - actual
    ).abs().max()

    add("Ledger GTR closure", err_gtr < 1e-9,
        f"error={err_gtr:.3e}")

    err_cost = abs(
        ledger["cost"].sum() - COST * path["gtr"].sum()
    )

    add("Cost closure", err_cost < 1e-9,
        f"error={err_cost:.3e}")

    expected = (
        (1 - COST * path["gtr"])
        * (1 + path["gross_return"]) - 1
    )

    err_net = abs(expected - path["net_return"]).max()

    add("Gross-to-net closure", err_net < 1e-10,
        f"error={err_net:.3e}")

    add(
        "Finite returns",
        np.isfinite(
            path[["gross_return", "net_return", "gtr"]]
            .to_numpy()
        ).all()
    )

    return pd.DataFrame(checks)


def main():
    cfg = gv.load_config(ROOT)

    if cfg["status"] != "FROZEN":
        raise ValueError("Frozen configuration required")

    OUT.mkdir(parents=True, exist_ok=True)

    eligible = load_universe()
    holdings_all = build_holdings(eligible)

    path, ledger, holdings = build_path(holdings_all)

    dynamic, static = check_alignment(path)

    report = acceptance(path, ledger, holdings)

    if not report["status"].eq("PASS").all():
        print(report.to_string(index=False))
        raise ValueError("Universe EW acceptance failed")

    rf = rg.load_weekly_series(
        ROOT, rg.RF_FILE, "risk_free_return",
        OOS_START, OOS_END
    )

    bench = rg.load_weekly_series(
        ROOT, rg.BENCH_FILE, "benchmark_total_return",
        OOS_START, OOS_END
    )

    strategies = {
        "Universe EW": path.set_index("signal_date")["net_return"],
        "Static MF": static.set_index("signal_date")["net_return"],
        "Dynamic FMOM": dynamic.set_index("signal_date")["net_return"],
        "S&P 500": bench,
    }

    expected_dates = pd.Index(path["signal_date"])

    if not rf.index.is_unique or not bench.index.is_unique:
        raise ValueError("Duplicate benchmark or RF dates")

    if not rf.reindex(expected_dates).notna().all():
        raise ValueError("Missing risk-free returns")

    if not bench.reindex(expected_dates).notna().all():
        raise ValueError("Missing benchmark returns")

    rows = []

    fig, ax = plt.subplots(figsize=(11, 6))

    for name, series in strategies.items():
        r = series.reindex(expected_dates)

        if r.isna().any():
            raise ValueError(f"{name}: missing returns")

        perf = mt.performance(r, rf)

        rows.append({
            "strategy": name,
            "CAGR": perf["ann_return"],
            "Volatility": perf["ann_vol"],
            "Sharpe": perf["sharpe"],
            "Sortino": perf["sortino"],
            "Max_Drawdown": perf["max_drawdown"],
            "Calmar": perf["calmar"],
        })

        nav = (1 + r).cumprod()

        ax.plot(
            pd.to_datetime(expected_dates),
            nav.to_numpy(),
            label=name
        )

    ax.set_title("US OOS: Four-Strategy Comparison")
    ax.set_xlabel("Signal Date")
    ax.set_ylabel("Growth of $1")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()

    fig.savefig(
        OUT / "four_strategy_nav.png", dpi=200
    )
    plt.close(fig)

    summary = pd.DataFrame(rows)

    # Universe EW portfolio metrics.
    ew_perf = mt.performance(
        path.set_index("signal_date")["net_return"], rf
    )

    ew_summary = {
        **ew_perf,
        "avg_n_holdings": holdings.groupby(
            "signal_date"
        ).size().mean(),
        "annual_turnover": path["turnover"].mean() * 52,
        "cost_bps": COST_BPS,
        "n_weeks": len(path),
    }

    path.to_csv(
        OUT / "universe_ew_weekly_path.csv",
        index=False
    )
    holdings.to_csv(
        OUT / "universe_ew_holdings.csv",
        index=False
    )
    ledger.to_csv(
        OUT / "universe_ew_trade_ledger.csv",
        index=False
    )

    pd.DataFrame([ew_summary]).to_csv(
        OUT / "universe_ew_summary.csv", index=False
    )
    report.to_csv(
        OUT / "universe_ew_acceptance.csv", index=False
    )
    summary.to_csv(
        OUT / "four_strategy_comparison.csv", index=False
    )

    print("\n=== Universe EW Acceptance ===")
    print(report.to_string(index=False))

    print("\n=== Four Strategy Comparison ===")
    print(
        summary.to_string(
            index=False,
            float_format=lambda x: f"{x:.5f}"
        )
    )

    print("\nUniverse EW annual turnover:",
          f"{ew_summary['annual_turnover']:.4f}")

    print("Average eligible holdings:",
          f"{ew_summary['avg_n_holdings']:.1f}")

    print("\nResults saved to:", OUT)


if __name__ == "__main__":
    main()
