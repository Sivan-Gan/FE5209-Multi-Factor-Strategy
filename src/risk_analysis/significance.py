
"""Member 5: FMOM incremental performance and statistical inference.

Run from repository root:
    python -m src.risk_analysis.significance

Inputs:
    Frozen Dynamic OOS weekly_path.csv
    Accepted Static OOS static_weekly_path.csv
    Weekly risk-free returns

This script does NOT change the frozen strategy.
"""

from pathlib import Path
from math import erf, sqrt

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]

DYNAMIC_FILE = (
    ROOT / "outputs/strategy/frozen/hist_oos/weekly_path.csv"
)
STATIC_FILE = (
    ROOT / "outputs/strategy/benchmarks/hist_oos/"
    "static_weekly_path.csv"
)
RF_FILE = (
    ROOT / "handoff/us_downstream/data/"
    "risk_free_weekly.parquet"
)
ACCEPTANCE_FILE = (
    ROOT / "outputs/risk_analysis/us_hist_oos/"
    "static_acceptance_report.csv"
)

OUT = ROOT / "outputs/risk_analysis/us_hist_oos"

WEEKS_PER_YEAR = 52
HAC_LAG = 4
BLOCK_LENGTH = 8
N_BOOTSTRAP = 5000
SEED = 42
ALPHA = 0.05
COST_ONEWAY = 0.001


def load_data():
    # Static benchmark must pass its acceptance tests.
    report = pd.read_csv(ACCEPTANCE_FILE)
    if (report["status"] != "PASS").any():
        raise ValueError("Static benchmark acceptance failed")

    dynamic = pd.read_csv(DYNAMIC_FILE)
    static = pd.read_csv(STATIC_FILE)
    rf = pd.read_parquet(RF_FILE)

    required = [
        "signal_date", "period_end", "gross_return",
        "net_return", "gtr", "turnover"
    ]

    for name, df in [("Dynamic", dynamic), ("Static", static)]:
        missing = set(required) - set(df.columns)
        if missing:
            raise ValueError(f"{name} missing columns: {missing}")
        if df["signal_date"].duplicated().any():
            raise ValueError(f"{name}: duplicate signal dates")

    dynamic = dynamic[required].rename(
        columns={
            "period_end": "period_end_dynamic",
            "gross_return": "gross_dynamic",
            "net_return": "net_dynamic",
            "gtr": "gtr_dynamic",
            "turnover": "turnover_dynamic",
        }
    )

    static = static[required].rename(
        columns={
            "period_end": "period_end_static",
            "gross_return": "gross_static",
            "net_return": "net_static",
            "gtr": "gtr_static",
            "turnover": "turnover_static",
        }
    )

    df = dynamic.merge(
        static,
        on="signal_date",
        how="outer",
        validate="one_to_one",
        indicator=True,
    )

    if not (df["_merge"] == "both").all():
        raise ValueError("Dynamic and Static weeks do not match")

    if not (
        df["period_end_dynamic"] == df["period_end_static"]
    ).all():
        raise ValueError("Holding periods do not match")

    if rf["signal_date"].duplicated().any():
        raise ValueError("Duplicate risk-free signal dates")

    df = df.merge(
        rf[["signal_date", "risk_free_return"]],
        on="signal_date",
        how="left",
        validate="one_to_one",
    )

    df["signal_date"] = pd.to_datetime(df["signal_date"])
    df = df.sort_values("signal_date").reset_index(drop=True)

    numeric = [
        "gross_dynamic", "gross_static",
        "net_dynamic", "net_static",
        "gtr_dynamic", "gtr_static",
        "turnover_dynamic", "turnover_static",
        "risk_free_return",
    ]

    if not np.isfinite(df[numeric].to_numpy(dtype=float)).all():
        raise ValueError("Missing or non-finite data")

    if len(df) != 259:
        raise ValueError(f"Unexpected OOS weeks: {len(df)}")

    if not (
        pd.to_datetime(df["period_end_dynamic"])
        <= pd.Timestamp("2025-12-31")
    ).all():
        raise ValueError("Returns beyond OOS cutoff")

    # Independently check both net-return accounting identities.
    for name in ("dynamic", "static"):
        expected = (
            (1 - COST_ONEWAY * df[f"gtr_{name}"])
            * (1 + df[f"gross_{name}"]) - 1
        )
        if not np.allclose(
            expected, df[f"net_{name}"], atol=1e-10, rtol=0
        ):
            raise ValueError(f"{name} accounting mismatch")

    df["delta_net"] = df["net_dynamic"] - df["net_static"]
    df["delta_gross"] = (
        df["gross_dynamic"] - df["gross_static"]
    )
    df["delta_turnover"] = (
        df["turnover_dynamic"] - df["turnover_static"]
    )
    df["delta_gtr"] = df["gtr_dynamic"] - df["gtr_static"]

    return df


