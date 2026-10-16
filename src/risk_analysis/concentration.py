
"""Member 5: US OOS Portfolio Concentration Analysis.

Run:
    python -m src.risk_analysis.concentration
"""

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/risk_analysis/us_hist_oos"

FILES = {
    "Dynamic FMOM": (
        ROOT / "outputs/strategy/frozen/hist_oos/"
        "target_holdings.csv"
    ),
    "Static MF": (
        ROOT / "outputs/strategy/benchmarks/hist_oos/"
        "static_target_holdings.csv"
    ),
    "Universe EW": OUT / "universe_ew_holdings.csv",
}

SECTOR_CAP = 0.30

GICS = {
    "10": "Energy",
    "15": "Materials",
    "20": "Industrials",
    "25": "Consumer Discretionary",
    "30": "Consumer Staples",
    "35": "Health Care",
    "40": "Financials",
    "45": "Information Technology",
    "50": "Communication Services",
    "55": "Utilities",
    "60": "Real Estate",
}


def load_holdings():
    frames = []

    completed = pd.read_csv(
        ROOT / "outputs/strategy/frozen/hist_oos/weekly_path.csv"
    )

    valid_dates = set(completed["signal_date"])

    for name, path in FILES.items():
        df = pd.read_csv(
            path,
            dtype={"PERMNO": str, "sector": str}
        )
        df = df[df["signal_date"].isin(valid_dates)].copy()

        required = {"signal_date", "PERMNO", "weight"}
        if not required.issubset(df.columns):
            raise ValueError(f"{name}: missing columns")

        if df.duplicated(
            ["signal_date", "PERMNO"]
        ).any():
            raise ValueError(f"{name}: duplicate holdings")

        if not np.isfinite(df["weight"]).all():
            raise ValueError(f"{name}: invalid weights")

        if (df["weight"] < 0).any():
            raise ValueError(f"{name}: negative weights")

        if "sector" not in df.columns:
            df["sector"] = pd.NA

        df["strategy"] = name
        frames.append(df)

    return pd.concat(frames, ignore_index=True)


def stock_concentration(df):
    rows = []

    for (strategy, date), g in df.groupby(
        ["strategy", "signal_date"]
    ):
        w = g["weight"].to_numpy(dtype=float)
        sorted_w = np.sort(w)[::-1]

        stock_hhi = np.sum(w ** 2)

        rows.append({
            "strategy": strategy,
            "signal_date": date,
            "n_holdings": len(g),
            "stock_weight_sum": w.sum(),
            "top5_weight": sorted_w[:5].sum(),
            "top10_weight": sorted_w[:10].sum(),
            "max_stock_weight": sorted_w[0],
            "stock_hhi": stock_hhi,
            "effective_n": 1 / stock_hhi
                           if stock_hhi > 0 else np.nan,
        })

    result = pd.DataFrame(rows)

    if not np.allclose(
        result["stock_weight_sum"], 1.0, atol=1e-10
    ):
        raise ValueError("Portfolio weights do not sum to one")

    return result


def sector_concentration(df):
    # Only strategies with sector information.
    available = df.dropna(subset=["sector"]).copy()

    available["sector"] = (
        available["sector"].astype(str).str.strip()
    )

    available["sector_name"] = (
        available["sector"].map(GICS)
        .fillna("Unknown")
    )

    sector_weights = (
        available.groupby(
            ["strategy", "signal_date", "sector_name"],
            as_index=False
        )["weight"].sum()
    )

    rows = []

    for (strategy, date), g in sector_weights.groupby(
        ["strategy", "signal_date"]
    ):
        w = g["weight"].to_numpy()

        rows.append({
            "strategy": strategy,
            "signal_date": date,
            "n_sectors": len(g),
            "max_sector_weight": w.max(),
            "sector_hhi": np.sum(w ** 2),
            "sector_cap_violation": int(
                strategy != "Universe EW"
                and w.max() > SECTOR_CAP + 1e-10
            ),
        })

    return sector_weights, pd.DataFrame(rows)


def summarize(stock, sector):
    summary = stock.groupby("strategy").agg(
        n_weeks=("signal_date", "nunique"),
        avg_holdings=("n_holdings", "mean"),
        min_holdings=("n_holdings", "min"),
        max_holdings=("n_holdings", "max"),
        avg_top5_weight=("top5_weight", "mean"),
        avg_top10_weight=("top10_weight", "mean"),
        avg_stock_hhi=("stock_hhi", "mean"),
        avg_effective_n=("effective_n", "mean"),
    ).reset_index()

    if not sector.empty:
        sector_summary = sector.groupby("strategy").agg(
            avg_max_sector_weight=(
                "max_sector_weight", "mean"
            ),
            avg_sector_hhi=("sector_hhi", "mean"),
            sector_cap_violations=(
                "sector_cap_violation", "sum"
            ),
        ).reset_index()

        summary = summary.merge(
            sector_summary, on="strategy", how="left"
        )

    return summary


def make_plots(stock, sector_weights):
    fig, ax = plt.subplots(figsize=(11, 5))

    for name, g in stock.groupby("strategy"):
        ax.plot(
            pd.to_datetime(g["signal_date"]),
            g["effective_n"],
            label=name
        )

    ax.set_title("US OOS: Effective Number of Holdings")
    ax.set_ylabel("Effective N")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(
        OUT / "effective_holdings.png", dpi=200
    )
    plt.close(fig)

    # Sector weight time series for Dynamic FMOM.
    dynamic = sector_weights[
        sector_weights["strategy"] == "Dynamic FMOM"
    ]

    if not dynamic.empty:
        wide = dynamic.pivot(
            index="signal_date",
            columns="sector_name",
            values="weight"
        ).fillna(0).sort_index()

        fig, ax = plt.subplots(figsize=(12, 6))

        ax.stackplot(
            pd.to_datetime(wide.index),
            *[wide[col] for col in wide.columns],
            labels=wide.columns
        )

        ax.set_title("Dynamic FMOM: Sector Weights")
        ax.set_ylabel("Portfolio Weight")
        ax.set_ylim(0, 1.05)
        ax.legend(
            loc="upper left",
            bbox_to_anchor=(1.01, 1),
            fontsize=8
        )
        fig.tight_layout()
        fig.savefig(
            OUT / "dynamic_sector_weights.png",
            dpi=200, bbox_inches="tight"
        )
        plt.close(fig)


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    holdings = load_holdings()

    stock = stock_concentration(holdings)
    sector_weights, sector = sector_concentration(holdings)
    summary = summarize(stock, sector)

    stock.to_csv(
        OUT / "concentration_weekly.csv", index=False
    )
    sector_weights.to_csv(
        OUT / "sector_weights_weekly.csv", index=False
    )
    sector.to_csv(
        OUT / "sector_concentration_weekly.csv",
        index=False
    )
    summary.to_csv(
        OUT / "concentration_summary.csv", index=False
    )

    make_plots(stock, sector_weights)

    print("\n=== Portfolio Concentration Summary ===")
    print(
        summary.to_string(
            index=False,
            float_format=lambda x: f"{x:.5f}"
        )
    )

    print("\nResults saved to:", OUT)


if __name__ == "__main__":
    main()
