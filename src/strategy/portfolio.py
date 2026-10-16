"""Step 21: weekly target holdings from composite scores (SSOT Step 21 and 4.7).

Order (SSOT 4.7):
  no buffer:  Rank -> CandidateSelection -> SectorConstraint -> Weights
  buffer:     Rank -> RetainExistingHoldingsByExitRule -> FillNewNamesByEntryRule
              -> SectorConstraint -> Weights

Rules:
  - N_target = max(1, floor(0.20 * N)), N = stocks with a composite score this week.
  - Rank: composite score descending, ties by PERMNO ascending (never row order).
  - Equal weight 1 / N_target per name, long only.
  - Sector cap 30%: at most floor(0.30 * N_target) names per sector.  Names are added
    from best to worst rank and a name is skipped if its sector is already full
    (SSOT 4.7 algorithm 2).  If every name has been tried and N_target is still not
    reached, the unfilled weight stays in cash and constraint_infeasible = 1.
  - Buffer (optional, --buffer): last week's holdings are kept while they rank in the
    top 30%; the rest of the book is filled with the best-ranked names not held
    (top 20% first; lower names only if the sector cap blocks them).
    Retained names are placed first, then new names, all under the same sector cap.

Input:  outputs/strategy/stock_scores_weekly_2016_2020_<config>.csv (composite_score.py)
Usage (run from the repository root):
  python -m src.strategy.portfolio                  # baseline config, no buffer
  python -m src.strategy.portfolio --buffer
  python -m src.strategy.portfolio --J 13 --lam 1 --min-factors 3
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

from src.strategy import factor_returns as fr
from src.strategy import fmom_weights as fw

SELECT_FRACTION = 0.20
EXIT_FRACTION = 0.30          # buffer: existing holdings are kept while inside this fraction
SECTOR_CAP = 0.30
UNKNOWN_SECTOR = "UNKNOWN"


def config_tag(J: int, lam: float, risk_adjusted: bool, min_factors: int) -> str:
    return f"J{J}_lam{lam:g}_ra{int(risk_adjusted)}_min{min_factors}"


# ----------------------------------------------------------------- 1. rank
def rank_week(week: pd.DataFrame) -> pd.DataFrame:
    """Scored stocks of one week, best first; rank 1 = best."""
    scored = week[week["score_available"] == 1].copy()
    scored = scored.sort_values(["composite_score", "PERMNO"], ascending=[False, True], kind="mergesort")
    scored["rank"] = np.arange(1, len(scored) + 1)
    return scored.reset_index(drop=True)


# --------------------------------------------------------- 2. one week
def select_week(ranked: pd.DataFrame, previous: set[str], use_buffer: bool) -> tuple[pd.DataFrame, dict]:
    n = len(ranked)
    n_target = max(1, math.floor(SELECT_FRACTION * n))
    n_entry = n_target
    n_exit = math.floor(EXIT_FRACTION * n)
    cap_names = math.floor(SECTOR_CAP * n_target)

    if use_buffer and previous:
        keep = ranked["PERMNO"].isin(previous) & (ranked["rank"] <= n_exit)
        order = pd.concat([ranked[keep], ranked[~keep]])   # retained names get first claim on sector room
    else:
        order = ranked

    picked, sector_count, skipped = [], {}, 0
    for row in order.itertuples(index=False):
        if len(picked) == n_target or cap_names < 1:
            break
        if sector_count.get(row.sector, 0) >= cap_names:
            skipped += 1
            continue
        sector_count[row.sector] = sector_count.get(row.sector, 0) + 1
        picked.append(row)

    hold = pd.DataFrame(picked, columns=order.columns)
    hold["weight"] = 1.0 / n_target
    held_before = hold["PERMNO"].isin(previous)
    hold["entry"] = np.where(~held_before, "NEW",
                             np.where(hold["rank"] > n_entry, "KEPT_BY_BUFFER", "KEPT"))
    sector_w = hold.groupby("sector")["weight"].sum()
    stats = {
        "n_scored": n, "n_target": n_target, "n_held": len(hold), "sector_cap_names": cap_names,
        "n_retained_by_buffer": int((hold["entry"] == "KEPT_BY_BUFFER").sum()),
        "n_new": int((~held_before).sum()),
        "n_new_outside_entry": int(((~held_before) & (hold["rank"] > n_entry)).sum()),
        "n_skipped_by_sector_cap": skipped,
        "cash_weight": 1.0 - hold["weight"].sum(),
        "constraint_infeasible": int(len(hold) < n_target),
        "max_sector_weight": float(sector_w.max()) if len(sector_w) else 0.0,
        "n_sectors_held": int(len(sector_w)),
    }
    return hold, stats


# ------------------------------------------------------- 3. all weeks
def build_holdings(scores: pd.DataFrame, use_buffer: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    scores = scores.copy()
    scores["PERMNO"] = scores["PERMNO"].astype(str)
    sector = scores["historical_gics_sector"].astype(object)
    scores["sector"] = (sector.where(sector.notna(), UNKNOWN_SECTOR).astype(str).str.strip()
                        .replace({"nan": UNKNOWN_SECTOR, "": UNKNOWN_SECTOR, "None": UNKNOWN_SECTOR}))
    holdings, weekly, previous, prev_w = [], [], set(), pd.Series(dtype=float)
    for signal_date, week in scores.groupby("signal_date", sort=True):
        hold, stats = select_week(rank_week(week), previous, use_buffer)
        w = hold.set_index("PERMNO")["weight"]
        # target-to-target turnover (ignores price drift between rebalances; M5 does the real one)
        both = w.index.union(prev_w.index)
        stats["one_way_turnover"] = 0.5 * float((w.reindex(both, fill_value=0)
                                                 - prev_w.reindex(both, fill_value=0)).abs().sum())
        if prev_w.empty:
            stats["one_way_turnover"] = np.nan   # first week: initial build, not turnover
        stats["signal_date"] = signal_date
        stats["execution_date"] = str(week["execution_date"].iloc[0])
        weekly.append(stats)
        holdings.append(hold.assign(signal_date=signal_date))
        previous, prev_w = set(w.index), w
    cols = ["signal_date", "execution_date", "PERMNO", "sector", "composite_score", "rank", "entry", "weight"]
    return pd.concat(holdings, ignore_index=True)[cols], pd.DataFrame(weekly)


# ---------------------------------------------------------------- 4. checks
def check_holdings(hold: pd.DataFrame, weekly: pd.DataFrame) -> list[str]:
    problems = []
    if (hold["weight"] < 0).any():
        problems.append("negative weight")
    if hold.duplicated(["signal_date", "PERMNO"]).any():
        problems.append("duplicate holding")
    total = hold.groupby("signal_date")["weight"].sum().reindex(weekly["signal_date"]).fillna(0).to_numpy()
    if not np.allclose(total + weekly["cash_weight"].to_numpy(), 1.0, atol=1e-12):
        problems.append("weights + cash do not sum to 1")
    if (weekly["max_sector_weight"] > SECTOR_CAP + 1e-12).any():
        problems.append("sector cap violated")
    if (weekly["n_held"] > weekly["n_target"]).any():
        problems.append("more names than target")
    if (hold["signal_date"] > fr.GATE_B_LAST_DATE).any():
        problems.append("signal date after the Gate B window")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--J", type=int, default=fw.BASELINE["J"], choices=fw.J_GRID)
    parser.add_argument("--lam", type=float, default=fw.BASELINE["lam"], choices=fw.LAMBDA_GRID)
    parser.add_argument("--risk-adjusted", action="store_true")
    parser.add_argument("--min-factors", type=int, default=fw.K, choices=[3, 4])
    parser.add_argument("--buffer", action="store_true")
    parser.add_argument("--period", default="2016_2020")
    args = parser.parse_args()
    root = fr.project_root()

    tag = config_tag(args.J, args.lam, args.risk_adjusted, args.min_factors)
    scores_file = root / f"outputs/strategy/stock_scores_weekly_{args.period}_{tag}.csv"
    if not scores_file.exists():
        raise FileNotFoundError(f"{scores_file.relative_to(root)} not found - run composite_score.py "
                                f"with the same settings first")
    scores = pd.read_csv(scores_file, dtype={"PERMNO": str, "historical_gics_sector": str})
    hold, weekly = build_holdings(scores, args.buffer)

    out_tag = f"{tag}_buf{int(args.buffer)}"
    out_dir = root / "outputs/strategy"
    hold.to_csv(out_dir / f"target_holdings_weekly_{args.period}_{out_tag}.csv", index=False)
    weekly.to_csv(out_dir / f"holdings_summary_weekly_{args.period}_{out_tag}.csv", index=False)

    print(f"Wrote {len(hold)} holding rows for {len(weekly)} weeks ({out_tag})")
    print(f"names per week: target avg {weekly['n_target'].mean():.1f}, held avg {weekly['n_held'].mean():.1f}")
    print(f"weeks where the sector cap skipped a name: {(weekly['n_skipped_by_sector_cap'] > 0).sum()}; "
          f"max sector weight seen: {weekly['max_sector_weight'].max():.4f}")
    print(f"weeks with cash left (constraint_infeasible): {weekly['constraint_infeasible'].sum()}")
    print(f"avg one-way target turnover per week: {weekly['one_way_turnover'].mean():.4f}")
    if args.buffer:
        print(f"avg names kept only because of the buffer: {weekly['n_retained_by_buffer'].mean():.1f}; "
              f"weeks needing new names outside top 20%: {(weekly['n_new_outside_entry'] > 0).sum()}")
    first = hold[hold["signal_date"] == hold["signal_date"].min()]
    print(f"\nFirst week {first['signal_date'].iloc[0]}: {len(first)} names; sector counts:")
    print(first["sector"].value_counts().to_string())
    problems = check_holdings(hold, weekly)
    print("\nPASS: no negatives, no duplicates, weights + cash = 1, sector cap respected" if not problems
          else "\nCHECK: " + "; ".join(problems))


if __name__ == "__main__":
    main()
