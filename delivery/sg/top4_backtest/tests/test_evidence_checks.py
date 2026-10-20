import unittest
import numpy as np
import pandas as pd
from sgbt.data_quality import corporate_action_audit
from sgbt.capacity import fixed_nav_capacity


class EvidenceChecks(unittest.TestCase):
    def test_split_adjusted_close_is_flagged_not_applied_again(self):
        b = pd.DataFrame(dict(date=['2024-01-02', '2024-01-03'], ticker=['A', 'A'],
                              close=[10., 10.], adj_close=[9., 9.]))
        a = pd.DataFrame([dict(date='2024-01-03', ticker='A', action_type='split_or_reverse_split',
                               dividend=np.nan, split_ratio=2.)])
        r = corporate_action_audit(b, a)
        self.assertIn('already_split_adjusted_like', r['events'].split_price_screen.iloc[0])
        self.assertFalse(r['summary']['raw_price_basis_verified'])
        self.assertFalse(r['summary']['payment_date_field_present'])

    def test_cash_distribution_and_unexplained_adjustment(self):
        b = pd.DataFrame(dict(date=['2024-01-02', '2024-01-03', '2024-01-04'], ticker=['A']*3,
                              close=[10., 9., 9.], adj_close=[8.1, 8.1, 9.]))
        a = pd.DataFrame([dict(date='2024-01-03', ticker='A', action_type='cash_dividend',
                               dividend=1., split_ratio=np.nan)])
        r = corporate_action_audit(b, a)
        self.assertEqual(r['summary']['distribution_consistency_screen_pass'], 1)
        self.assertEqual(r['summary']['material_steps_without_source_event'], 1)

    def test_duplicate_event_rejected(self):
        b = pd.DataFrame(dict(date=['2024-01-02'], ticker=['A'], close=[10.], adj_close=[10.]))
        a = pd.DataFrame([dict(date='2024-01-02', ticker='A', action_type='cash_dividend',
                               dividend=1., split_ratio=np.nan)]*2)
        with self.assertRaises(ValueError):
            corporate_action_audit(b, a)

    def test_blank_holiday_row_does_not_hide_previous_observed_price(self):
        b = pd.DataFrame(dict(date=['2024-01-02', '2024-01-03', '2024-01-04'], ticker=['A']*3,
                              close=[10., np.nan, 9.], adj_close=[9., np.nan, 9.]))
        a = pd.DataFrame([dict(date='2024-01-04', ticker='A', action_type='cash_dividend',
                               dividend=1., split_ratio=np.nan)])
        r = corporate_action_audit(b, a)
        self.assertEqual(r['summary']['distribution_consistency_screen_pass'], 1)
        self.assertEqual(r['summary']['events_missing_price_comparison'], 0)

    def test_fixed_nav_capacity_scaling_stress_and_no_future_adv(self):
        cal = pd.bdate_range('2024-01-02', periods=15)
        b = pd.DataFrame(dict(date=cal, ticker='A', close=10., volume=100.))
        t = pd.DataFrame([dict(signal_date=cal[3], execution_date=cal[4], ticker='A', weight=1.)])
        detail, summary = fixed_nav_capacity(b, cal, t, navs=(1000., 10000.),
            shocks=(1., .5), participation=.1, lot=10, reserve=0, adv_window=2, horizons=(1, 5))
        s = summary[summary['mode'].eq('fresh_build')].set_index(['nav', 'volume_multiplier'])
        self.assertAlmostEqual(s.loc[(1000., 1.), 'completion_upper_bound_5d'], .5)
        self.assertAlmostEqual(s.loc[(10000., 1.), 'completion_upper_bound_5d'], .05)
        self.assertEqual(s.loc[(1000., .5), 'completion_upper_bound_5d'], 0.)
        b.loc[4, 'volume'] = 1e9
        detail2, _ = fixed_nav_capacity(b, cal, t, navs=(1000.,), shocks=(1.,),
            participation=.1, lot=10, reserve=0, adv_window=2, horizons=(1,))
        self.assertEqual(detail2.iloc[0].budget_shares_1d, 10.)

    def test_capacity_end_windows_are_censored_not_completed(self):
        cal = pd.bdate_range('2024-01-02', periods=8)
        b = pd.DataFrame(dict(date=cal, ticker='A', close=10., volume=1000.))
        t = pd.DataFrame([dict(signal_date=cal[5], execution_date=cal[6], ticker='A', weight=1.)])
        d, s = fixed_nav_capacity(b, cal, t, navs=(1000.,), shocks=(1.,),
                                  lot=10, adv_window=2, horizons=(5,))
        self.assertTrue(d.filled_value_upper_bound_5d.isna().all())
        self.assertTrue(s.completion_upper_bound_5d.isna().all())


if __name__ == '__main__':
    unittest.main()
