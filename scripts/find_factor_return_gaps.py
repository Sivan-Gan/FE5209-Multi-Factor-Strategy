"""List real gaps in the weekly factor-return history (seed 2006-2015 + Step 16 2016-2020)
and the stocks with an invalid return status in those weeks.

Run from the repository root:  python scripts/find_factor_return_gaps.py
Read-only: it only prints, it writes nothing.
"""
import pandas as pd

SINCE = "2013-01-01"   # earlier gaps cannot reach a 104-week window that starts in 2016
VALID = {"VALID", "VALID_CONFIRMED_DELISTING_CASH"}

seed = pd.read_parquet("handoff/us_downstream/data/final_factor_returns_seed_weekly.parquet")
mine = pd.read_csv("outputs/strategy/factor_returns_weekly_2016_2020.csv")
gaps = pd.concat([seed.assign(source="seed 2006-2015"), mine.assign(source="Step16 2016-2020")])
gaps["signal_date"] = gaps["signal_date"].astype(str)
gaps["unavailable_reason"] = gaps["unavailable_reason"].fillna("")
gaps = gaps[(gaps["factor_return_available"] == 0)
            & (gaps["unavailable_reason"] != "ENDPOINT_AFTER_WINDOW")
            & (gaps["signal_date"] >= SINCE)]

pd.set_option("display.width", 200)
print(f"=== 1. Factor-return gaps since {SINCE} ===")
cols = ["source", "signal_date", "next_execution_date", "family", "top_count", "bottom_count", "unavailable_reason"]
print(gaps[cols].to_string(index=False) if len(gaps) else "none")

weeks = sorted(gaps["signal_date"].unique())
if weeks:
    r = pd.read_parquet("handoff/us_downstream/data/security_weekly_execution_returns.parquet",
                        columns=["signal_date", "PERMNO", "execution_feasible",
                                 "execution_to_next_execution_total_return", "unavailable_reason"])
    r["signal_date"] = r["signal_date"].astype(str)
    r = r[r["signal_date"].isin(weeks)]
    reason = r["unavailable_reason"].fillna("").replace("", "VALID")
    bad = r[(r["execution_feasible"] == 1) & ~reason.isin(VALID)]
    print("\n=== 2. Executable stocks with an invalid return status in those weeks ===")
    print(bad[["signal_date", "PERMNO", "execution_to_next_execution_total_return", "unavailable_reason"]]
          .to_string(index=False) if len(bad) else "none")