def normal_cdf(x):
    return 0.5 * (1 + erf(x / sqrt(2)))


def hac_mean_test(x, lag=4):
    """Newey-West Bartlett HAC test for a constant mean."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    mean = x.mean()
    centered = x - mean

    # Biased autocovariance estimator with denominator n.
    gamma0 = np.dot(centered, centered) / n
    omega = gamma0

    for k in range(1, lag + 1):
        gamma_k = (
            np.dot(centered[k:], centered[:-k]) / n
        )
        bartlett = 1 - k / (lag + 1)
        omega += 2 * bartlett * gamma_k

    if omega <= 0:
        raise ValueError("Non-positive HAC long-run variance")

    se = np.sqrt(omega / n)
    t_stat = mean / se

    # Two-sided asymptotic normal approximation.
    p_value = 2 * (1 - normal_cdf(abs(t_stat)))

    ci_low = mean - 1.96 * se
    ci_high = mean + 1.96 * se

    return {
        "mean_weekly": mean,
        "mean_annualized": mean * WEEKS_PER_YEAR,
        "hac_se_weekly": se,
        "hac_tstat": t_stat,
        "hac_pvalue": p_value,
        "hac_ci_low_ann": ci_low * WEEKS_PER_YEAR,
        "hac_ci_high_ann": ci_high * WEEKS_PER_YEAR,
        "hac_lag": lag,
    }


def annualized_sharpe(r, rf):
    """Annualized Sharpe from weekly excess returns."""
    excess = np.asarray(r) - np.asarray(rf)
    vol = np.std(excess, ddof=1)

    if vol <= 0:
        return np.nan

    return (
        np.mean(excess) / vol * np.sqrt(WEEKS_PER_YEAR)
    )


def bootstrap_paired(df):
    """Paired moving-block bootstrap with fixed-length blocks."""
    rng = np.random.default_rng(SEED)

    arr = df[
        ["net_dynamic", "net_static", "risk_free_return"]
    ].to_numpy(dtype=float)

    n = len(arr)
    n_blocks = int(np.ceil(n / BLOCK_LENGTH))
    max_start = n - BLOCK_LENGTH

    mean_increment = np.empty(N_BOOTSTRAP)
    sharpe_difference = np.empty(N_BOOTSTRAP)

    for b in range(N_BOOTSTRAP):
        starts = rng.integers(
            0, max_start + 1, size=n_blocks
        )

        indices = np.concatenate([
            np.arange(s, s + BLOCK_LENGTH)
            for s in starts
        ])[:n]

        sample = arr[indices]

        d = sample[:, 0]
        s = sample[:, 1]
        rf = sample[:, 2]

        mean_increment[b] = (
            np.mean(d - s) * WEEKS_PER_YEAR
        )

        sharpe_difference[b] = (
            annualized_sharpe(d, rf)
            - annualized_sharpe(s, rf)
        )

    valid = np.isfinite(sharpe_difference)
    if valid.sum() < 0.99 * N_BOOTSTRAP:
        raise ValueError("Too many invalid bootstrap Sharpes")

    return {
        "bootstrap_increment_ci_low": np.quantile(
            mean_increment, ALPHA / 2
        ),
        "bootstrap_increment_ci_high": np.quantile(
            mean_increment, 1 - ALPHA / 2
        ),
        "bootstrap_sharpe_ci_low": np.quantile(
            sharpe_difference[valid], ALPHA / 2
        ),
        "bootstrap_sharpe_ci_high": np.quantile(
            sharpe_difference[valid], 1 - ALPHA / 2
        ),
        "bootstrap_reps": N_BOOTSTRAP,
        "bootstrap_block_length": BLOCK_LENGTH,
        "bootstrap_seed": SEED,
    }


def main():
    df = load_data()

    hac_net = hac_mean_test(df["delta_net"], HAC_LAG)
    hac_gross = hac_mean_test(df["delta_gross"], HAC_LAG)

    boot = bootstrap_paired(df)

    rf = df["risk_free_return"].to_numpy()
    sharpe_dynamic = annualized_sharpe(
        df["net_dynamic"], rf
    )
    sharpe_static = annualized_sharpe(
        df["net_static"], rf
    )

    delta_sharpe = sharpe_dynamic - sharpe_static

    result = {
        "n_weeks": len(df),
        "dynamic_sharpe": sharpe_dynamic,
        "static_sharpe": sharpe_static,
        "delta_sharpe": delta_sharpe,
        "gross_increment_ann": hac_gross["mean_annualized"],
        "gross_hac_tstat": hac_gross["hac_tstat"],
        "gross_hac_pvalue": hac_gross["hac_pvalue"],
        "net_increment_ann": hac_net["mean_annualized"],
        "net_hac_tstat": hac_net["hac_tstat"],
        "net_hac_pvalue": hac_net["hac_pvalue"],
        "net_hac_ci_low_ann": hac_net["hac_ci_low_ann"],
        "net_hac_ci_high_ann": hac_net["hac_ci_high_ann"],
        "dynamic_turnover_ann": (
            df["turnover_dynamic"].mean() * WEEKS_PER_YEAR
        ),
        "static_turnover_ann": (
            df["turnover_static"].mean() * WEEKS_PER_YEAR
        ),
        "incremental_gtr_ann": (
            df["delta_gtr"].mean() * WEEKS_PER_YEAR
        ),
        "incremental_direct_cost_ann": (
            COST_ONEWAY * df["delta_gtr"].mean()
            * WEEKS_PER_YEAR
        ),
        **boot,
        "hac_lag": HAC_LAG,
    }

    OUT.mkdir(parents=True, exist_ok=True)

    df.to_csv(
        OUT / "fmom_increment_weekly.csv", index=False
    )

    pd.DataFrame([result]).to_csv(
        OUT / "fmom_significance_summary.csv", index=False
    )

    print("\n=== FMOM OOS Statistical Inference ===")
    print(f"Weeks: {result['n_weeks']}")

    print("\nNet Return Increment (Dynamic - Static)")
    print(f"Annualized mean: {result['net_increment_ann']:.4%}")
    print(f"HAC t-stat:       {result['net_hac_tstat']:.4f}")
    print(f"HAC p-value:      {result['net_hac_pvalue']:.6f}")
    print(
        "HAC 95% CI:      "
        f"[{result['net_hac_ci_low_ann']:.4%}, "
        f"{result['net_hac_ci_high_ann']:.4%}]"
    )

    print("\nSharpe Difference")
    print(f"Dynamic Sharpe:   {sharpe_dynamic:.4f}")
    print(f"Static Sharpe:    {sharpe_static:.4f}")
    print(f"Delta Sharpe:     {delta_sharpe:.4f}")
    print(
        "Bootstrap 95% CI:"
        f" [{boot['bootstrap_sharpe_ci_low']:.4f}, "
        f"{boot['bootstrap_sharpe_ci_high']:.4f}]"
    )

    print("\nGross Increment")
    print(f"Annualized mean: {result['gross_increment_ann']:.4%}")
    print(f"HAC p-value:      {result['gross_hac_pvalue']:.6f}")

    print("\nTrading")
    print(
        f"Dynamic annual turnover: "
        f"{result['dynamic_turnover_ann']:.4f}"
    )
    print(
        f"Static annual turnover:  "
        f"{result['static_turnover_ann']:.4f}"
    )
    print(
        f"Annualized direct cost difference: "
        f"{result['incremental_direct_cost_ann']:.4%}"
    )

    print(f"\nResults saved to: {OUT}")


if __name__ == "__main__":
    main()
