"""Daily, long-only, self-financing execution scenarios, not a tick fill engine.

Close prices are proxy executions. Full-day volume limits are ex-post scenario
constraints, not information available at an opening auction. All sizing is
frozen at the prior signal close. A daily aggregate fill pays one minimum fee.
"""
from dataclasses import dataclass, asdict
import math
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ExecutionConfig:
    initial_cash: float = 100000.
    default_lot: int = 100
    reserve_fraction: float = .01
    participation: float = .10
    adv_window: int = 20
    max_order_sessions: int = 5
    broker_bps: float = 5.
    minimum_commission: float = 5.
    market_bps: float = 4.
    fee_tax_rate: float = 0.
    slippage_bps: float = 10.
    impact_y: float = 0.
    impact_domain: float = .20
    settlement_sessions: int = 2
    max_stale_sessions: int = 5
    action_policy: str = 'strict'

    def __post_init__(self):
        numbers = list(asdict(self).values())[:-1]
        if not all(np.isfinite(v) for v in numbers):
            raise ValueError('Nonfinite execution setting')
        if self.initial_cash <= 0 or not 0 < self.participation <= 1:
            raise ValueError('Invalid cash or participation')
        if not 0 <= self.reserve_fraction < 1 or self.default_lot < 1:
            raise ValueError('Invalid cash reserve or lot')
        for value in [self.default_lot, self.adv_window, self.max_order_sessions,
                      self.settlement_sessions, self.max_stale_sessions]:
            if int(value) != value or value < 0:
                raise ValueError('Expected nonnegative integer setting')
        if self.adv_window < 2 or self.max_order_sessions < 1:
            raise ValueError('Insufficient rolling history or order life')
        if min(self.broker_bps, self.minimum_commission, self.market_bps,
               self.fee_tax_rate, self.slippage_bps, self.impact_y) < 0:
            raise ValueError('Negative cost')
        if not 0 < self.impact_domain <= 1 or self.slippage_bps >= 10000:
            raise ValueError('Invalid impact domain or slippage')
        if self.action_policy not in ['strict', 'price_only_diagnostic']:
            raise ValueError('Unknown action policy')


@dataclass(frozen=True)
class Rebalance:
    signal_date: pd.Timestamp
    execution_date: pd.Timestamp
    weights: dict


def plans_from_targets(targets):
    return [Rebalance(pd.Timestamp(date), pd.Timestamp(g.execution_date.iloc[0]),
                      g.set_index('ticker').weight.to_dict())
            for date, g in targets.groupby('signal_date', sort=True)]


