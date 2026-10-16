
"""Member 5: Consolidated US Strategy Risk Map.

Run:
    python -m src.risk_analysis.risk_map

All existing research outputs are read-only.
"""

from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/risk_analysis/us_hist_oos"

DYNAMIC_PATH = (
    ROOT / "outputs/strategy/frozen/hist_oos/weekly_path.csv"
)
STATIC_PATH = (
    ROOT / "outputs/strategy/benchmarks/hist_oos/"
    "static_weekly_path.csv"
)
EW_PATH = OUT / "universe_ew_weekly_path.csv"

N_WEEKS = 259

STRATEGIES = [
    "Dynamic FMOM",
    "Static MF",
    "Universe EW",
]

FILES = {
    "four_strategy_comparison":
        "four_strategy_comparison.csv",
    "cost_sensitivity":
        "cost_sensitivity_summary.csv",
    "breakeven":
        "breakeven_cost.csv",
    "concentration":
        "concentration_summary.csv",
    "market_cap":
        "market_cap_sector_summary.csv",
    "liquidity":
        "liquidity_capacity_summary.csv",
    "capacity":
        "capacity_limits.csv",
    "regime":
        "regime_performance_summary.csv",
    "stress":
        "historical_stress_summary.csv",
    "ff6":
        "ff6_regression_summary.csv",
    "ff6_coefficients":
        "ff6_regression_coefficients.csv",
}


def read_inputs():
    result = {}

    for key, filename in FILES.items():
        path = OUT / filename

        if not path.exists():
            raise FileNotFoundError(
                f"Required risk input missing: {path}"
            )

        result[key] = pd.read_csv(path)

    return result


def load_paths():
    paths = {
        "Dynamic FMOM": DYNAMIC_PATH,
        "Static MF": STATIC_PATH,
        "Universe EW": EW_PATH,
    }

    result = {}

    for name, path in paths.items():
        df = pd.read_csv(path)

        if len(df) != N_WEEKS:
            raise ValueError(
                f"{name}: expected {N_WEEKS} weeks"
            )

        if df["signal_date"].duplicated().any():
            raise ValueError(f"{name}: duplicate dates")

        result[name] = df.sort_values(
            "signal_date"
        ).reset_index(drop=True)

    ref = result["Dynamic FMOM"][
        ["signal_date", "period_end"]
    ]

    for name, df in result.items():
        if not df[
            ["signal_date", "period_end"]
        ].equals(ref):
            raise ValueError(
                f"{name}: holding periods do not align"
            )

    return result


def lookup(df, filters, value):
    """Strict lookup: require exactly one matching row."""
    selected = df

    for col, target in filters.items():
        if col not in selected.columns:
            raise ValueError(f"Missing column: {col}")

        selected = selected[selected[col] == target]

    if len(selected) != 1:
        raise ValueError(
            f"Expected one row for {filters}, "
            f"found {len(selected)}"
        )

    if value not in selected.columns:
        raise ValueError(f"Missing metric: {value}")

    result = selected.iloc[0][value]

    if pd.isna(result):
        return np.nan

    return float(result)


def build_summary(data, paths):
    rows = []

    for strategy in STRATEGIES:
        perf = data["four_strategy_comparison"]

        row = {
            "strategy": strategy,
            "n_weeks": N_WEEKS,
            "CAGR": lookup(
                perf, {"strategy": strategy}, "CAGR"
            ),
            "volatility": lookup(
                perf, {"strategy": strategy}, "Volatility"
            ),
            "sharpe": lookup(
                perf, {"strategy": strategy}, "Sharpe"
            ),
            "max_drawdown": lookup(
                perf, {"strategy": strategy}, "Max_Drawdown"
            ),
        }

        row["annual_turnover"] = (
            paths[strategy]["turnover"].mean() * 52
        )

        row["stock_hhi"] = lookup(
            data["concentration"],
            {"strategy": strategy},
            "avg_stock_hhi"
        )

        row["sector_hhi"] = lookup(
            data["market_cap"],
            {"strategy": strategy},
            "avg_sector_hhi"
        )

        row["weighted_median_cap_usd"] = lookup(
            data["market_cap"],
            {"strategy": strategy},
            "avg_weighted_median_cap_usd"
        )

        row["breakeven_cost_bps"] = lookup(
            data["breakeven"],
            {"strategy": strategy},
            "breakeven_cost_bps"
        )

        row["CAGR_20bp"] = lookup(
            data["cost_sensitivity"],
            {"strategy": strategy, "cost_bps": 20},
            "CAGR"
        )

        row["capacity_5pct_usd"] = lookup(
            data["capacity"],
            {"strategy": strategy,
             "participation_limit": 0.05},
            "capacity_usd"
        )

        row["participation_p95_50M"] = lookup(
            data["liquidity"],
            {
                "strategy": strategy,
                "AUM": 50_000_000,
                "scenario": "Base",
            },
            "avg_p95_participation"
        )

        rows.append(row)

    return pd.DataFrame(rows)


