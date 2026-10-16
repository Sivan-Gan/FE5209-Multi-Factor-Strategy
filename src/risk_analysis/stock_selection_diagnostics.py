
"""Member 5: Stock-level transmission diagnosis.

Decompose Dynamic - Static gross return differences into
security and sector contributions, then reconcile net returns.

Run:
    python -m src.risk_analysis.stock_selection_diagnostics
"""

from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from src.strategy import run_gate_b as rg

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/risk_analysis/us_hist_oos"

DYNAMIC = ROOT / "outputs/strategy/frozen/hist_oos"
STATIC = ROOT / "outputs/strategy/benchmarks/hist_oos"

START = "2021-01-01"
END = "2025-12-31"
COST = 0.001
TOL = 1e-9


def load_data():
    d = pd.read_csv(
        DYNAMIC / "target_holdings.csv",
        dtype={"PERMNO": str, "sector": str}
    )
    s = pd.read_csv(
        STATIC / "static_target_holdings.csv",
        dtype={"PERMNO": str, "sector": str}
    )

    dp = pd.read_csv(DYNAMIC / "weekly_path.csv")
    sp = pd.read_csv(STATIC / "static_weekly_path.csv")

    if d.duplicated(["signal_date", "PERMNO"]).any():
        raise ValueError("Duplicate Dynamic holdings")
    if s.duplicated(["signal_date", "PERMNO"]).any():
        raise ValueError("Duplicate Static holdings")

    if dp["signal_date"].duplicated().any() or \
       sp["signal_date"].duplicated().any():
        raise ValueError("Duplicate weekly paths")

    path = dp.merge(
        sp, on="signal_date", how="outer",
        suffixes=("_d", "_s"),
        indicator=True, validate="one_to_one"
    )

    if not path["_merge"].eq("both").all():
        raise ValueError("Dynamic/Static periods differ")

    if not path["period_end_d"].eq(path["period_end_s"]).all():
        raise ValueError("Holding period end mismatch")

    path = path.drop(columns="_merge")

    # Only fully completed OOS return periods.
    dates = set(path["signal_date"])
    d = d[d["signal_date"].isin(dates)].copy()
    s = s[s["signal_date"].isin(dates)].copy()

    returns = rg.load_returns(ROOT, START, END)

    return d, s, path, returns


def stock_contributions(d, s, returns):
    keys = ["signal_date", "PERMNO"]

    d = d[keys + ["weight", "sector"]].rename(
        columns={"weight": "weight_d", "sector": "sector_d"}
    )
    s = s[keys + ["weight", "sector"]].rename(
        columns={"weight": "weight_s", "sector": "sector_s"}
    )

    m = d.merge(
        s, on=keys, how="outer",
        validate="one_to_one"
    )

    m["held_d"] = m["weight_d"].notna()
    m["held_s"] = m["weight_s"].notna()

    m["category"] = np.select(
        [
            m["held_d"] & m["held_s"],
            m["held_d"] & ~m["held_s"],
            ~m["held_d"] & m["held_s"],
        ],
        ["Common", "Dynamic Only", "Static Only"],
        default="ERROR"
    )

    # Ensure shared securities have consistent sector labels.
    both = m["held_d"] & m["held_s"]
    mismatch = (
        m.loc[both, "sector_d"].fillna("UNKNOWN")
        != m.loc[both, "sector_s"].fillna("UNKNOWN")
    )
    if mismatch.any():
        raise ValueError("Sector labels disagree across portfolios")

    m["sector"] = (
        m["sector_d"].combine_first(m["sector_s"])
        .fillna("UNKNOWN")
    )

    m["weight_d"] = m["weight_d"].fillna(0.0)
    m["weight_s"] = m["weight_s"].fillna(0.0)

    if m["category"].eq("ERROR").any():
        raise ValueError("Invalid holding category")

    m = m.merge(
        returns[
            keys + [
                "execution_to_next_execution_total_return",
                "usable"
            ]
        ],
        on=keys, how="left", validate="many_to_one"
    )

    usable = m["usable"].fillna(0).eq(1)

    m["stock_return"] = np.where(
        usable,
        m["execution_to_next_execution_total_return"],
        0.0
    )

    if not np.isfinite(m["stock_return"]).all():
        raise ValueError("Invalid stock return")

    m["weight_difference"] = (
        m["weight_d"] - m["weight_s"]
    )

    m["dynamic_contribution"] = (
        m["weight_d"] * m["stock_return"]
    )
    m["static_contribution"] = (
        m["weight_s"] * m["stock_return"]
    )
    m["increment_contribution"] = (
        m["dynamic_contribution"]
        - m["static_contribution"]
    )

    return m


