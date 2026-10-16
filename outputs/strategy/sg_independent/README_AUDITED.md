# Singapore independent research: corrected run

## What changed

This run uses the authorised `.TRISTI` STI Total Return export and fixes the
transaction-cost convention. A provisional 20 bp **per-side** cost is charged
as `20 bp × GTR`, where `GTR = sum(abs(target weight - drifted pre-trade weight))`.
The separately reported one-way turnover is `GTR / 2`.

## Research sequence

| Stage | Dates | Result |
| --- | --- | --- |
| Single-factor screen | 2011--2015 | Quality, Momentum, LowRisk and Investment meet the pre-declared operational rule. |
| Configuration selection | 2016--2020 | `J13_lam0.5_ra1_buf1` has the highest weekly zero-rate Sharpe in the declared grid. |
| Frozen evaluation | 2021-01-08--2026-09-11 | No OOS parameter retuning. |

## Frozen OOS result at 20 bp per side

| Series | Annualized return | Weekly Sharpe | Maximum drawdown |
| --- | ---: | ---: | ---: |
| Selected FMOM | 17.20% | 1.32 | 15.83% |
| Static equal-factor | 16.69% | 1.34 | 17.70% |
| STI Total Return (`.TRISTI`) | 17.57% | 1.39 | 10.48% |

The selected FMOM strategy is economically close to, but slightly below, STI
Total Return over the matched window: 145.30% cumulative net return versus
149.69% for STI Total Return. Its information ratio versus STI is -0.027.
The weekly FMOM-minus-static Newey-West t-statistic is 0.194, so the small
FMOM/static difference is not statistically distinguishable from zero.

## Audit status

There are 13 mechanical PASS checks and zero mechanical FAIL checks. The
material limitations remain: an accounting availability proxy rather than
verified filing timestamps, potential restatements, survivorship bias, an
unverified `.TRISTI` gross/net tax convention, a provisional cost assumption,
and no absolute trading-value capacity filter.

Do not present this as a final alpha claim until the `.TRISTI` gross/net
dividend convention and the group's cost scenario are documented.
