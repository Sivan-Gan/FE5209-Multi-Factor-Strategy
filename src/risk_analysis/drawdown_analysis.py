
"""Member 5: US OOS Rolling Performance & Drawdown Diagnostics.

Run:
    python -m src.risk_analysis.drawdown_analysis
"""

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]

BASE = ROOT / "outputs/risk_analysis/us_hist_oos"
STATIC = (
    ROOT / "outputs/strategy/benchmarks/hist_oos/"
    "static_weekly_path.csv"
)

WEEKS_PER_YEAR = 52
WINDOWS = (13, 26, 52)

RETURN_COLS = {
    "Dynamic": "dynamic",
    "Static": "static",
    "SP500": "market",
}


def load_data():
    base = pd.read_csv(BASE / "benchmark_aligned_weekly.csv")
    static = pd.read_csv(STATIC)

    required = {
        "signal_date", "period_end", "net_return",
        "benchmark_total_return", "risk_free_return"
    }
    if not required.issubset(base.columns):
        raise ValueError(
            f"Missing columns: {required - set(base.columns)}"
        )

    base = base.rename(columns={
        "net_return": "dynamic",
        "benchmark_total_return": "market",
        "risk_free_return": "rf",
    })

    static = static[
        ["signal_date", "period_end", "net_return"]
    ].rename(columns={
        "period_end": "static_end",
        "net_return": "static",
    })

    if base["signal_date"].duplicated().any():
        raise ValueError("Duplicate Dynamic dates")
    if static["signal_date"].duplicated().any():
        raise ValueError("Duplicate Static dates")

    df = base.merge(
        static, on="signal_date", how="outer",
        validate="one_to_one", indicator=True
    )

    if not df["_merge"].eq("both").all():
        raise ValueError("Dynamic/Static sample mismatch")

    if not (
        pd.to_datetime(df["period_end"])
        == pd.to_datetime(df["static_end"])
    ).all():
        raise ValueError("Holding period mismatch")

    df["signal_date"] = pd.to_datetime(df["signal_date"])
    df["period_end"] = pd.to_datetime(df["period_end"])
    df = df.sort_values("signal_date").reset_index(drop=True)

    cols = ["dynamic", "static", "market", "rf"]
    if not np.isfinite(df[cols].to_numpy(dtype=float)).all():
        raise ValueError("Non-finite return or RF")

    if (df[["dynamic", "static", "market"]] <= -1).any().any():
        raise ValueError("Return <= -100%")

    if len(df) != 259:
        raise ValueError(f"Unexpected sample length: {len(df)}")

    return df


def rolling_diagnostics(df):
    result = df[["signal_date", "period_end"]].copy()

    for name, col in RETURN_COLS.items():
        r = df[col]
        excess = r - df["rf"]

        for w in WINDOWS:
            result[f"{name}_return_{w}w"] = (
                (1 + r).rolling(w, min_periods=w)
                .apply(np.prod, raw=True) - 1
            )

            mean = excess.rolling(w, min_periods=w).mean()
            vol = excess.rolling(w, min_periods=w).std(ddof=1)

            result[f"{name}_sharpe_{w}w"] = (
                mean / vol.replace(0, np.nan)
                * np.sqrt(WEEKS_PER_YEAR)
            )

    return result


def nav_and_drawdown(df):
    out = df[["signal_date", "period_end"]].copy()

    for name, col in RETURN_COLS.items():
        nav = (1 + df[col]).cumprod()
        peak = np.maximum(1.0, nav.cummax())

        out[f"{name}_nav"] = nav
        out[f"{name}_drawdown"] = nav / peak - 1

    out["relative_nav"] = (
        out["Dynamic_nav"] / out["Static_nav"]
    )
    out["relative_drawdown"] = (
        out["relative_nav"]
        / np.maximum(1.0, out["relative_nav"].cummax())
        - 1
    )

    return out


def drawdown_episodes(nav_df, name):
    """Identify peak-to-trough-to-recovery drawdown episodes.

    Initial NAV=1 is treated as the initial high-water mark.
    """
    values = nav_df[f"{name}_nav"].to_numpy()
    dates = nav_df["period_end"].to_list()

    records = []
    peak_nav = 1.0
    peak_date = pd.NaT
    active = None

    for i, value in enumerate(values):
        date = dates[i]

        if value >= peak_nav - 1e-12:
            if active is not None:
                active["recovery_date"] = date
                active["duration_weeks"] = (
                    i - active["peak_index"]
                )
                records.append(active)
                active = None

            peak_nav = value
            peak_date = date
            continue

        dd = value / peak_nav - 1

        if active is None:
            active = {
                "strategy": name,
                "peak_date": peak_date,
                "peak_index": i - 1 if pd.isna(peak_date)
                              else dates.index(peak_date),
                "trough_date": date,
                "max_drawdown": dd,
                "recovery_date": pd.NaT,
                "duration_weeks": np.nan,
            }

        if dd < active["max_drawdown"]:
            active["max_drawdown"] = dd
            active["trough_date"] = date

    if active is not None:
        active["duration_weeks"] = (
            len(values) - 1 - active["peak_index"]
        )
        records.append(active)

    result = pd.DataFrame(records)
    if not result.empty:
        result = result.drop(columns="peak_index")
        result = result.sort_values("max_drawdown")

    return result