def weekly_analysis(m, path):
    # Stock-level gross return reconciliation.
    weekly = m.groupby("signal_date").agg(
        gross_dynamic_rebuilt=("dynamic_contribution", "sum"),
        gross_static_rebuilt=("static_contribution", "sum"),
        gross_increment_rebuilt=("increment_contribution", "sum"),
        n_dynamic=("held_d", "sum"),
        n_static=("held_s", "sum"),
    ).reset_index()

    common = (
        m[m["category"] == "Common"]
        .groupby("signal_date").size()
    )
    weekly["n_common"] = (
        weekly["signal_date"].map(common).fillna(0).astype(int)
    )

    weekly["overlap"] = (
        weekly["n_common"]
        / weekly[["n_dynamic", "n_static"]].min(axis=1)
    )
    weekly["jaccard"] = (
        weekly["n_common"]
        / (
            weekly["n_dynamic"]
            + weekly["n_static"]
            - weekly["n_common"]
        )
    )

    cat = m.pivot_table(
        index="signal_date", columns="category",
        values="increment_contribution",
        aggfunc="sum", fill_value=0
    )

    for name in ["Common", "Dynamic Only", "Static Only"]:
        weekly[name.lower().replace(" ", "_") + "_contribution"] = (
            weekly["signal_date"]
            .map(cat[name] if name in cat else pd.Series(dtype=float))
            .fillna(0.0)
        )

    weekly = weekly.merge(
        path, on="signal_date",
        validate="one_to_one"
    )

    for suffix, name in [("d", "dynamic"), ("s", "static")]:
        error = (
            weekly[f"gross_{name}_rebuilt"]
            - weekly[f"gross_return_{suffix}"]
        ).abs().max()

        if error > TOL:
            raise ValueError(
                f"{name} gross return reconciliation failed: {error}"
            )

    weekly["gross_increment"] = (
        weekly["gross_return_d"]
        - weekly["gross_return_s"]
    )

    error = (
        weekly["gross_increment_rebuilt"]
        - weekly["gross_increment"]
    ).abs().max()

    if error > TOL:
        raise ValueError(f"Increment closure failed: {error}")

    weekly["net_increment"] = (
        weekly["net_return_d"] - weekly["net_return_s"]
    )

    # Exact transaction-cost effect under frozen accounting.
    weekly["cost_effect"] = -COST * (
        weekly["gtr_d"] * (1 + weekly["gross_return_d"])
        - weekly["gtr_s"] * (1 + weekly["gross_return_s"])
    )

    error = (
        weekly["gross_increment"] + weekly["cost_effect"]
        - weekly["net_increment"]
    ).abs().max()

    if error > TOL:
        raise ValueError(f"Net return closure failed: {error}")

    weekly["signal_year"] = pd.to_datetime(
        weekly["signal_date"]
    ).dt.year

    return weekly


def sector_analysis(m, weekly):
    sectors = m.groupby(
        ["signal_date", "sector"], as_index=False
    ).agg(
        dynamic_weight=("weight_d", "sum"),
        static_weight=("weight_s", "sum"),
        increment_contribution=("increment_contribution", "sum")
    )

    sectors["weight_difference"] = (
        sectors["dynamic_weight"] - sectors["static_weight"]
    )

    closure = (
        sectors.groupby("signal_date")["increment_contribution"]
        .sum()
    )

    expected = weekly.set_index("signal_date")["gross_increment"]

    if not np.allclose(
        closure.reindex(expected.index, fill_value=0),
        expected, atol=TOL, rtol=0
    ):
        raise ValueError("Sector contribution closure failed")

    sectors["signal_year"] = pd.to_datetime(
        sectors["signal_date"]
    ).dt.year

    return sectors


