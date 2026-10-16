
"""Member 5: US OOS Market and Incremental Factor Regression.

Models:
1. Dynamic excess return ~ Market excess return
2. Static excess return ~ Market excess return
3. Dynamic - Static ~ Market excess return

All regressions use OLS with Newey-West HAC(4) inference.

Run:
    python -m src.risk_analysis.factor_regression
"""

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import statsmodels.api as sm


ROOT = Path(__file__).resolve().parents[2]

DYNAMIC_FILE = (
    ROOT / "outputs/strategy/frozen/hist_oos/weekly_path.csv"
)

STATIC_FILE = (
    ROOT / "outputs/strategy/benchmarks/hist_oos/"
    "static_weekly_path.csv"
)

MARKET_FILE = (
    ROOT / "handoff/us_downstream/data/"
    "benchmark_weekly_execution_returns.parquet"
)

RF_FILE = (
    ROOT / "handoff/us_downstream/data/"
    "risk_free_weekly.parquet"
)

OUT = ROOT / "outputs/risk_analysis/us_hist_oos"

HAC_LAG = 4
WEEKS_PER_YEAR = 52
ROLLING_WINDOW = 52


def load_data():
    """Load and align four weekly return series."""

    dynamic = pd.read_csv(DYNAMIC_FILE)
    static = pd.read_csv(STATIC_FILE)
    market = pd.read_parquet(MARKET_FILE)
    rf = pd.read_parquet(RF_FILE)

    for name, data in [
        ("Dynamic", dynamic),
        ("Static", static),
        ("Market", market),
        ("Risk-free", rf),
    ]:
        if data["signal_date"].duplicated().any():
            raise ValueError(f"Duplicate dates in {name}")

    dynamic = dynamic[
        ["signal_date", "period_end", "net_return"]
    ].rename(columns={
        "period_end": "period_end_dynamic",
        "net_return": "dynamic",
    })

    static = static[
        ["signal_date", "period_end", "net_return"]
    ].rename(columns={
        "period_end": "period_end_static",
        "net_return": "static",
    })

    market = market[
        ["signal_date", "next_execution_date",
         "benchmark_total_return"]
    ].rename(columns={
        "benchmark_total_return": "market"
    })

    rf = rf[
        ["signal_date", "next_execution_date",
         "risk_free_return"]
    ].rename(columns={
        "next_execution_date": "rf_period_end",
        "risk_free_return": "rf"
    })

    df = dynamic.merge(
        static, on="signal_date",
        how="left", validate="one_to_one"
    ).merge(
        market, on="signal_date",
        how="left", validate="one_to_one"
    ).merge(
        rf, on="signal_date",
        how="left", validate="one_to_one"
    )

    # Check that all returns cover the same holding periods.
    dates = [
        "period_end_dynamic",
        "period_end_static",
        "next_execution_date",
        "rf_period_end",
    ]

    if df[dates].isna().any().any():
        raise ValueError("Missing holding-period dates")

    for col in dates[1:]:
        if not (
            pd.to_datetime(df[col])
            == pd.to_datetime(df[dates[0]])
        ).all():
            raise ValueError(f"Period mismatch: {col}")

    numeric = ["dynamic", "static", "market", "rf"]

    if not np.isfinite(
        df[numeric].to_numpy(dtype=float)
    ).all():
        raise ValueError("Missing or invalid returns")

    if len(df) != 259:
        raise ValueError(f"Unexpected OOS length: {len(df)}")

    df["signal_date"] = pd.to_datetime(df["signal_date"])
    df["period_end"] = pd.to_datetime(
        df["period_end_dynamic"]
    )

    df = df.sort_values("signal_date").reset_index(drop=True)

    # Regression variables.
    df["market_excess"] = df["market"] - df["rf"]
    df["dynamic_excess"] = df["dynamic"] - df["rf"]
    df["static_excess"] = df["static"] - df["rf"]

    # RF cancels when comparing two strategies.
    df["increment"] = df["dynamic"] - df["static"]

    return df


