"""Ex-post exposures for reporting; these do not change trading decisions."""
import numpy as np
import pandas as pd


def risk_diagnostics(result, security_master):
    h = result['holdings']
    if h.empty:
        return pd.DataFrame()
    h = h.copy()
    if 'six_sector' in security_master:
        h = h.merge(security_master[['ticker', 'six_sector']], on='ticker', how='left', validate='many_to_one')
        sector = h.groupby(['date', 'six_sector'], dropna=False).weight.sum().groupby('date').max()
    else:
        sector = pd.Series(dtype=float)
    risk = h.groupby('date').agg(max_stock_weight=('weight', 'max'),
                                 invested_weight=('weight', 'sum'),
                                 max_missing_mark_sessions=('missing_mark_sessions', 'max'),
                                 zero_volume_marked_names=('source_zero_volume', 'sum'))
    risk['stock_weight_square_sum'] = h.assign(sq=h.weight**2).groupby('date').sq.sum()
    risk['hhi'] = risk.stock_weight_square_sum/risk.invested_weight**2
    risk['effective_stock_count'] = np.where(risk.hhi > 0, 1/risk.hhi, 0)
    risk['max_sector_weight_current_mapping'] = sector
    risk['sector_mapping_scope'] = 'provided master; historical membership not independently certified'
    return risk.reset_index()
