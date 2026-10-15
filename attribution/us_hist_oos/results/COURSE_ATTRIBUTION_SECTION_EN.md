# US Performance Attribution, 2021–2025

This section attributes the existing weekly backtest delivered on the member5-risk-analysis branch (commit 7b48586e45b1c456478c8a977e96043bc3108dad). The analysis uses the frozen dynamic and static portfolios over the same 259 completed holding periods. Signals are observed at the weekly close and executed at the next trading-day close; returns cover the interval from that execution close, excluded, to the next execution close, included. No factors, strategy parameters, holdings or trades are changed.

The dynamic strategy generated a cumulative net return of 35.45%, compared with 57.23% for the static strategy. Their weekly-frequency CAGRs were 6.28% and 9.51%. The dynamic strategy therefore ended with 0.2178 less wealth per unit of initial capital. These figures reproduce the existing M5 results.

## Holdings contributions

Weekly security gross contribution is its executed target weight multiplied by its holding-period total return. Sector contributions aggregate those security contributions using historical GICS. For net contributions, we apply the existing cost-before-holding convention: c_net(i,t) = (1−TC_t)c_gross(i,t)−cost(i,t). Securities sold out of the portfolio remain in the ledger for their exit costs. Cumulative contributions are linked using pre-period net wealth and add to cumulative net return. They are expressed as percentage points of initial capital, rather than components of CAGR.

Dynamic strategy: largest positive contributors

| Historical ticker | Issuer | Net contribution, pp |
| --- | --- | --- |
| HWM | HOWMET AEROSPACE INC | +1.71 |
| WDC | WESTERN DIGITAL CORP | +1.59 |
| GE | GENERAL ELECTRIC CO | +1.42 |
| AVGO | BROADCOM INC | +1.16 |
| CTAS | CINTAS CORP | +1.03 |

Dynamic strategy: largest negative contributors

| Historical ticker | Issuer | Net contribution, pp |
| --- | --- | --- |
| FI | FISERV INC | -1.00 |
| DECK | DECKERS OUTDOOR CORP | -1.00 |
| PAYC | PAYCOM SOFTWARE INC | -0.94 |
| UAL | UNITED AIRLINES HOLDINGS INC | -0.79 |
| TPL | TEXAS PACIFIC LAND CORP | -0.75 |

The largest sector shortfalls relative to Static were Consumer Discretionary, Industrials and Financials. Information Technology provided a positive relative contribution. These are holdings-based wealth differences between two strategy portfolios. Benchmark constituent and sector weights were not supplied, so no Brinson allocation/selection decomposition relative to the S&P 500 is claimed.

## Factor-momentum increment and costs

The annualized arithmetic mean of the paired Dynamic-minus-Static weekly net return was -2.87 percentage points, decomposing exactly into a gross-return increment of -2.43 points and an incremental transaction-cost effect of -0.44 points. The frozen one-way cost was 10 basis points. The HAC(4) p-value was 0.1249, with an annualized 95% interval of [-6.54, +0.80] points. The negative point estimate is not statistically different from zero at the 5% level under that test. This arithmetic mean increment is a different statistic from the difference in CAGRs.

The exact terminal-wealth bridge was -19.12 initial-capital points from the gross wealth paths and -2.66 points from additional cost-related terminal wealth loss, giving -21.78 points in total. The terminal cost effect includes the subsequent growth foregone on fees and therefore differs from the sum of fee cashflows.

## Drawdown and factor diagnostics

The dynamic maximum drawdown was 21.17%, from 27 December 2021 to 30 October 2023. Security and sector contributions over that exact peak-to-trough window are rebased to peak wealth and sum to the signed drawdown. The full-period and drawdown contributor rankings answer different questions.

The factor-mimicking timing diagnostic uses 256 complete-case weeks and yields an annualized arithmetic increment of -2.28 points. Momentum and Low Risk contribute negatively in this diagnostic, while Quality contributes slightly positively. This mimicking-portfolio diagnostic is separate from the final long-only holdings decomposition. Actual holdings-weighted Quality and Low Risk scores are lower in Dynamic than in Static, while Investment exposure is higher. These exposures provide a description of the transmission through stock ranking, buffer rules and sector constraints; they do not establish a unique causal attribution to each input factor.

The FF6 regression explanation reproduces the M5 execution-aligned aggregation and HAC(4) inference. RF, alpha, beta-times-factor terms and the residual add back to each weekly net return. The model factors and their explanatory components must not be added to holdings contributions.

## Accounting checks and limitations

All critical numerical checks pass, with maximum reported residual 9.714e-15. Input payloads are verified against Git LFS hashes and remain unchanged. The existing strategy uses a zero return for four unpriced held-security weeks in each portfolio; the affected observations are preserved and disclosed, rather than treated as validated zero realized returns. One inherited exit-cost observation lacks a verifiable same-signal-date sector and is retained as UNKNOWN. Annual tables group by signal year. Final team approval of the weekly-versus-daily NAV reporting convention and the other open upstream validation items remains necessary before presenting all formal gates as closed.
