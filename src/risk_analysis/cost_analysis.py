
"""Member 5: US OOS transaction cost sensitivity.

Uses frozen gross returns and realized GTR.
Does not change holdings or strategy parameters.

Run:
    python -m src.risk_analysis.cost_analysis
"""

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from src.strategy import metrics as mt


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/risk_analysis/us_hist_oos"

FILES = {
    "Dynamic FMOM": (
        ROOT / "outputs/strategy/frozen/hist_oos/weekly_path.csv"
    ),
    "Static MF": (
        ROOT / "outputs/strategy/benchmarks/hist_oos/"
        "static_weekly_path.csv"
    ),
    "Universe EW": OUT / "universe_ew_weekly_path.csv",
}

COST_GRID_BPS = [0, 10, 20, 30]
BASE_COST_BPS = 10
WEEKS_PER_YEAR = 52


def load_paths():
    paths = {}

    for name, file in FILES.items():
        df = pd.read_csv(file)
        df = df.sort_values("signal_date").reset_index(drop=True)

        if len(df) != 259:
            raise ValueError(f"{name}: expected 259 weeks")

        if df["signal_date"].duplicated().any():
            raise ValueError(f"{name}: duplicate dates")

        if not np.isfinite(
            df[["gross_return", "gtr"]].to_numpy()
        ).all():
            raise ValueError(f"{name}: nonfinite values")

        if (df["gross_return"] <= -1).any():
            raise ValueError(f"{name}: invalid gross return")

        if (df["gtr"] < 0).any():
            raise ValueError(f"{name}: negative GTR")

        paths[name] = df

    ref = paths["Dynamic FMOM"][
        ["signal_date", "period_end"]
    ].reset_index(drop=True)

    for name, df in paths.items():
        dates = df[
            ["signal_date", "period_end"]
        ].reset_index(drop=True)

        if not dates.equals(ref):
            raise ValueError(f"{name}: period mismatch")

    return paths


def calculate(path, cost_bps):
    cost = cost_bps / 10000

    net = mt.net_returns(path, cost)

    nav = (1 + net).cumprod()

    n = len(net)

    return {
        "cost_bps": cost_bps,
        "CAGR": nav.iloc[-1] ** (WEEKS_PER_YEAR / n) - 1,
        "annual_volatility": net.std(ddof=1)
                             * np.sqrt(WEEKS_PER_YEAR),
        "max_drawdown": float(
            -(
                nav / np.maximum(1.0, nav.cummax()) - 1
            ).min()
        ),
        "ending_nav": nav.iloc[-1],
        "annual_turnover": path["turnover"].mean()
                           * WEEKS_PER_YEAR,
        "annual_direct_cost": (
            cost * path["gtr"].mean() * WEEKS_PER_YEAR
        ),
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    paths = load_paths()
    rows = []
    breakeven_rows = []
    nav_rows = []

    for name, path in paths.items():

        # Verify that the frozen 10bp path is reproduced.
        reproduced = mt.net_returns(
            path, BASE_COST_BPS / 10000
        )

        error = np.max(
            np.abs(reproduced - path["net_return"])
        )

        if error > 1e-10:
            raise ValueError(
                f"{name}: base-cost closure failed: {error}"
            )

        breakeven = mt.break_even_cost(path)

        breakeven_rows.append({
            "strategy": name,
            "breakeven_cost_bps": (
                breakeven * 10000
                if np.isfinite(breakeven) else np.nan
            ),
            "base_cost_closure_error": error,
        })

        for bps in COST_GRID_BPS:
            result = calculate(path, bps)
            result["strategy"] = name
            rows.append(result)

            net = mt.net_returns(path, bps)
            nav = (1 + net).cumprod()

            nav_rows.extend([
                {
                    "signal_date": date,
                    "strategy": name,
                    "cost_bps": bps,
                    "nav": value,
                }
                for date, value in zip(
                    path["signal_date"], nav
                )
            ])

    summary = pd.DataFrame(rows)
    breakeven_df = pd.DataFrame(breakeven_rows)
    nav_df = pd.DataFrame(nav_rows)

    summary.to_csv(
        OUT / "cost_sensitivity_summary.csv",
        index=False
    )

    breakeven_df.to_csv(
        OUT / "breakeven_cost.csv",
        index=False
    )

    nav_df.to_csv(
        OUT / "cost_sensitivity_nav.csv",
        index=False
    )

    # Plot CAGR sensitivity.
    fig, ax = plt.subplots(figsize=(10, 6))

    for name, group in summary.groupby("strategy"):
        group = group.sort_values("cost_bps")
        ax.plot(
            group["cost_bps"],
            group["CAGR"] * 100,
            marker="o",
            label=name
        )

    ax.axvline(
        BASE_COST_BPS,
        linestyle="--",
        linewidth=1,
        label="Base Cost: 10bp"
    )

    ax.set_title("US OOS: Transaction Cost Sensitivity")
    ax.set_xlabel("One-Way Transaction Cost (bps)")
    ax.set_ylabel("CAGR (%)")
    ax.grid(alpha=0.3)
    ax.legend()

    fig.tight_layout()
    fig.savefig(
        OUT / "cost_sensitivity_cagr.png",
        dpi=200
    )
    plt.close(fig)

    print("\n=== Cost Sensitivity ===")
    print(
        summary[
            ["strategy", "cost_bps", "CAGR",
             "annual_volatility", "max_drawdown",
             "annual_direct_cost"]
        ].to_string(
            index=False,
            float_format=lambda x: f"{x:.5f}"
        )
    )

    print("\n=== Break-Even Cost ===")
    print(
        breakeven_df.to_string(
            index=False,
            float_format=lambda x: f"{x:.5f}"
        )
    )

    print("\nAll base-cost closure checks PASSED.")
    print("Results saved to:", OUT)


if __name__ == "__main__":
    main()
