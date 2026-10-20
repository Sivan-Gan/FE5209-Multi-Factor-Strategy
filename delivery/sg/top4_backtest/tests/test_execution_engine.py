import unittest
from dataclasses import replace
import numpy as np
import pandas as pd
from sgbt.engine import ExecutionConfig, Rebalance, simulate
from sgbt.metrics import matched_benchmark


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.cal = pd.bdate_range('2024-01-02', periods=18)
        self.bars = pd.DataFrame([dict(date=d, ticker=t, close=10., volume=10000.)
                                  for d in self.cal for t in ['A', 'B']])
        self.c = ExecutionConfig(initial_cash=1000, default_lot=10, reserve_fraction=0,
                                 participation=1, adv_window=2, broker_bps=0,
                                 minimum_commission=0, market_bps=0, slippage_bps=0,
                                 settlement_sessions=0)
        self.plan = [Rebalance(self.cal[3], self.cal[4], {'A': 1.})]

    def run_case(self, bars=None, plans=None, config=None, **kwargs):
        return simulate(self.bars if bars is None else bars, self.cal,
                        self.plan if plans is None else plans, config or self.c,
                        end=kwargs.pop('end', self.cal[10]), actions_verified=True, **kwargs)

    def test_flat_price_conserves_initial_cash(self):
        r = self.run_case()
        self.assertEqual(r['fills'].shares.tolist(), [100])
        np.testing.assert_allclose(r['daily'].nav, 1000)
        self.assertLess(r['audit']['max_accounting_error'], 1e-8)

    def test_zero_volume_delays_fill_and_preserves_cash(self):
        b = self.bars.copy()
        b.loc[(b.date == self.cal[4]) & (b.ticker == 'A'), 'volume'] = 0
        r = self.run_case(bars=b)
        self.assertEqual(r['fills'].date.min(), self.cal[5])
        self.assertIn('SOURCE_ZERO_VOLUME', r['attempts'].reason.tolist())
        np.testing.assert_allclose(r['daily'].nav, 1000)

    def test_gap_uses_signal_mark_not_future_fill_close(self):
        b = self.bars.copy()
        b.loc[(b.date >= self.cal[4]) & (b.ticker == 'A'), 'close'] = 20
        r = self.run_case(bars=b)
        self.assertEqual(r['signals'].target_shares.iloc[0], 100)
        self.assertEqual(r['fills'].shares.sum(), 50)
        self.assertTrue((r['daily'].cash >= 0).all())

    def test_minimum_fee_and_lot_do_not_overdraw(self):
        r = self.run_case(config=replace(self.c, minimum_commission=10))
        self.assertEqual(r['fills'].shares.tolist(), [90])
        self.assertEqual(r['fills'].total_fee.sum(), 10)
        self.assertEqual(r['daily'].nav.iloc[-1], 990)

    def test_cash_allocation_scales_buys_and_is_deterministic(self):
        plans = [Rebalance(self.cal[3], self.cal[4], {'A': .5, 'B': .5})]
        r = self.run_case(plans=plans, config=replace(self.c, minimum_commission=10))
        self.assertEqual(r['fills'].shares.tolist(), [40, 40])
        self.assertEqual(r['daily'].cash.iloc[-1], 180)

    def test_sale_settlement_blocks_same_day_reuse(self):
        plans = self.plan+[Rebalance(self.cal[6], self.cal[7], {'B': 1.})]
        r = self.run_case(plans=plans, config=replace(self.c, settlement_sessions=2))
        f = r['fills']
        self.assertEqual(f[(f.ticker == 'A') & (f.shares < 0)].date.iloc[0], self.cal[7])
        self.assertEqual(f[f.ticker == 'B'].date.iloc[0], self.cal[9])
        day = r['daily'].set_index('date').loc[self.cal[7]]
        self.assertEqual(day.receivable, 1000)
        self.assertEqual(day.cash, 0)

    def test_split_keeps_nav_constant(self):
        b = self.bars.copy()
        b.loc[(b.date >= self.cal[6]) & (b.ticker == 'A'), 'close'] = 5
        a = pd.DataFrame([dict(event_id='split', ticker='A', ex_date=self.cal[6],
                               kind='split', value=2., pay_date=pd.NaT)])
        r = self.run_case(bars=b, actions=a)
        self.assertEqual(r['holdings'].shares.iloc[-1], 200)
        np.testing.assert_allclose(r['daily'].nav, 1000)

    def test_dividend_entitlement_survives_sale_before_payment(self):
        b = self.bars.copy()
        b.loc[(b.date >= self.cal[6]) & (b.ticker == 'A'), 'close'] = 9
        a = pd.DataFrame([dict(event_id='div', ticker='A', ex_date=self.cal[6],
                               kind='dividend', value=1., pay_date=self.cal[9])])
        plans = self.plan+[Rebalance(self.cal[6], self.cal[7], {})]
        r = self.run_case(bars=b, plans=plans, actions=a)
        self.assertEqual(r['daily'].set_index('date').loc[self.cal[6], 'receivable'], 100)
        self.assertEqual(r['daily'].cash.iloc[-1], 1000)
        np.testing.assert_allclose(r['daily'].nav, 1000)

    def test_bonus_value_is_recognized_before_new_shares_can_be_sold(self):
        b = self.bars.copy()
        b.loc[(b.date >= self.cal[6]) & b.ticker.eq('A'), 'close'] = 10/1.1
        a = pd.DataFrame([dict(event_id='bonus', ticker='A', ex_date=self.cal[6],
            kind='split', value=1.1, pay_date=pd.NaT, share_trade_date=self.cal[9])])
        plans = self.plan+[Rebalance(self.cal[6], self.cal[7], {})]
        r = self.run_case(bars=b, plans=plans, actions=a)
        sells = r['fills'][r['fills'].shares.lt(0)]
        self.assertEqual(sells.shares.tolist(), [-100, -10])
        self.assertEqual(sells.date.tolist(), [self.cal[7], self.cal[9]])
        np.testing.assert_allclose(r['daily'].nav, 1000)
        self.assertAlmostEqual(r['daily'].cash.iloc[-1], 1000)

    def test_delayed_reverse_consolidation_rejected(self):
        a = pd.DataFrame([dict(event_id='reverse', ticker='A', ex_date=self.cal[6],
            kind='split', value=.5, pay_date=pd.NaT, share_trade_date=self.cal[9])])
        with self.assertRaisesRegex(ValueError, 'Delayed reverse'):
            self.run_case(actions=a)

    def test_zero_recovery_delist_is_not_free_price_fill(self):
        plans = [Rebalance(self.cal[3], self.cal[4], {'A': .5})]
        a = pd.DataFrame([dict(event_id='delist', ticker='A', ex_date=self.cal[6],
                               kind='delist_cash', value=0., pay_date=self.cal[6])])
        r = self.run_case(plans=plans, actions=a)
        self.assertEqual(r['daily'].nav.iloc[-1], 500)
        self.assertEqual(r['daily'].n_held.iloc[-1], 0)

    def test_complete_delisting_loss_terminates_with_negative_one_return(self):
        a = pd.DataFrame([dict(event_id='delist', ticker='A', ex_date=self.cal[6],
                               kind='delist_cash', value=0., pay_date=self.cal[6])])
        r = self.run_case(actions=a)
        self.assertEqual(r['daily'].nav.iloc[-1], 0)
        self.assertEqual(r['daily'].net_return.iloc[-1], -1)
        self.assertTrue(r['audit']['terminated_bankrupt'])

    def test_delisted_security_cannot_be_selected_again(self):
        plans = [Rebalance(self.cal[3], self.cal[4], {'A': .5}),
                 Rebalance(self.cal[7], self.cal[8], {'A': .5})]
        a = pd.DataFrame([dict(event_id='delist', ticker='A', ex_date=self.cal[6],
                               kind='delist_cash', value=1., pay_date=self.cal[6])])
        with self.assertRaisesRegex(ValueError, 'already delisted'):
            self.run_case(plans=plans, actions=a)

    def test_missing_mark_stops_instead_of_dropping_position(self):
        b = self.bars[~((self.bars.date >= self.cal[5]) & (self.bars.ticker == 'A'))]
        with self.assertRaisesRegex(ValueError, 'Stale held mark'):
            self.run_case(bars=b, config=replace(self.c, max_stale_sessions=1))

    def test_unfinished_order_expires_and_new_plan_cancels_old(self):
        b = self.bars.copy()
        b.loc[b.ticker == 'A', 'volume'] = 0
        r = self.run_case(bars=b, config=replace(self.c, max_order_sessions=2))
        self.assertEqual(r['orders'].status.tolist(), ['EXPIRED'])
        plans = self.plan+[Rebalance(self.cal[6], self.cal[7], {'B': 1.})]
        r = self.run_case(bars=b, plans=plans)
        self.assertEqual(r['orders'].status.iloc[0], 'CANCELLED_REPLACED')
        self.assertEqual(r['fills'].ticker.tolist(), ['B'])

    def test_realized_volume_and_lagged_adv_both_limit_fills(self):
        b = self.bars.copy()
        b.loc[(b.date == self.cal[4]) & (b.ticker == 'A'), 'volume'] = 250
        r = self.run_case(bars=b, config=replace(self.c, participation=.1))
        self.assertEqual(r['fills'].iloc[0].shares, 20)
        f = r['fills']
        self.assertTrue((f.shares.abs() <= .1*f.source_volume+1e-8).all())
        self.assertTrue((f.shares.abs()*f.reference_close <= .1*f.lagged_adv+1e-8).all())

    def test_strict_mode_blocks_unverified_actions(self):
        with self.assertRaisesRegex(ValueError, 'verified corporate-action'):
            simulate(self.bars, self.cal, self.plan, self.c)

    def test_duplicate_and_invalid_calendar_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate price'):
            self.run_case(bars=pd.concat([self.bars, self.bars.iloc[:1]]))
        with self.assertRaisesRegex(ValueError, 'valid sessions'):
            self.run_case(plans=[Rebalance(self.cal[3], pd.Timestamp('2024-01-07'), {'A': 1})])

    def test_tick_and_effective_lot_rules(self):
        rules = pd.DataFrame([dict(ticker='A', effective_date=self.cal[0], lot_size=20,
                                   tick_size=.05, tradable=True)])
        r = self.run_case(config=replace(self.c, slippage_bps=1), rules=rules)
        self.assertEqual(r['fills'].shares.iloc[0] % 20, 0)
        self.assertAlmostEqual(r['fills'].price.iloc[0], 10.05)

    def test_explicit_halt_and_missing_volume_block_fills(self):
        rules = pd.DataFrame([dict(ticker='A', effective_date=self.cal[0], lot_size=10,
                                   tick_size=0., tradable=False)])
        r = self.run_case(rules=rules)
        self.assertTrue(r['fills'].empty)
        self.assertIn('NOT_TRADABLE', r['attempts'].reason.tolist())
        b = self.bars.copy()
        b.loc[(b.date == self.cal[4]) & (b.ticker == 'A'), 'volume'] = np.nan
        r = self.run_case(bars=b)
        self.assertIn('MISSING_VOLUME', r['attempts'].reason.tolist())

    def test_future_bars_do_not_change_past_signals_or_fills(self):
        b = self.bars.copy()
        b.loc[b.date >= self.cal[8], ['close', 'volume']] = [99., 100.]
        a, z = self.run_case(), self.run_case(bars=b)
        pd.testing.assert_frame_equal(a['signals'], z['signals'])
        pd.testing.assert_frame_equal(a['fills'][a['fills'].date < self.cal[8]],
                                      z['fills'][z['fills'].date < self.cal[8]])

    def test_actual_holdings_selector_receives_real_holdings(self):
        seen = []
        plans = self.plan+[Rebalance(self.cal[6], self.cal[7], {'B': 1.})]
        def selector(d, held):
            seen.append((d, held))
            return {'A': 1.}
        self.run_case(plans=plans, selector=selector)
        self.assertEqual(seen[0][1], set())
        self.assertEqual(seen[1][1], {'A'})

    def test_sell_slippage_and_fees_reconcile(self):
        plans = self.plan+[Rebalance(self.cal[6], self.cal[7], {})]
        r = self.run_case(plans=plans, config=replace(self.c, slippage_bps=20,
                                                    minimum_commission=2, fee_tax_rate=.09))
        self.assertGreater(r['fills'].slippage_cost.sum(), 0)
        self.assertAlmostEqual(1000-r['daily'].nav.iloc[-1],
                               r['fills'].total_fee.sum()+r['fills'].slippage_cost.sum())
        a = r['contributions'].groupby('date').contribution_return.sum()
        d = r['daily'].set_index('date').net_return.reindex(a.index)
        np.testing.assert_allclose(a, d, atol=1e-10)

    def test_split_between_signal_and_activation_adjusts_prepared_shares(self):
        b = self.bars.copy()
        b.loc[(b.date >= self.cal[4]) & (b.ticker == 'A'), 'close'] = 5
        a = pd.DataFrame([dict(event_id='split', ticker='A', ex_date=self.cal[4],
                               kind='split', value=2., pay_date=pd.NaT)])
        r = self.run_case(bars=b, actions=a)
        self.assertEqual(r['fills'].shares.tolist(), [200])
        np.testing.assert_allclose(r['daily'].nav, 1000)

    def test_unsettled_bought_shares_are_not_sold_early(self):
        plans = self.plan+[Rebalance(self.cal[4], self.cal[5], {})]
        r = self.run_case(plans=plans, config=replace(self.c, settlement_sessions=2))
        self.assertEqual(r['fills'][r['fills'].shares < 0].date.iloc[0], self.cal[6])

    def test_fractional_split_and_unsupported_rights_fail_closed(self):
        plans = [Rebalance(self.cal[3], self.cal[4], {'A': .3})]
        a = pd.DataFrame([dict(event_id='split', ticker='A', ex_date=self.cal[6],
                               kind='split', value=1.01, pay_date=pd.NaT)])
        with self.assertRaisesRegex(ValueError, 'Fractional split'):
            self.run_case(plans=plans, actions=a)
        a['kind'] = 'rights'
        with self.assertRaisesRegex(ValueError, 'Unsupported action'):
            self.run_case(actions=a)



if __name__ == "__main__":
    unittest.main()
