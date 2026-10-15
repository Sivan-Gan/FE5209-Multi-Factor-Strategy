# SG independent run: audit conclusion

## What passed mechanically

- The restored Desktop source folder contains 102 workbooks; all 102 hashes
  exactly match the files used to rebuild the research panel.
- The price panel has no duplicate ticker-date records and no observed
  non-positive adjusted closes.
- The strategy has no duplicate holdings, no negative weights, weights plus
  cash equal one, a maximum sector weight of 29.41%, and execution dates after
  signal dates.
- The trading calendar now excludes three provider placeholder dates on which
  every security had a missing close. The prior results before this correction
  must not be used.

## Corrected research result

The frozen SG FMOM configuration selected in 2016--2020 is
`J13_lam1_ra0_buf0`. In 2021--latest it produces annualized net return 15.38%,
annualized volatility 14.04%, zero-rate weekly Sharpe 1.09, and max drawdown
20.92%, under the provisional 20 bp one-way cost scenario.

It exceeds the supplied STI **price-index** cumulative return over the matched
period, but that is not a final total-return comparison because STI dividends
are absent.

## Critical out-of-sample finding

The selected FMOM strategy does **not** beat the static equal-weight
multi-factor baseline out of sample:

| Strategy | OOS annualized net return | OOS Sharpe | OOS max drawdown |
|---|---:|---:|---:|
| FMOM selected | 15.38% | 1.09 | 20.92% |
| Static equal factor | 17.72% | 1.41 | 17.39% |
| Equal-weight eligible universe | 8.76% | 0.71 | 16.72% |

Therefore the defensible current conclusion is: the SG selected-factor
composite appears stronger than the equal-weight eligible universe, but the
Factor Momentum timing overlay did not generalize better than static equal
factor weights in the frozen OOS period. Do not claim that FMOM is the source
of the SG advantage.

## Material restrictions still open

1. Accounting availability is fiscal-period-end + 183-day proxy, not verified
   SGX announcement timestamps; restated vendor history may remain.
2. The universe is susceptible to survivorship bias.
3. STI Total Return Index is required before a dividend-consistent alpha claim.
4. The 20 bp SG cost and factor-selection threshold require group ratification.
5. The liquidity screen has no absolute SGD trading-value capacity threshold.
6. Four source exports were unresolved; one mapped source (`S35.SI`) has no
   corresponding price series and is filtered from strategy scoring.
