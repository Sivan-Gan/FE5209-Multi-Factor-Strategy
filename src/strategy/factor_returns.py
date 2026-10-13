"""Step 16: weekly long-short returns for the four frozen final factors.

Rule (identical to the frozen 2006-2015 seed):
  - each signal week, take eligible stocks whose factor score is available;
  - rank by score: Top 30% = long leg, Bottom 30% = short leg
    (count = floor(0.30 * N), ties broken by PERMNO ascending);
  - each leg is equal-weighted; only stocks that could actually be executed count;
  - hold from execution close E_t to the next execution close E_{t+1};
  - factor return = long-leg mean return - short-leg mean return.

Usage (run from the repository root):
  python -m src.strategy.factor_returns --check-seed        # recompute 2011-2015, compare with the seed
  python -m src.strategy.factor_returns --start 2016-01-01 --end 2020-12-31
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

# The four frozen factors: (family, specification_id, score column, availability column)
FINAL_FACTORS = [
    ("Quality", "QUALITY_CURRENT", "quality_score", "quality_available"),
    ("Momentum", "MOMENTUM_12_1", "momentum_score", "momentum_available"),
    ("Low Risk", "LOWRISK_CURRENT", "lowrisk_score", "lowrisk_available"),
    ("Investment", "INVESTMENT_CURRENT", "investment_score", "investment_available"),
]
LEG_FRACTION = 0.30
VALID_RETURN_STATUSES = {"VALID", "VALID_CONFIRMED_DELISTING_CASH"}
GATE_B_LAST_DATE = "2020-12-31"  # never read anything after this in Gate B


def project_root() -> Path:
    """Repository root: this file lives in src/strategy/, so go up two levels."""
    return Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- 1. read data
def load_inputs(root: Path, start: str, end: str, allow_oos: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read factor scores and weekly execution returns for signal dates in [start, end].

    Anything after 2020-12-31 needs allow_oos=True AND a FROZEN strategy config (governance.py).
    """
    from src.strategy import governance
    governance.check_window(end, allow_oos, root)

    score_cols = ["signal_date", "PERMNO", "execution_date", "research_period", "eligible_universe_flag"]
    for _, _, score, avail in FINAL_FACTORS:
        score_cols += [score, avail]
    scores = pd.read_parquet(
        root / "handoff/final_factor/data/factor_scores_weekly.parquet",
        columns=score_cols,
        filters=[("signal_date", ">=", start), ("signal_date", "<=", end)],
    )

    returns = pd.read_parquet(
        root / "handoff/us_downstream/data/security_weekly_execution_returns.parquet",
        columns=["signal_date", "next_execution_date", "PERMNO",
                 "execution_to_next_execution_total_return", "execution_feasible", "unavailable_reason"],
        filters=[("signal_date", ">=", start), ("signal_date", "<=", end)],
    )
    for df in (scores, returns):
        df["signal_date"] = df["signal_date"].astype(str)
        df["PERMNO"] = df["PERMNO"].astype(str)
    return scores, returns


# ------------------------------------------------------- 2. one leg, one week
def pick_leg(week: pd.DataFrame, score_col: str, top: bool) -> pd.DataFrame:
    """Top or bottom 30% of the week's eligible stocks; ties broken by PERMNO ascending."""
    count = math.floor(LEG_FRACTION * len(week))
    if count < 1:
        return week.iloc[0:0]
    ranked = week.sort_values([score_col, "PERMNO"], ascending=[not top, True])
    return ranked.head(count)


def leg_return(leg: pd.DataFrame) -> tuple[float | None, int]:
    """Equal-weight mean return of the executed stocks in a leg.

    Returns (mean return or None if the leg is unusable, number executed).
    """
    executed = leg[leg["execution_feasible"] == 1]
    if executed.empty:
        return None, 0
    status = executed["unavailable_reason"].fillna("").replace("", "VALID")
    if not status.isin(VALID_RETURN_STATUSES).all():
        return None, len(executed)
    return float(executed["execution_to_next_execution_total_return"].mean()), len(executed)


