
"""Member 5: Factor Momentum Failure Diagnosis.

Run:
    python -m src.risk_analysis.factor_timing_diagnostics
"""

from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/risk_analysis/us_hist_oos"
WEIGHTS = ROOT / "outputs/strategy/frozen/hist_oos/factor_weights.csv"

FAMILIES = ["Quality", "Momentum", "Low Risk", "Investment"]
K = len(FAMILIES)
ANNUAL = 52


def load_data():
    w = pd.read_csv(WEIGHTS)
    r = pd.read_csv(BASE / "oos_factor_returns_weekly.csv")

    if w.duplicated(["signal_date", "family"]).any():
        raise ValueError("Duplicate factor weights")
    if r.duplicated(["signal_date", "family"]).any():
        raise ValueError("Duplicate factor returns")

    cols = [
        "signal_date", "family", "factor_return_gross",
        "factor_return_available"
    ]

    df = w.merge(
        r[cols],
        on=["signal_date", "family"],
        how="inner",
        validate="one_to_one"
    )

    if len(df) != 259 * K:
        raise ValueError("Unexpected factor-week observations")

    df["valid"] = (
        df["factor_return_available"].eq(1)
        & np.isfinite(df["factor_return_gross"])
    )

    coverage = df.groupby("signal_date")["valid"].sum()
    complete_dates = coverage[coverage == K].index

    print("Total OOS weeks:", len(coverage))
    print("Complete-case weeks:", len(complete_dates))
    print("Incomplete weeks:", len(coverage) - len(complete_dates))

    incomplete = df[
        ~df["signal_date"].isin(complete_dates)
    ]
    incomplete.to_csv(
        BASE / "factor_timing_missing_weeks.csv",
        index=False
    )

    df = df[df["signal_date"].isin(complete_dates)].copy()

    if not np.allclose(
        df.groupby("signal_date")["weight"].sum(), 1.0
    ):
        raise ValueError("Factor weights do not sum to one")

    return df


def factor_performance(df):
    rows = []

    for family, g in df.groupby("family"):
        r = g["factor_return_gross"].to_numpy()

        rows.append({
            "family": family,
            "n_weeks": len(r),
            "annual_mean": np.mean(r) * ANNUAL,
            "annual_vol": np.std(r, ddof=1) * np.sqrt(ANNUAL),
            "cumulative_return": np.prod(1 + r) - 1,
            "positive_week_fraction": np.mean(r > 0),
        })

    return pd.DataFrame(rows)


def predictive_tests(df):
    rows = []

    for date, g in df.groupby("signal_date"):
        signal = g["fmom_signal"].to_numpy(dtype=float)
        future = g["factor_return_gross"].to_numpy(dtype=float)

        # Spearman cross-sectional correlation.
        ic = spearmanr(signal, future).statistic

        # Deterministic tie-breaking for winner/loser.
        ranked = g.sort_values(
            ["fmom_signal", "family"],
            ascending=[False, True]
        )

        winner = ranked.iloc[0]
        loser = ranked.iloc[-1]

        future_best = g["factor_return_gross"].max()

        rows.append({
            "signal_date": date,
            "rank_ic": ic,
            "winner": winner["family"],
            "loser": loser["family"],
            "winner_return": winner["factor_return_gross"],
            "loser_return": loser["factor_return_gross"],
            "winner_hit": int(
                np.isclose(
                    winner["factor_return_gross"],
                    future_best
                )
            ),
            "top_minus_bottom": (
                winner["factor_return_gross"]
                - loser["factor_return_gross"]
            ),
        })

    return pd.DataFrame(rows)


