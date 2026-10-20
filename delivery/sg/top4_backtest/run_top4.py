"""Reproduce the repository's recommended OLD-DATA Top4, then execute scenarios.

No SGX_TEAM_HANDOFF input, PIT replacement, pipeline.live call or grid selection.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time
import zipfile
import io
import hashlib
import numpy as np
import pandas as pd
import scipy
from support import HERE, REPO, SOURCE_COMMIT, digest, save_json, check_source_lock
from legacy_adapter import (LEGACY_TAG, extract_inputs, load_old_data, source_strategy,
                            canonical_targets, independent_weekly)
from sgbt.engine import ExecutionConfig, plans_from_targets, simulate
from sgbt.metrics import execution_metrics
from sgbt.risk import risk_diagnostics
from sgbt.data_quality import corporate_action_audit


def validate_config(c):
    expected = dict(strategy=LEGACY_TAG, data_route='original_114_ticker_LSEG_proxy_package',
                    top_k=4, lookback_years=3, method='IV',
                    size_direction='small_cap_high_panel_Size_score_as_given',
                    stock_selection_fraction=.2, exit_fraction=.3, sector_cap=.3,
                    start='2016-01-01', evaluation_start='2021-01-01', eval_end='2026-09-30')
    for key, value in expected.items():
        if c.get(key) != value:
            raise ValueError(f'This adapter freezes the existing old-data specification: {key}')
    if c['factor_pool'] != ['Quality', 'Momentum', 'Low Risk', 'Investment', 'Size', 'Value']:
        raise ValueError('Not the reviewed legacy six-factor pool')
    if c['one_way_cost_bps'] != [10, 20, 30]:
        raise ValueError('Frozen reporting costs are 10/20/30 bp')


def period_metrics(data, result, k):
    mt, path = data['mt'], result['path']
    periods = {'PRE_2016_18': ('2016-01-01', '2018-12-31'),
               'OBS_2019_20': ('2019-01-01', '2020-12-31'),
               'EVAL_2021_LATEST': ('2021-01-01', data['end']),
               'EVAL_2021_25': ('2021-01-01', '2025-12-31')}
    rows = []
    for period, (start, end) in periods.items():
        z = path[path.index.to_series().between(start, end)]
        for bps in [10, 20, 30]:
            r = z[f'net_return_{bps}bp']
            rows.append(dict(strategy=f'TUNE_T{k}L3_IV_N20_EQ_NONE', period=period, cost_bps=bps,
                             initialization='continuous_target_and_cash_account_since_2016',
                             **mt.performance(r, None, z.STI_TR)))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True, help='Original SGX_comprehensive...zip')
    parser.add_argument('--output', type=Path, required=True, help='Fresh local, ignored output directory')
    parser.add_argument('--config', type=Path, default=HERE/'configs/legacy_top4.json')
    parser.add_argument('--execution-settings', type=Path, default=HERE/'configs/execution_settings.json')
    parser.add_argument('--data-lock', type=Path, help='Optional private expected-member hash file; never publish vendor fingerprints')
    parser.add_argument('--skip-execution', action='store_true')
    args = parser.parse_args()
    if not args.archive.is_file():
        parser.error('Original comprehensive ZIP is missing; new team-data ZIP is not used')
    out = args.output.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error('Output must be new or empty')
    if any(p.resolve().is_relative_to(out) for p in [args.archive, args.config, args.execution_settings]):
        parser.error('Output cannot contain input files')
    cfg = json.loads(args.config.read_text(encoding='utf-8'))
    validate_config(cfg)
    check_source_lock()
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    archive_hash = digest(args.archive)
    manifest = dict(status='STARTED', source_commit=SOURCE_COMMIT, strategy=LEGACY_TAG,
                    original_archive_sha256=archive_hash, configuration=cfg,
                    new_team_data_used=False, pit_replacement_used=False,
                    grid_search_run=False, live_period_opened=False,
                    runtime=dict(pandas=pd.__version__, numpy=np.__version__, scipy=scipy.__version__),
                    known_limits=['114-current-ticker research universe', 'fundamental PIT proxies',
                                  'daily close/volume source conflicts', 'unverified corporate actions for share execution'])
    save_json(out/'run_manifest.json', manifest)
    data_dir = extract_inputs(args.archive, out/'_private_inputs', args.data_lock)
    from legacy_adapter import MEMBERS
    save_json(out/'INPUT_DATA_HASHES.json', {'archive_sha256': archive_hash,
              'member_sha256': {name: digest(data_dir/name) for name in MEMBERS}})
    # The reviewed package also has an event inventory. Audit it explicitly;
    # missing payment dates and split-adjusted closes prohibit blind ingestion.
    prices_all = pd.read_csv(data_dir/'data/prices_daily_2006.csv')
    action_member = 'SGX_comprehensive_2006_2026_handoff/data/corporate_actions_2006.csv'
    with zipfile.ZipFile(args.archive) as z:
        count = z.namelist().count(action_member)
        if count > 1:
            raise ValueError('Duplicate source corporate-action member')
        action_bytes = z.read(action_member) if count == 1 else None
    if action_bytes is not None:
        qa = corporate_action_audit(prices_all, pd.read_csv(io.BytesIO(action_bytes)))
        qa['summary']['source_member_sha256'] = hashlib.sha256(action_bytes).hexdigest()
        qa['events'].to_csv(out/'corporate_action_events_AUDIT.csv', index=False)
        qa['adjustment_steps'].to_csv(out/'price_adjustment_steps_AUDIT.csv', index=False)
        save_json(out/'corporate_action_readiness.json', qa['summary'])
    else:
        save_json(out/'corporate_action_readiness.json', {
            'complete_actions_verified': False, 'blockers': ['Source corporate-action member missing']})
    data = load_old_data(data_dir, cfg['eval_end'])
    data['factor_returns'].to_csv(out/'factor_returns.csv', index=False)
    results, targets, summary, checks = {}, {}, [], {}
    for k in [3, 4]:
        result = source_strategy(data, k)
        d = out/f'top{k}'
        d.mkdir()
        for name in ['holdings', 'construction', 'scores', 'ledger', 'weights']:
            result[name].to_csv(d/f'{name}.csv', index=False)
        result['path'].to_csv(d/'source_weekly_path.csv')
        target = canonical_targets(result['holdings'], data['calendar'])
        target.to_csv(d/'targets.csv', index=False)
        results[k], targets[k] = result, target
        replay = independent_weekly(result['holdings'], data['returns'], data['calendar'], 10).set_index('signal_date')
        errors = {col: float(np.max(np.abs(replay[col]-result['path'][source_col])))
                  for col, source_col in [('gross_return', 'gross_return'), ('gtr', 'gtr'),
                                          ('net_return', 'net_return_10bp'), ('unpriced_weight', 'unpriced_weight')]}
        if max(errors.values()) > 1e-10:
            raise AssertionError(f'Independent weekly reconciliation failed: {errors}')
        checks[f'top{k}'] = dict(weeks=len(replay), held_rows=len(target), errors=errors,
                                 max_sector_weight=float(result['construction'].max_sector_weight.max()))
        summary += period_metrics(data, result, k)
        # Cash-reset source-convention benchmark for the execution evaluation.
        ev = result['holdings'][result['holdings'].signal_date.ge(cfg['evaluation_start'])]
        independent_weekly(ev, data['returns'], data['calendar'], 10).to_csv(d/'evaluation_cash_reset_10bp.csv', index=False)
        print(f'Old data Top{k}: {len(replay)} weeks, independent reconciliation passed', flush=True)
    pd.DataFrame(summary).to_csv(out/'source_performance_summary.csv', index=False)
    save_json(out/'source_reconciliation.json', checks)
    # Same-date total-return benchmark is from the supplied LSEG STI workbook.
    bench = data['sti'][data['sti'].index.to_series().ge(cfg['evaluation_start'])]
    save_json(out/'benchmark_summary.json', data['mt'].performance(bench))
    execution_rows = []
    if not args.skip_execution:
        prices = prices_all[['date', 'ticker', 'close', 'volume']].copy()
        prices.date = pd.to_datetime(prices.date)
        prices = prices[prices.date.ge('2020-09-01')].copy()
        master = pd.read_csv(data_dir/'data/stock_universe_six_sectors.csv')
        if master.ticker.duplicated().any():
            raise ValueError('Duplicate security master')
        currency = master.set_index('ticker').currency
        coverage = prices.assign(positive=prices.volume.gt(0)).groupby('date').positive.sum()
        sessions = coverage[coverage.ge(50)].index
        coverage[~coverage.index.isin(sessions)].to_csv(out/'calendar_proxy_exclusions.csv')
        pd.DataFrame({'date': sessions}).to_csv(out/'sessions_PROXY.csv', index=False)
        settings = json.loads(args.execution_settings.read_text(encoding='utf-8'))
        for scenario in cfg['execution_scenarios']:
            k, name = scenario['top_k'], scenario['name']
            if scenario['target_mode'] != 'frozen':
                raise ValueError('CLI follows frozen source targets; actual-buffer integration uses API')
            t = targets[k][targets[k].signal_date.ge(cfg['evaluation_start'])].copy()
            if not currency.reindex(t.ticker.unique()).eq('SGD').all():
                raise ValueError('Non-SGD or unknown traded currency; FX accounting required')
            ec = ExecutionConfig(**(settings | {'initial_cash': scenario['initial_cash'],
                                               'action_policy': 'price_only_diagnostic'}))
            result = simulate(prices, sessions, plans_from_targets(t), ec,
                              end=pd.Timestamp(t.next_execution_date.max()))
            d = out/'execution'/name
            d.mkdir(parents=True)
            for key, value in result.items():
                if isinstance(value, pd.DataFrame):
                    value.to_csv(d/f'{key}.csv', index=False)
            risk_diagnostics(result, master).to_csv(d/'risk_diagnostics.csv', index=False)
            result['audit'].update(strategy=f'TUNE_T{k}L3_IV_N20_EQ_NONE',
                                    initialization=cfg['execution_initialization'],
                                    calendar_source='>=50 names reporting positive volume; proxy',
                                    corporate_action_and_price_basis_verified=False)
            save_json(d/'audit.json', result['audit'])
            f = result['fills']
            if not f.empty:
                assert (f.shares.abs() <= ec.participation*f.source_volume+1e-6).all()
                assert (f.shares.abs()*f.reference_close <= ec.participation*f.lagged_adv+1e-5).all()
                assert ((f.shares.abs() % f.lot_size) == 0).all()
                assert (f.date > f.order_id.map(result['orders'].set_index('order_id').signal_date)).all()
            assert result['daily'].cash.ge(-1e-7).all()
            execution_rows.append(dict(scenario=name, top_k=k, initial_cash=ec.initial_cash,
                                       **execution_metrics(result)))
            print(f'{name}: daily-account checks passed', flush=True)
        pd.DataFrame(execution_rows).to_csv(out/'execution_diagnostic_summary.csv', index=False)
    assert digest(args.archive) == archive_hash
    check_source_lock()
    manifest.update(status='COMPLETED', weeks=checks['top4']['weeks'],
                    last_holding_period_end=data['end'], input_archive_unchanged=True,
                    source_reconciliation=checks, execution_scenarios=len(execution_rows),
                    elapsed_seconds=time.perf_counter()-started)
    save_json(out/'run_manifest.json', manifest)
    print(out, flush=True)


if __name__ == '__main__':
    main()
