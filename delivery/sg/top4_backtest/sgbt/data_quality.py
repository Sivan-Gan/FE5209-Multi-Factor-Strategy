"""Evidence checks, not a conversion of vendor events into verified cash flows.

Prices named `close` need not be unadjusted. An event inventory is not proof of
complete coverage. This module never invents payment dates or share bases.
"""
import numpy as np
import pandas as pd


def corporate_action_audit(bars, actions):
    b, a = bars.copy(), actions.copy()
    if {'date', 'ticker', 'close', 'adj_close'} - set(b):
        raise ValueError('Audit needs close and adjusted close')
    if {'date', 'ticker', 'action_type', 'dividend', 'split_ratio'} - set(a):
        raise ValueError('Missing source event fields')
    b['date'], a['date'] = pd.to_datetime(b.date), pd.to_datetime(a.date)
    if b.duplicated(['date', 'ticker']).any() or a.duplicated(['date', 'ticker', 'action_type']).any():
        raise ValueError('Duplicate price/event key')
    b = b.sort_values(['ticker', 'date'])
    for col in ['close', 'adj_close']:
        b[col] = pd.to_numeric(b[col], errors='raise')
        if (b[col].dropna() <= 0).any() or np.isinf(b[col]).any():
            raise ValueError('Invalid audit price')
    # The source has blank holiday rows. Compare with the previous *observed*
    # pair of prices, not a blank calendar row; never backfill from the future.
    observed = b[['close', 'adj_close']].notna().all(axis=1)
    for col in ['close', 'adj_close', 'date']:
        b['previous_'+col] = b.loc[observed].groupby('ticker')[col].shift().reindex(b.index)
    b['adjustment_factor'] = b.adj_close / b.close
    previous_factor = b.previous_adj_close / b.previous_close
    b['factor_step'] = b.adjustment_factor / previous_factor - 1
    b['close_return'] = b.close / b.previous_close - 1
    b['adjusted_return'] = b.adj_close / b.previous_adj_close - 1
    # Yahoo's documented multiplicative adjustment implies this amount on the
    # vendor share basis. It is a diagnostic, not an actual receivable.
    b['implied_distribution_on_vendor_basis'] = b.previous_close * (1 - previous_factor/b.adjustment_factor)
    ev = a.merge(b[['date', 'ticker', 'previous_date', 'previous_close', 'close',
                    'factor_step', 'implied_distribution_on_vendor_basis']],
                 on=['date', 'ticker'], how='left', validate='many_to_one')
    div = ev.action_type.eq('cash_dividend')
    split = ev.action_type.eq('split_or_reverse_split')
    if (~(div | split)).any():
        raise ValueError('Unknown source event kind')
    if (ev.loc[div, 'dividend'].isna() | (ev.loc[div, 'dividend'] < 0)).any():
        raise ValueError('Invalid cash distribution')
    if (ev.loc[split, 'split_ratio'].isna() | (ev.loc[split, 'split_ratio'] <= 0)).any():
        raise ValueError('Invalid split ratio')
    ev['price_row_missing'] = ev.close.isna() | ev.previous_close.isna()
    ev['distribution_difference'] = ev.implied_distribution_on_vendor_basis - ev.dividend
    # A 2% tolerance is only a source consistency screen, not vendor certification.
    ev['distribution_matches_vendor_adjustment_screen'] = div & ~ev.price_row_missing & (
        ev.distribution_difference.abs() <= 1e-5 + .02*ev.dividend.abs())
    ratio = ev.close / ev.previous_close
    continuity = np.abs(np.log(ratio))
    raw_split_continuity = np.abs(np.log(ratio*ev.split_ratio))
    ev['split_price_screen'] = 'not_split'
    ev.loc[split, 'split_price_screen'] = 'ambiguous'
    ev.loc[split & ev.price_row_missing, 'split_price_screen'] = 'missing_price'
    ev.loc[split & ~ev.price_row_missing & (continuity+.05 < raw_split_continuity),
           'split_price_screen'] = 'already_split_adjusted_like_REVIEW_NOT_PROOF'
    ev.loc[split & ~ev.price_row_missing & (raw_split_continuity+.05 < continuity),
           'split_price_screen'] = 'raw_split_like_REVIEW_NOT_PROOF'
    keys = a.groupby(['date', 'ticker']).action_type.agg(','.join).rename('source_events')
    changes = b[b.factor_step.abs().gt(2e-5)].merge(keys, on=['date', 'ticker'], how='left')
    changes['source_event_present'] = changes.source_events.notna()
    summary = dict(
        source_event_rows=len(a), cash_distribution_rows=int(div.sum()), split_rows=int(split.sum()),
        event_tickers=int(a.ticker.nunique()), duplicate_keys=0,
        events_missing_price_comparison=int(ev.price_row_missing.sum()),
        distribution_consistency_screen_pass=int(ev.distribution_matches_vendor_adjustment_screen.sum()),
        material_adjustment_steps=len(changes),
        material_steps_without_source_event=int((~changes.source_event_present).sum()),
        split_price_screen_counts=ev.loc[split, 'split_price_screen'].value_counts().to_dict(),
        payment_date_field_present='pay_date' in a,
        distribution_currency_field_present='currency' in a,
        raw_price_basis_verified=False, complete_actions_verified=False,
        diagnostic_only=True,
        blockers=['payment dates and currency/share basis must be verified',
                  'close is not certified unadjusted; do not apply split events blindly',
                  'rights, spin-offs, mergers and delisting recovery coverage unverified'])
    return dict(summary=summary, events=ev, adjustment_steps=changes)
