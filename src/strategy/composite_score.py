"""Step 20: weekly composite stock score for one Gate B configuration.

Score_{i,t} = sum_k w_{k,t} * F_{k,i,t}

Missing-factor policy is NOT frozen yet (SSOT Step 20), so it is a parameter:
  --min-factors 4  complete-factor sample: a stock needs all K scores.
  --min-factors 3  minimum-available-factor sample: a stock needs >= 3 scores;
                   the weights of its available factors are renormalized to 1:
                   Score = sum_{k avail} w_k F_k / sum_{k avail} w_k.
For a stock with all K scores both policies give the same number.

Per SSOT Step 20 every week also records: the effective_factor_count
distribution, the share of incomplete-factor stocks, and the factor weight
reallocated because of missingness.

Inputs:
  - handoff/final_factor/data/factor_scores_weekly.parquet
  - outputs/strategy/factor_weights_weekly_2016_2020.csv (from fmom_weights.py)

Usage (run from the repository root):
  python -m src.strategy.composite_score                      # baseline J=26, lambda=0.5, RA off, min 4
  python -m src.strategy.composite_score --min-factors 3
  python -m src.strategy.composite_score --J 13 --lam 1 --risk-adjusted
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from src.strategy import factor_returns as fr
from src.strategy import fmom_weights as fw

SCORE_COLS = [score for _, _, score, _ in fr.FINAL_FACTORS]
AVAIL_COLS = [avail for _, _, _, avail in fr.FINAL_FACTORS]
WEIGHT_COLS = [f"w_{family.replace(' ', '')}" for family in fw.FAMILIES]
SCORES_FILE = "handoff/final_factor/data/factor_scores_weekly.parquet"
WEIGHTS_FILE = "outputs/strategy/factor_weights_weekly_2016_2020.csv"


# ------------------------------------------------------------- 1. inputs
def load_scores(root: Path, start: str, end: str, allow_oos: bool = False) -> pd.DataFrame:
    from src.strategy import governance
    governance.check_window(end, allow_oos, root)
    cols = ["signal_date", "execution_date", "PERMNO", "historical_gics_sector",
            "eligible_universe_flag"] + SCORE_COLS + AVAIL_COLS
    scores = pd.read_parquet(root / SCORES_FILE, columns=cols,
                             filters=[("signal_date", ">=", start), ("signal_date", "<=", end)])
    for c in ("signal_date", "execution_date", "PERMNO", "historical_gics_sector"):
        scores[c] = scores[c].astype(str)
    return scores


def weights_for_config(weights: pd.DataFrame, J: int, lam: float, risk_adjusted: bool) -> pd.DataFrame:
    """One row per signal_date with the K factor weights as columns w_Quality ... w_Investment."""
    sel = weights[(weights["J"] == J) & np.isclose(weights["lambda"], lam)
                  & (weights["risk_adjusted"] == int(risk_adjusted))]
    if sel.empty:
        raise ValueError(f"no weights for J={J}, lambda={lam}, risk_adjusted={risk_adjusted}")
    wide = sel.pivot(index="signal_date", columns="family", values="weight")[fw.FAMILIES]
    wide.columns = WEIGHT_COLS
    wide.index = wide.index.astype(str)
    return wide.reset_index()


# --------------------------------------------------------- 2. the score
def compute_scores(scores: pd.DataFrame, week_weights: pd.DataFrame, min_factors: int) -> pd.DataFrame:
    if not 1 <= min_factors <= fw.K:
        raise ValueError(f"min_factors must be between 1 and {fw.K}")
    data = scores[scores["eligible_universe_flag"] == 1].merge(week_weights, on="signal_date", how="left")
    if data[WEIGHT_COLS].isna().any().any():
        missing = sorted(data.loc[data[WEIGHT_COLS[0]].isna(), "signal_date"].unique())
        raise ValueError(f"factor weights missing for signal dates: {missing[:5]} ...")

    F = data[SCORE_COLS].to_numpy(dtype=float)
    avail = (data[AVAIL_COLS].to_numpy() == 1) & np.isfinite(F)
    W = data[WEIGHT_COLS].to_numpy(dtype=float)

    used_w = np.where(avail, W, 0.0)
    weight_used = used_w.sum(axis=1)
    n_avail = avail.sum(axis=1)
    ok = (n_avail >= min_factors) & (weight_used > 0)
    raw = (used_w * np.where(avail, F, 0.0)).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        composite = np.where(ok, raw / weight_used, np.nan)

    out = data[["signal_date", "execution_date", "PERMNO", "historical_gics_sector"] + SCORE_COLS + WEIGHT_COLS].copy()
    out[SCORE_COLS] = np.where(avail, F, np.nan)   # a score flagged unavailable is shown as blank
    out["effective_factor_count"] = n_avail
    out["weight_used_sum"] = weight_used
    reallocated = np.where(np.abs(1.0 - weight_used) < 1e-12, 0.0, 1.0 - weight_used)  # drop float noise
    out["reallocated_weight"] = np.where(ok, reallocated, np.nan)
    out["composite_score"] = composite
    out["score_available"] = ok.astype(int)
    # percentile rank within the week among scored stocks (1 = best); used by stock selection
    out["score_rank_pct"] = (out[out["score_available"] == 1]
                             .groupby("signal_date")["composite_score"].rank(pct=True, ascending=False))
    return out


# ---------------------------------------------------------------- 3. checks
def missingness_report(out: pd.DataFrame) -> pd.DataFrame:
    """Per week: eligible count, scored count, factor-count distribution, reallocated weight."""
    flagged = out.assign(incomplete_scored=((out["score_available"] == 1)
                                            & (out["effective_factor_count"] < fw.K)).astype(int))
    g = flagged.groupby("signal_date")
    rep = pd.DataFrame({
        "eligible": g.size(),
        "scored": g["score_available"].sum(),
        "incomplete_scored": g["incomplete_scored"].sum(),
        "mean_reallocated_weight": g["reallocated_weight"].mean(),
    })
    counts = out.pivot_table(index="signal_date", columns="effective_factor_count",
                             values="PERMNO", aggfunc="count", fill_value=0)
    counts.columns = [f"n_with_{c}_factors" for c in counts.columns]
    return rep.join(counts)


def check_scores(out: pd.DataFrame, n_spot: int = 3, seed: int = 0) -> list[str]:
    problems = []
    if (out["signal_date"] > fr.GATE_B_LAST_DATE).any():
        problems.append("signal date after the Gate B window")
    if not np.allclose(out[WEIGHT_COLS].sum(axis=1), 1.0, atol=1e-12):
        problems.append("factor weights do not sum to 1")
    if out.duplicated(["signal_date", "PERMNO"]).any():
        problems.append("duplicate signal_date x PERMNO")
    # independent recomputation for a few stocks, written the slow way
    scored = out[out["score_available"] == 1]
    for _, r in scored.sample(min(n_spot, len(scored)), random_state=seed).iterrows():
        num = den = 0.0
        for s, w in zip(SCORE_COLS, WEIGHT_COLS):
            if np.isfinite(r[s]):
                num += r[w] * r[s]
                den += r[w]
        if abs(num / den - r["composite_score"]) > 1e-12:
            problems.append(f"spot check failed for {r['PERMNO']} on {r['signal_date']}")
    return problems


def spot_check_table(out: pd.DataFrame, n: int = 3, seed: int = 0) -> pd.DataFrame:
    scored = out[out["score_available"] == 1]
    cols = ["signal_date", "PERMNO"] + SCORE_COLS + WEIGHT_COLS + ["composite_score"]
    return scored.sample(min(n, len(scored)), random_state=seed)[cols]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default=fr.GATE_B_LAST_DATE)
    parser.add_argument("--J", type=int, default=fw.BASELINE["J"], choices=fw.J_GRID)
    parser.add_argument("--lam", type=float, default=fw.BASELINE["lam"], choices=fw.LAMBDA_GRID)
    parser.add_argument("--risk-adjusted", action="store_true")
    parser.add_argument("--min-factors", type=int, default=fw.K, choices=[3, 4])
    parser.add_argument("--weights-csv", default=WEIGHTS_FILE)
    args = parser.parse_args()
    root = fr.project_root()

    weights = pd.read_csv(root / args.weights_csv)
    week_weights = weights_for_config(weights, args.J, args.lam, args.risk_adjusted)
    out = compute_scores(load_scores(root, args.start, args.end), week_weights, args.min_factors)
    tag = f"J{args.J}_lam{args.lam:g}_ra{int(args.risk_adjusted)}_min{args.min_factors}"
    for c, v in (("J", args.J), ("lambda", args.lam), ("risk_adjusted", int(args.risk_adjusted)),
                 ("min_factors", args.min_factors)):
        out[c] = v

    out_dir = root / "outputs/strategy"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"stock_scores_weekly_{args.start[:4]}_{args.end[:4]}_{tag}.csv"
    out.to_csv(out_file, index=False)
    rep = missingness_report(out)
    rep.to_csv(out_dir / f"score_missingness_weekly_{args.start[:4]}_{args.end[:4]}_{tag}.csv")

    print(f"Wrote {len(out)} rows to {out_file.relative_to(root)}")
    print(f"weeks: {out['signal_date'].nunique()}; eligible per week (avg): {rep['eligible'].mean():.1f}; "
          f"scored per week (avg): {rep['scored'].mean():.1f}")
    print(f"stock-weeks by effective_factor_count:\n{out['effective_factor_count'].value_counts().sort_index().to_string()}")
    print(f"incomplete-factor stocks among scored (avg share): "
          f"{(rep['incomplete_scored'] / rep['scored']).mean():.4f}; "
          f"mean reallocated weight among scored: {out['reallocated_weight'].mean():.4f}")
    print("\nSpot check (recompute these by hand: sum of w x score over available factors / sum of those w):")
    print(spot_check_table(out).to_string(index=False))
    problems = check_scores(out)
    print("\nPASS: weights sum to 1, no duplicates, spot checks match" if not problems
          else "\nCHECK: " + "; ".join(problems))


if __name__ == "__main__":
    main()
