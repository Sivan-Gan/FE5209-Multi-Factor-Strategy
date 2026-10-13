"""Run the frozen US Base Strategy (Gate C) for one sample period.

The configuration is read ONLY from config/strategy/us_base_strategy.json; nothing is chosen here.

  python -m src.strategy.run_frozen --period gate_b     # 2016-2020: Gate C acceptance run
  python -m src.strategy.run_frozen --period hist_oos   # 2021-2025: only after status = FROZEN

The strategy is run continuously from 2016-01-01 (FMOM warm-up from the frozen seed) up to the
period end, so a HIST_OOS run starts from the drifted end-2020 book rather than from cash;
only the requested period's rows are written.

Outputs (outputs/strategy/frozen/<period>/, not committed):
  target_holdings.csv, trade_ledger.csv, factor_weights.csv, weekly_path.csv,
  acceptance_test_report.csv (SSOT 0.10), run_manifest.csv (SSOT 0.8).
For gate_b the weekly metrics are printed and compared with the Gate B summary row.
For hist_oos NO performance statistics are printed: the formal OOS evaluation is M5's,
on daily NAV, done once.
"""
from __future__ import annotations

import argparse
import hashlib
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.strategy import acceptance as ac
from src.strategy import composite_score as cs
from src.strategy import factor_returns as fr
from src.strategy import fmom_weights as fw
from src.strategy import governance as gv
from src.strategy import metrics as mt
from src.strategy import portfolio as pf
from src.strategy import run_gate_b as rg

