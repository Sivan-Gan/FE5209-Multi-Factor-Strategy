"""Adapter to the actual old-data recommended Top4, not the new WRDS pipeline."""
from pathlib import Path
import hashlib
import json
import zipfile
import numpy as np
import pandas as pd
from support import HERE, add_repo

PREFIX = 'SGX_comprehensive_2006_2026_handoff/'
MEMBERS = [
    'factor_panels/sg_seven_factor_candidate_panel_2006_latest.csv.gz',
    'data/prices_daily_2006.csv',
    'data/stock_universe_six_sectors.csv',
    'benchmark/STI_Total_Return_LSEG_export.xlsx',
]
LEGACY_TAG = 'TUNE_T4L3_IV_N20_EQ_NONE'


def extract_inputs(archive, destination, lock_path=None):
    """Read only four locked data members; no archive scripts or bulk extraction."""
    destination = Path(destination)
    local_lock = Path(lock_path) if lock_path else None
    lock = json.loads(local_lock.read_text(encoding='utf-8')) if local_lock and local_lock.exists() else None
    if lock_path and lock is None:
        raise ValueError('Explicit local data-lock file does not exist')
    with zipfile.ZipFile(archive) as z:
        for member in MEMBERS:
            full = PREFIX+member
            if z.namelist().count(full) != 1:
                raise ValueError(f'Expected original comprehensive data, missing/duplicate member: {full}')
            data = z.read(full)
            if lock is not None and hashlib.sha256(data).hexdigest() != lock['member_sha256'][member]:
                raise ValueError(f'Old-data member differs from validated package: {member}')
            target = destination/member
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return destination


def modules(data_dir):
    add_repo()
    from src.sandbox import sg_tune_lab as T
    from src.sandbox import sg_adaptive_lab as L
    from src.strategy import portfolio as pf, metrics as mt
    expected = {'Quality': 'Quality_score', 'Momentum': 'Momentum_score',
                'Low Risk': 'LowRisk_score', 'Investment': 'Investment_score',
                'Size': 'SizeSmall_score', 'Value': 'Value_score'}
    # Match sg_tune_lab import side effect explicitly, independent of import order.
    L.COLS = expected
    T.POOL = list(expected)
    L.SG = Path(data_dir)
    pf.SELECT_FRACTION, pf.EXIT_FRACTION, pf.SECTOR_CAP = .20, .30, .30
    return T, L, pf, mt


def load_old_data(data_dir, eval_end):
    T, L, pf, mt = modules(data_dir)
    panel, universe, returns, last = L.load_panel()
    # L first flips Size for the early 7F lab. T.main flips it back for 6F small-cap.
    universe['SizeSmall_score'] = -universe['SizeSmall_score']
    if not np.allclose(universe.SizeSmall_score, universe.Size_score, equal_nan=True):
        raise AssertionError('Small-cap Size sign not preserved')
    if panel.duplicated(['signal_date', 'PERMNO']).any():
        raise ValueError('Duplicate old-panel stock-week')
    end = min(str(last), eval_end)
    factor_returns = L.factor_returns(universe, end)
    done = set(returns.loc[returns.next_execution_date.le(end), 'signal_date'])
    dates = sorted(d for d in universe.signal_date.unique() if d >= '2016-01-01' and d in done)
    if not dates:
        raise ValueError('No complete old-data holding periods')
    cal = universe[universe.signal_date.isin(dates)].groupby('signal_date')[['execution_date', 'next_execution_date']].first()
    sti = L.sti_weekly(cal.reset_index())
    return dict(T=T, L=L, pf=pf, mt=mt, panel=panel, universe=universe, returns=returns,
                factor_returns=factor_returns, dates=dates, calendar=cal, sti=sti, end=end)


def source_strategy(data, k):
    T, pf, mt = data['T'], data['pf'], data['mt']
    decisions = T.decisions(data['factor_returns'], data['dates'], f'T{k}L3')
    scores = T.scored_for(data['universe'], data['dates'], decisions, 'IV')
    holdings, construction = pf.build_holdings(scores, use_buffer=True)
    path, ledger = mt.weekly_path_and_ledger(holdings, data['returns'], data['end'], .001)
    path = path.set_index('signal_date')
    for bps in [10, 20, 30]:
        path[f'net_return_{bps}bp'] = mt.net_returns(path, bps/10000)
    path['STI_TR'] = data['sti'].reindex(path.index)
    weights = []
    for year, (selected, methods) in decisions.items():
        weights.append(dict(year=int(year), selected=','.join(selected), **methods['IV']))
    return dict(holdings=holdings, construction=construction, scores=scores, path=path,
                ledger=ledger, weights=pd.DataFrame(weights))


def canonical_targets(holdings, calendar):
    # Company/ticker IDs remain strings; no security crosswalk is fabricated.
    target = holdings[['signal_date', 'execution_date', 'PERMNO', 'weight']].rename(columns={'PERMNO': 'ticker'}).copy()
    c = calendar.reset_index()[['signal_date', 'next_execution_date']]
    target = target.merge(c, on='signal_date', validate='many_to_one')
    for col in ['signal_date', 'execution_date', 'next_execution_date']:
        target[col] = pd.to_datetime(target[col])
    return target


def independent_weekly(holdings, returns, calendar, cost_bps):
    """Independent source fee/drift reconstruction, including source missing-return=0."""
    r = returns.set_index(['signal_date', 'PERMNO'])
    if not r.index.is_unique:
        raise ValueError('Duplicate weekly return keys')
    previous, nav, rows = pd.Series(dtype=float), 1., []
    for date, g in holdings.groupby('signal_date', sort=True):
        target = g.set_index('PERMNO').weight
        if target.sum() > 1+1e-9:
            raise ValueError('Overinvested source target')
        values, missing = [], 0.
        for t, weight in target.items():
            if (date, t) not in r.index or r.loc[(date, t), 'usable'] != 1:
                values.append(0.)
                missing += weight
            else:
                values.append(float(r.loc[(date, t), 'execution_to_next_execution_total_return']))
        ret = pd.Series(values, index=target.index)
        if not np.isfinite(ret).all():
            raise ValueError('Nonfinite usable held return')
        idx = target.index.union(previous.index)
        gtr = float((target.reindex(idx, fill_value=0)-previous.reindex(idx, fill_value=0)).abs().sum())
        gross = float((target*ret).sum())
        net = (1-gtr*cost_bps/10000)*(1+gross)-1
        if gross <= -1 or net <= -1:
            raise ValueError('Source-style weekly path cannot continue after total loss')
        previous = target*(1+ret)/(1+gross)
        nav *= 1+net
        rows.append(dict(signal_date=date, gross_return=gross, gtr=gtr, net_return=net,
                         nav=nav, unpriced_weight=missing,
                         next_execution_date=calendar.loc[date, 'next_execution_date']))
    return pd.DataFrame(rows)