def build_long_risk_map(summary, data):
    rows = []

    dimensions = {
        "Market": [
            "volatility", "max_drawdown", "sharpe"
        ],
        "Portfolio": [
            "stock_hhi", "sector_hhi",
            "weighted_median_cap_usd"
        ],
        "Trading": [
            "annual_turnover", "breakeven_cost_bps",
            "CAGR_20bp"
        ],
        "Liquidity": [
            "capacity_5pct_usd",
            "participation_p95_50M"
        ],
    }

    for _, record in summary.iterrows():
        name = record["strategy"]

        for dimension, metrics in dimensions.items():
            for metric in metrics:
                rows.append({
                    "strategy": name,
                    "risk_dimension": dimension,
                    "metric": metric,
                    "value": record[metric],
                    "source": "Consolidated US OOS",
                })

    # FF6 exists for Dynamic and Static, not Universe EW.
    model_map = {
        "Dynamic FMOM": "Dynamic FMOM",
        "Static MF": "Static Multi-Factor",
    }

    for strategy, model in model_map.items():
        ff = data["ff6"]

        for metric, source_col in [
            ("ff6_alpha_annualized", "alpha_annualized"),
            ("ff6_alpha_pvalue", "alpha_pvalue"),
            ("ff6_r_squared", "r_squared"),
        ]:
            rows.append({
                "strategy": strategy,
                "risk_dimension": "Factor",
                "metric": metric,
                "value": lookup(
                    ff, {"model": model}, source_col
                ),
                "source": "FF6 HAC Regression",
            })

        coef = data["ff6_coefficients"]

        for factor in [
            "Mkt-RF", "SMB", "HML",
            "RMW", "CMA", "Mom"
        ]:
            rows.append({
                "strategy": strategy,
                "risk_dimension": "Factor",
                "metric": f"ff6_beta_{factor}",
                "value": lookup(
                    coef,
                    {"model": model, "factor": factor},
                    "coefficient",
                ),
                "source": "FF6 HAC Regression",
            })

    # Market regime diagnostics.
    regime_map = {
        "Dynamic FMOM": "Dynamic",
        "Static MF": "Static",
        "Universe EW": "Universe_EW",
    }

    for strategy, model in regime_map.items():
        for regime_type, regimes in [
            ("trend_regime", ["Bull", "Bear"]),
            ("vol_regime", ["High Vol", "Low Vol"]),
        ]:
            for regime in regimes:
                value = lookup(
                    data["regime"],
                    {
                        "regime_type": regime_type,
                        "regime": regime,
                        "strategy": model,
                    },
                    "annualized_arithmetic_mean",
                )

                rows.append({
                    "strategy": strategy,
                    "risk_dimension": "Regime",
                    "metric": f"{regime_type}_{regime}",
                    "value": value,
                    "source": "Lagged Market Regime",
                })

        for regime in [
            "Market Crash Weeks",
            "Market Rally Weeks",
            "Normal Weeks",
        ]:
            value = lookup(
                data["stress"],
                {
                    "regime": regime,
                    "strategy": model,
                },
                "mean_return",
            )

            rows.append({
                "strategy": strategy,
                "risk_dimension": "Stress",
                "metric": regime,
                "value": value,
                "source": "Realized Market Stress",
            })

    return pd.DataFrame(rows)


def validate(summary, data, paths):
    checks = []

    def add(name, status, detail=""):
        checks.append({
            "check": name,
            "status": status,
            "detail": detail,
        })

    add(
        "259 aligned completed weeks",
        "PASS",
        "Dynamic, Static and Universe EW",
    )

    for name in STRATEGIES:
        r = summary[
            summary["strategy"] == name
        ].iloc[0]

        base = lookup(
            data["cost_sensitivity"],
            {"strategy": name, "cost_bps": 10},
            "CAGR",
        )

        error = abs(r["CAGR"] - base)

        add(
            f"{name}: 10bp CAGR closure",
            "PASS" if error < 1e-8 else "FAIL",
            f"error={error:.3e}",
        )

        add(
            f"{name}: valid GTR",
            "PASS" if (
                np.isfinite(paths[name]["gtr"]).all()
                and (paths[name]["gtr"] >= 0).all()
            ) else "FAIL",
        )

    add(
        "Liquidity coverage and bottleneck QA",
        "PENDING",
        "Missing ADV observations require review",
    )

    add(
        "Independent upstream PIT/return audit",
        "PENDING",
        "Deferred for final validation",
    )

    add(
        "Market impact calibration",
        "PENDING",
        "Not included in fixed-cost model",
    )

    add(
        "High/Low Rate regime",
        "PENDING",
        "Not yet analyzed",
    )

    add(
        "Mandatory OOS robustness",
        "PENDING",
        "Separate exploratory sensitivity analysis",
    )

    return pd.DataFrame(checks)


def make_dashboard(summary):
    fig, ax = plt.subplots(figsize=(10, 6))

    labels = summary["strategy"]
    x = np.arange(len(labels))
    width = 0.35

    ax.bar(
        x - width / 2,
        summary["CAGR"] * 100,
        width,
        label="CAGR",
    )

    ax.bar(
        x + width / 2,
        summary["volatility"] * 100,
        width,
        label="Annual Volatility",
    )

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Percent (%)")
    ax.set_title("US OOS: Return and Risk Comparison")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    fig.savefig(
        OUT / "us_risk_dashboard.png", dpi=200
    )
    plt.close(fig)


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    data = read_inputs()
    paths = load_paths()

    summary = build_summary(data, paths)
    risk_map = build_long_risk_map(summary, data)
    validation = validate(summary, data, paths)

    summary.to_csv(
        OUT / "us_strategy_risk_summary.csv",
        index=False,
    )
    risk_map.to_csv(
        OUT / "us_strategy_risk_map.csv",
        index=False,
    )
    validation.to_csv(
        OUT / "us_risk_map_validation.csv",
        index=False,
    )

    make_dashboard(summary)

    print("\n=== US Strategy Risk Summary ===")
    print(
        summary.to_string(
            index=False,
            float_format=lambda x: f"{x:.5f}"
        )
    )

    print("\n=== Risk Map Validation ===")
    print(validation.to_string(index=False))

    if validation["status"].eq("FAIL").any():
        raise ValueError("Risk Map validation failed")

    print("\nRisk Map generated successfully.")
    print("Results saved to:", OUT)


if __name__ == "__main__":
    main()
