
"""Member 5: US OOS Market-Cap and Sector Analysis.

Run:
    python -m src.risk_analysis.market_cap_analysis

Read-only diagnostic: frozen holdings are never modified.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/risk_analysis/us_hist_oos"

LIQUIDITY = (
    ROOT / "handoff/us_downstream/data/"
    "execution_liquidity_weekly.parquet"
)

PATH = (
    ROOT / "outputs/strategy/frozen/hist_oos/"
    "weekly_path.csv"
)

HOLDINGS = {
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

# CRSP DlyCap is in thousands of USD.
CAP_SCALE = 1000.0

CAP_LABELS = [
    "Small (<2B)",
    "Mid (2-10B)",
    "Large (10-200B)",
    "Mega (>=200B)",
]

CAP_BINS = [0, 2e9, 10e9, 200e9, np.inf]

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


def weighted_median(values, weights):
    order = np.argsort(values)
    v = np.asarray(values)[order]
    w = np.asarray(weights)[order]
    return float(v[np.searchsorted(
        np.cumsum(w), w.sum() / 2
    )])


def load_data():
    valid_dates = set(
        pd.read_csv(PATH)["signal_date"]
    )

    if len(valid_dates) != 259:
        raise ValueError("Expected 259 completed OOS weeks")

    liq = pd.read_parquet(
        LIQUIDITY,
        columns=[
            "signal_date", "execution_date", "PERMNO",
            "issuer_market_cap",
            "issuer_market_cap_available",
            "historical_gics_sector",
        ],
    )

    liq = liq[
        liq["signal_date"].isin(valid_dates)
    ].copy()

    liq["PERMNO"] = liq["PERMNO"].astype(str)

    keys = ["signal_date", "PERMNO"]

    if liq.duplicated(keys).any():
        raise ValueError("Duplicate market-data keys")

    liq["market_cap_usd"] = np.where(
        liq["issuer_market_cap_available"].eq(1)
        & (liq["issuer_market_cap"] > 0)
        & np.isfinite(liq["issuer_market_cap"]),
        liq["issuer_market_cap"] * CAP_SCALE,
        np.nan,
    )

    liq["sector_code"] = (
        liq["historical_gics_sector"]
        .astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )

    liq["sector_name"] = liq["sector_code"].map(GICS)

    frames = []

    for name, path in HOLDINGS.items():
        h = pd.read_csv(path, dtype={"PERMNO": str})

        h = h[
            h["signal_date"].isin(valid_dates)
        ].copy()

        if h.duplicated(keys).any():
            raise ValueError(f"{name}: duplicate holdings")

        if h["signal_date"].nunique() != 259:
            raise ValueError(f"{name}: missing OOS weeks")

        h = h[
            ["signal_date", "execution_date", "PERMNO", "weight"]
        ].copy()

        h = h.merge(
            liq[keys + [
                "execution_date", "market_cap_usd",
                "sector_name"
            ]],
            on=keys,
            how="left",
            suffixes=("", "_source"),
            validate="one_to_one",
            indicator=True,
        )

        if not h.loc[
            h["_merge"].eq("both"),
            "execution_date"
        ].eq(
            h.loc[
                h["_merge"].eq("both"),
                "execution_date_source"
            ]
        ).all():
            raise ValueError(f"{name}: execution mismatch")

        h["source_matched"] = h["_merge"].eq("both")
        h = h.drop(
            columns=["_merge", "execution_date_source"]
        )

        h["strategy"] = name

        frames.append(h)

    return pd.concat(frames, ignore_index=True)


def calculate_coverage(df):
    rows = []

    for name, g in df.groupby("strategy"):
        total_weight = g["weight"].sum()

        cap_ok = g["market_cap_usd"].notna()
        sector_ok = g["sector_name"].notna()

        rows.append({
            "strategy": name,
            "n_holdings_rows": len(g),
            "source_match_rate": g["source_matched"].mean(),
            "cap_row_coverage": cap_ok.mean(),
            "cap_weight_coverage":
                g.loc[cap_ok, "weight"].sum() / total_weight,
            "sector_row_coverage": sector_ok.mean(),
            "sector_weight_coverage":
                g.loc[sector_ok, "weight"].sum() / total_weight,
        })

    return pd.DataFrame(rows)


def market_cap_analysis(df):
    valid = df.dropna(subset=["market_cap_usd"]).copy()

    valid["cap_group"] = pd.cut(
        valid["market_cap_usd"],
        bins=CAP_BINS,
        labels=CAP_LABELS,
        right=False,
    )

    weekly_rows = []
    group_rows = []

    for (name, date), g in valid.groupby(
        ["strategy", "signal_date"]
    ):
        w = g["weight"].to_numpy()
        cap = g["market_cap_usd"].to_numpy()

        weekly_rows.append({
            "strategy": name,
            "signal_date": date,
            "weighted_mean_cap_usd":
                float(np.average(cap, weights=w)),
            "weighted_median_cap_usd":
                weighted_median(cap, w),
            "median_stock_cap_usd":
                float(np.median(cap)),
            "valid_cap_weight": float(w.sum()),
        })

        for label in CAP_LABELS:
            weight = g.loc[
                g["cap_group"].eq(label), "weight"
            ].sum()

            group_rows.append({
                "strategy": name,
                "signal_date": date,
                "cap_group": label,
                "portfolio_weight": float(weight),
            })

    return (
        pd.DataFrame(weekly_rows),
        pd.DataFrame(group_rows),
    )


def sector_analysis(df):
    valid = df.dropna(subset=["sector_name"]).copy()

    sectors = valid.groupby(
        ["strategy", "signal_date", "sector_name"],
        as_index=False,
    )["weight"].sum()

    rows = []

    for (name, date), g in sectors.groupby(
        ["strategy", "signal_date"]
    ):
        w = g["weight"].to_numpy()

        rows.append({
            "strategy": name,
            "signal_date": date,
            "sector_hhi": float(np.sum(w ** 2)),
            "max_sector_weight": float(w.max()),
            "known_sector_weight": float(w.sum()),
            "n_sectors": len(g),
        })

    return sectors, pd.DataFrame(rows)


def make_plots(cap_groups, weekly_cap, sectors):
    # Average portfolio allocation across cap groups.
    avg = cap_groups.groupby(
        ["strategy", "cap_group"],
        observed=True,
    )["portfolio_weight"].mean().unstack(fill_value=0)

    avg = avg.reindex(columns=CAP_LABELS, fill_value=0)

    ax = (avg * 100).plot(
        kind="bar", stacked=True, figsize=(11, 6)
    )

    ax.set_title("US OOS: Market-Cap Distribution")
    ax.set_ylabel("Average Portfolio Weight (%)")
    ax.set_xlabel("")
    ax.legend(title="Market-Cap Group")
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig(
        OUT / "market_cap_distribution.png", dpi=200
    )
    plt.close()

    # Weighted median cap over time.
    fig, ax = plt.subplots(figsize=(11, 5))

    for name, g in weekly_cap.groupby("strategy"):
        ax.plot(
            pd.to_datetime(g["signal_date"]),
            g["weighted_median_cap_usd"] / 1e9,
            label=name,
        )

    ax.set_title("US OOS: Weighted Median Issuer Cap")
    ax.set_ylabel("USD Billions")
    ax.legend()
    ax.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(
        OUT / "weighted_median_market_cap.png", dpi=200
    )
    plt.close(fig)

    # Average sector weights.
    avg_sector = sectors.groupby(
        ["strategy", "sector_name"]
    )["weight"].mean().unstack(fill_value=0)

    ax = (avg_sector * 100).T.plot(
        kind="bar", figsize=(12, 6)
    )

    ax.set_title("US OOS: Average Sector Exposure")
    ax.set_ylabel("Average Portfolio Weight (%)")
    ax.set_xlabel("")
    ax.legend(title="Strategy")
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig(
        OUT / "sector_exposure_comparison.png",
        dpi=200,
    )
    plt.close()


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    data = load_data()
    coverage = calculate_coverage(data)

    weekly_cap, cap_groups = market_cap_analysis(data)
    sectors, sector_conc = sector_analysis(data)

    cap_summary = weekly_cap.groupby("strategy").agg(
        avg_weighted_mean_cap_usd=(
            "weighted_mean_cap_usd", "mean"
        ),
        avg_weighted_median_cap_usd=(
            "weighted_median_cap_usd", "mean"
        ),
        avg_median_stock_cap_usd=(
            "median_stock_cap_usd", "mean"
        ),
    ).reset_index()

    sector_summary = sector_conc.groupby("strategy").agg(
        avg_sector_hhi=("sector_hhi", "mean"),
        avg_max_sector_weight=("max_sector_weight", "mean"),
    ).reset_index()

    summary = cap_summary.merge(
        sector_summary, on="strategy", how="left"
    ).merge(
        coverage, on="strategy", how="left"
    )

    data.to_csv(
        OUT / "holdings_with_market_data.csv", index=False
    )
    coverage.to_csv(
        OUT / "market_data_coverage.csv", index=False
    )
    weekly_cap.to_csv(
        OUT / "market_cap_weekly.csv", index=False
    )
    cap_groups.to_csv(
        OUT / "market_cap_groups_weekly.csv", index=False
    )
    sectors.to_csv(
        OUT / "all_strategy_sector_weights.csv", index=False
    )
    sector_conc.to_csv(
        OUT / "all_strategy_sector_concentration.csv",
        index=False
    )
    summary.to_csv(
        OUT / "market_cap_sector_summary.csv", index=False
    )

    make_plots(cap_groups, weekly_cap, sectors)

    print("\n=== Market Data Coverage ===")
    print(coverage.to_string(index=False))

    print("\n=== Market Cap & Sector Summary ===")
    print(
        summary.to_string(
            index=False,
            float_format=lambda x: f"{x:.5f}"
        )
    )

    print("\n=== Average Market-Cap Group Weights ===")
    print(
        cap_groups.groupby(
            ["strategy", "cap_group"],
            observed=True,
        )["portfolio_weight"]
        .mean()
        .unstack(fill_value=0)
        .to_string(float_format=lambda x: f"{x:.4%}")
    )

    print("\nResults saved to:", OUT)


if __name__ == "__main__":
    main()
