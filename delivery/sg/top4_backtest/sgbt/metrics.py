import numpy as np
import pandas as pd


def drawdown(nav, initial=1.):
    n = np.r_[initial, np.asarray(nav, dtype=float)]
    return float(np.max(1-n/np.maximum.accumulate(n)))


def source_metrics(weekly):
    r = weekly.net_return.to_numpy(float)
    std = np.std(r, ddof=1)
    return dict(weeks=len(r), cumulative_return=float(weekly.nav.iloc[-1]-1),
                cagr_52w=float(weekly.nav.iloc[-1]**(52/len(r))-1),
                volatility_52w=float(std*np.sqrt(52)),
                sharpe_zero_rf=float(np.mean(r)/std*np.sqrt(52)) if std else None,
                max_drawdown=drawdown(weekly.nav), annual_gtr=float(weekly.gtr.mean()*52))


def execution_metrics(result):
    d, f, o = result['daily'], result['fills'], result['orders']
    initial = result['audit']['config']['initial_cash']
    elapsed = (d.date.iloc[-1]-d.date.iloc[0]).days
    # Price-only figures are explicitly tagged and not treated as strategy TR.
    return dict(scope=result['audit']['performance_scope'],
                execution=result['audit']['execution_scope'], sessions=len(d),
                ending_nav=float(d.nav.iloc[-1]),
                diagnostic_cumulative_change=float(d.nav.iloc[-1]/initial-1),
                diagnostic_cagr_calendar=float((d.nav.iloc[-1]/initial)**(365.25/elapsed)-1) if elapsed else None,
                max_drawdown=drawdown(d.nav, initial),
                cash_fraction_mean=float((d.cash/d.nav).mean()),
                receivable_fraction_mean=float((d.receivable/d.nav).mean()),
                fills=len(f), orders=len(o),
                incomplete_orders=int((o.remaining != 0).sum()) if len(o) else 0,
                total_fees=float(f.total_fee.sum()) if len(f) else 0.,
                proxy_slippage_cost=float(f.slippage_cost.sum()) if len(f) else 0.,
                accounting_error_max=result['audit']['max_accounting_error'])


def matched_benchmark(strategy_nav, benchmark_nav, *, total_return_verified=False):
    """No price-only versus total-return index comparisons or silent date fill."""
    if not total_return_verified:
        raise ValueError('Benchmark comparison requires matching verified total-return bases')
    a, b = strategy_nav.copy(), benchmark_nav.copy()
    a['date'], b['date'] = pd.to_datetime(a.date), pd.to_datetime(b.date)
    if a.date.duplicated().any() or b.date.duplicated().any():
        raise ValueError('Duplicate benchmark dates')
    a, b = a.sort_values('date'), b.sort_values('date')
    if not a.date.reset_index(drop=True).equals(b.date.reset_index(drop=True)):
        raise ValueError('Benchmark dates must match exactly; no silent forward fill')
    if not np.isfinite(a.nav).all() or not np.isfinite(b.nav).all() or (a.nav <= 0).any() or (b.nav <= 0).any():
        raise ValueError('Invalid benchmark NAV')
    return pd.DataFrame(dict(date=a.date.to_numpy(), strategy=a.nav.to_numpy()/a.nav.iloc[0],
                             benchmark=b.nav.to_numpy()/b.nav.iloc[0]))
