"""Steps 17-19: factor momentum (FMOM) and blended factor weights for Gate B.

Rules (SSOT 4.3 D/E and Steps 17-19):
  - FMOM_{k,t} = sum of the last J *completed* weekly factor returns, where a
    factor-return period u counts as completed at signal cutoff S_t only if
    E_{u+1} <= S_t (its holding period has fully ended).  In practice the most
    recent usable period is usually two signal weeks back.
  - All J periods in the window must have a valid return for every factor;
    missing returns are never filled with 0.  If any factor fails, the whole
    dynamic component falls back to equal weight that week.
  - Optional risk adjustment: FMOM / std(last 104 completed returns), needing
    104 valid weeks.
  - S = max(FMOM, 0); w_FMOM = S / sum(S), or 1/K if every S is 0.
  - w = (1 - lambda) / K + lambda * w_FMOM.

Grid compared in Gate B: J in {13, 26, 52}, lambda in {0, 0.5, 1},
risk adjustment off / on.  Baseline candidate: J=26, lambda=0.5, off.

Inputs:
  - handoff/us_downstream/data/final_factor_returns_seed_weekly.parquet (2006-2015, frozen)
  - outputs/strategy/factor_returns_weekly_2016_2020.csv (Step 16 output)

Usage (run from the repository root):
  python -m src.strategy.fmom_weights
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from src.strategy import factor_returns as fr

FAMILIES = [family for family, _, _, _ in fr.FINAL_FACTORS]
K = len(FAMILIES)
J_GRID = (13, 26, 52)
LAMBDA_GRID = (0.0, 0.5, 1.0)
RISK_ADJ_GRID = (False, True)
RA_WINDOW = 104
BASELINE = {"J": 26, "lam": 0.5, "risk_adjusted": False}
SEED_LAST_DATE = "2015-12-31"

SEED_FILE = "handoff/us_downstream/data/final_factor_returns_seed_weekly.parquet"
GATE_B_RETURNS_FILE = "outputs/strategy/factor_returns_weekly_2016_2020.csv"
HISTORY_COLS = ["signal_date", "next_execution_date", "family",
                "factor_return_gross", "factor_return_available"]


# ------------------------------------------------------------ 1. history
def _clean(df: pd.DataFrame, source: str) -> pd.DataFrame:
    df = df[HISTORY_COLS].copy()
    df["signal_date"] = df["signal_date"].astype(str)
    df["next_execution_date"] = df["next_execution_date"].fillna("").astype(str)
    df["factor_return_available"] = df["factor_return_available"].fillna(0).astype(int)
    df["source"] = source
    return df


def fill_seed_boundary(root: Path, seed: pd.DataFrame) -> pd.DataFrame:
    """Recompute the last 2015 signal weeks whose holding period ends in 2016.

    The seed stops at 2015-12-31, so a week formed in late December 2015 and
    held into January 2016 may be marked unavailable there.  Those returns
    are fully realized well before 2020-12-31, so Gate B may use them; without
    them every early-2016 FMOM window would contain a hole and fall back to
    equal weight for up to J weeks.
    """
    gap = seed[(seed["factor_return_available"] == 0)
               & (seed["signal_date"] >= "2015-11-01")
               & ((seed["next_execution_date"] > SEED_LAST_DATE) | (seed["next_execution_date"] == ""))]
    if gap.empty:
        return pd.DataFrame(columns=HISTORY_COLS + ["source"])
    start = gap["signal_date"].min()
    scores, returns = fr.load_inputs(root, start, SEED_LAST_DATE)
    redo = fr.compute_factor_returns(scores, returns, end=fr.GATE_B_LAST_DATE)
    return _clean(redo, "step16_boundary_recompute")


def load_history(root: Path, returns_csv: Path) -> pd.DataFrame:
    """Seed (2006-2015) + Step 16 output (2016-2020), one row per week x factor.

    Where the same week appears twice, an available return wins; ties go to
    the frozen seed.
    """
    seed = _clean(pd.read_parquet(root / SEED_FILE), "seed")
    parts = [seed, fill_seed_boundary(root, seed), _clean(pd.read_csv(returns_csv), "step16")]
    hist = pd.concat(parts, ignore_index=True)
    hist["_seed_first"] = (hist["source"] != "seed").astype(int)
    hist = (hist.sort_values(["signal_date", "family", "factor_return_available", "_seed_first"],
                             ascending=[True, True, False, True])
                .drop_duplicates(["signal_date", "family"])
                .drop(columns="_seed_first"))
    if (hist["signal_date"] > fr.GATE_B_LAST_DATE).any():
        raise ValueError("history contains signal dates after the Gate B window")
    return hist.reset_index(drop=True)


def to_wide(hist: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Returns matrix (signal_date x family, NaN = unavailable) and E_{u+1} per week."""
    valid = hist[hist["factor_return_available"] == 1]
    wide = (valid.pivot(index="signal_date", columns="family", values="factor_return_gross")
                 .reindex(index=sorted(hist["signal_date"].unique()), columns=FAMILIES))
    next_exec = (hist[hist["next_execution_date"] != ""]
                 .groupby("signal_date")["next_execution_date"].first()
                 .reindex(wide.index).fillna(""))
    return wide, next_exec