def timing_increment(df):
    rows = []

    for date, g in df.groupby("signal_date"):
        r = g["factor_return_gross"].to_numpy(dtype=float)
        w = g["weight"].to_numpy(dtype=float)

        dynamic = float(np.dot(w, r))
        equal = float(np.mean(r))

        # Separate the pure FMOM sleeve from the 50/50 blend.
        pure = float(np.dot(
            g["w_fmom"].to_numpy(dtype=float), r
        ))

        rows.append({
            "signal_date": date,
            "dynamic_factor_return": dynamic,
            "equal_factor_return": equal,
            "pure_fmom_factor_return": pure,
            "timing_increment": dynamic - equal,
            "pure_fmom_increment": pure - equal,
            "dynamic_status": g["dynamic_status"].iloc[0],
            "max_factor_weight": float(g["weight"].max()),
        })

    result = pd.DataFrame(rows).sort_values("signal_date")

    result["cum_timing_increment"] = (
        result["timing_increment"].cumsum()
    )

    # Diagnostic compounded wealth, not a tradable NAV.
    result["dynamic_factor_nav"] = (
        1 + result["dynamic_factor_return"]
    ).cumprod()

    result["equal_factor_nav"] = (
        1 + result["equal_factor_return"]
    ).cumprod()

    return result


def make_plots(df, timing):
    wide = df.pivot(
        index="signal_date",
        columns="family",
        values="weight"
    )[FAMILIES]

    fig, ax = plt.subplots(figsize=(12, 5))
    im = ax.imshow(
        wide.T.to_numpy(),
        aspect="auto",
        interpolation="nearest",
        vmin=0,
        vmax=0.625,
        cmap="viridis"
    )

    ax.set_yticks(range(K))
    ax.set_yticklabels(FAMILIES)

    positions = np.linspace(0, len(wide) - 1, 6).astype(int)
    ax.set_xticks(positions)
    ax.set_xticklabels(
        pd.to_datetime(wide.index[positions]).strftime("%Y-%m")
    )

    ax.set_title("Dynamic Factor Weight Heatmap")
    fig.colorbar(im, ax=ax, label="Factor Weight")
    fig.tight_layout()
    fig.savefig(BASE / "factor_weight_heatmap.png", dpi=200)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 5))
    dates = pd.to_datetime(timing["signal_date"])

    ax.plot(
        dates,
        timing["cum_timing_increment"]
    )
    ax.axhline(0, linestyle="--", linewidth=0.8)
    ax.set_title("Cumulative Factor Timing Increment")
    ax.set_ylabel("Cumulative Arithmetic Return Difference")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(BASE / "factor_timing_increment.png", dpi=200)
    plt.close(fig)


def main():
    BASE.mkdir(parents=True, exist_ok=True)

    df = load_data()

    perf = factor_performance(df)
    pred = predictive_tests(df)
    timing = timing_increment(df)

    perf.to_csv(BASE / "factor_oos_performance.csv", index=False)
    pred.to_csv(BASE / "factor_predictive_tests.csv", index=False)
    timing.to_csv(BASE / "factor_timing_weekly.csv", index=False)

    make_plots(df, timing)

    print("\n=== Factor Performance ===")
    print(perf.to_string(index=False))

    print("\n=== FMOM Predictive Ability ===")
    print("Mean Rank IC:", pred["rank_ic"].mean())
    print("Median Rank IC:", pred["rank_ic"].median())
    print("Winner Hit Rate:", pred["winner_hit"].mean())
    print(
        "Annualized Top-Bottom:",
        pred["top_minus_bottom"].mean() * ANNUAL
    )

    print("\n=== Factor-Level Timing ===")
    print(
        "Annualized Timing Increment:",
        timing["timing_increment"].mean() * ANNUAL
    )
    print(
        "Annualized Pure FMOM Increment:",
        timing["pure_fmom_increment"].mean() * ANNUAL
    )
    print(
        "Average Maximum Factor Weight:",
        timing["max_factor_weight"].mean()
    )

    print("\n=== Timing Increment by Year ===")
    timing["year"] = pd.to_datetime(
        timing["signal_date"]
    ).dt.year

    annual = timing.groupby("year").agg(
        n_weeks=("timing_increment", "size"),
        mean_increment=("timing_increment", "mean"),
        total_increment=("timing_increment", "sum"),
    )
    annual["annualized_mean"] = annual["mean_increment"] * ANNUAL
    print(annual.to_string())

    print("\nResults saved to:", BASE)


if __name__ == "__main__":
    main()
