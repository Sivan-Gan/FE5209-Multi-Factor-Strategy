"""Audit existing actions and run fixed-NAV capacity upper-bound screens locally."""
import argparse
import hashlib
import io
import zipfile
from pathlib import Path
import pandas as pd
from support import save_json, digest
from legacy_adapter import PREFIX
from sgbt.data_quality import corporate_action_audit
from sgbt.capacity import fixed_nav_capacity


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--targets', type=Path, required=True)
    p.add_argument('--sessions', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    out = args.output.resolve()
    inputs = [args.archive, args.targets, args.sessions]
    if out.exists() and any(out.iterdir()):
        p.error('Output must be new or empty')
    if any(path.resolve().is_relative_to(out) for path in inputs):
        p.error('Output cannot contain input files')
    original_hash = digest(args.archive)
    members = ['data/prices_daily_2006.csv', 'data/corporate_actions_2006.csv']
    with zipfile.ZipFile(args.archive) as z:
        if any(z.namelist().count(PREFIX+name) != 1 for name in members):
            p.error('Expected unique old-data price and event members')
        content = {name: z.read(PREFIX+name) for name in members}
    bars = pd.read_csv(io.BytesIO(content[members[0]]), parse_dates=['date'])
    actions = pd.read_csv(io.BytesIO(content[members[1]]), parse_dates=['date'])
    targets = pd.read_csv(args.targets, parse_dates=['signal_date', 'execution_date'])
    targets = targets[targets.signal_date.ge('2021-01-01')].copy()
    sessions = pd.DatetimeIndex(pd.to_datetime(pd.read_csv(args.sessions).date))
    out.mkdir(parents=True, exist_ok=True)
    qa = corporate_action_audit(bars, actions)
    save_json(out/'corporate_action_readiness.json', qa['summary'])
    qa['events'].to_csv(out/'corporate_action_events_AUDIT.csv', index=False)
    qa['adjustment_steps'].to_csv(out/'price_adjustment_steps_AUDIT.csv', index=False)
    detail, summary = fixed_nav_capacity(bars, sessions, targets)
    detail.to_csv(out/'fixed_nav_capacity_orders.csv', index=False)
    summary.to_csv(out/'fixed_nav_capacity_summary.csv', index=False)
    save_json(out/'manifest.json', dict(
        source_archive_sha256=original_hash,
        source_member_hashes={name: hashlib.sha256(raw).hexdigest() for name, raw in content.items()},
        targets_sha256=digest(args.targets), sessions_sha256=digest(args.sessions),
        source_strategy_unchanged=True, original_archive_unchanged=digest(args.archive)==original_hash,
        capacity_scope='fixed_NAV_independent_cross_sectional_upper_bound_not_NAV_backtest',
        resets='hypothetical normalization of idealized previous target price drift; not external cash flows',
        modes=['rebalance_from_previous_idealized_target', 'fresh_build_each_week', 'liquidate_target_each_week'],
        limitations=['cash, settlement, spread, queue and market impact omitted from capacity upper bound',
                     'source close/volume share basis not verified; all capacity conclusions provisional',
                     'future daily volumes used only for ex-post diagnosis; never for strategy signals',
                     '95% is an illustrative screen, not an agreed project gate or industry standard',
                     'incomplete terminal windows censored; snapshots are not a constant-NAV return series'],
        action_scope='inventory and price consistency only; no inferred payment dates or certified total return'))
    print(summary[['nav', 'volume_multiplier', 'mode', 'completion_upper_bound_5d']].to_string(index=False))
    print(out)


if __name__ == '__main__':
    main()
