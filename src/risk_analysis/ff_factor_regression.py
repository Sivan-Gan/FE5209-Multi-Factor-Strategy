
"""Member 5: Execution-aligned FF5 + Momentum regression.

Run:
    python -m src.risk_analysis.ff_factor_regression

The frozen Dynamic and accepted Static strategy outputs are read-only.
"""

from pathlib import Path
import json

import numpy as np
import pandas as pd
import statsmodels.api as sm


ROOT = Path(__file__).resolve().parents[2]
FF_PATH = (
    ROOT / "handoff/us_downstream/data/"
    "ff_factors_daily.parquet"
)
OUT = ROOT / "outputs/risk_analysis/us_hist_oos"

DYNAMIC = ROOT / "outputs/strategy/frozen/hist_oos/weekly_path.csv"
STATIC = ROOT / "outputs/strategy/benchmarks/hist_oos/static_weekly_path.csv"
EXECUTION = (
    ROOT / "handoff/us_downstream/data/"
    "benchmark_weekly_execution_returns.parquet"
)

FACTOR_COLS = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom"]
HAC_LAG = 4
WEEKS_PER_YEAR = 52



def load_factors():
    """Load frozen FF5 + Momentum daily factor data.

    Source:
        handoff/us_downstream/data/ff_factors_daily.parquet

    The source already uses DECIMAL_RETURN.
    No division by 100 is required.
    """

    factors = pd.read_parquet(FF_PATH)

    required = [
        "date", "sample_role", "unit",
        "mktrf", "smb", "hml",
        "rmw", "cma", "rf", "umd",
    ]

    missing_cols = set(required) - set(factors.columns)

    if missing_cols:
        raise ValueError(
            f"Missing FF factor columns: {missing_cols}"
        )

    factors["date"] = pd.to_datetime(
        factors["date"], errors="raise"
    )

    factors = factors.rename(columns={
        "mktrf": "Mkt-RF",
        "smb": "SMB",
        "hml": "HML",
        "rmw": "RMW",
        "cma": "CMA",
        "rf": "RF",
        "umd": "Mom",
    })

    # Restrict to the official OOS sample.
    factors = factors[
        factors["sample_role"] == "HIST_OOS_BASE"
    ].copy()

    if factors.empty:
        raise ValueError("No HIST_OOS_BASE FF factor data")

    if not factors["unit"].eq("DECIMAL_RETURN").all():
        raise ValueError(
            "FF factors must use DECIMAL_RETURN"
        )

    if factors["date"].duplicated().any():
        raise ValueError("Duplicate FF factor dates")

    factor_cols = FACTOR_COLS + ["RF"]

    if not np.isfinite(
        factors[factor_cols].to_numpy(dtype=float)
    ).all():
        raise ValueError("Missing or invalid FF factors")

    factors = factors[
        ["date"] + factor_cols
    ].sort_values("date").reset_index(drop=True)

    return factors


def load_strategy():
    dynamic = pd.read_csv(DYNAMIC)
    static = pd.read_csv(STATIC)
    execution = pd.read_parquet(EXECUTION)

    for name, df in [
        ("Dynamic", dynamic),
        ("Static", static),
        ("Execution", execution),
    ]:
        if df["signal_date"].duplicated().any():
            raise ValueError(f"Duplicate dates: {name}")

    dynamic = dynamic[
        ["signal_date", "period_end", "net_return"]
    ].rename(columns={
        "period_end": "dynamic_end",
        "net_return": "dynamic",
    })

    static = static[
        ["signal_date", "period_end", "net_return"]
    ].rename(columns={
        "period_end": "static_end",
        "net_return": "static",
    })

    execution = execution[
        ["signal_date", "execution_date", "next_execution_date"]
    ]

    df = dynamic.merge(
        static, on="signal_date",
        how="outer", indicator=True,
        validate="one_to_one"
    )

    if not df["_merge"].eq("both").all():
        raise ValueError("Dynamic/Static sample mismatch")

    df = df.drop(columns="_merge").merge(
        execution, on="signal_date",
        how="left", validate="one_to_one"
    )

    date_cols = [
        "signal_date", "dynamic_end", "static_end",
        "execution_date", "next_execution_date"
    ]

    for col in date_cols:
        df[col] = pd.to_datetime(df[col], errors="raise")

    if not (
        df["dynamic_end"].eq(df["static_end"]).all()
        and df["dynamic_end"].eq(df["next_execution_date"]).all()
    ):
        raise ValueError("Holding period endpoints do not match")

    if not (df["execution_date"] < df["dynamic_end"]).all():
        raise ValueError("Invalid execution period")

    if not np.isfinite(
        df[["dynamic", "static"]].to_numpy(dtype=float)
    ).all():
        raise ValueError("Invalid strategy returns")

    df = df.sort_values("signal_date").reset_index(drop=True)

    if len(df) != 259:
        raise ValueError(f"Expected 259 periods, got {len(df)}")

    return df


def compound(r):
    """Compound a series of daily decimal returns."""
    return float(np.prod(1.0 + r) - 1.0)