RUN_FROM = "2016-01-01"


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True,
                              cwd=gv.project_root()).stdout.strip()
    except Exception:
        return "UNKNOWN"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def run(period: str) -> str:
    t0 = time.time()
    root = gv.project_root()
    cfg = gv.load_config(root)
    p = cfg["parameters"]
    start, end, role = gv.PERIODS[period]
    allow_oos = period != "gate_b"
    gv.check_window(end, allow_oos, root)                     # raises unless FROZEN for hist_oos
    cost = cfg["transaction_cost"]["oneway_bps"] / 1e4
    tag = cfg["gate_b_selection"]["tag"]
    print(f"[{period}] {tag}  window {start}..{end}  config status {cfg['status']}  cost {cost * 1e4:g} bp")

    # Step 16: factor returns from 2016 to the period end (needed for FMOM history)
    fret = fr.compute_factor_returns(*fr.load_inputs(root, RUN_FROM, end, allow_oos), end=end)
    # Steps 17-19: FMOM weights for the frozen configuration only
    seed = fw._clean(pd.read_parquet(root / fw.SEED_FILE), "seed")
    hist = fw.combine_history([seed, fw.fill_seed_boundary(root, seed), fw._clean(fret, "step16")])
    wide, next_exec = fw.to_wide(hist)
    dates = [d for d in wide.index if RUN_FROM <= d <= end]
    weights = fw.compute_weights(wide, next_exec, dates, p["fmom_lookback_weeks_J"], p["lambda"],
                                 p["risk_adjusted_fmom"])
    # Step 20: composite score
    week_w = cs.weights_for_config(weights, p["fmom_lookback_weeks_J"], p["lambda"], p["risk_adjusted_fmom"])
    scored = cs.compute_scores(cs.load_scores(root, RUN_FROM, end, allow_oos), week_w, p["min_available_factors"])
    # Step 21: holdings (continuous from 2016 so the buffer has its real history)
    hold_all, constr_all = pf.build_holdings(scored, p["buffer"])
    # Step 22 accounting: path + trade ledger
    returns = rg.load_returns(root, RUN_FROM, end)
    path_all, ledger_all = mt.weekly_path_and_ledger(hold_all, returns, end, cost)

    def window(df):
        return df[df["signal_date"].between(start, end)].reset_index(drop=True)

    hold, constr, path, ledger, w_out = (window(hold_all), window(constr_all), window(path_all),
                                         window(ledger_all), window(weights))
    report = ac.run_checks(weights=w_out, scored=window(scored), hold=hold, construction=constr, path=path,
                           ledger=ledger, returns=returns, cost=cost, start=start, end=end)

    out = root / "outputs/strategy/frozen" / period
    out.mkdir(parents=True, exist_ok=True)
    hold.to_csv(out / "target_holdings.csv", index=False)
    ledger.to_csv(out / "trade_ledger.csv", index=False)
    w_out.to_csv(out / "factor_weights.csv", index=False)
    path.assign(net_return=mt.net_returns(path, cost)).to_csv(out / "weekly_path.csv", index=False)

    # gate_b only: reproduce the Gate B selection row
    if period == "gate_b":
        perf = mt.performance(mt.net_returns(path.set_index("signal_date"), cost))
        summ = root / "outputs/strategy/gate_b/gate_b_config_summary.csv"
        detail = "Gate B summary not found"
        ok = True
        if summ.exists():
            ref = pd.read_csv(summ).set_index("tag")
            if tag in ref.index and ref.loc[tag, "cost_bps"] == cost * 1e4:
                gross_ref = ref.loc[tag, "gross_ann_return"]
                gross_now = mt.performance(path.set_index("signal_date")["gross_return"])["ann_return"]
                d = max(abs(gross_ref - gross_now), abs(ref.loc[tag, "net_ann_return"] - perf["ann_return"]))
                ok = d < 1e-9
                detail = f"max |ann return diff| (gross, net) = {d:.2e}"
        report = pd.concat([report, pd.DataFrame([ac._row(
            "REPRO_01", "Reproduction", "frozen run reproduces the Gate B row for the selected config",
            ok, detail, critical=False)])], ignore_index=True)
        print(f"  weekly net (provisional cost): ann return {perf['ann_return']:.4f}, "
              f"vol {perf['ann_vol']:.4f}, max DD {perf['max_drawdown']:.4f}")

    status = ac.verdict(report)
    report.to_csv(out / "acceptance_test_report.csv", index=False)

    run_id = f"US_BASE_{period}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    data = root / "handoff"
    inputs = ["final_factor/data/factor_scores_weekly.parquet",
              "us_downstream/data/security_weekly_execution_returns.parquet",
              "us_downstream/data/final_factor_returns_seed_weekly.parquet"]
    manifest = {
        "run_id": run_id,
        "run_timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "market": "US",
        "sample_role": role,
        "sample_start": start, "sample_end": end,
        "strategy_id": cfg["strategy_id"], "selected_config": tag,
        "data_version": "; ".join(f"{i}={_sha(data / i)}" for i in inputs),
        "universe_version": "handoff/final_factor eligible_universe_flag (frozen Gate A handoff)",
        "factor_formula_version": "K=4 frozen Gate A: " + ",".join(p["final_factor_set"]),
        "config_version": cfg["config_version"], "config_hash": gv.config_hash(root)[:16],
        "config_status": cfg["status"],
        "code_commit": _git("rev-parse", "HEAD"),
        "code_dirty": "YES" if _git("status", "--porcelain", "src/strategy") else "NO",
        "environment": f"python {platform.python_version()}; pandas {pd.__version__}",
        "output_artifact_version": run_id,
        "primary_owner": cfg["primary_owner"], "reviewer": cfg["reviewer"],
        "gate_status": status,
        "cost_oneway_bps": cost * 1e4,
        "n_weeks": int(hold["signal_date"].nunique()), "n_holding_rows": len(hold), "n_trades": len(ledger),
    }
    pd.DataFrame([manifest]).to_csv(out / "run_manifest.csv", index=False)

    pd.set_option("display.width", 200)
    print("\n" + report[["check_id", "status", "critical", "description", "detail"]].to_string(index=False))
    print(f"\nrun_id {run_id}  ->  {status}   ({time.time() - t0:.0f}s, outputs in {out.relative_to(root)})")
    if manifest["code_dirty"] == "YES":
        print("NOTE: src/strategy has uncommitted changes; commit before the official Gate C run.")
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--period", choices=list(gv.PERIODS), default="gate_b")
    args = parser.parse_args()
    run(args.period)


if __name__ == "__main__":
    main()
