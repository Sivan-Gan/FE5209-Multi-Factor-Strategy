"""One-click runner: every SG adaptive-strategy variant tested on 2026-10-18 -> one Excel file.

Run from the repository root (PyCharm terminal):

    python -m src.sandbox.run_sg_all_strategies

Needs: pandas, numpy, scipy, openpyxl   (pip install pandas numpy scipy openpyxl)
Takes about 10-20 minutes.  Nothing is uploaded; outputs stay in the private folder.

Inputs (private LSEG-derived data, NOT in git -- get the two zips from the group drive):
  put them in  <repo>/private_data/   (folder is git-ignored), i.e.
      private_data/SGX_comprehensive_2006_2026_handoff(1).zip   (or ..._handoff.zip)
      private_data/SGX_PIT_2006_2026_FINAL_STATIC_DELIVERY.zip
  (fallback: the folder two levels above the repo).  They are extracted once into
  private_data/SG_data_private/ and results go to private_data/SG_results_private/.
  Override with --sg-zip / --pit-zip / --data-dir / --out-dir.

What is run (all weekly, net of one-way cost, rf = 0, benchmark STI total return):
  1. TUNE  : teammate spec (6 factors, Size = small cap, Top-3, 3y, IV) + 143 variants
             (select x weight x names x stock weighting x drawdown control)    -> sg_tune_lab
  2. PIT   : proxy vs PIT-rebuilt fundamentals, 6 and 8 factors, Top-3 / Top-4 -> sg_pit_lab
  3. SIZE  : small-cap vs large-cap Size, proxy and PIT, Top-3 / Top-4
  4. EARLY : first 7-factor exploration (with Dividend; its Size is LARGE cap)  -> sg_adaptive_lab
Then every weekly return series is re-scored with the SAME metric code over the same periods:
  PRE 2016-18, OBS 2019-20 (selection period), EVAL 2021-01..2026-09, EVAL 2021-25, FULL 2016-26.

Output: <out-dir>/SG_strategies_for_meeting.csv   <- open this one in the meeting
  one row per strategy (10 bp), Chinese description of what changed, key metrics, ranked by
  evaluation Sharpe, with the teammate baseline and the recommended Top-4 flagged.
Re-build the tables without re-running the backtests:
    python -m src.sandbox.run_sg_all_strategies --consolidate-only <out-dir>
Full detail: <out-dir>/SG_all_strategies_metrics.xlsx
  sheet summary      one row per strategy, all periods (baseline and recommended flagged)
  sheet yearly       calendar-year returns (signal-date years)
  sheet tune_raw / pit_raw / early_raw   each lab's own result table (incl. trade statistics)
  + the weekly return CSVs.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GROUP = REPO.parents[1]
PRIVATE = REPO / "private_data"          # git-ignored folder inside the repo: put the two zips here
SG_ZIP_NAMES = ("SGX_comprehensive_2006_2026_handoff(1).zip", "SGX_comprehensive_2006_2026_handoff.zip")
PIT_ZIP_NAMES = ("SGX_PIT_2006_2026_FINAL_STATIC_DELIVERY.zip",)


def find_zip(names) -> str:
    """First match in <repo>/private_data, then the folder two levels above the repo."""
    for folder in (PRIVATE, GROUP):
        for n in names:
            if (folder / n).exists():
                return str(folder / n)
    return str(PRIVATE / names[0])


def extract(zip_path: Path, data_dir: Path, folder: str) -> Path:
    target = data_dir / folder
    if target.exists():
        return target
    if not zip_path.exists():
        sys.exit(f"Missing input: {zip_path}\nPut the zip there or pass the path with the command-line option.")
    print(f"extracting {zip_path.name} -> {data_dir}")
    data_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(data_dir)
    return target


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sg-zip", default=find_zip(SG_ZIP_NAMES))
    ap.add_argument("--pit-zip", default=find_zip(PIT_ZIP_NAMES))
    base = PRIVATE if PRIVATE.exists() else GROUP
    ap.add_argument("--data-dir", default=str(base / "SG_data_private"))
    ap.add_argument("--out-dir", default=str(base / "SG_results_private" / "all_strategies"))
    ap.add_argument("--skip-early", action="store_true", help="skip the first 7-factor exploration (~4 min)")
    args = ap.parse_args()

    data_dir, out = Path(args.data_dir), Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sg = extract(Path(args.sg_zip), data_dir, "SGX_comprehensive_2006_2026_handoff")
    pit = extract(Path(args.pit_zip), data_dir, "SGX_PIT_STATIC_DELIVERY_2026_10_18")
    os.environ.update({"SG_DATA": str(sg), "SG_PIT": str(pit),
                       "SG_LAB_OUT": str(out / "early"), "SG_TUNE_OUT": str(out / "tune"),
                       "SG_PIT_OUT": str(out / "pit")})

    # imports only after the environment variables are set (the modules read them at import)
    import numpy as np
    import pandas as pd
    from src.sandbox import sg_adaptive_lab as L
    early_cols = dict(L.COLS)                      # 7-factor column set of the first exploration
    from src.sandbox import sg_tune_lab as T       # sets L.COLS to the 6-factor set
    tune_cols = dict(L.COLS)
    from src.sandbox import sg_pit_lab as S
    from src.strategy import metrics as mt

    t0 = time.time()
    print("\n[1/4] TUNE grid (144 variants) ...", flush=True)
    L.COLS = dict(tune_cols)
    T.main()
    print(f"      done {time.time() - t0:.0f}s\n[2/4] PIT comparison ...", flush=True)
    L.COLS = dict(tune_cols)
    S.main()
    print(f"      done {time.time() - t0:.0f}s\n[3/4] SIZE direction check ...", flush=True)
    L.COLS = dict(tune_cols)                       # S.main leaves L.COLS on the PIT columns
    size_paths = size_direction(L, S, mt, np, pd, out / "size")
    if not args.skip_early:
        print(f"      done {time.time() - t0:.0f}s\n[4/4] EARLY 7-factor exploration ...", flush=True)
        L.COLS = dict(early_cols)
        L.POOLS = {"P7": list(early_cols), "P6": [f for f in early_cols if f != "Size"]}
        L.main()
    print(f"      done {time.time() - t0:.0f}s\nconsolidating ...", flush=True)
    consolidate(out, size_paths, mt, pd, np, S, include_early=not args.skip_early)
    print(f"\nALL DONE in {time.time() - t0:.0f}s -> {out / 'SG_all_strategies_metrics.xlsx'}")


def size_direction(L, S, mt, np, pd, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    panel, _, rets, last = L.load_panel()
    end = str(last)
    panel["SizePanel_score"] = panel["Size_score"]                       # high = SMALL cap
    panel["SizePanel_score__available"] = ((panel["Size_available"] == 1) & np.isfinite(panel["Size_score"])).astype(int)
    f = pd.read_csv(S.OUT / "sg_pit_features.csv.gz")
    full = panel.merge(f, on=["signal_date", "PERMNO"], how="left")
    full["SizeBig_panel"] = -full["SizePanel_score"]
    full["SizeBig_panel__available"] = full["SizePanel_score__available"]
    full["SizeBig_PIT"] = -full["Size_PIT"]
    for c in ("Size_PIT", "Value_PIT", "Quality_PIT", "Investment_PIT", "SizeBig_PIT"):
        full[f"{c}__available"] = np.isfinite(full[c]).astype(int)
    elig = full[full["price_eligible"].astype(bool)].copy()

    def uni(cols):
        ok = np.ones(len(elig), bool)
        for c in cols.values():
            ok &= (elig[f"{c}__available"].eq(1) & np.isfinite(pd.to_numeric(elig[c], errors="coerce"))).to_numpy()
        return elig[ok].copy()

    base = {"Quality": "Quality_score", "Momentum": "Momentum_score", "Low Risk": "LowRisk_score",
            "Investment": "Investment_score", "Value": "Value_score"}
    pitb = {"Quality": "Quality_PIT", "Momentum": "Momentum_score", "Low Risk": "LowRisk_score",
            "Investment": "Investment_PIT", "Value": "Value_PIT"}
    specs = {"proxy_small": {**base, "Size": "SizePanel_score"}, "proxy_large": {**base, "Size": "SizeBig_panel"},
             "PIT_small": {**pitb, "Size": "Size_PIT"}, "PIT_large": {**pitb, "Size": "SizeBig_PIT"}}
    u0 = uni(specs["proxy_small"])
    done = set(rets.loc[rets["next_execution_date"] <= end, "signal_date"])
    dates = sorted(d for d in u0["signal_date"].unique() if d >= "2016-01-01" and d in done)
    cal = u0[u0.signal_date.isin(dates)].groupby("signal_date")[["execution_date", "next_execution_date"]].first()
    sti = L.sti_weekly(cal.reset_index())
    paths = {}
    for nm, cols in specs.items():
        for k in (3, 4):
            path, *_ = S.run(uni(cols), rets, cols, end, dates, k, sti)
            paths[f"SIZE_{nm}_Top{k}"] = mt.net_returns(path, 0.001)
    P = pd.DataFrame(paths)
    P.to_csv(out / "size_weekly_returns.csv")
    return P


PERIODS = {"PRE_2016_18": ("2016-01-01", "2018-12-31"), "OBS_2019_20": ("2019-01-01", "2020-12-31"),
           "EVAL_2021_26": ("2021-01-01", "2026-12-31"), "EVAL_2021_25": ("2021-01-01", "2025-12-31"),
           "FULL_2016_26": ("2016-01-01", "2026-12-31")}
KEYS = ("ann_return", "ann_vol", "sharpe", "sortino", "max_drawdown", "calmar", "drawdown_episodes",
        "longest_drawdown_weeks", "period_hit_rate", "beta")


def consolidate(out: Path, size_paths, mt, pd, np, S, include_early: bool):
    series, meta = {}, {}
    tune = pd.read_csv(out / "tune" / "sg_tune_weekly_returns.csv", index_col=0)
    for c in tune.columns:
        if c in ("STI_TR", "EW_UNIVERSE_gross"):
            continue
        sel, w, n, st, ct = c.split("_")
        series[f"TUNE_{c}"] = tune[c]
        meta[f"TUNE_{c}"] = {"group": "1 TUNE (teammate spec + variants)", "data": "proxy", "size": "small",
                             "factors": 6, "select": sel, "weight": w, "names": n, "stock_weight": st,
                             "control": ct, "cost_bp": 10}
    sti, ewu = tune["STI_TR"], tune["EW_UNIVERSE_gross"]
    pitw = pd.read_csv(out / "pit" / "sg_pit_weekly_returns.csv", index_col=0)
    for c in pitw.columns:
        if c == "STI_TR":
            continue
        tag, cost = c.split("@")
        run, k = tag.rsplit("_", 1)
        series[f"PIT_{c}"] = pitw[c]
        meta[f"PIT_{c}"] = {"group": "2 PIT comparison", "data": "PIT" if run.startswith(("C_", "D_")) else "proxy",
                            "size": "small", "factors": 8 if run.startswith("D_") else 6,
                            "select": k.replace("Top", "T") + "L3", "weight": "IV", "names": "N20",
                            "stock_weight": "EQ", "control": "NONE", "cost_bp": int(cost.replace("bp", "")),
                            "note": {"A": "原股票池", "B": "代理因子但限制在PIT股票池",
                                     "C": "PIT重建6因子", "D": "PIT 6因子+低杠杆+现金盈利"}[run[0]]}
    for c in size_paths.columns:
        _, data, size, k = c.split("_")
        series[c] = size_paths[c]
        meta[c] = {"group": "3 SIZE direction", "data": data, "size": size, "factors": 6,
                   "select": k.replace("Top", "T") + "L3", "weight": "IV", "names": "N20", "stock_weight": "EQ",
                   "control": "NONE", "cost_bp": 10}
    if include_early:
        ew = pd.read_csv(out / "early" / "sg_weekly_returns.csv", index_col=0)
        for c in ew.columns:
            if c.endswith(("__gross", "__stress30")) or c in ("STI_TR", "EW_UNIVERSE_gross"):
                continue
            pool, sel, w, st = c.split("_")
            series[f"EARLY_{c}"] = ew[c]
            meta[f"EARLY_{c}"] = {"group": "4 EARLY 7-factor exploration", "data": "proxy",
                                  "size": "large" if pool == "P7" else "none",
                                  "factors": 7 if pool == "P7" else 6, "select": sel, "weight": w, "names": "N20",
                                  "stock_weight": st, "control": "NONE", "cost_bp": 10,
                                  "note": "含股息因子(早期版本)" if pool == "P7" else "含股息因子,不含Size(早期版本)"}
    series["STI_TR"], meta["STI_TR"] = sti, {"group": "0 BENCHMARK", "note": "FTSE STI total return"}
    series["EW_UNIVERSE_gross"], meta["EW_UNIVERSE_gross"] = ewu, {"group": "0 BENCHMARK",
                                                                   "note": "equal weight, same universe, no cost"}

    rows = []
    for tag, r in series.items():
        r = r.dropna()
        row = {"strategy": tag, **meta[tag]}
        for pname, (a, b) in PERIODS.items():
            s = r[(r.index >= a) & (r.index <= b)]
            if len(s) < 10:
                continue
            p = mt.performance(s, None, sti.reindex(s.index))
            row.update({f"{pname}_{k}": p.get(k) for k in KEYS})
        rows.append(row)
    summ = pd.DataFrame(rows)
    flags = {"TUNE_T3L3_IV_N20_EQ_NONE": "BASELINE (teammate)", "TUNE_T4L3_IV_N20_EQ_NONE": "RECOMMENDED (Top-4)",
             "TUNE_T3L5_EW_N20_EQ_NONE": "rule pick on OBS Calmar"}
    summ.insert(1, "flag", summ["strategy"].map(flags).fillna(""))
    summ = summ.sort_values(["group", "EVAL_2021_26_sharpe"], ascending=[True, False])

    yr = {}
    for tag, r in series.items():
        r = r.dropna()
        yr[tag] = (1 + r).groupby(pd.to_datetime(r.index).year).prod() - 1
    yearly = pd.DataFrame(yr).T
    yearly.index.name = "strategy"

    path = out / "SG_all_strategies_metrics.xlsx"
    with pd.ExcelWriter(path) as xw:
        summ.to_excel(xw, sheet_name="summary", index=False)
        yearly.to_excel(xw, sheet_name="yearly")
        pd.read_csv(out / "tune" / "sg_tune_results.csv").to_excel(xw, sheet_name="tune_raw", index=False)
        pd.read_csv(out / "pit" / "sg_pit_results.csv").to_excel(xw, sheet_name="pit_raw", index=False)
        if include_early:
            pd.read_csv(out / "early" / "sg_grid_results.csv").to_excel(xw, sheet_name="early_raw", index=False)
        pd.DataFrame({"period": list(PERIODS), "start": [v[0] for v in PERIODS.values()],
                      "end": [v[1] for v in PERIODS.values()]}).to_excel(xw, sheet_name="periods", index=False)
    summ.to_csv(out / "SG_all_strategies_summary.csv", index=False)
    meeting_csv(summ, out, pd)

    show =["strategy", "flag", "EVAL_2021_26_ann_return", "EVAL_2021_26_sharpe", "EVAL_2021_26_max_drawdown",
            "EVAL_2021_26_calmar", "OBS_2019_20_calmar"]
    pd.set_option("display.width", 220)
    key = summ[summ["flag"].ne("") | summ["strategy"].isin(["STI_TR", "EW_UNIVERSE_gross"])]
    print("\nKey strategies (EVAL = 2021-01 .. 2026-09):")
    print(key[show].round(3).to_string(index=False))
    print(f"\n{len(summ)} strategies in the summary sheet")


ZH = {
    "select": {"T3L3": "每年选3个因子(回看3年)", "T3L5": "每年选3个因子(回看5年)",
               "T4L3": "每年选4个因子(回看3年)", "ALL": "不选因子,全部使用"},
    "weight": {"IV": "因子逆波动率加权", "EW": "因子等权", "MV": "因子最小方差加权"},
    "names": {"N20": "持仓前20%", "N30": "持仓前30%"},
    "stock_weight": {"EQ": "个股等权", "IVOL": "个股按波动率倒数加权", "LOWVOL": "先剔除波动最高20%"},
    "control": {"NONE": "", "DDCTRL": "回撤超10%降半仓"},
}


def describe(r) -> str:
    if str(r.get("group", "")).startswith("0"):
        return {"STI_TR": "基准:STI全收益指数", "EW_UNIVERSE_gross": "基准:同股票池等权(未扣成本)"}[r["strategy"]]
    parts = [f"{int(r['factors'])}因子", {"small": "Size押小盘", "large": "Size押大盘"}.get(r["size"], "不含Size"),
             "PIT数据" if r["data"] == "PIT" else "代理数据"]
    for k in ("select", "weight", "names", "stock_weight", "control"):
        v = ZH[k].get(str(r.get(k)), "")
        if v:
            parts.append(v)
    if isinstance(r.get("note"), str) and r["note"]:
        parts.append(r["note"])
    return ", ".join(parts)


def meeting_csv(summ, out: Path, pd) -> None:
    """Compact, meeting-friendly table: one row per strategy at 10 bp, key metrics, ranked."""
    m = summ[(summ["cost_bp"].isna()) | (summ["cost_bp"] == 10)].copy()
    # the same strategy appears in several groups (e.g. the recommended Top-4 in TUNE, PIT-A and SIZE):
    # keep one row, preferring the earliest group
    key = ["EVAL_2021_26_ann_return", "EVAL_2021_26_sharpe", "EVAL_2021_26_max_drawdown", "PRE_2016_18_sharpe",
           "OBS_2019_20_sharpe"]
    m = m.sort_values(["group", "flag"], ascending=[True, False])
    m = m[~m[key].round(10).duplicated(keep="first")]
    m["说明"] = m.apply(describe, axis=1)
    m = m.sort_values("EVAL_2021_26_sharpe", ascending=False)
    m.insert(0, "评估期夏普排名", range(1, len(m) + 1))
    cols = {"评估期夏普排名": "评估期夏普排名", "flag": "标记", "strategy": "策略代码", "说明": "说明",
            "group": "组别",
            "EVAL_2021_26_ann_return": "评估期年化收益", "EVAL_2021_26_ann_vol": "评估期年化波动",
            "EVAL_2021_26_sharpe": "评估期夏普", "EVAL_2021_26_max_drawdown": "评估期最大回撤",
            "EVAL_2021_26_calmar": "评估期Calmar", "EVAL_2021_26_period_hit_rate": "评估期周胜率",
            "EVAL_2021_26_beta": "评估期Beta(对STI)",
            "OBS_2019_20_sharpe": "观察期夏普", "OBS_2019_20_calmar": "观察期Calmar",
            "PRE_2016_18_sharpe": "2016-18夏普", "PRE_2016_18_calmar": "2016-18Calmar",
            "EVAL_2021_25_ann_return": "2021-25年化收益", "EVAL_2021_25_sharpe": "2021-25夏普",
            "FULL_2016_26_ann_return": "2016-26年化收益", "FULL_2016_26_max_drawdown": "2016-26最大回撤"}
    t = m[list(cols)].rename(columns=cols)
    for c in t.columns:
        if t[c].dtype.kind == "f":
            t[c] = t[c].round(4)
    t.to_csv(out / "SG_strategies_for_meeting.csv", index=False, encoding="utf-8-sig")
    print(f"meeting table: {out / 'SG_strategies_for_meeting.csv'} ({len(t)} strategies)")


if __name__ == "__main__":
    if "--consolidate-only" in sys.argv:          # re-build the tables from saved lab outputs
        import numpy as np
        import pandas as pd
        out = Path(sys.argv[sys.argv.index("--consolidate-only") + 1])
        os.environ.setdefault("SG_PIT_OUT", str(out / "pit"))
        from src.sandbox import sg_pit_lab as S
        from src.strategy import metrics as mt
        sp = pd.read_csv(out / "size" / "size_weekly_returns.csv", index_col=0)
        consolidate(out, sp, mt, pd, np, S, include_early=(out / "early" / "sg_weekly_returns.csv").exists())
    else:
        main()
