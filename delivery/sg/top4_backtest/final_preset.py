"""The group's frozen final research specification, agreed 2026-10-20."""
import numpy as np
import pandas as pd

RELEASE = 'FINAL_100K_DIV14_V1'
SCENARIO = 'legacy_top4_frozen_100k_div14_final'
PERFORMANCE_SCOPE = 'SOURCE_DIVIDEND_14D_ESTIMATE_UNVERIFIED_PRICE_SHARE_BASIS'
DIVIDEND_POLICY = dict(
    enabled=True, source_member='data/corporate_actions_2006.csv',
    payment_delay_calendar_days=14,
    payment_roll='first_proxy_session_on_or_after_assumed_payment_date',
    amount_basis='source_vendor_per_share_as_given',
    currency_assumption='SGD',
    tax_assumption='source_amount_used_without_additional_tax_deduction',
    apply_source_split_events=False)
EXECUTION_SETTINGS = dict(
    initial_cash=100000, default_lot=100, reserve_fraction=.01,
    participation=.1, adv_window=20, max_order_sessions=5,
    broker_bps=5, minimum_commission=5, market_bps=4, fee_tax_rate=0,
    slippage_bps=10, impact_y=0, impact_domain=.2, settlement_sessions=2,
    max_stale_sessions=5, action_policy='price_only_diagnostic')


def validate_final_preset(config, execution_settings=None):
    expected = [dict(name=SCENARIO, top_k=4, target_mode='frozen', initial_cash=100000)]
    if (config.get('framework_release') != RELEASE or config.get('main_execution_scenario') != SCENARIO
            or config.get('dividend_policy') != DIVIDEND_POLICY
            or config.get('execution_scenarios') != expected):
        raise ValueError('Final preset is frozen: Top4, SGD100000, source dividends with ex-date+14 calendar days')
    if execution_settings is not None and execution_settings != EXECUTION_SETTINGS:
        raise ValueError('Execution settings differ from the frozen final preset')


def prepare_source_dividends(source, targets, sessions, end):
    """Retain vendor per-share units. Never certify payment dates or add splits.

    Only source cash events in the evaluation span and ever-targeted names are
    included. Actual entitlement is calculated by the engine from pre-ex-date
    holdings. Source dates outside the calendar stop the run, not silently roll.
    """
    if source is None:
        raise ValueError('Final dividend preset requires the original source corporate-action member')
    a = source.copy()
    required = {'date', 'ticker', 'action_type', 'dividend', 'split_ratio'}
    if required-set(a):
        raise ValueError('Source corporate-action fields missing')
    a['date'] = pd.to_datetime(a.date, errors='raise')
    if a.date.isna().any() or a.duplicated(['date','ticker','action_type']).any():
        raise ValueError('Missing or duplicate source event date/key')
    selected = (a.action_type.eq('cash_dividend') & a.ticker.isin(targets.ticker)
                & a.date.between(pd.Timestamp(targets.signal_date.min()), pd.Timestamp(end)))
    a = a[selected].copy()
    a['dividend'] = pd.to_numeric(a.dividend, errors='raise')
    if not np.isfinite(a.dividend).all() or (a.dividend < 0).any():
        raise ValueError('Invalid source cash dividend')
    calendar = pd.DatetimeIndex(pd.to_datetime(sessions))
    if not a.date.isin(calendar).all():
        raise ValueError('Source ex-date outside proxy sessions: resolve rather than silently shift')
    if a.empty:
        raise ValueError('Final preset found no source cash events; check package and evaluation coverage')
    result = pd.DataFrame(dict(
        event_id='SOURCE_DIVIDEND_ASSUMED14D_'+a.index.astype(str),
        ticker=a.ticker, ex_date=a.date, kind='dividend', value=a.dividend,
        pay_date=a.date+pd.Timedelta(days=14)))
    result['payment_date_is_assumed'] = True
    result['currency_is_assumed'] = True
    return result.reset_index(drop=True)
