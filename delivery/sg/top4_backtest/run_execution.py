"""Portable target-file execution entry point, with explicit data readiness gate."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sgbt.engine import ExecutionConfig, plans_from_targets, simulate
from sgbt.metrics import execution_metrics
from sgbt.risk import risk_diagnostics
from support import digest, save_json


def main():
    parser = argparse.ArgumentParser()
    for field in ['prices', 'sessions', 'targets', 'security-master', 'settings', 'output']:
        parser.add_argument('--'+field, type=Path, required=True)
    parser.add_argument('--actions', type=Path)
    parser.add_argument('--rules', type=Path)
    parser.add_argument('--verification', type=Path)
    parser.add_argument('--strategy', help='Required if target file has multiple strategy IDs')
    parser.add_argument('--end', required=True, help='Last marking session, YYYY-MM-DD')
    parser.add_argument('--allow-price-only', action='store_true', help='Explicitly allow non-TR diagnostics')
    args = parser.parse_args()
    files = {name: value for name, value in vars(args).items() if isinstance(value, Path) and name != 'output'}
    out = args.output.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error('Output directory must be new or empty')
    if any(path.resolve().is_relative_to(out) for path in files.values()):
        parser.error('Output cannot contain input files')
    config = json.loads(args.settings.read_text(encoding='utf-8'))
    config['action_policy'] = 'price_only_diagnostic' if args.allow_price_only else 'strict'
    c = ExecutionConfig(**config)
    p = pd.read_csv(args.prices)
    sessions = pd.read_csv(args.sessions).date
    targets = pd.read_csv(args.targets)
    if 'strategy' in targets:
        strategies = targets.strategy.drop_duplicates().tolist()
        if args.strategy:
            targets = targets[targets.strategy.eq(args.strategy)].copy()
        elif len(strategies) != 1:
            parser.error('Select exactly one --strategy')
    for col in ['signal_date', 'execution_date']:
        targets[col] = pd.to_datetime(targets[col])
    if targets.empty or targets.duplicated(['signal_date', 'ticker']).any():
        parser.error('Empty or duplicate targets')
    if (targets.groupby('signal_date').execution_date.nunique() != 1).any():
        parser.error('Inconsistent execution date')
    if not np.isfinite(targets.weight).all() or (targets.weight < 0).any():
        parser.error('Invalid target weights')
    master = pd.read_csv(args.security_master)
    if master.ticker.duplicated().any():
        parser.error('Duplicate security master')
    currencies = master.set_index('ticker').currency.reindex(targets.ticker.unique())
    if not currencies.eq('SGD').all():
        parser.error('This version supports SGD only; do not silently aggregate currencies')
    verification = json.loads(args.verification.read_text(encoding='utf-8')) if args.verification else {}
    if not args.allow_price_only:
        for flag in ['corporate_actions_verified', 'historical_currency_verified',
                     'session_calendar_verified', 'instrument_rules_verified',
                     'signals_pit_verified', 'historical_universe_verified']:
            if verification.get(flag) is not True:
                parser.error(f'Strict data gate not satisfied: {flag}')
        if verification.get('price_basis') != 'unadjusted' or verification.get('volume_unit') != 'shares':
            parser.error('Strict mode requires unadjusted prices and share volume')
        if not verification.get('source') or not args.actions or not args.rules:
            parser.error('Provide source description, complete actions file (may be empty), and historical rules')
        first, last = pd.Timestamp(verification.get('start_date')), pd.Timestamp(verification.get('end_date'))
        if pd.isna(first) or pd.isna(last) or first > targets.signal_date.min() or last < pd.Timestamp(args.end):
            parser.error('Verified data coverage does not span simulation dates')
    actions = pd.read_csv(args.actions) if args.actions else None
    rules = pd.read_csv(args.rules) if args.rules else None
    if not args.allow_price_only:
        if rules.empty:
            parser.error('Strict mode requires historical instrument rules')
        rules['effective_date'] = pd.to_datetime(rules.effective_date)
        first_rule = rules.groupby('ticker').effective_date.min()
        first_target = targets.groupby('ticker').signal_date.min()
        if first_rule.reindex(first_target.index).isna().any() or \
           (first_rule.reindex(first_target.index) > first_target).any():
            parser.error('Instrument rules do not cover first target date')
    verified = not args.allow_price_only
    result = simulate(p, sessions, plans_from_targets(targets), c, actions=actions,
                      actions_verified=verified, rules=rules, end=args.end)
    out.mkdir(parents=True, exist_ok=True)
    for name, value in result.items():
        if isinstance(value, pd.DataFrame):
            value.to_csv(out/f'{name}.csv', index=False)
    risk_diagnostics(result, master).to_csv(out/'risk_diagnostics.csv', index=False)
    save_json(out/'audit.json', result['audit'])
    save_json(out/'summary.json', execution_metrics(result))
    save_json(out/'run_manifest.json', dict(inputs={key: dict(path=str(path.resolve()), sha256=digest(path))
                                                   for key, path in files.items()},
                                           verification=verification, settings=asdict(c),
                                           caveat='Verification is supplied evidence metadata, not automatic vendor certification'))
    print(out)


if __name__ == '__main__':
    main()