def fit_market_model(y, x, model_name):
    """OLS coefficients with HAC(4) standard errors."""

    X = sm.add_constant(
        pd.DataFrame({"market_excess": x}),
        has_constant="add"
    )

    result = sm.OLS(y, X).fit(
        cov_type="HAC",
        cov_kwds={"maxlags": HAC_LAG, "use_correction": True}
    )

    alpha_weekly = result.params["const"]
    beta = result.params["market_excess"]

    # Annualized arithmetic alpha, not compounded alpha.
    alpha_annual = alpha_weekly * WEEKS_PER_YEAR

    # Additional regression diagnostics.
    summary = {
        "model": model_name,
        "n_weeks": int(result.nobs),
        "alpha_weekly": alpha_weekly,
        "alpha_annualized": alpha_annual,
        "alpha_tstat_hac": result.tvalues["const"],
        "alpha_pvalue_hac": result.pvalues["const"],
        "beta_market": beta,
        "beta_tstat_hac": result.tvalues["market_excess"],
        "beta_pvalue_hac": result.pvalues["market_excess"],
        "r_squared": result.rsquared,
        "hac_lag": HAC_LAG,
    }

    ci = result.conf_int(alpha=0.05)

    summary["alpha_ci_low_annualized"] = (
        ci.loc["const", 0] * WEEKS_PER_YEAR
    )
    summary["alpha_ci_high_annualized"] = (
        ci.loc["const", 1] * WEEKS_PER_YEAR
    )
    summary["beta_ci_low"] = ci.loc["market_excess", 0]
    summary["beta_ci_high"] = ci.loc["market_excess", 1]

    coefficients = []

    for variable in ["const", "market_excess"]:
        coefficients.append({
            "model": model_name,
            "variable": variable,
            "coefficient": result.params[variable],
            "std_error_hac": result.bse[variable],
            "tstat_hac": result.tvalues[variable],
            "pvalue_hac": result.pvalues[variable],
            "ci_low": ci.loc[variable, 0],
            "ci_high": ci.loc[variable, 1],
        })

    return summary, coefficients


def rolling_beta(df):
    """52-week rolling market beta for both strategies."""

    x = df["market_excess"]
    market_variance = x.rolling(
        ROLLING_WINDOW, min_periods=ROLLING_WINDOW
    ).var()

    result = df[["signal_date", "period_end"]].copy()

    for name in ["dynamic", "static"]:
        y = df[f"{name}_excess"]

        covariance = y.rolling(
            ROLLING_WINDOW, min_periods=ROLLING_WINDOW
        ).cov(x)

        result[f"{name}_beta"] = (
            covariance / market_variance.replace(0, np.nan)
        )

    result["incremental_beta"] = (
        result["dynamic_beta"] - result["static_beta"]
    )

    return result


def plot_rolling_beta(df):
    plt.figure(figsize=(11, 6))

    plt.plot(
        df["period_end"],
        df["dynamic_beta"],
        label="Dynamic FMOM",
    )

    plt.plot(
        df["period_end"],
        df["static_beta"],
        label="Static Multi-Factor",
    )

    plt.axhline(
        1.0, linestyle="--",
        linewidth=1, label="Market Beta = 1"
    )

    plt.title("US OOS: Rolling 52-Week Market Beta")
    plt.xlabel("Holding Period End")
    plt.ylabel("Market Beta")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()

    plt.savefig(
        OUT / "rolling_market_beta.png",
        dpi=200
    )
    plt.close()


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    df = load_data()

    models = [
        ("Dynamic FMOM", "dynamic_excess"),
        ("Static Multi-Factor", "static_excess"),
        ("Dynamic - Static", "increment"),
    ]

    summaries = []
    coefficients = []

    for name, target in models:
        summary, coef = fit_market_model(
            df[target],
            df["market_excess"],
            name
        )

        summaries.append(summary)
        coefficients.extend(coef)

    summary_df = pd.DataFrame(summaries)
    coefficient_df = pd.DataFrame(coefficients)

    rolling = rolling_beta(df)

    summary_df.to_csv(
        OUT / "market_regression_summary.csv",
        index=False
    )

    coefficient_df.to_csv(
        OUT / "market_regression_coefficients.csv",
        index=False
    )

    rolling.to_csv(
        OUT / "rolling_market_beta.csv",
        index=False
    )

    plot_rolling_beta(rolling)

    print("\n=== US OOS Market Regression ===")

    display_cols = [
        "model",
        "alpha_annualized",
        "alpha_tstat_hac",
        "alpha_pvalue_hac",
        "beta_market",
        "beta_pvalue_hac",
        "r_squared",
    ]

    print(
        summary_df[display_cols].to_string(
            index=False,
            float_format=lambda x: f"{x:.5f}"
        )
    )

    print("\nHAC lag:", HAC_LAG)
    print("Rolling beta window:", ROLLING_WINDOW)
    print("Results saved to:", OUT)


if __name__ == "__main__":
    main()