# -------------------------------------------------- 2. one configuration
def compute_weights(wide: pd.DataFrame, next_exec: pd.Series, signal_dates: list[str],
                    J: int, lam: float, risk_adjusted: bool) -> pd.DataFrame:
    returns = wide.to_numpy(dtype=float)
    ends = next_exec.to_numpy(dtype=object)
    dates = wide.index.to_numpy(dtype=object)
    need = max(J, RA_WINDOW) if risk_adjusted else J
    rows = []
    for s_t in signal_dates:
        # completed periods: E_{u+1} known and <= S_t.  Weeks are in signal order and
        # E_{u+1} is increasing, but a mask is used so a blank date can never sneak in.
        done = np.flatnonzero((ends != "") & (ends <= s_t))
        fmom = np.full(K, np.nan)
        signal = np.full(K, np.nan)
        status, first, last, last_end = "OK", "", "", ""
        if len(done) < need:
            status = "FALLBACK_INSUFFICIENT_HISTORY"
        else:
            window = done[-J:]
            first, last, last_end = dates[window[0]], dates[window[-1]], ends[window[-1]]
            block = returns[window]
            if np.isnan(block).any():
                status = "FALLBACK_MISSING_FACTOR_RETURN"
            else:
                fmom = block.sum(axis=0)
                signal = fmom.copy()
                if risk_adjusted:
                    ra_block = returns[done[-RA_WINDOW:]]
                    sigma = np.nanstd(ra_block, axis=0, ddof=1)
                    if np.isnan(ra_block).any():
                        status = "FALLBACK_MISSING_FACTOR_RETURN_RA"
                    elif (sigma <= 0).any():
                        status = "FALLBACK_ZERO_VOLATILITY"
                    else:
                        signal = fmom / sigma
        if status == "OK":
            positive = np.maximum(signal, 0.0)
            if positive.sum() > 0:
                w_fmom = positive / positive.sum()
            else:
                w_fmom, status = np.full(K, 1.0 / K), "FALLBACK_ALL_NONPOSITIVE"
        else:
            positive, w_fmom = np.full(K, np.nan), np.full(K, 1.0 / K)
        weight = (1 - lam) / K + lam * w_fmom
        for k, family in enumerate(FAMILIES):
            rows.append({
                "signal_date": s_t, "J": J, "lambda": lam, "risk_adjusted": int(risk_adjusted),
                "family": family, "specification_id": fr.FINAL_FACTORS[k][1],
                "fmom": fmom[k], "fmom_signal": signal[k], "fmom_positive": positive[k],
                "w_fmom": w_fmom[k], "w_equal": 1.0 / K, "weight": weight[k],
                "dynamic_status": status,
                "window_first_signal_date": first, "window_last_signal_date": last,
                "window_last_next_execution_date": last_end,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 3. checks
def check_weights(out: pd.DataFrame) -> list[str]:
    problems = []
    keys = ["signal_date", "J", "lambda", "risk_adjusted"]
    sums = out.groupby(keys)["weight"].sum()
    if not np.allclose(sums, 1.0, atol=1e-12):
        problems.append(f"weights do not sum to 1 in {(~np.isclose(sums, 1.0)).sum()} weeks")
    if (out["weight"] < -1e-15).any():
        problems.append("negative weight found")
    used = out[out["window_last_next_execution_date"] != ""]
    if (used["window_last_next_execution_date"] > used["signal_date"]).any():
        problems.append("look-ahead: a window uses a return that ends after the signal date")
    if (out["signal_date"] > fr.GATE_B_LAST_DATE).any():
        problems.append("signal date after the Gate B window")
    if (out.groupby(keys)["family"].count() != K).any():
        problems.append("not every week has all K factors")
    return problems


def summarize(out: pd.DataFrame) -> pd.DataFrame:
    keys = ["J", "lambda", "risk_adjusted"]
    weekly = out.drop_duplicates(keys + ["signal_date"])
    status = (weekly.assign(ok=weekly["dynamic_status"].eq("OK"))
                    .groupby(keys).agg(weeks=("signal_date", "size"), dynamic_ok_weeks=("ok", "sum")))
    avg_w = out.pivot_table(index=keys, columns="family", values="weight", aggfunc="mean")[FAMILIES]
    avg_w.columns = [f"avg_w_{c.replace(' ', '')}" for c in avg_w.columns]
    return status.join(avg_w).round(4)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default=fr.GATE_B_LAST_DATE)
    parser.add_argument("--returns-csv", default=GATE_B_RETURNS_FILE)
    args = parser.parse_args()
    if args.end > fr.GATE_B_LAST_DATE:
        raise ValueError(f"Gate B may not go beyond {fr.GATE_B_LAST_DATE}; got end={args.end}")
    root = fr.project_root()

    hist = load_history(root, root / args.returns_csv)
    boundary = (hist["source"] == "step16_boundary_recompute").sum()
    print(f"history: {hist['signal_date'].nunique()} weeks "
          f"({hist['signal_date'].min()} .. {hist['signal_date'].max()}); "
          f"boundary week x factor rows recomputed: {boundary}")
    wide, next_exec = to_wide(hist)
    signal_dates = [d for d in wide.index if args.start <= d <= args.end]

    out = pd.concat([compute_weights(wide, next_exec, signal_dates, J, lam, ra)
                     for J in J_GRID for lam in LAMBDA_GRID for ra in RISK_ADJ_GRID],
                    ignore_index=True)
    problems = check_weights(out)

    out_file = root / f"outputs/strategy/factor_weights_weekly_{args.start[:4]}_{args.end[:4]}.csv"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_file, index=False)
    print(f"Wrote {len(out)} rows to {out_file.relative_to(root)}\n")
    print(summarize(out).to_string())

    base = out[(out["J"] == BASELINE["J"]) & (out["lambda"] == BASELINE["lam"])
               & (out["risk_adjusted"] == int(BASELINE["risk_adjusted"]))]
    first = base.drop_duplicates("signal_date").iloc[0]
    print(f"\nBaseline first week {first['signal_date']}: window "
          f"{first['window_first_signal_date']} .. {first['window_last_signal_date']} "
          f"(last return ends {first['window_last_next_execution_date']}), status {first['dynamic_status']}")
    print("\nPASS: weights sum to 1, no negatives, no look-ahead" if not problems
          else "\nCHECK: " + "; ".join(problems))


if __name__ == "__main__":
    main()