def aggregate_factors(strategy, daily):
    rows = []

    available_dates = set(daily["date"])

    for row in strategy.itertuples(index=False):
        start = row.execution_date
        end = row.next_execution_date

        if start not in available_dates:
            raise ValueError(f"Missing execution date: {start}")
        if end not in available_dates:
            raise ValueError(f"Missing period end: {end}")

        # Exclude E_t and include E_(t+1).
        period = daily.loc[
            (daily["date"] > start)
            & (daily["date"] <= end)
        ].copy()

        if period.empty:
            raise ValueError(f"Empty factor period: {start} -> {end}")

        expected = daily.loc[
            (daily["date"] > start)
            & (daily["date"] <= end), "date"
        ]

        if not period["date"].reset_index(drop=True).equals(
            expected.reset_index(drop=True)
        ):
            raise ValueError("Incomplete factor calendar")

        values = {
            "signal_date": row.signal_date,
            "execution_date": start,
            "period_end": end,
            "n_factor_days": len(period),
            "dynamic": row.dynamic,
            "static": row.static,
        }

        # Compound RF and market total return consistently.
        rf_period = compound(period["RF"])
        market_total = compound(period["Mkt-RF"] + period["RF"])

        values["RF"] = rf_period
        values["Mkt-RF"] = market_total - rf_period

        # Approximate compounding of daily long-short factors.
        for col in ["SMB", "HML", "RMW", "CMA", "Mom"]:
            values[col] = compound(period[col])

        rows.append(values)

    result = pd.DataFrame(rows)

    if len(result) != len(strategy):
        raise ValueError("Factor aggregation lost periods")

    result["dynamic_excess"] = result["dynamic"] - result["RF"]
    result["static_excess"] = result["static"] - result["RF"]
    result["increment"] = result["dynamic"] - result["static"]

    return result


def fit_ff6(df, target, label):
    X = sm.add_constant(
        df[FACTOR_COLS].astype(float),
        has_constant="add"
    )
    y = df[target].astype(float)

    fit = sm.OLS(y, X).fit(
        cov_type="HAC",
        cov_kwds={
            "maxlags": HAC_LAG,
            "use_correction": True,
        },
    )

    ci = fit.conf_int(alpha=0.05)

    summary = {
        "model": label,
        "n_weeks": int(fit.nobs),
        "alpha_weekly": fit.params["const"],
        "alpha_annualized": fit.params["const"] * WEEKS_PER_YEAR,
        "alpha_tstat": fit.tvalues["const"],
        "alpha_pvalue": fit.pvalues["const"],
        "alpha_ci_low_ann": ci.loc["const", 0] * WEEKS_PER_YEAR,
        "alpha_ci_high_ann": ci.loc["const", 1] * WEEKS_PER_YEAR,
        "r_squared": fit.rsquared,
        "hac_lag": HAC_LAG,
    }

    coefficients = []

    for variable in ["const"] + FACTOR_COLS:
        coefficients.append({
            "model": label,
            "factor": variable,
            "coefficient": fit.params[variable],
            "hac_se": fit.bse[variable],
            "hac_tstat": fit.tvalues[variable],
            "hac_pvalue": fit.pvalues[variable],
            "ci_low": ci.loc[variable, 0],
            "ci_high": ci.loc[variable, 1],
        })

    return summary, coefficients


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    daily = load_factors()
    strategy = load_strategy()
    aligned = aggregate_factors(strategy, daily)

    print("\n=== FF6 Execution Alignment ===")
    print("Strategy periods:", len(strategy))
    print("Aligned periods:", len(aligned))
    print("Daily factor rows:", len(daily))
    print(
        "Factor days per period:",
        aligned["n_factor_days"].min(),
        "to",
        aligned["n_factor_days"].max(),
    )

    summaries = []
    coefficients = []

    for label, target in [
        ("Dynamic FMOM", "dynamic_excess"),
        ("Static Multi-Factor", "static_excess"),
        ("Dynamic - Static", "increment"),
    ]:
        summary, coef = fit_ff6(aligned, target, label)
        summaries.append(summary)
        coefficients.extend(coef)

    summary_df = pd.DataFrame(summaries)
    coef_df = pd.DataFrame(coefficients)

    # Algebraic consistency of regressions on identical X.
    pivot = coef_df.pivot(
        index="factor", columns="model", values="coefficient"
    )

    error = (
        pivot["Dynamic FMOM"]
        - pivot["Static Multi-Factor"]
        - pivot["Dynamic - Static"]
    ).abs().max()

    if error > 1e-10:
        raise ValueError(
            f"Increment regression identity failed: {error}"
        )

    aligned.to_csv(
        OUT / "ff6_aligned_weekly.csv", index=False
    )
    summary_df.to_csv(
        OUT / "ff6_regression_summary.csv", index=False
    )
    coef_df.to_csv(
        OUT / "ff6_regression_coefficients.csv", index=False
    )
    pivot.to_csv(
        OUT / "ff6_exposure_comparison.csv"
    )

    metadata = {
        "model": "Execution-aligned FF5 + Momentum",
        "frequency": "weekly strategy execution intervals",
        "factor_source": "Kenneth French daily data",
        "factor_aggregation": (
            "market total and RF compounded consistently; "
            "long-short factors compounded approximately"
        ),
        "hac_lag": HAC_LAG,
        "n_periods": len(aligned),
        "increment_identity_max_error": float(error),
    }

    with open(OUT / "ff6_methodology.json", "w") as f:
        json.dump(metadata, f, indent=2)

    print("\n=== FF6 Regression Summary ===")
    print(
        summary_df[
            ["model", "alpha_annualized",
             "alpha_tstat", "alpha_pvalue", "r_squared"]
        ].to_string(index=False, float_format=lambda x: f"{x:.5f}")
    )

    print("\n=== Factor Exposures ===")
    print(pivot.round(4).to_string())

    print("\nIncrement regression identity error:", error)
    print("Results saved to:", OUT)


if __name__ == "__main__":
    main()
