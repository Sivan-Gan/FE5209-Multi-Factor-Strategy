"""Accounting helpers for attribution of the existing frozen weekly paths.

No portfolio construction or parameter selection occurs here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def wealth_before(returns):
    r = np.asarray(returns, dtype=float)
    return np.r_[1.0, np.cumprod(1.0 + r)[:-1]]


def stock_net_contributions(gross_contributions, security_costs, total_cost):
    """Post-cost holdings P&L minus pre-cost-NAV trade expenses.

    Costs may belong to securities sold out of the portfolio. Both arrays
    must include the union of held and traded securities.
    """
    return (1.0 - total_cost) * np.asarray(gross_contributions) - np.asarray(security_costs)


def terminal_cost_loss(gross_returns, net_returns):
    """Per-week exact gross-vs-net terminal wealth loss, including future growth."""
    g = np.asarray(gross_returns, dtype=float)
    n = np.asarray(net_returns, dtype=float)
    future = np.r_[np.cumprod((1.0 + g[:0:-1]))[::-1], 1.0]
    return wealth_before(n) * (g - n) * future


def drawdown_window(net_returns):
    """Indices into a NAV vector that includes the initial NAV of one.

    Returns (peak NAV index, trough NAV index, recovery NAV index or None).
    The contributing return indices are peak through trough-1.
    """
    nav = np.r_[1.0, np.cumprod(1.0 + np.asarray(net_returns, dtype=float))]
    dd = nav / np.maximum.accumulate(nav) - 1.0
    trough = int(np.argmin(dd))
    peak = int(np.argmax(nav[:trough + 1]))
    recovery_candidates = np.flatnonzero(nav[trough + 1:] >= nav[peak])
    recovery = int(trough + 1 + recovery_candidates[0]) if len(recovery_candidates) else None
    return peak, trough, recovery


def hac_mean(values, lag=4):
    """The M5 mean-test convention: Bartlett HAC, normal asymptotic inference."""
    from math import erf, sqrt
    x = np.asarray(values, dtype=float)
    n = len(x)
    mean = x.mean()
    z = x - mean
    omega = np.dot(z, z) / n
    for k in range(1, lag + 1):
        omega += 2 * (1 - k / (lag + 1)) * np.dot(z[k:], z[:-k]) / n
    se = np.sqrt(omega / n)
    t = mean / se
    p = 2 * (1 - (0.5 * (1 + erf(abs(t) / sqrt(2)))))
    return dict(mean_weekly=float(mean), mean_annualized=float(mean * 52),
                hac_t=float(t), hac_p=float(p), hac_lag=lag,
                ci_low_ann=float((mean - 1.96 * se) * 52),
                ci_high_ann=float((mean + 1.96 * se) * 52))


def link_yearly(stock, paths):
    """Rebase wealth contributions within M5 signal-year groups, not calendar years."""
    pieces = []
    for (strategy, year), p in paths.groupby(['strategy', 'signal_year'], sort=True):
        p = p.sort_values('signal_date')
        x = stock[(stock.strategy == strategy) & stock.signal_date.isin(p.signal_date)].copy()
        x['net_linked_within_year'] = x.net_linked / p.iloc[0].net_nav_before
        x['gross_linked_within_year'] = x.gross_linked / p.iloc[0].gross_nav_before
        x['signal_year'] = year
        pieces.append(x)
    return pd.concat(pieces, ignore_index=True)