def yearly_returns(df):
    rows = []

    # Group by signal year, consistent with the weekly research
    # convention; these are not exact calendar-year NAV returns.
    for year, g in df.groupby(df["signal_date"].dt.year):
        row = {"signal_year": year, "n_weeks": len(g)}

        for name, col in RETURN_COLS.items():
            row[f"{name}_return"] = (
                np.prod(1 + g[col]) - 1
            )

        row["dynamic_minus_static_pp"] = (
            100 * (
                row["Dynamic_return"] - row["Static_return"]
            )
        )
        rows.append(row)

    return pd.DataFrame(rows)


def make_plots(nav, rolling):
    # Cumulative NAV
    fig, ax = plt.subplots(figsize=(11, 6))

    for name in RETURN_COLS:
        ax.plot(
            nav["period_end"], nav[f"{name}_nav"],
            label=name
        )

    ax.set_title("US Historical OOS: Cumulative NAV")
    ax.set_ylabel("Growth of $1")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(BASE / "risk_cumulative_nav.png", dpi=200)
    plt.close(fig)

    # Drawdowns
    fig, ax = plt.subplots(figsize=(11, 6))

    for name in RETURN_COLS:
        ax.plot(
            nav["period_end"],
            100 * nav[f"{name}_drawdown"],
            label=name
        )

    ax.set_title("US Historical OOS: Drawdown")
    ax.set_ylabel("Drawdown (%)")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(BASE / "risk_drawdown.png", dpi=200)
    plt.close(fig)

    # Rolling Sharpe
    fig, ax = plt.subplots(figsize=(11, 6))

    for name in RETURN_COLS:
        ax.plot(
            rolling["period_end"],
            rolling[f"{name}_sharpe_52w"],
            label=name
        )

    ax.axhline(0, linestyle="--", linewidth=0.8)
    ax.set_title("52-Week Rolling Sharpe")
    ax.set_ylabel("Annualized Sharpe")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(BASE / "rolling_sharpe_52w.png", dpi=200)
    plt.close(fig)

    # Relative wealth
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(nav["period_end"], nav["relative_nav"])
    ax.axhline(1, linestyle="--", linewidth=0.8)
    ax.set_title("Dynamic / Static Relative Wealth")
    ax.set_ylabel("Relative NAV")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(BASE / "dynamic_static_relative_nav.png", dpi=200)
    plt.close(fig)


def main():
    BASE.mkdir(parents=True, exist_ok=True)

    df = load_data()
    rolling = rolling_diagnostics(df)
    nav = nav_and_drawdown(df)
    yearly = yearly_returns(df)

    episodes = []
    for name in RETURN_COLS:
        ep = drawdown_episodes(nav, name)
        if not ep.empty:
            episodes.append(ep)

    episodes = (
        pd.concat(episodes, ignore_index=True)
        if episodes else pd.DataFrame()
    )

    rolling.to_csv(
        BASE / "rolling_performance.csv", index=False
    )
    nav.to_csv(
        BASE / "risk_nav_drawdown.csv", index=False
    )
    yearly.to_csv(
        BASE / "risk_yearly_returns.csv", index=False
    )
    episodes.to_csv(
        BASE / "drawdown_episodes.csv", index=False
    )

    make_plots(nav, rolling)

    print("\n=== US OOS Yearly Returns ===")
    print(
        yearly.to_string(
            index=False,
            float_format=lambda x: f"{x:.4f}"
        )
    )

    print("\n=== Maximum Drawdown ===")
    for name in RETURN_COLS:
        mdd = nav[f"{name}_drawdown"].min()
        print(f"{name}: {mdd:.4%}")

    print("\n=== Relative Performance ===")
    print(
        "Final Dynamic / Static NAV:",
        f"{nav['relative_nav'].iloc[-1]:.4f}"
    )

    print("\n=== Worst Drawdown Episodes ===")
    if not episodes.empty:
        print(
            episodes.groupby("strategy")
            .head(2).to_string(index=False)
        )

    print("\nResults saved to:", BASE)


if __name__ == "__main__":
    main()
