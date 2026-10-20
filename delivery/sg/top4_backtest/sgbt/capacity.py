"""Fixed-NAV cross-sectional capacity screens, not a self-financing NAV path.

Every signal independently resets an idealized previous target portfolio to the
specified NAV. This isolates scale from compounding; hypothetical resets are not
trades or returns. Volume budgets are ex-post upper bounds, ignoring cash and
settlement. Capacity failure here is informative; passing is not a fill promise.
"""
import numpy as np
import pandas as pd


def fixed_nav_capacity(bars, sessions, targets, *, navs=(100000., 500000., 1000000.),
                       shocks=(1., .5), participation=.1, lot=100, reserve=.01,
                       adv_window=20, horizons=(1, 5, 10, 20)):
    if not 0 < participation <= 1 or lot < 1 or int(lot) != lot or not 0 <= reserve < 1:
        raise ValueError('Invalid capacity settings')
    if min(navs) <= 0 or not np.isfinite(navs).all() or not np.isfinite(shocks).all() or min(shocks) <= 0 or max(shocks) > 1:
        raise ValueError('Invalid NAV or volume stress')
    if adv_window < 2 or min(horizons) < 1 or any(int(h) != h for h in horizons):
        raise ValueError('Invalid rolling or execution window')
    b, t = bars.copy(), targets.copy()
    cal = pd.DatetimeIndex(pd.to_datetime(sessions))
    if cal.has_duplicates or not cal.is_monotonic_increasing:
        raise ValueError('Invalid capacity sessions')
    b['date'] = pd.to_datetime(b.date)
    for col in ['close', 'volume']:
        b[col] = pd.to_numeric(b[col], errors='raise')
    if np.isinf(b[['close', 'volume']]).any().any() or (b.close.dropna() <= 0).any() or (b.volume.dropna() < 0).any():
        raise ValueError('Invalid capacity price or volume')
    if b.duplicated(['date', 'ticker']).any():
        raise ValueError('Duplicate bars')
    for field in ['signal_date', 'execution_date']:
        t[field] = pd.to_datetime(t[field])
    if t.duplicated(['signal_date', 'ticker']).any() or not np.isfinite(t.weight).all() or (t.weight < 0).any():
        raise ValueError('Invalid targets')
    if (t.groupby('signal_date').weight.sum() > 1+1e-9).any():
        raise ValueError('Overinvested targets')
    px = b.pivot(index='date', columns='ticker', values='close').reindex(cal)
    vol = b.pivot(index='date', columns='ticker', values='volume').reindex(cal)
    adv = (px*vol).rolling(adv_window, min_periods=adv_window).mean().shift(1)
    # Keep missing observations blocked. Never forward-fill executable prices.
    budgets = {shock: np.floor(np.minimum(participation*vol*shock,
                participation*adv*shock/px)/lot+1e-9)*lot for shock in shocks}
    budget_arrays = {shock: frame.fillna(0).to_numpy() for shock, frame in budgets.items()}
    column_index = {name: i for i, name in enumerate(px.columns)}
    price_end = min(b.date.max(), cal[-1])
    previous_weights, previous_date = {}, None
    rows = []
    for signal, g in t.groupby('signal_date', sort=True):
        if g.execution_date.nunique() != 1:
            raise ValueError('Multiple execution dates')
        execution = g.execution_date.iloc[0]
        if signal not in cal or execution not in cal or execution <= signal:
            raise ValueError('Invalid target dates')
        weights = g.set_index('ticker').weight.to_dict()
        names = sorted(set(previous_weights) | set(weights))
        price = px.loc[signal].reindex(names)
        if price.isna().any() or (price <= 0).any():
            raise ValueError('Missing signal price; resolve rather than silently fill')
        # Idealized previous target held from previous signal. Price-only drift,
        # with residual cash; no assertion of actual filled holdings or dividends.
        drift = {name: (1-reserve)*previous_weights.get(name, 0.) * (
            price[name]/px.at[previous_date, name] if previous_date is not None else 1.) for name in names}
        denominator = 1-(1-reserve)*sum(previous_weights.values())+sum(drift.values())
        old_w = {name: drift[name]/denominator for name in names}
        activation = cal.get_loc(execution)
        for nav in navs:
            old = {name: int(nav*old_w[name]/price[name]//lot)*lot for name in names}
            new = {name: int(nav*(1-reserve)*weights.get(name, 0.)/price[name]//lot)*lot for name in names}
            for mode in ['rebalance', 'fresh_build', 'liquidate_target']:
                for name in names:
                    q = (new[name]-old[name] if mode == 'rebalance' else
                         new[name] if mode == 'fresh_build' else -new[name])
                    if not q:
                        continue
                    for shock in shocks:
                        row = dict(signal_date=signal, execution_date=execution, nav=nav,
                                   volume_multiplier=shock, mode=mode, ticker=name,
                                   requested_shares=q, signal_price=price[name],
                                   requested_value=abs(q)*price[name])
                        for h in horizons:
                            window = cal[activation:activation+h]
                            complete = len(window) == h and window[-1] <= price_end
                            if complete:
                                capacity = float(budget_arrays[shock][activation:activation+h, column_index[name]].sum())
                                quantity = min(abs(q), capacity)
                                row[f'budget_shares_{h}d'] = capacity
                                row[f'filled_value_upper_bound_{h}d'] = quantity*price[name]
                            else:
                                row[f'budget_shares_{h}d'] = np.nan
                                row[f'filled_value_upper_bound_{h}d'] = np.nan
                        rows.append(row)
        previous_weights, previous_date = weights, signal
    detail = pd.DataFrame(rows)
    summary = []
    for keys, g in detail.groupby(['nav', 'volume_multiplier', 'mode']):
        row = dict(zip(['nav', 'volume_multiplier', 'mode'], keys))
        for h in horizons:
            col = f'filled_value_upper_bound_{h}d'
            valid = g[col].notna()
            v = g[valid]
            requested = v.requested_value.sum()
            row[f'completion_upper_bound_{h}d'] = float(v[col].sum()/requested) if requested else None
            row[f'censored_orders_{h}d'] = int((~valid).sum())
            weekly = v.groupby('signal_date')[[col, 'requested_value']].sum()
            ratios = weekly[col]/weekly.requested_value
            row[f'weeks_below_95pct_{h}d'] = int((ratios < .95-1e-10).sum())
            row[f'eligible_weeks_{h}d'] = len(weekly)
        summary.append(row)
    return detail, pd.DataFrame(summary)