def yearly_analysis(weekly, sectors):
    rows = []

    for year, g in weekly.groupby("signal_year"):
        rows.append({
            "year": year,
            "n_weeks": len(g),
            "avg_overlap": g["overlap"].mean(),
            "avg_jaccard": g["jaccard"].mean(),
            "gross_increment_sum": g["gross_increment"].sum(),
            "cost_effect_sum": g["cost_effect"].sum(),
            "net_increment_sum": g["net_increment"].sum(),
            "common_contribution_sum":
                g["common_contribution"].sum(),
            "dynamic_only_contribution_sum":
                g["dynamic_only_contribution"].sum(),
            "static_only_contribution_sum":
                g["static_only_contribution"].sum(),
        })

    annual = pd.DataFrame(rows)

    sector_year = (
        sectors.groupby(["signal_year", "sector"], as_index=False)
        ["increment_contribution"].sum()
    )

    return annual, sector_year


def make_plots(weekly, annual, sector_year):
    # Holdings overlap over time.
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(
        pd.to_datetime(weekly["signal_date"]),
        weekly["overlap"]
    )
    ax.set_title("Dynamic vs Static: Holdings Overlap")
    ax.set_ylabel("Overlap Ratio")
    ax.set_ylim(0, 1.05)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "holdings_overlap.png", dpi=200)
    plt.close(fig)

    # Yearly arithmetic contribution decomposition.
    cols = [
        "common_contribution_sum",
        "dynamic_only_contribution_sum",
        "static_only_contribution_sum",
    ]

    fig, ax = plt.subplots(figsize=(11, 6))
    x = np.arange(len(annual))
    bottom_pos = np.zeros(len(annual))
    bottom_neg = np.zeros(len(annual))

    for col in cols:
        values = annual[col].to_numpy() * 100
        bottoms = np.where(values >= 0, bottom_pos, bottom_neg)
        ax.bar(x, values, bottom=bottoms, label=col)
        bottom_pos += np.maximum(values, 0)
        bottom_neg += np.minimum(values, 0)

    ax.axhline(0, linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(annual["year"].astype(str))
    ax.set_ylabel("Sum of Weekly Contributions (pp)")
    ax.set_title("Dynamic - Static: Stock Contribution Decomposition")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "stock_contribution_by_year.png", dpi=200)
    plt.close(fig)

    # Sector contributions for 2025.
    y2025 = sector_year[
        sector_year["signal_year"] == 2025
    ].sort_values("increment_contribution")

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.barh(
        y2025["sector"].astype(str),
        y2025["increment_contribution"] * 100
    )
    ax.axvline(0, linewidth=0.8)
    ax.set_xlabel("Sum of Weekly Contributions (pp)")
    ax.set_title("2025: Sector Contributions to Dynamic - Static")
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "sector_contribution_2025.png", dpi=200)
    plt.close(fig)


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    d, s, path, returns = load_data()

    stocks = stock_contributions(d, s, returns)
    weekly = weekly_analysis(stocks, path)
    sectors = sector_analysis(stocks, weekly)
    annual, sector_year = yearly_analysis(weekly, sectors)

    stocks.to_csv(
        OUT / "stock_increment_contributions.csv", index=False
    )
    weekly.to_csv(
        OUT / "stock_transmission_weekly.csv", index=False
    )
    sectors.to_csv(
        OUT / "sector_increment_contributions.csv", index=False
    )
    annual.to_csv(
        OUT / "stock_transmission_yearly.csv", index=False
    )
    sector_year.to_csv(
        OUT / "sector_contribution_yearly.csv", index=False
    )

    make_plots(weekly, annual, sector_year)

    print("\n=== Stock Transmission by Year ===")
    print(
        annual.to_string(
            index=False, float_format=lambda x: f"{x:.5f}"
        )
    )

    print("\n=== 2025 Sector Contributions ===")
    print(
        sector_year[
            sector_year["signal_year"] == 2025
        ].sort_values("increment_contribution")
        .to_string(index=False)
    )

    print("\nOverall average overlap:",
          f"{weekly['overlap'].mean():.2%}")
    print("Overall average Jaccard:",
          f"{weekly['jaccard'].mean():.2%}")
    print("\nAll gross/net/sector closure checks PASSED.")
    print("Results saved to:", OUT)


if __name__ == "__main__":
    main()
