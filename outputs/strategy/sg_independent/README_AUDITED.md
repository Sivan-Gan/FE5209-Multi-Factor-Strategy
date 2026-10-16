# Singapore independent research: corrected run

## Source-date correction

Two partial provider-outage dates were removed from the weekly calendar:
2026-05-01 had adjusted closes for 2 of 114 names and 2026-06-01 had one of
114. They had previously created artificial one- or two-stock portfolios. The
calendar now requires at least 80% of trailing typical adjusted-close coverage,
with a floor of 20 securities. This is a source-validity correction, not an
OOS performance filter.

## Frozen OOS result at 20 bp per side

Cost is `20 bp × GTR`, where GTR sums absolute target-versus-drifted-pretrade
weight differences. Reported one-way turnover is `GTR / 2`.

| Series | Annualized return | Weekly Sharpe | Maximum drawdown |
| --- | ---: | ---: | ---: |
| Selected FMOM | 14.97% | 1.17 | 15.83% |
| Static equal-factor | 15.17% | 1.23 | 17.70% |
| Equal-weight scoreable universe | 11.84% | 1.00 | 16.71% |
| STI Total Return (`.TRISTI`) | 17.15% | 1.36 | 10.48% |

FMOM and Static both exceed the same-universe equal-weight control, but Static
is modestly higher than FMOM after the source-date correction. The FMOM-minus-
Static Newey-West t-statistic is -0.048, so their difference is not
statistically distinguishable from zero. FMOM remains below STI Total Return:
121.81% cumulative net return versus 146.92%.

## Remaining limitations

The annual accounting availability date is fiscal-period-end plus 183 days,
not a verified filing timestamp. The current screened universe may have
survivorship bias; the `.TRISTI` gross/net tax convention is undocumented; the
20 bp per-side cost is provisional; and there is no absolute ADV capacity
filter. Do not make a final alpha claim.
