"""Attribute existing M4/M5 2021-2025 outputs; never rebuild a strategy.

Run with the virtual-environment Python documented in README.md.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from attribution_engine import (wealth_before, stock_net_contributions,
                                terminal_cost_loss, drawdown_window, hac_mean, link_yearly)

HERE = Path(__file__).resolve().parent
os.environ.setdefault('MPLCONFIGDIR', str(HERE/'.matplotlib-cache'))
SOURCE_COMMIT = '7b48586e45b1c456478c8a977e96043bc3108dad'
KEY = ['signal_date', 'PERMNO']
TOL = 1e-9
FAMILIES = ['Quality', 'Momentum', 'Low Risk', 'Investment']
SCORES = ['quality_score', 'momentum_score', 'lowrisk_score', 'investment_score']
SECTORS = {'10': 'Energy', '15': 'Materials', '20': 'Industrials',
           '25': 'Consumer Discretionary', '30': 'Consumer Staples', '35': 'Health Care',
           '40': 'Financials', '45': 'Information Technology', '50': 'Communication Services',
           '55': 'Utilities', '60': 'Real Estate', 'CASH': 'Cash', 'UNKNOWN': 'Unknown'}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def clean_ids(frame):
    for c in ['PERMNO', 'sector', 'historical_gics_sector']:
        if c in frame:
            frame[c] = frame[c].astype('string').str.replace(r'\.0$', '', regex=True)
    return frame


def csv(path):
    return clean_ids(pd.read_csv(path, dtype={'PERMNO': str, 'sector': str}))


class Analysis:
    def __init__(self, source, data, output):
        self.source, self.data, self.out = source.resolve(), data.resolve(), output.resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.qa_rows, self.tables, self.manifest = [], {}, []
        self.cost = 0.001

    def check(self, name, ok, detail='', critical=True, metric=None):
        detail = str(detail).replace(str(self.source), '<repo>').replace(str(self.data), '<data>').replace(str(HERE), '<attribution>')
        self.qa_rows.append(dict(check=name, status='PASS' if ok else ('FAIL' if critical else 'DISCLOSURE'),
                                 critical=critical, metric=metric, detail=str(detail)))
        if not ok and critical:
            pd.DataFrame(self.qa_rows).to_csv(self.out/'qa_checks.csv', index=False)
            raise ValueError(f'{name}: {detail}')

    def close(self, name, actual, expected):
        a, b = np.asarray(actual, dtype=float), np.asarray(expected, dtype=float)
        error = float(np.max(np.abs(a - b))) if a.size else 0.0
        self.check(name, a.shape == b.shape and np.isfinite(a).all() and error < TOL,
                   f'max absolute residual={error:.3e}; tolerance={TOL}', metric=error)

    def save(self, name, frame, parquet=False):
        self.tables[name] = frame
        if parquet:
            frame.to_parquet(self.out/f'{name}.parquet', index=False, compression='zstd')
        else:
            frame.to_csv(self.out/f'{name}.csv', index=False, encoding='utf-8-sig')

    def parquet(self, relative, **kwargs):
        return clean_ids(pd.read_parquet(self.data/relative, **kwargs))

    def record_inputs(self):
        contract_path = HERE/'source_contract.json'
        contract = json.loads(contract_path.read_text(encoding='utf-8'))
        self.check('pinned_source_commit', contract['source_commit'] == SOURCE_COMMIT)
        self.manifest.append(dict(path=str(contract_path), role='analysis_source_contract',
                                  bytes=contract_path.stat().st_size, sha256=sha(contract_path), expected_git_lfs_sha256=''))
        for item in contract['inputs']:
            p = self.source/item['path']
            self.check('source_exists_'+item['path'], p.is_file(), p)
            if item['role'] == 'frozen_lfs_payload':
                expected = item['sha256']
                with p.open('rb') as handle:
                    head = handle.read(128)
                if head.startswith(b'version https://git-lfs'):
                    self.check('source_pointer_'+p.stem, expected in p.read_text(encoding='utf-8'), p)
                else:
                    self.check('source_payload_'+p.stem, sha(p) == expected, p)
                self.manifest.append(dict(path=str(p), role='frozen_source_payload_or_pointer',
                                          bytes=p.stat().st_size, sha256=sha(p), expected_git_lfs_sha256=expected))
                actual_path = self.data/item['path']
                digest = sha(actual_path)
                self.check('input_sha_'+p.stem, digest == expected and actual_path.stat().st_size == item['bytes'], actual_path)
                self.manifest.append(dict(path=str(actual_path), role='frozen_handoff_payload',
                                          bytes=actual_path.stat().st_size, sha256=digest,
                                          expected_git_lfs_sha256=expected))
            else:
                digest_lf = hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
                self.check('frozen_text_'+item['path'], digest_lf == item['sha256_lf'], p)
                self.manifest.append(dict(path=str(p), role='member5_source_snapshot',
                                          bytes=p.stat().st_size, sha256=sha(p), expected_git_lfs_sha256=''))
        # This baseline is checked again after report generation.
        public_manifest = []
        for item in self.manifest:
            row = dict(item)
            p = Path(row['path'])
            if row['role'] == 'frozen_handoff_payload':
                row['path'] = 'data:'+p.relative_to(self.data).as_posix()
            else:
                row['path'] = 'repo:'+p.relative_to(self.source).as_posix()
            public_manifest.append(row)
        self.save('input_manifest', pd.DataFrame(public_manifest))

    def load(self):
        config = json.loads((self.source/'config/strategy/us_base_strategy.json').read_text())
        self.check('frozen_config_recorded', config['status'] == 'FROZEN', config['status'])
        self.check('frozen_k4', config['parameters']['final_factor_set'] ==
                   ['QUALITY_CURRENT', 'MOMENTUM_12_1', 'LOWRISK_CURRENT', 'INVESTMENT_CURRENT'])
        self.cost = config['transaction_cost']['oneway_bps'] / 10000
        self.check('cost_matches_frozen_10bp', abs(self.cost-.001) < 1e-15)
        dynamic = self.source/'outputs/strategy/frozen/hist_oos'
        static = self.source/'outputs/strategy/benchmarks/hist_oos'
        self.inputs = {
            'Dynamic': (csv(dynamic/'target_holdings.csv'), csv(dynamic/'trade_ledger.csv'), csv(dynamic/'weekly_path.csv')),
            'Static': (csv(static/'static_target_holdings.csv'), csv(static/'static_trade_ledger.csv'), csv(static/'static_weekly_path.csv')),
        }
        accept = csv(dynamic/'acceptance_test_report.csv')
        self.check('upstream_dynamic_acceptance', accept.status.eq('PASS').all(), f'{len(accept)} upstream checks')
        static_manifest = json.loads((static/'static_manifest.json').read_text())
        self.check('static_comparison_contract', static_manifest['lambda'] == 0 and
                   static_manifest['buffer'] == config['parameters']['buffer'] and
                   static_manifest['cost_oneway_bps'] == config['transaction_cost']['oneway_bps'])
        dp, sp = self.inputs['Dynamic'][2], self.inputs['Static'][2]
        self.check('exact_259_completed_periods', len(dp) == len(sp) == 259)
        self.check('paired_periods', dp.signal_date.tolist() == sp.signal_date.tolist() and
                   dp.period_end.tolist() == sp.period_end.tolist())
        self.dates = dp.signal_date.tolist()
        self.calendar = self.parquet('handoff/us_downstream/data/weekly_execution_calendar.parquet')
        self.calendar = self.calendar[self.calendar.signal_date.isin(self.dates)].copy()
        self.check('calendar_unique_and_complete', len(self.calendar) == 259 and not self.calendar.signal_date.duplicated().any())
        self.calendar = self.calendar.set_index('signal_date').loc[self.dates].reset_index()
        self.check('calendar_matches_paths', self.calendar.next_execution_date.tolist() == dp.period_end.tolist())
        self.check('strict_next_close_timing', (self.calendar.signal_date < self.calendar.execution_date).all() and
                   (self.calendar.execution_date < self.calendar.next_execution_date).all())
        self.check('completed_before_2026', dp.period_end.le('2025-12-31').all())
        self.returns = self.parquet('handoff/us_downstream/data/security_weekly_execution_returns.parquet',
                                   filters=[('signal_date', '>=', '2021-01-01'), ('signal_date', '<=', '2025-12-31')])
        self.returns = self.returns[self.returns.signal_date.isin(self.dates)].copy()
        self.check('security_returns_unique', not self.returns.duplicated(KEY).any())
        status = self.returns.unavailable_reason.fillna('').replace('', 'VALID')
        self.returns['usable'] = ((self.returns.execution_feasible == 1) &
                                  status.isin(['VALID', 'VALID_CONFIRMED_DELISTING_CASH']) &
                                  np.isfinite(self.returns.execution_to_next_execution_total_return)).astype(int)
        self.universe = self.parquet('handoff/final_factor/data/universe_weekly.parquet',
                                    filters=[('signal_date', '>=', '2021-01-01'), ('signal_date', '<=', '2025-12-31')])
        self.universe = self.universe[self.universe.signal_date.isin(self.dates)].copy()
        self.scores = self.parquet('handoff/final_factor/data/factor_scores_weekly.parquet',
                                  filters=[('signal_date', '>=', '2021-01-01'), ('signal_date', '<=', '2025-12-31')])
        self.scores = self.scores[self.scores.signal_date.isin(self.dates)].copy()
        self.reference = self.parquet('handoff/us_downstream/data/security_reference_history.parquet')
        self.rf = self.parquet('handoff/us_downstream/data/risk_free_weekly.parquet')
        self.benchmark = self.parquet('handoff/us_downstream/data/benchmark_weekly_execution_returns.parquet')
        self.check('universe_keys_unique', not self.universe.duplicated(KEY).any())
        self.check('score_keys_unique', not self.scores.duplicated(KEY).any())
        self.weights = csv(dynamic/'factor_weights.csv')
        self.weights = self.weights[self.weights.signal_date.isin(self.dates)].copy()
        self.check('factor_weight_keys_unique', not self.weights.duplicated(['signal_date', 'family']).any())
        self.check('factor_weight_full_k4', len(self.weights) == 259 * 4 and set(self.weights.family) == set(FAMILIES))
        self.close('factor_weights_sum_to_one', self.weights.groupby('signal_date').weight.sum(), np.ones(259))
        self.check('factor_weights_completed_information',
                   self.weights.window_last_next_execution_date.fillna('').le(self.weights.signal_date).all())

    def identity(self, pairs):
        keys = pairs[KEY].drop_duplicates()
        joined = keys.merge(self.reference[['PERMNO', 'effective_start_date', 'effective_end_date', 'ticker', 'issuer']],
                            on='PERMNO', how='left')
        end = joined.effective_end_date.fillna('9999-12-31').replace('', '9999-12-31')
        valid = joined[(joined.signal_date >= joined.effective_start_date) & (joined.signal_date <= end)]
        self.check('no_ambiguous_reference_names', not valid.duplicated(KEY).any())
        result = keys.merge(valid[KEY+['ticker', 'issuer']], on=KEY, how='left', validate='one_to_one')
        result['identity_status'] = np.where(result.issuer.notna(), 'TIME_VALID_REFERENCE', 'NO_TIME_VALID_REFERENCE')
        result[['ticker','issuer']] = result[['ticker','issuer']].fillna('')
        result.loc[result.PERMNO.eq('CASH'), ['ticker', 'issuer', 'identity_status']] = ['CASH', 'Cash', 'CASH']
        return result

    def holdings_contributions(self):
        stocks, paths, raw_missing = [], [], []
        exclusions = []
        for strategy, (hold_all, ledger_all, path) in self.inputs.items():
            for date in sorted(set(hold_all.signal_date)-set(self.dates)):
                exclusions.append(dict(strategy=strategy, signal_date=date,
                                       holding_rows=int(hold_all.signal_date.eq(date).sum()),
                                       reason='NOT_IN_FROZEN_COMPLETED_OOS_PATH'))
            hold = hold_all[hold_all.signal_date.isin(self.dates)].copy()
            ledger = ledger_all[ledger_all.signal_date.isin(self.dates)].copy()
            self.check(strategy+'_unique_holdings', not hold.duplicated(KEY).any())
            self.check(strategy+'_unique_trades', not ledger.duplicated(KEY).any())
            self.check(strategy+'_nonnegative_weights', np.isfinite(hold.weight).all() and hold.weight.ge(0).all())
            universe_check = hold.merge(self.universe[KEY+['eligible_universe_flag', 'historical_gics_sector']], on=KEY, how='left', validate='one_to_one')
            self.check(strategy+'_eligible_holdings', universe_check.eligible_universe_flag.eq(1).all())
            self.check(strategy+'_historical_sector_matches', universe_check.sector.fillna('UNKNOWN').equals(
                       universe_check.historical_gics_sector.fillna('UNKNOWN')))
            weight_sum = hold.groupby('signal_date').weight.sum().reindex(self.dates)
            self.check(strategy+'_stock_weights_at_most_one', weight_sum.le(1+1e-12).all())
            hh = hold.merge(self.returns, on=KEY, how='left', validate='one_to_one', suffixes=('', '_return'), indicator=True)
            self.check(strategy+'_held_return_keys_present', hh['_merge'].eq('both').all())
            self.check(strategy+'_execution_matches_returns', hh.execution_date.eq(hh.execution_date_return).all())
            end_map = path.set_index('signal_date').period_end
            self.check(strategy+'_held_return_end_matches', hh.next_execution_date.eq(hh.signal_date.map(end_map)).all())
            hh['stock_return_used'] = np.where(hh.usable.eq(1), hh.execution_to_next_execution_total_return, 0.0)
            hh['gross_contribution'] = hh.weight * hh.stock_return_used
            missing = hh[~hh.usable.eq(1)].copy()
            missing['strategy'] = strategy
            raw_missing.append(missing)
            actual_unpriced = hh.weight.where(~hh.usable.eq(1), 0).groupby(hh.signal_date).sum().reindex(self.dates)
            self.close(strategy+'_unpriced_weight_matches_upstream', actual_unpriced, path.unpriced_weight)
            rebuilt = hh.groupby('signal_date').gross_contribution.sum().reindex(self.dates)
            self.close(strategy+'_gross_from_holdings', rebuilt, path.gross_return)
            self.close(strategy+'_trade_cost_per_row', ledger.cost, self.cost * ledger.trade_weight.abs())
            self.close(strategy+'_trade_deltas', ledger.target_weight-ledger.pretrade_weight, ledger.trade_weight)
            gtr = ledger.groupby('signal_date').trade_weight.apply(lambda x: x.abs().sum()).reindex(self.dates, fill_value=0)
            cost = ledger.groupby('signal_date').cost.sum().reindex(self.dates, fill_value=0)
            self.close(strategy+'_gtr_from_ledger', gtr, path.gtr)
            self.close(strategy+'_reported_turnover', path.turnover, path.gtr / 2)
            self.close(strategy+'_weekly_cost_from_ledger', cost, self.cost * path.gtr)
            self.close(strategy+'_net_exact_identity', (1-cost.to_numpy())*(1+rebuilt.to_numpy())-1, path.net_return)
            # Check every ledger target against the saved strategy holdings.
            target = ledger.merge(hold[KEY+['weight']], on=KEY, how='left', validate='one_to_one')
            self.close(strategy+'_ledger_target_matches_holdings', target.target_weight, target.weight.fillna(0))
            # First OOS pre-trade weights are inherited from 2020. For later
            # weeks independently reconstruct drift from the preceding saved holdings.
            prior = None
            drift_error = 0.0
            for date in self.dates:
                current = hh[hh.signal_date == date]
                trades = ledger[ledger.signal_date == date]
                target_map = dict(zip(current.PERMNO, current.weight))
                if prior is not None:
                    union = set(prior) | set(target_map) | set(trades.PERMNO)
                    actual = dict(zip(trades.PERMNO, trades.trade_weight))
                    for p in union:
                        drift_error = max(drift_error, abs(actual.get(p, 0) - (target_map.get(p, 0)-prior.get(p, 0))))
                gross = current.gross_contribution.sum()
                prior = dict(zip(current.PERMNO, current.weight*(1+current.stock_return_used)/(1+gross)))
            self.close(strategy+'_drifted_trade_weights_after_first_oos_week', [drift_error], [0.0])
            p = path.copy()
            p['strategy'] = strategy
            p['signal_year'] = p.signal_date.str[:4].astype(int)
            p['execution_date'] = p.signal_date.map(self.calendar.set_index('signal_date').execution_date)
            p['cost_ratio'] = cost.to_numpy()
            p['cash_weight'] = np.maximum(1-weight_sum.to_numpy(), 0)
            p['gross_nav_before'] = wealth_before(p.gross_return)
            p['net_nav_before'] = wealth_before(p.net_return)
            p['gross_nav'] = p.gross_nav_before*(1+p.gross_return)
            p['net_nav'] = p.net_nav_before*(1+p.net_return)
            p['exact_cost_drag_return'] = p.gross_return-p.net_return
            p['paid_cost_per_initial_capital'] = p.net_nav_before*p.cost_ratio
            p['terminal_cost_loss'] = terminal_cost_loss(p.gross_return, p.net_return)
            self.close(strategy+'_terminal_cost_counterfactual', [p.terminal_cost_loss.sum()], [p.gross_nav.iloc[-1]-p.net_nav.iloc[-1]])
            paths.append(p)
            m = hh[KEY+['sector', 'weight', 'stock_return_used', 'gross_contribution', 'usable', 'unavailable_reason']].merge(
                ledger[KEY+['cost', 'pretrade_weight', 'trade_weight']], on=KEY, how='outer', validate='one_to_one')
            for c in ['weight', 'gross_contribution', 'cost', 'trade_weight']:
                m[c] = m[c].fillna(0.0)
            m['pretrade_weight'] = m.pretrade_weight.fillna(m.weight)
            m = m.merge(self.universe[KEY+['historical_gics_sector']], on=KEY, how='left', validate='many_to_one')
            m['sector_source'] = np.where(m.sector.notna(), 'FROZEN_HOLDING_SECTOR', 'SIGNAL_HISTORICAL_GICS')
            m['sector'] = m.sector.fillna(m.historical_gics_sector)
            prior_sector = hold_all[hold_all.signal_date.le(self.dates[-1])][KEY+['sector']].copy()
            for idx in m.index[m.sector.isna()]:
                q = prior_sector[(prior_sector.PERMNO == m.at[idx, 'PERMNO']) &
                                 (prior_sector.signal_date < m.at[idx, 'signal_date'])].sort_values('signal_date')
                if len(q):
                    m.at[idx, 'sector'] = q.iloc[-1].sector
                    m.at[idx, 'sector_source'] = 'LAST_HELD_SECTOR_EXIT_COST_ONLY'
                else:
                    m.at[idx, 'sector'] = 'UNKNOWN'
                    m.at[idx, 'sector_source'] = 'UNKNOWN'
            cash = pd.DataFrame({'signal_date': self.dates, 'PERMNO': 'CASH', 'sector': 'CASH',
                                 'sector_source': 'CASH', 'weight': p.cash_weight.to_numpy(),
                                 'gross_contribution': 0.0, 'cost': 0.0, 'pretrade_weight': np.nan,
                                 'trade_weight': 0.0, 'stock_return_used': 0.0, 'usable': 1,
                                 'unavailable_reason': ''})
            m = pd.concat([m, cash], ignore_index=True)
            m['strategy'] = strategy
            m = m.merge(p[['signal_date', 'execution_date', 'period_end', 'cost_ratio', 'gross_nav_before', 'net_nav_before']],
                        on='signal_date', how='left', validate='many_to_one')
            m['holding_after_cost_contribution'] = (1-m.cost_ratio)*m.gross_contribution
            m['direct_cost_contribution'] = -m.cost
            m['net_contribution'] = stock_net_contributions(m.gross_contribution, m.cost, m.cost_ratio)
            m['gross_linked'] = m.gross_nav_before*m.gross_contribution
            m['holding_linked_in_net_account'] = m.net_nav_before*m.holding_after_cost_contribution
            m['direct_cost_linked'] = -m.net_nav_before*m.cost
            m['net_linked'] = m.net_nav_before*m.net_contribution
            self.close(strategy+'_stock_net_weekly_closure', m.groupby('signal_date').net_contribution.sum().reindex(self.dates), p.net_return)
            self.close(strategy+'_stock_terminal_wealth_closure', [m.net_linked.sum()], [p.net_nav.iloc[-1]-1])
            stocks.append(m)
        self.path = pd.concat(paths, ignore_index=True)
        self.stock = pd.concat(stocks, ignore_index=True)
        self.stock = self.stock.merge(self.identity(self.stock), on=KEY, how='left', validate='many_to_one')
        self.stock['sector_name'] = self.stock.sector.map(SECTORS).fillna(self.stock.sector)
        self.save('excluded_incomplete_periods', pd.DataFrame(exclusions))
        self.save('unknown_sector_rows', self.stock[self.stock.sector.eq('UNKNOWN')])
        self.check('unknown_sector_costs_disclosed', not self.stock.sector.eq('UNKNOWN').any(),
                   'Unverifiable sector classifications are preserved as UNKNOWN, including exit costs on securities inherited from before OOS.', critical=False)
        self.save('unpriced_holdings', pd.concat(raw_missing, ignore_index=True)[[
            'strategy', 'signal_date', 'execution_date', 'PERMNO', 'sector', 'weight',
            'execution_to_next_execution_total_return', 'stock_return_used', 'unavailable_reason', 'usable']])
        self.check('legacy_unpriced_zero_policy_disclosed', len(pd.concat(raw_missing)) == 0,
                   f'{sum(len(x) for x in raw_missing)} held security-weeks use the original M4 zero-return convention; no fix or imputation is introduced.', critical=False)
        self.check('weekly_annualization_not_daily_gate', False,
                   'Report reproduces M5 weekly NAV and 52-week annualization. Final daily-NAV and team sign-off remain upstream open items.', critical=False)
        self.save('portfolio_weekly', self.path)
        self.save('stock_contributions_weekly', self.stock, parquet=True)

    def summaries(self):
        latest = self.stock.sort_values('signal_date').groupby(['strategy', 'PERMNO']).tail(1)[
            ['strategy', 'PERMNO', 'ticker', 'issuer', 'signal_date', 'identity_status']].rename(columns={'signal_date': 'name_as_of'})
        stock_summary = self.stock.groupby(['strategy', 'PERMNO'], as_index=False).agg(
            holding_weeks=('weight', lambda x: int((x > 1e-12).sum())), weight_sum=('weight', 'sum'),
            gross_linked=('gross_linked', 'sum'), holding_linked_in_net_account=('holding_linked_in_net_account', 'sum'),
            direct_cost_linked=('direct_cost_linked', 'sum'), net_linked=('net_linked', 'sum'))
        stock_summary['mean_weight_all_259_weeks'] = stock_summary.pop('weight_sum')/259
        stock_summary = stock_summary.merge(latest, on=['strategy', 'PERMNO'], validate='one_to_one')
        self.save('stock_contributors_full_period', stock_summary.sort_values(['strategy', 'net_linked'], ascending=[True, False]))
        sector_week = self.stock.groupby(['strategy', 'signal_date', 'sector', 'sector_name'], as_index=False).agg(
            weight=('weight', 'sum'), gross_contribution=('gross_contribution', 'sum'),
            net_contribution=('net_contribution', 'sum'), direct_cost_contribution=('direct_cost_contribution', 'sum'),
            gross_linked=('gross_linked', 'sum'), holding_linked_in_net_account=('holding_linked_in_net_account', 'sum'),
            direct_cost_linked=('direct_cost_linked', 'sum'), net_linked=('net_linked', 'sum'))
        self.save('sector_contributions_weekly', sector_week)
        sector_summary = sector_week.groupby(['strategy', 'sector', 'sector_name'], as_index=False).agg(
            weight_sum=('weight', 'sum'), gross_linked=('gross_linked', 'sum'),
            holding_linked_in_net_account=('holding_linked_in_net_account', 'sum'),
            direct_cost_linked=('direct_cost_linked', 'sum'), net_linked=('net_linked', 'sum'))
        sector_summary['mean_weight_all_259_weeks'] = sector_summary.pop('weight_sum')/259
        self.save('sector_contributors_full_period', sector_summary)
        for strategy in ['Dynamic', 'Static']:
            p = self.path[self.path.strategy == strategy]
            self.close(strategy+'_sector_terminal_closure', [sector_summary[sector_summary.strategy == strategy].net_linked.sum()], [p.net_nav.iloc[-1]-1])
        year_stock = link_yearly(self.stock, self.path)
        yearly = year_stock.groupby(['strategy', 'signal_year', 'PERMNO'], as_index=False).agg(
            gross_linked_within_year=('gross_linked_within_year', 'sum'), net_linked_within_year=('net_linked_within_year', 'sum'))
        self.save('stock_contributors_by_signal_year', yearly.merge(latest[['strategy', 'PERMNO', 'ticker', 'issuer', 'name_as_of']], on=['strategy', 'PERMNO'], how='left'))
        ys = year_stock.groupby(['strategy', 'signal_year', 'sector', 'sector_name'], as_index=False).agg(
            gross_linked_within_year=('gross_linked_within_year', 'sum'), net_linked_within_year=('net_linked_within_year', 'sum'))
        self.save('sector_contributors_by_signal_year', ys)
        summary = []
        rf = self.rf.set_index('signal_date').risk_free_return.reindex(self.dates)
        self.check('rf_has_all_completed_intervals', rf.notna().all())
        for strategy, p in self.path.groupby('strategy'):
            n = len(p)
            er = p.net_return.to_numpy()-rf.to_numpy()
            peak, trough, recovery = drawdown_window(p.net_return)
            nav = np.r_[1., p.net_nav.to_numpy()]
            row = dict(strategy=strategy, n_weeks=n, cumulative_gross_return=p.gross_nav.iloc[-1]-1,
                       cumulative_net_return=p.net_nav.iloc[-1]-1,
                       cagr_gross=p.gross_nav.iloc[-1]**(52/n)-1, cagr_net=p.net_nav.iloc[-1]**(52/n)-1,
                       annualized_vol=p.net_return.std(ddof=1)*np.sqrt(52), sharpe=np.mean(er)/np.std(er, ddof=1)*np.sqrt(52),
                       max_drawdown=1-nav[trough]/nav[peak], annual_turnover=p.turnover.mean()*52,
                       paid_cost_per_initial_capital=p.paid_cost_per_initial_capital.sum(),
                       terminal_gross_minus_net=p.gross_nav.iloc[-1]-p.net_nav.iloc[-1],
                       mean_unpriced_weight=p.unpriced_weight.mean())
            summary.append(row)
        self.save('performance_reconciliation', pd.DataFrame(summary))
        old = csv(self.source/'outputs/risk_analysis/us_hist_oos/four_strategy_comparison.csv').set_index('strategy')
        for r in summary:
            name = 'Dynamic FMOM' if r['strategy'] == 'Dynamic' else 'Static MF'
            for ours, theirs in [('cagr_net', 'CAGR'), ('annualized_vol', 'Volatility'), ('sharpe', 'Sharpe'), ('max_drawdown', 'Max_Drawdown')]:
                self.close(r['strategy']+'_M5_'+ours, [r[ours]], [old.loc[name, theirs]])

    def paired_attribution(self):
        d = self.stock[self.stock.strategy == 'Dynamic'].copy()
        s = self.stock[self.stock.strategy == 'Static'].copy()
        columns = KEY+['weight', 'gross_contribution', 'net_contribution', 'gross_linked', 'net_linked', 'cost', 'sector', 'ticker', 'issuer']
        m = d[columns].merge(s[columns], on=KEY, how='outer', suffixes=('_dynamic', '_static'), validate='one_to_one')
        for c in ['weight', 'gross_contribution', 'net_contribution', 'gross_linked', 'net_linked', 'cost']:
            for suffix in ['dynamic', 'static']:
                m[f'{c}_{suffix}'] = m[f'{c}_{suffix}'].fillna(0.)
        both = m.weight_dynamic.gt(1e-12) & m.weight_static.gt(1e-12)
        self.check('paired_shared_holding_sectors', m.loc[both, 'sector_dynamic'].eq(m.loc[both, 'sector_static']).all())
        m['sector'] = m.sector_dynamic.combine_first(m.sector_static).fillna('UNKNOWN')
        m['sector_name'] = m.sector.map(SECTORS).fillna(m.sector)
        m['ticker'] = m.ticker_dynamic.combine_first(m.ticker_static)
        m['issuer'] = m.issuer_dynamic.combine_first(m.issuer_static)
        m['category'] = np.select([m.PERMNO.eq('CASH'), both, m.weight_dynamic.gt(1e-12), m.weight_static.gt(1e-12)],
                                  ['Cash', 'Common', 'Dynamic only', 'Static only'], default='Exit costs only')
        m['weight_delta'] = m.weight_dynamic-m.weight_static
        for c in ['gross_contribution', 'net_contribution', 'gross_linked', 'net_linked']:
            m[c+'_delta'] = m[c+'_dynamic']-m[c+'_static']
        self.save('dynamic_minus_static_stock_weekly', m, parquet=True)
        dp = self.path[self.path.strategy == 'Dynamic'].set_index('signal_date')
        sp = self.path[self.path.strategy == 'Static'].set_index('signal_date')
        w = pd.DataFrame(index=self.dates)
        w.index.name = 'signal_date'
        for name in ['gross_return', 'net_return', 'gtr', 'turnover', 'exact_cost_drag_return', 'net_nav', 'gross_nav']:
            w[name+'_dynamic'] = dp[name]
            w[name+'_static'] = sp[name]
            w[name+'_delta'] = dp[name]-sp[name]
        w['exact_cost_effect_on_increment'] = -w.exact_cost_drag_return_delta
        w['signal_year'] = w.index.str[:4].astype(int)
        self.close('paired_gross_stock_weekly', m.groupby('signal_date').gross_contribution_delta.sum().reindex(self.dates), w.gross_return_delta)
        self.close('paired_net_stock_weekly', m.groupby('signal_date').net_contribution_delta.sum().reindex(self.dates), w.net_return_delta)
        self.close('paired_gross_plus_cost_equals_net', w.gross_return_delta+w.exact_cost_effect_on_increment, w.net_return_delta)
        self.close('paired_terminal_wealth_gap', [m.net_linked_delta.sum()], [w.net_nav_delta.iloc[-1]])
        for date, g in m.groupby('signal_date'):
            a = set(g.loc[g.weight_dynamic.gt(1e-12) & g.PERMNO.ne('CASH'), 'PERMNO'])
            b = set(g.loc[g.weight_static.gt(1e-12) & g.PERMNO.ne('CASH'), 'PERMNO'])
            w.loc[date, 'holdings_overlap'] = len(a & b)/min(len(a), len(b))
            w.loc[date, 'jaccard'] = len(a & b)/len(a | b)
        self.save('dynamic_minus_static_weekly', w.reset_index())
        latest = m.sort_values('signal_date').groupby('PERMNO').tail(1)[['PERMNO', 'ticker', 'issuer', 'signal_date']].rename(columns={'signal_date': 'name_as_of'})
        summary = m.groupby('PERMNO', as_index=False).agg(
            gross_weekly_increment_sum=('gross_contribution_delta', 'sum'),
            net_weekly_increment_sum=('net_contribution_delta', 'sum'),
            terminal_gross_wealth_gap=('gross_linked_delta', 'sum'), terminal_net_wealth_gap=('net_linked_delta', 'sum'))
        self.save('dynamic_minus_static_stock_summary', summary.merge(latest, on='PERMNO').sort_values('terminal_net_wealth_gap'))
        sec = m.groupby(['sector', 'sector_name'], as_index=False).agg(
            gross_weekly_increment_sum=('gross_contribution_delta', 'sum'),
            net_weekly_increment_sum=('net_contribution_delta', 'sum'),
            terminal_net_wealth_gap=('net_linked_delta', 'sum'))
        self.save('dynamic_minus_static_sector_summary', sec.sort_values('terminal_net_wealth_gap'))
        self.save('dynamic_minus_static_holding_category', m.groupby('category', as_index=False).agg(
            gross_weekly_increment_sum=('gross_contribution_delta', 'sum'),
            net_weekly_increment_sum=('net_contribution_delta', 'sum'), terminal_net_wealth_gap=('net_linked_delta', 'sum')))
        yearly = []
        for year, g in w.groupby('signal_year'):
            row = dict(signal_year=year, n_weeks=len(g),
                       dynamic_net_compounded=np.prod(1+g.net_return_dynamic)-1,
                       static_net_compounded=np.prod(1+g.net_return_static)-1,
                       gross_increment_arithmetic_sum=g.gross_return_delta.sum(),
                       exact_cost_effect_arithmetic_sum=g.exact_cost_effect_on_increment.sum(),
                       net_increment_arithmetic_sum=g.net_return_delta.sum(),
                       mean_holdings_overlap=g.holdings_overlap.mean())
            yearly.append(row)
        self.save('dynamic_minus_static_by_signal_year', pd.DataFrame(yearly))
        stat = hac_mean(w.net_return_delta)
        stat.update({'gross_'+k:v for k,v in hac_mean(w.gross_return_delta).items()})
        stat.update(dict(mean_overlap=w.holdings_overlap.mean(), mean_jaccard=w.jaccard.mean(),
                         annualized_exact_cost_effect=w.exact_cost_effect_on_increment.mean()*52,
                         terminal_net_wealth_gap=w.net_nav_delta.iloc[-1],
                         terminal_gross_wealth_gap=w.gross_nav_delta.iloc[-1]))
        old = csv(self.source/'outputs/risk_analysis/us_hist_oos/fmom_significance_summary.csv').iloc[0]
        self.close('M5_HAC_net_increment', [stat['mean_annualized'], stat['hac_p']], [old.net_increment_ann, old.net_hac_pvalue])
        self.save('timing_increment_summary', pd.DataFrame([stat]))

    def drawdowns(self):
        events, detail = [], []
        for strategy, p in self.path.groupby('strategy', sort=False):
            p = p.reset_index(drop=True)
            peak, trough, recovery = drawdown_window(p.net_return)
            nav = np.r_[1., p.net_nav.to_numpy()]
            nav_dates = [p.execution_date.iloc[0]]+p.period_end.tolist()
            dates = p.signal_date.iloc[peak:trough]
            x = self.stock[(self.stock.strategy == strategy) & self.stock.signal_date.isin(dates)].copy()
            x['drawdown_net_linked'] = x.net_linked/nav[peak]
            x['drawdown_direct_cost_linked'] = x.direct_cost_linked/nav[peak]
            x['drawdown_holding_linked'] = x.holding_linked_in_net_account/nav[peak]
            detail.append(x)
            signed = nav[trough]/nav[peak]-1
            self.close(strategy+'_drawdown_contribution_closure', [x.drawdown_net_linked.sum()], [signed])
            events.append(dict(strategy=strategy, peak_date=nav_dates[peak], trough_date=nav_dates[trough],
                               recovery_date=nav_dates[recovery] if recovery is not None else '',
                               peak_to_trough_weeks=trough-peak,
                               peak_to_recovery_weeks=recovery-peak if recovery is not None else np.nan,
                               signed_drawdown_return=signed))
        self.save('max_drawdown_events', pd.DataFrame(events))
        x = pd.concat(detail, ignore_index=True)
        self.save('drawdown_sector_contributors', x.groupby(['strategy', 'sector', 'sector_name'], as_index=False).agg(
            drawdown_net_linked=('drawdown_net_linked', 'sum'), drawdown_holding_linked=('drawdown_holding_linked', 'sum'),
            drawdown_direct_cost_linked=('drawdown_direct_cost_linked', 'sum')))
        last = x.sort_values('signal_date').groupby(['strategy', 'PERMNO']).tail(1)[['strategy', 'PERMNO', 'ticker', 'issuer']]
        self.save('drawdown_stock_contributors', x.groupby(['strategy', 'PERMNO'], as_index=False).agg(
            drawdown_net_linked=('drawdown_net_linked', 'sum')).merge(last, on=['strategy', 'PERMNO']))

    def factors(self):
        # Reuse the committed M4 implementation, not a new factor definition.
        fr = module(self.source/'src/strategy/factor_returns.py', 'frozen_factor_returns')
        rcols = KEY+['next_execution_date', 'execution_to_next_execution_total_return', 'execution_feasible', 'unavailable_reason']
        f = fr.compute_factor_returns(self.scores.copy(), self.returns[rcols].copy(), end='2025-12-31')
        self.check('factor_mimicking_259_by_k4', len(f) == 259*4)
        self.save('factor_mimicking_returns_weekly', f)
        f = self.weights.merge(f[KEY[:1]+['family', 'factor_return_gross', 'factor_return_available']],
                               on=['signal_date', 'family'], validate='one_to_one')
        ok = f.factor_return_available.eq(1) & np.isfinite(f.factor_return_gross)
        complete = f.signal_date.map(ok.groupby(f.signal_date).sum()).eq(4)
        self.save('factor_timing_excluded_weeks', f[~complete])
        f = f[complete].copy()
        f['factor_timing_increment_diagnostic'] = (f.weight-.25)*f.factor_return_gross
        self.save('factor_timing_contributions_weekly', f)
        self.save('factor_timing_family_summary', f.groupby('family', as_index=False).agg(
            complete_weeks=('signal_date', 'size'), mean_weight_complete_sample=('weight', 'mean'),
            diagnostic_annual_mean_contribution=('factor_timing_increment_diagnostic', lambda x: x.mean()*52),
            factor_ls_annual_mean=('factor_return_gross', lambda x: x.mean()*52)))
        fw = self.weights.groupby('family', as_index=False).agg(
            weeks=('signal_date', 'size'), mean_weight=('weight', 'mean'), min_weight=('weight', 'min'), max_weight=('weight', 'max'),
            overweight_fraction=('weight', lambda x: (x>.25+1e-12).mean()), equalweight_fraction=('weight', lambda x: (abs(x-.25)<1e-12).mean()))
        self.save('factor_weights_summary', fw)
        exposure = []
        for strategy, (hold, _, _) in self.inputs.items():
            h = hold[hold.signal_date.isin(self.dates)].merge(self.scores[KEY+SCORES], on=KEY, validate='one_to_one')
            self.check(strategy+'_score_exposure_complete', np.isfinite(h[SCORES].to_numpy()).all())
            for family, col in zip(FAMILIES, SCORES):
                ex = (h.weight*h[col]).groupby(h.signal_date).sum()
                exposure.extend(dict(strategy=strategy, signal_date=date, family=family, holdings_weighted_score=float(v)) for date,v in ex.items())
        self.save('holdings_factor_score_exposure_weekly', pd.DataFrame(exposure))
        self.save('holdings_factor_score_exposure_summary', pd.DataFrame(exposure).groupby(['strategy', 'family'], as_index=False).agg(
            mean_holdings_weighted_score=('holdings_weighted_score', 'mean')))
        # Reuse M5's same complete-case diagnostic and disclose its smaller sample.
        ft = module(self.source/'src/risk_analysis/factor_timing_diagnostics.py', 'm5_factor_diagnostics')
        predictive = ft.predictive_tests(f)
        self.save('factor_timing_predictive_weekly', predictive)
        self.save('factor_timing_predictive_summary', pd.DataFrame([dict(
            complete_weeks=len(predictive), excluded_weeks=259-len(predictive),
            mean_rank_ic=predictive.rank_ic.mean(), winner_hit_rate=predictive.winner_hit.mean(),
            diagnostic_annual_mean_increment=f.groupby('signal_date').factor_timing_increment_diagnostic.sum().mean()*52)]))

    def regressions(self):
        ff = module(self.source/'src/risk_analysis/ff_factor_regression.py', 'm5_ff6')
        daily = self.parquet('handoff/us_downstream/data/ff_factors_daily.parquet')
        daily = daily[daily.sample_role.eq('HIST_OOS_BASE')].copy()
        self.check('ff_decimal_units', daily.unit.eq('DECIMAL_RETURN').all())
        daily = daily.rename(columns=dict(mktrf='Mkt-RF', smb='SMB', hml='HML', rmw='RMW', cma='CMA', rf='RF', umd='Mom'))
        daily['date'] = pd.to_datetime(daily.date)
        daily = daily[['date']+ff.FACTOR_COLS+['RF']].sort_values('date').reset_index(drop=True)
        dp = self.inputs['Dynamic'][2].set_index('signal_date')
        sp = self.inputs['Static'][2].set_index('signal_date')
        strategy = self.calendar[['signal_date', 'execution_date', 'next_execution_date']].copy()
        strategy['dynamic'] = strategy.signal_date.map(dp.net_return)
        strategy['static'] = strategy.signal_date.map(sp.net_return)
        for c in ['signal_date', 'execution_date', 'next_execution_date']:
            strategy[c] = pd.to_datetime(strategy[c])
        aligned = ff.aggregate_factors(strategy, daily)
        self.save('ff6_aligned_weekly', aligned)
        summaries, coefficients, components = [], [], []
        for label, target in [('Dynamic FMOM', 'dynamic_excess'), ('Static Multi-Factor', 'static_excess'), ('Dynamic - Static', 'increment')]:
            summary, coef = ff.fit_ff6(aligned, target, label)
            summaries.append(summary)
            coefficients.extend(coef)
            beta = {r['factor']:r['coefficient'] for r in coef}
            fit = np.full(len(aligned), beta['const'])
            for factor in ff.FACTOR_COLS:
                fit += beta[factor]*aligned[factor].to_numpy()
            residual = aligned[target].to_numpy()-fit
            for i, row in aligned.iterrows():
                if label != 'Dynamic - Static':
                    components.append(dict(model=label, signal_date=row.signal_date, component='RF', contribution=row.RF))
                components.append(dict(model=label, signal_date=row.signal_date, component='Alpha', contribution=beta['const']))
                for factor in ff.FACTOR_COLS:
                    components.append(dict(model=label, signal_date=row.signal_date, component=factor, contribution=beta[factor]*row[factor]))
                components.append(dict(model=label, signal_date=row.signal_date, component='Residual', contribution=residual[i]))
            total = pd.DataFrame(components)
            actual = total[total.model == label].groupby('signal_date').contribution.sum().reindex(aligned.signal_date)
            expected = aligned[target] if label == 'Dynamic - Static' else aligned[target]+aligned.RF
            self.close(label+'_regression_component_closure', actual, expected)
        result = pd.DataFrame(summaries)
        upstream = csv(self.source/'outputs/risk_analysis/us_hist_oos/ff6_regression_summary.csv').set_index('model')
        for r in summaries:
            self.close(r['model']+'_M5_FF6_reproduction', [r['alpha_annualized'], r['r_squared'], r['alpha_pvalue']],
                       upstream.loc[r['model'], ['alpha_annualized', 'r_squared', 'alpha_pvalue']].astype(float))
        self.save('ff6_regression_summary', result)
        self.save('ff6_coefficients', pd.DataFrame(coefficients))
        self.save('regression_components_weekly', pd.DataFrame(components))
        means = pd.DataFrame(components).groupby(['model', 'component'], as_index=False).agg(
            mean_weekly_contribution=('contribution', 'mean'))
        means['annualized_arithmetic_mean_contribution'] = means.mean_weekly_contribution*52
        self.save('regression_mean_return_explanation', means)

    def finish(self):
        for i,row in enumerate(self.manifest):
            self.check(f'unchanged_{i:03d}_'+Path(row['path']).name, sha(row['path']) == row['sha256'], row['path'])
        self.save('qa_checks', pd.DataFrame(self.qa_rows))
        environment = {p:version(p) for p in ['numpy', 'pandas', 'pyarrow', 'matplotlib', 'statsmodels', 'scipy']}
        manifest = dict(created_at=datetime.now(timezone(timedelta(hours=8))).isoformat(),
                        source_branch='member5-risk-analysis', source_commit=SOURCE_COMMIT,
                        source_run_id=csv(self.source/'outputs/strategy/frozen/hist_oos/run_manifest.csv').iloc[0].run_id,
                        sample='HIST_OOS_BASE', completed_weeks=259, annualization=52,
                        stock_net_attribution='post-cost holdings P&L minus security trade expenses; linked with net pre-period wealth',
                        cumulative_unit='wealth per unit of initial capital, not CAGR contributions',
                        regenerable_untracked_outputs=['stock_contributions_weekly.parquet', 'dynamic_minus_static_stock_weekly.parquet'],
                        strategy_parameters_modified=False, source_files_modified=False,
                        python=sys.version, environment=environment,
                        analysis_requirements_sha256=sha(HERE/'requirements-attribution.txt'),
                        analysis_code_sha256={p.name:sha(p) for p in sorted(HERE.glob('*.py'))})
        (self.out/'run_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        from reporting import build_report
        build_report(self)
        # Include all engine/reporting code, and a reproducible file index.
        pd.DataFrame([dict(file=p.relative_to(self.out).as_posix(), bytes=p.stat().st_size, sha256=sha(p))
                      for p in sorted(self.out.rglob('*')) if p.is_file() and p.name != 'output_manifest.csv' and p.suffix != '.parquet']).to_csv(self.out/'output_manifest.csv', index=False)

    def run(self):
        for stage in ['record_inputs','load','holdings_contributions','summaries',
                      'paired_attribution','drawdowns','factors','regressions','finish']:
            print('Running '+stage, flush=True)
            getattr(self,stage)()
        print(f'Attribution complete: {self.out}')
        print(self.tables['performance_reconciliation'].to_string(index=False))
        print(self.tables['timing_increment_summary'].to_string(index=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=HERE.parents[1])
    parser.add_argument('--data', type=Path, default=HERE.parents[1])
    parser.add_argument('--output', type=Path, default=HERE/'results')
    args = parser.parse_args()
    Analysis(args.source, args.data, args.output).run()