def simulate(bars, sessions, plans, config=ExecutionConfig(), *, selector=None,
             actions=None, actions_verified=False, rules=None, end=None):
    """selector(signal_date, actual_held_names) may provide closed-loop targets.

    Optional actions: event_id,ticker,ex_date,kind,value,pay_date. Supported kinds
    are split, dividend (net cash/share), delist_cash (cash/share). Split events
    may supply share_trade_date for bonus shares credited after the ex-date;
    incremental shares are valued at ex-date but blocked from sale until then.
    A delayed reverse consolidation or overlapping delayed bonus needs explicit
    resolution. Explicit cash
    delisting recovery may be zero; unsupported reorganizations fail closed.
    Rules: ticker,effective_date,lot_size,tick_size,tradable. Historical coverage
    of these fields is supplied by caller, not inferred by the engine.
    """
    c = config
    cal = pd.DatetimeIndex(pd.to_datetime(sessions))
    if cal.empty or cal.has_duplicates or not cal.is_monotonic_increasing or cal.tz is not None:
        raise ValueError('Sessions must be unique, sorted, naive Singapore dates')
    if not (cal == cal.normalize()).all() or (cal.dayofweek >= 5).any():
        raise ValueError('Invalid session dates')
    if not plans:
        raise ValueError('No rebalance plans')
    if c.action_policy == 'strict' and not actions_verified:
        raise ValueError('Strict execution requires verified corporate-action coverage and raw-price basis')
    b = bars.copy()
    required = {'date', 'ticker', 'close', 'volume'}
    if required-set(b):
        raise ValueError(f'Missing price fields: {required-set(b)}')
    b['date'] = pd.to_datetime(b.date)
    if b.duplicated(['date', 'ticker']).any():
        raise ValueError('Duplicate price bars')
    for col in ['close', 'volume']:
        b[col] = pd.to_numeric(b[col], errors='raise')
    if (b.close.dropna() <= 0).any() or (b.volume.dropna() < 0).any():
        raise ValueError('Invalid price or volume')
    if np.isinf(b[['close', 'volume']]).any().any():
        raise ValueError('Infinite price or volume')
    b = b[b.date.isin(cal)].copy()
    close = b.pivot(index='date', columns='ticker', values='close').reindex(cal)
    volume = b.pivot(index='date', columns='ticker', values='volume').reindex(cal)
    adv = (close*volume).rolling(c.adv_window, min_periods=c.adv_window).mean().shift(1)
    sigma = close.pct_change(fill_method=None).rolling(c.adv_window, min_periods=c.adv_window).std().shift(1)
    by_signal, by_exec = {}, {}
    for p in plans:
        s, e = pd.Timestamp(p.signal_date), pd.Timestamp(p.execution_date)
        if s not in cal or e not in cal or s >= e:
            raise ValueError('Signal and execution must be ordered valid sessions; no silent calendar roll')
        if s in by_signal or e in by_exec:
            raise ValueError('Duplicate signal or activation date')
        by_signal[s] = p
        by_exec[e] = p
    ordered = sorted(plans, key=lambda p: p.signal_date)
    if any(ordered[i].execution_date > ordered[i+1].signal_date for i in range(len(ordered)-1)):
        raise ValueError('Overlapping signal and activation sequence')
    start = min(by_signal)
    finish = pd.Timestamp(end) if end is not None else cal[-1]
    if finish not in cal or finish < max(by_exec):
        raise ValueError('Invalid end or insufficient simulation horizon')
    a = pd.DataFrame() if actions is None else actions.copy()
    if not a.empty:
        if {'event_id', 'ticker', 'ex_date', 'kind', 'value', 'pay_date'}-set(a):
            raise ValueError('Missing action fields')
        if a.event_id.duplicated().any():
            raise ValueError('Duplicate corporate event')
        a.ex_date = pd.to_datetime(a.ex_date)
        a.pay_date = pd.to_datetime(a.pay_date)
        if 'share_trade_date' not in a:
            a['share_trade_date'] = pd.NaT
        a['share_trade_date'] = pd.to_datetime(a.share_trade_date)
        for row in a.itertuples():
            if row.kind not in ['split', 'dividend', 'delist_cash']:
                raise ValueError('Unsupported action: resolve rights/mergers manually')
            if row.ex_date not in cal or not np.isfinite(row.value) or row.value < 0:
                raise ValueError('Invalid action')
            if row.kind == 'split' and row.value <= 0:
                raise ValueError('Invalid split ratio')
            if row.kind == 'split' and pd.notna(row.share_trade_date):
                if row.share_trade_date < row.ex_date or row.share_trade_date not in cal:
                    raise ValueError('Invalid bonus share tradability date')
                if row.value < 1 and row.share_trade_date > row.ex_date:
                    raise ValueError('Delayed reverse consolidation requires explicit handling')
            if row.kind != 'split' and (pd.isna(row.pay_date) or row.pay_date < row.ex_date):
                raise ValueError('Invalid cash event payment date')
    r = pd.DataFrame() if rules is None else rules.copy()
    if not r.empty:
        if {'ticker', 'effective_date', 'lot_size', 'tick_size', 'tradable'}-set(r):
            raise ValueError('Missing instrument rule fields')
        r.effective_date = pd.to_datetime(r.effective_date)
        if r.duplicated(['ticker', 'effective_date']).any():
            raise ValueError('Duplicate instrument rules')
        if ((r.lot_size < 1) | (r.lot_size % 1 != 0) | (r.tick_size < 0)).any():
            raise ValueError('Invalid lot or tick rule')
        if not r.tradable.isin([True, False]).all():
            raise ValueError('tradable must be boolean')
    rule_map = {t: g.sort_values('effective_date') for t, g in r.groupby('ticker')} if not r.empty else {}

    def rule(t, d):
        z = rule_map.get(t)
        if z is not None:
            z = z[z.effective_date.le(d)]
            if not z.empty:
                row = z.iloc[-1]
                return int(row.lot_size), float(row.tick_size), bool(row.tradable)
        return c.default_lot, 0., True

    positions, last_mark, mark_idx, delisted = {}, {}, {}, set()
    # Warmup marks are explicit observations; no backfill from future bars.
    for i, d in enumerate(cal[cal < start]):
        for t, price in close.loc[d].dropna().items():
            last_mark[t], mark_idx[t] = float(price), i
    cash = float(c.initial_cash)
    receipts, prepared, pending, unsettled_shares = [], {}, {}, []
    orders, fills, attempts, daily, holdings, signals, events, contributions = [], [], [], [], [], [], [], []
    order_seq = 0
    prev_nav = c.initial_cash
    max_error = 0.
    terminated_bankrupt = False
    for d in cal[(cal >= start) & (cal <= finish)]:
        i = cal.get_loc(d)
        pnl = fees_today = action_income = 0.
        slip_today = 0.
        by_price, by_income, by_fee, by_slippage = {}, {}, {}, {}
        for receipt in list(receipts):
            if receipt['pay_date'] <= d:
                cash += receipt['amount']
                receipts.remove(receipt)
                events.append(dict(date=d, kind='cash_settlement', **receipt))
        unsettled_shares = [x for x in unsettled_shares if x['settlement_date'] > d]
        if not a.empty:
            for row in a[a.ex_date.eq(d)].itertuples():
                t, q = row.ticker, positions.get(row.ticker, 0)
                if row.kind == 'delist_cash':
                    delisted.add(t)
                    if t in pending:
                        pending[t]['status'] = 'CANCELLED_DELIST'
                        pending.pop(t)
                    for source in prepared.values():
                        source.pop(t, None)
                if row.kind == 'split':
                    delayed_bonus = pd.notna(row.share_trade_date) and row.share_trade_date > d
                    if delayed_bonus and any(x.get('reason') == 'bonus' and x['ticker'] == t
                                             for x in unsettled_shares):
                        raise ValueError('Overlapping delayed bonus requires explicit handling')
                    for source in [positions] + list(prepared.values()):
                        if t in source:
                            nq = source[t]*row.value
                            if abs(nq-round(nq)) > 1e-7:
                                raise ValueError('Fractional split shares require explicit cash-in-lieu handling')
                            source[t] = int(round(nq))
                    if t in last_mark:
                        last_mark[t] /= row.value
                    if t in pending:
                        nq = pending[t]['remaining']*row.value
                        if abs(nq-round(nq)) > 1e-7:
                            raise ValueError('Fractional pending split order')
                        pending[t]['remaining'] = int(round(nq))
                        pending[t]['requested'] = int(round(pending[t]['requested']*row.value))
                    if delayed_bonus:
                        extra = positions.get(t, 0)-q
                        if extra:
                            unsettled_shares.append(dict(ticker=t, shares=extra,
                                settlement_date=row.share_trade_date, reason='bonus'))
                    else:
                        for delivery in unsettled_shares:
                            if delivery['ticker'] == t:
                                delivery['shares'] *= row.value
                    events.append(dict(date=d, ticker=t, kind='split', quantity_before=q,
                                       ratio=row.value, quantity_after=positions.get(t, 0),
                                       share_trade_date=row.share_trade_date if delayed_bonus else d))
                elif q:
                    amount = q*row.value
                    receipt = dict(pay_date=row.pay_date, amount=amount, ticker=t, reason=row.kind)
                    if row.pay_date <= d:
                        cash += amount
                    else:
                        receipts.append(receipt)
                    if row.kind == 'dividend':
                        action_income += amount
                        by_income[t] = by_income.get(t, 0.)+amount
                    else:
                        income = amount-q*last_mark[t]
                        action_income += income
                        by_income[t] = by_income.get(t, 0.)+income
                        positions.pop(t)
                        unsettled_shares = [x for x in unsettled_shares if x['ticker'] != t]
                    events.append(dict(date=d, ticker=t, kind=row.kind, quantity=q, amount=amount))
        today = close.loc[d]
        for t, q in positions.items():
            price = today.get(t, np.nan)
            if pd.notna(price):
                stock_pnl = q*(float(price)-last_mark[t])
                pnl += stock_pnl
                by_price[t] = stock_pnl
            elif i-mark_idx[t] > c.max_stale_sessions:
                raise ValueError(f'Stale held mark exceeds limit: {d} {t}; resolve delisting/missing data')
        for t, price in today.dropna().items():
            last_mark[t], mark_idx[t] = float(price), i

        if d in by_exec:
            for order in pending.values():
                order['status'] = 'CANCELLED_REPLACED'
            pending = {}
            targets = prepared.pop(d)
            for t in sorted(set(targets) | set(positions)):
                delta = targets.get(t, 0)-positions.get(t, 0)
                if not delta:
                    continue
                order_seq += 1
                order = dict(order_id=order_seq, ticker=t, activation=d,
                             signal_date=by_exec[d].signal_date, requested=delta,
                             original_requested=delta,
                             remaining=delta, status='OPEN', activation_index=i)
                pending[t] = order
                orders.append(order)

        def quote(t, signed):
            q = abs(signed)
            side = 1 if signed > 0 else -1
            price = float(today[t])
            lagged = float(adv.at[d, t])
            rate = c.slippage_bps/10000
            if c.impact_y:
                v = float(sigma.at[d, t])
                participation = q*price/lagged
                if not np.isfinite(v) or participation > c.impact_domain:
                    raise ValueError('Impact estimate outside declared calibration domain')
                rate += c.impact_y*v*math.sqrt(participation)
            execution = price*(1+side*rate)
            lot, tick, tradable = rule(t, d)
            if tick:
                execution = (math.ceil(execution/tick-1e-10) if side > 0 else
                             math.floor(execution/tick+1e-10))*tick
            if execution <= 0:
                raise ValueError('Nonpositive execution price')
            notional = q*execution
            commission = max(c.minimum_commission, notional*c.broker_bps/10000)
            market = notional*c.market_bps/10000
            tax = (commission+market)*c.fee_tax_rate
            return execution, commission, market, tax

        candidates = {}
        for t, o in list(pending.items()):
            reason = None
            lot, tick, tradable = rule(t, d)
            if i-o['activation_index'] >= c.max_order_sessions:
                o['status'] = 'EXPIRED'
                pending.pop(t)
                reason = 'EXPIRED'
            elif not tradable or t in delisted:
                reason = 'NOT_TRADABLE'
            elif t not in today or pd.isna(today[t]):
                reason = 'MISSING_PRICE'
            elif pd.isna(volume.at[d, t]):
                reason = 'MISSING_VOLUME'
            elif volume.at[d, t] <= 0:
                reason = 'SOURCE_ZERO_VOLUME'
            elif pd.isna(adv.at[d, t]) or adv.at[d, t] <= 0:
                reason = 'INSUFFICIENT_LAGGED_ADV'
            else:
                # Freeze signal shares; constrain today's fills by both lagged
                # planned turnover and realized full-day volume (ex-post proxy).
                limit = min(c.participation*float(volume.at[d, t]),
                            c.participation*float(adv.at[d, t])/float(today[t]))
                qty = min(abs(o['remaining']), int(math.floor((limit+1e-8)/lot))*lot)
                if o['remaining'] < 0:
                    un = sum(x['shares'] for x in unsettled_shares if x['ticker'] == t)
                    qty = min(qty, positions.get(t, 0)-un)
                qty = int(qty//lot)*lot
                if qty == 0:
                    reason = 'BELOW_LOT_OR_VOLUME_CAP'
                else:
                    signed = qty if o['remaining'] > 0 else -qty
                    try:
                        quote(t, signed)
                        candidates[t] = signed
                    except ValueError:
                        reason = 'IMPACT_DOMAIN_OR_PRICE_REJECT'
            if reason:
                attempts.append(dict(date=d, order_id=o['order_id'], ticker=t, reason=reason,
                                     remaining=o['remaining']))

        def fill(t, signed):
            nonlocal cash, fees_today, slip_today
            o = pending[t]
            execution, comm, market, tax = quote(t, signed)
            fee = comm+market+tax
            amount = abs(signed)*execution
            due_i = i+c.settlement_sessions
            if due_i >= len(cal):
                raise ValueError('Calendar must extend beyond end to cover settlement')
            if signed > 0:
                if amount+fee > cash+1e-7:
                    raise AssertionError('Buy exceeds available settled cash')
                cash -= amount+fee
                if due_i > i:
                    unsettled_shares.append(dict(ticker=t, shares=signed, settlement_date=cal[due_i]))
            else:
                if amount < fee:
                    attempts.append(dict(date=d, order_id=o['order_id'], ticker=t,
                                         reason='SALE_BELOW_FEES', remaining=o['remaining']))
                    return
                receipt = dict(pay_date=cal[due_i], amount=amount-fee, ticker=t, reason='sale')
                if due_i == i:
                    cash += amount-fee
                else:
                    receipts.append(receipt)
            positions[t] = positions.get(t, 0)+signed
            if positions[t] < 0:
                raise AssertionError('Short position')
            if positions[t] == 0:
                positions.pop(t)
            slippage = signed*(execution-float(today[t]))
            slip_today += slippage
            fees_today += fee
            by_fee[t] = by_fee.get(t, 0.)+fee
            by_slippage[t] = by_slippage.get(t, 0.)+slippage
            o['remaining'] -= signed
            if o['remaining'] == 0:
                o['status'] = 'FILLED'
                pending.pop(t)
            else:
                o['status'] = 'PARTIAL'
            fills.append(dict(date=d, order_id=o['order_id'], ticker=t, shares=signed,
                              reference_close=float(today[t]), price=execution, notional=amount,
                              commission=comm, market_fee=market, tax=tax, total_fee=fee,
                              slippage_cost=slippage, source_volume=float(volume.at[d, t]),
                              lagged_adv=float(adv.at[d, t]), lot_size=rule(t, d)[0],
                              settlement_date=cal[due_i]))

        for t in sorted(t for t in candidates if candidates[t] < 0):
            fill(t, candidates[t])
        buys = {t: q for t, q in candidates.items() if q > 0}
        required_cash = 0.
        for t, q in buys.items():
            px, co, ma, tax = quote(t, q)
            required_cash += q*px+co+ma+tax
        scale = min(1., max(0., cash)/required_cash) if required_cash else 0.
        for t in sorted(buys):
            lot = rule(t, d)[0]
            q = int((buys[t]*scale+1e-8)//lot)*lot
            # Per-order minimum fees need not scale linearly. Binary search
            # affordable lots instead of allowing negative cash or slow loops.
            low, high = 0, q//lot
            while low < high:
                mid = (low+high+1)//2
                px, co, ma, tax = quote(t, mid*lot)
                if mid*lot*px+co+ma+tax <= cash+1e-8:
                    low = mid
                else:
                    high = mid-1
            q = low*lot
            if q:
                fill(t, q)
            else:
                attempts.append(dict(date=d, order_id=pending[t]['order_id'], ticker=t,
                                     reason='INSUFFICIENT_CASH_OR_LOT', remaining=pending[t]['remaining']))

        receivable = float(sum(x['amount'] for x in receipts))
        market_value = float(sum(q*last_mark[t] for t, q in positions.items()))
        nav = cash+receivable+market_value
        identity = prev_nav+pnl+action_income-fees_today-slip_today
        error = nav-identity
        max_error = max(max_error, abs(error))
        if cash < -1e-7 or nav < -1e-7 or abs(error) > 1e-7*max(1., nav):
            raise AssertionError(f'Cash/NAV accounting failed: {d}, error={error}')
        daily.append(dict(date=d, nav=nav, cash=cash, receivable=receivable, market_value=market_value,
                          price_pnl=pnl, action_income=action_income, fees=fees_today,
                          slippage_cost=slip_today, accounting_error=error,
                          net_return=nav/prev_nav-1, n_held=len(positions), pending_orders=len(pending)))
        contribution_sum = 0.
        for t in sorted(set(by_price) | set(by_income) | set(by_fee) | set(by_slippage)):
            amount = by_price.get(t, 0.)+by_income.get(t, 0.)-by_fee.get(t, 0.)-by_slippage.get(t, 0.)
            contribution_sum += amount
            contributions.append(dict(date=d, ticker=t, price_pnl=by_price.get(t, 0.),
                                      action_income=by_income.get(t, 0.), fees=by_fee.get(t, 0.),
                                      slippage_cost=by_slippage.get(t, 0.), contribution_amount=amount,
                                      contribution_return=amount/prev_nav))
        if abs(contribution_sum-(nav-prev_nav)) > 1e-7*max(1., nav):
            raise AssertionError('Per-stock contributions do not reconcile with NAV')
        if nav <= 1e-10:
            for order in pending.values():
                order['status'] = 'CANCELLED_BANKRUPT'
            pending = {}
            terminated_bankrupt = True
            break
        for t, q in sorted(positions.items()):
            holdings.append(dict(date=d, ticker=t, shares=q, mark=last_mark[t],
                                 weight=q*last_mark[t]/nav, missing_mark_sessions=i-mark_idx[t],
                                 unsettled_shares=sum(x['shares'] for x in unsettled_shares if x['ticker'] == t),
                                 source_zero_volume=bool(volume.at[d, t] == 0)))
        if d in by_signal:
            p = by_signal[d]
            weights = selector(d, set(positions)) if selector is not None else dict(p.weights)
            if any(not np.isfinite(w) or w < 0 for w in weights.values()) or sum(weights.values()) > 1+1e-9:
                raise ValueError('Invalid long-only target weights')
            target = {}
            for t, weight in weights.items():
                if weight == 0:
                    continue
                if t in delisted:
                    raise ValueError(f'Target includes already delisted security: {d}, {t}')
                if t not in last_mark or i-mark_idx[t] > c.max_stale_sessions:
                    raise ValueError(f'Missing/stale signal sizing mark: {d}, {t}')
                lot = rule(t, p.execution_date)[0]
                # Instrument lot effective on activation is a rule assumption;
                # changing historic rules must be independently verified.
                qty = int((nav*(1-c.reserve_fraction)*weight/last_mark[t]+1e-8)//lot)*lot
                target[t] = qty
                signals.append(dict(signal_date=d, execution_date=p.execution_date, ticker=t,
                                    target_weight=weight, signal_nav=nav, sizing_price=last_mark[t],
                                    sizing_price_date=cal[mark_idx[t]], target_shares=qty))
            prepared[p.execution_date] = target
        prev_nav = nav
    for o in pending.values():
        o['status'] = 'OPEN_AT_END'
    order_df = pd.DataFrame(orders)
    if not order_df.empty:
        order_df = order_df.drop(columns='activation_index')
    return dict(daily=pd.DataFrame(daily), holdings=pd.DataFrame(holdings),
                orders=order_df, fills=pd.DataFrame(fills), attempts=pd.DataFrame(attempts),
                signals=pd.DataFrame(signals), events=pd.DataFrame(events),
                contributions=pd.DataFrame(contributions),
                receipts_at_end=pd.DataFrame(receipts),
                unsettled_shares_at_end=pd.DataFrame(unsettled_shares),
                audit=dict(max_accounting_error=max_error, config=asdict(c),
                           corporate_action_coverage_verified=actions_verified,
                           target_mode='actual_holdings_buffer' if selector else 'frozen_target_replay',
                           execution_scope='daily_close_proxy_with_ex_post_day_volume_cap',
                           performance_scope=('raw_price_action_adjusted_scenario' if actions_verified else
                                              'PRICE_ONLY_UNVERIFIED_ACTIONS_NOT_TOTAL_RETURN'),
                           full_live_fill_simulation=False,
                           terminated_bankrupt=terminated_bankrupt))
