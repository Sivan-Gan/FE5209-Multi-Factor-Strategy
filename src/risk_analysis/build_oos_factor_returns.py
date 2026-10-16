
"""Build OOS factor portfolio returns for FMOM failure diagnosis.

This is a diagnostic output, not a new strategy backtest.

Run:
    python -m src.risk_analysis.build_oos_factor_returns
"""

from pathlib import Path
import numpy as np
import pandas as pd

from src.strategy import factor_returns as fr
from src.strategy import governance as gv


ROOT = Path(__file__).resolve().parents[2]

START = "2021-01-01"
END = "2025-12-31"

OUT = ROOT / "outputs/risk_analysis/us_hist_oos"
DYNAMIC = ROOT / "outputs/strategy/frozen/hist_oos/weekly_path.csv"


def main():
    cfg = gv.load_config(ROOT)

    if cfg["status"] != "FROZEN":
        raise ValueError("Strategy configuration is not frozen")

    scores, returns = fr.load_inputs(
        ROOT, START, END, allow_oos=True
    )

    factors = fr.compute_factor_returns(
        scores, returns, end=END
    )

    # Use exactly the 259 completed OOS holding periods.
    dynamic = pd.read_csv(DYNAMIC)
    periods = dynamic[
        ["signal_date", "period_end"]
    ].copy()

    if periods["signal_date"].duplicated().any():
        raise ValueError("Duplicate Dynamic signal dates")

    factors = factors.merge(
        periods,
        on="signal_date",
        how="inner",
        validate="many_to_one"
    )

    if len(factors) != len(periods) * len(fr.FINAL_FACTORS):
        raise ValueError("Missing factor-week observations")

    if not (
        factors["next_execution_date"]
        == factors["period_end"]
    ).all():
        raise ValueError("Factor holding periods do not align")

    # Check factor-return availability.
    availability = factors.groupby("family").agg(
        n_weeks=("signal_date", "size"),
        n_available=("factor_return_available", "sum"),
        n_missing=("factor_return_available", lambda x: (x == 0).sum()),
    )

    available = factors["factor_return_available"].eq(1)

    if not np.isfinite(
        factors.loc[available, "factor_return_gross"]
        .to_numpy(dtype=float)
    ).all():
        raise ValueError("Non-finite available factor returns")

    OUT.mkdir(parents=True, exist_ok=True)

    factors.to_csv(
        OUT / "oos_factor_returns_weekly.csv",
        index=False
    )

    availability.to_csv(
        OUT / "oos_factor_return_coverage.csv"
    )

    print("\n=== OOS Factor Return Coverage ===")
    print(availability.to_string())

    print("\nFactor return rows:", len(factors))
    print("Completed OOS weeks:", len(periods))
    print("Results saved to:", OUT)


if __name__ == "__main__":
    main()
