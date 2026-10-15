"""Accounting and period-boundary tests, independent of project data."""
import unittest
import numpy as np
from attribution_engine import (wealth_before, stock_net_contributions,
                                terminal_cost_loss, drawdown_window)


class AccountingTests(unittest.TestCase):
    def test_sold_security_still_has_cost_contribution(self):
        gross = np.array([.6*.1, .4*-.05, 0.])
        costs = np.array([0., 0., .003])
        net = stock_net_contributions(gross, costs, costs.sum())
        self.assertAlmostEqual(net[-1], -.003)
        self.assertAlmostEqual(net.sum(), (1-costs.sum())*(1+gross.sum())-1)

    def test_linked_contributions_close_compounded_return(self):
        gross = np.array([[.06, -.02], [-.03, .05], [.04, .01]])
        costs = np.array([[.001, 0], [0, .002], [.001, .001]])
        net = np.vstack([stock_net_contributions(g, c, c.sum()) for g,c in zip(gross, costs)])
        returns = net.sum(axis=1)
        linked = wealth_before(returns)[:,None]*net
        self.assertAlmostEqual(linked.sum(), np.prod(1+returns)-1)
        self.assertGreater(abs(net.sum()-linked.sum()), 1e-6)

    def test_terminal_cost_gap_includes_foregone_growth(self):
        gross = np.array([.2, -.1, .3])
        tc = np.array([.01, .02, .0])
        net = (1-tc)*(1+gross)-1
        loss = terminal_cost_loss(gross, net)
        self.assertAlmostEqual(loss.sum(), np.prod(1+gross)-np.prod(1+net))
        self.assertTrue((loss >= -1e-14).all())
        self.assertGreater(abs(loss.sum()-(wealth_before(net)*tc).sum()), 1e-5)

    def test_drawdown_uses_returns_after_peak_through_trough(self):
        r = [.1, -.2, .1, .2]
        peak, trough, recovery = drawdown_window(r)
        self.assertEqual((peak,trough,recovery), (1,2,4))
        self.assertAlmostEqual(np.prod(1+np.array(r[peak:trough]))-1, -.2)

    def test_initial_nav_can_be_drawdown_peak(self):
        self.assertEqual(drawdown_window([-.1, .05, -.1, .3]), (0,3,4))

    def test_paired_wealth_gap_uses_each_strategy_wealth(self):
        d, s = np.array([.2,-.1,.3]), np.array([.1,.05,.1])
        gap = (wealth_before(d)*d).sum()-(wealth_before(s)*s).sum()
        self.assertAlmostEqual(gap, np.prod(1+d)-np.prod(1+s))
        self.assertGreater(abs(gap-(d-s).sum()), 1e-4)


if __name__ == '__main__':
    unittest.main()
