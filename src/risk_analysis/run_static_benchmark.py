
"""Member 5: Frozen-rule Static Multi-Factor OOS Benchmark.

Only factor weighting differs from the Dynamic FMOM strategy.
Run:
    python -m src.risk_analysis.run_static_benchmark
"""

from pathlib import Path
import json

import numpy as np
import pandas as pd

from src.strategy import composite_score as cs
from src.strategy import portfolio as pf
from src.strategy import run_gate_b as rg
from src.strategy import metrics as mt
from src.strategy import governance as gv
from src.strategy import fmom_weights as fw


ROOT = Path(__file__).resolve().parents[2]

START_HISTORY = "2016-01-01"
OOS_START = "2021-01-01"
OOS_END = "2025-12-31"

OUT = ROOT / "outputs/strategy/benchmarks/hist_oos"

DYNAMIC_FILE = (
    ROOT / "outputs/strategy/frozen/hist_oos/weekly_path.csv"
)


def main():
    # 1. Read the frozen strategy configuration.
    cfg = gv.load_config(ROOT)
    if cfg["status"] != "FROZEN":
        raise ValueError("US Base configuration is not frozen")

    p = cfg["parameters"]

    if p["min_available_factors"] != 4:
        raise ValueError("Unexpected minimum factor count")

    if not p["buffer"]:
        raise ValueError("Frozen strategy requires buffer")

    cost = cfg["transaction_cost"]["oneway_bps"] / 10000

    # 2. Read the same stock factor scores as Dynamic FMOM.
    scores = cs.load_scores(
        ROOT, START_HISTORY, OOS_END, allow_oos=True
    )

    # 3. Construct static equal factor weights.
    # This is the ONLY strategy rule that differs.
    dates = sorted(scores["signal_date"].unique())

    static_weights = pd.DataFrame({
        "signal_date": dates
    })

    for col in cs.WEIGHT_COLS:
        static_weights[col] = 1.0 / fw.K

    # 4. Use Member 4's stock scoring function.
    scored = cs.compute_scores(
        scores,
        static_weights,
        p["min_available_factors"]
    )

    # 5. Build holdings continuously from 2016.
    # Buffer must retain its historical state.
    holdings_all, construction_all = pf.build_holdings(
        scored, use_buffer=p["buffer"]
    )

    # 6. Load the identical executable stock returns.
    returns = rg.load_returns(
        ROOT, START_HISTORY, OOS_END
    )

    # 7. Use the shared portfolio accounting engine.
    path_all, ledger_all = mt.weekly_path_and_ledger(
        holdings_all, returns, OOS_END, cost
    )

    path_all["net_return"] = mt.net_returns(
        path_all, cost
    )

    # 8. Extract only the formal historical OOS.
    def oos(df):
        return df[
            df["signal_date"].between(OOS_START, OOS_END)
        ].copy().reset_index(drop=True)

    holdings = oos(holdings_all)
    construction = oos(construction_all)
    path = oos(path_all)
    ledger = oos(ledger_all)

    # 9. Match exactly the Dynamic FMOM evaluation periods.
    dynamic = pd.read_csv(DYNAMIC_FILE)

    if dynamic["signal_date"].duplicated().any():
        raise ValueError("Duplicate Dynamic signal dates")

    if path["signal_date"].duplicated().any():
        raise ValueError("Duplicate Static signal dates")

    if set(path["signal_date"]) != set(dynamic["signal_date"]):
        missing = set(dynamic["signal_date"]) - set(path["signal_date"])
        extra = set(path["signal_date"]) - set(dynamic["signal_date"])
        raise ValueError(
            f"OOS weeks differ. Missing={sorted(missing)[:5]}, "
            f"Extra={sorted(extra)[:5]}"
        )

    aligned = path.merge(
        dynamic[["signal_date", "period_end"]],
        on="signal_date",
        suffixes=("_static", "_dynamic"),
        validate="one_to_one"
    )

    if not (
        aligned["period_end_static"]
        == aligned["period_end_dynamic"]
    ).all():
        raise ValueError("Static/Dynamic holding periods differ")

    # 10. Check accounting.
    expected_net = (
        (1 - cost * path["gtr"])
        * (1 + path["gross_return"]) - 1
    )

    if not np.allclose(
        expected_net, path["net_return"], atol=1e-12
    ):
        raise ValueError("Static net return accounting failed")

    if not np.allclose(
        path["turnover"], path["gtr"] / 2, atol=1e-12
    ):
        raise ValueError("Static turnover convention failed")

    # 11. Save outputs.
    OUT.mkdir(parents=True, exist_ok=True)

    path.to_csv(OUT / "static_weekly_path.csv", index=False)
    holdings.to_csv(
        OUT / "static_target_holdings.csv", index=False
    )
    ledger.to_csv(
        OUT / "static_trade_ledger.csv", index=False
    )
    construction.to_csv(
        OUT / "static_construction.csv", index=False
    )

    # 12. Save a simple benchmark provenance record.
    manifest = {
        "benchmark": "STATIC_EQUAL_FACTOR_K4",
        "role": "HIST_OOS_BENCHMARK",
        "base_strategy": cfg["strategy_id"],
        "base_config_version": cfg["config_version"],
        "factor_weighting": "equal",
        "lambda": 0.0,
        "min_available_factors": p["min_available_factors"],
        "buffer": p["buffer"],
        "cost_oneway_bps": cfg["transaction_cost"]["oneway_bps"],
        "history_start": START_HISTORY,
        "oos_start": OOS_START,
        "oos_end": OOS_END,
        "n_return_weeks": len(path),
    }

    with open(OUT / "static_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    # 13. Display preliminary results.
    rf = rg.load_weekly_series(
        ROOT, rg.RF_FILE, "risk_free_return",
        OOS_START, OOS_END
    )

    static_returns = path.set_index("signal_date")["net_return"]

    perf = mt.performance(static_returns, rf)

    print("\n=== Static Multi-Factor OOS ===")
    print("Number of weeks:", len(path))
    print("Annualized Return:", f"{perf['ann_return']:.4%}")
    print("Annual Volatility:", f"{perf['ann_vol']:.4%}")
    print("Sharpe:", f"{perf['sharpe']:.4f}")
    print("Max Drawdown:", f"{perf['max_drawdown']:.4%}")
    print(
        "Annual Turnover:",
        f"{path['turnover'].mean() * 52:.4f}"
    )
    print("\nStatic benchmark completed.")
    print("Outputs:", OUT)


if __name__ == "__main__":
    main()