# ------------------------------------------------- 3. all factors, all weeks
def compute_factor_returns(scores: pd.DataFrame, returns: pd.DataFrame, end: str) -> pd.DataFrame:
    data = scores.merge(returns, on=["signal_date", "PERMNO"], how="left")
    data["execution_feasible"] = data["execution_feasible"].fillna(0).astype(int)

    rows = []
    for signal_date, week in data.groupby("signal_date", sort=True):
        next_exec = week["next_execution_date"].dropna()
        next_exec = str(next_exec.iloc[0]) if len(next_exec) else ""
        # The holding period must end inside the allowed window (no peeking past `end`).
        endpoint_ok = bool(next_exec) and next_exec <= end

        for family, spec, score_col, avail_col in FINAL_FACTORS:
            eligible = week[(week["eligible_universe_flag"] == 1)
                            & (week[avail_col] == 1)
                            & np.isfinite(week[score_col])]
            top, bottom = pick_leg(eligible, score_col, True), pick_leg(eligible, score_col, False)
            top_ret, top_n = leg_return(top) if endpoint_ok else (None, 0)
            bot_ret, bot_n = leg_return(bottom) if endpoint_ok else (None, 0)
            available = top_ret is not None and bot_ret is not None
            rows.append({
                "signal_date": signal_date,
                "execution_date": str(week["execution_date"].iloc[0]),
                "next_execution_date": next_exec,
                "family": family,
                "specification_id": spec,
                "top_return_gross": top_ret,
                "bottom_return_gross": bot_ret,
                "factor_return_gross": (top_ret - bot_ret) if available else None,
                "top_count": top_n,
                "bottom_count": bot_n,
                "eligible_count": len(eligible),
                "factor_return_available": int(available),
                "sample_role": str(week["research_period"].iloc[0]),
                "unavailable_reason": "" if available else
                    ("FORWARD_RETURN_INCOMPLETE_OR_EMPTY" if endpoint_ok else "ENDPOINT_AFTER_WINDOW"),
            })
    return pd.DataFrame(rows)


# ------------------------------------------------ 4. check against the seed
def check_against_seed(root: Path, mine: pd.DataFrame) -> pd.DataFrame:
    seed = pd.read_parquet(root / "handoff/us_downstream/data/final_factor_returns_seed_weekly.parquet")
    seed["signal_date"] = seed["signal_date"].astype(str)
    both = mine.merge(seed, on=["signal_date", "family"], suffixes=("_mine", "_seed"))
    both = both[(both["factor_return_available_mine"] == 1) & (both["factor_return_available_seed"] == 1)]
    both["abs_diff"] = (both["factor_return_gross_mine"] - both["factor_return_gross_seed"]).abs()
    return both.groupby("family").agg(
        matched_weeks=("abs_diff", "size"),
        max_abs_diff=("abs_diff", "max"),
        mean_abs_diff=("abs_diff", "mean"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2020-12-31")
    parser.add_argument("--check-seed", action="store_true",
                        help="recompute 2011-2015 and compare with the frozen seed")
    args = parser.parse_args()
    root = project_root()

    if args.check_seed:
        start, end = "2011-01-01", "2015-12-31"
        mine = compute_factor_returns(*load_inputs(root, start, end), end=end)
        result = check_against_seed(root, mine)
        print(result.to_string())
        ok = (result["max_abs_diff"] < 1e-10).all() and len(result) == 4
        print("\nPASS: matches the seed" if ok else "\nCHECK: differences found, see table above")
        return

    mine = compute_factor_returns(*load_inputs(root, args.start, args.end), end=args.end)
    out_dir = root / "outputs/strategy"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"factor_returns_weekly_{args.start[:4]}_{args.end[:4]}.csv"
    mine.to_csv(out_file, index=False)
    print(f"Wrote {len(mine)} rows to {out_file.relative_to(root)}")
    print(mine.groupby("family")["factor_return_available"].agg(["sum", "size"]).to_string())


if __name__ == "__main__":
    main()