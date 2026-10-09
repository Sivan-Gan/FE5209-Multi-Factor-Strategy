# DEV Price-Factor Engineering Contract

## Status

- Evidence stage: `DEV_CURRENT_UNIVERSE`.
- Implemented: canonical US market inputs, weekly signals, `MOM12-1`, `MOM6-1`, 252-day realised volatility, and date-local preprocessing.
- Excluded: SEC fundamentals, IVOL, neutralization, Forward IC, portfolios, FMOM, execution, strategy returns, and backtests.

This is preliminary factor-input engineering, not factor-effectiveness evidence or formal Gate A evidence. The frozen manual is unchanged and authoritative.

## Lineage and derived objects

```text
immutable Yahoo rows + current security master
-> canonical market data + weekly calendar
-> raw price metrics -> winsorized -> z-score -> direction-normalized score
```

Run `PYTHONDONTWRITEBYTECODE=1 python -m src.data.run_price_factor_pipeline --root .` to reproduce all derived objects. Every object carries `DEV_CURRENT_UNIVERSE`.

| Object | Path | Grain |
| --- | --- | --- |
| Canonical master | `data/dev_legacy/canonical/us_security_master_dev.csv` | Current security / canonical ticker |
| Canonical daily data | `data/dev_legacy/canonical/us_daily_market_dev.csv.gz` | Canonical `(ticker, date)` |
| Weekly calendar | `data/dev_legacy/canonical/us_weekly_signal_calendar_dev.csv` | US ISO natural week |
| Reproducible preprocessing staging data | `data/dev_legacy/interim/us_price_factor_preprocess_dev.sqlite` | Stock × signal date × metric |
| Raw metrics | `outputs/factor_research/dev_legacy/price_factors/us_price_metrics_raw_dev.csv.gz` | Stock × signal date × metric |
| Final input scores | `outputs/factor_research/dev_legacy/price_factors/us_price_factor_scores_dev.csv.gz` | Stock × signal date × metric |

The canonical master is the sole downstream identity source. It retains both canonical `ticker` and vendor `yahoo_ticker`, including `BF.B → BF-B` and `BRK.B → BRK-B`. SPY remains in daily data only as a benchmark/calendar record with `is_stock_cross_section=false`; it is never a stock-factor row.

## Data, eligibility, and signal semantics

Canonical daily data retains source OHLC, `adj_close`, volume, dividends, splits, source return fields, quality status/issues, and eligibility provenance. It does not alter corporate-action records, forward-fill, delete zero-volume or review securities, or correct HUBB's delivered OHLC exception.

`market_trading_day_index` is the zero-based index in the delivered SPY market calendar. The base-entry fields use data through the row only:

- `has_24m_price_history`: at least 24 calendar months since first delivered price date.
- `has_80pct_trading_coverage`: at least 48 positive-volume dates over the last 60 expected SPY market dates; absent issuer rows count as not traded.
- `base_price_eligibility`: both conditions, with reason code otherwise.

This base flag is separate from metric availability. A security lacking one metric is retained for its other metric rows.

The weekly calendar uses the last actual SPY trading date in each ISO Monday–Sunday natural week, never assuming Friday. It records previous and next delivered trading dates. The final week ends 2026-09-18 and has no later source date, so `next_trading_date` is deliberately blank rather than inferred.

## Raw metric contract

All raw metrics use canonical `adj_close`. At signal index `t`:

| Metric | Formula | Availability | Direction |
| --- | --- | --- | --- |
| `MOM12_1` | `P[t-21] / P[t-252] - 1` | All market dates `t-252...t` exist with positive adjusted closes. | `+1` |
| `MOM6_1` | `P[t-21] / P[t-126] - 1` | All market dates `t-126...t` exist with positive adjusted closes. | `+1` |
| `VOL252` | Std of 252 adjacent adjusted-price returns ending at `t`, × `sqrt(252)` | At least 200 valid returns; raw vol must be positive; zero return is valid. | `-1` |

No metric reads price data after its signal date. Missing adjacent prices create missing returns rather than forward-filled zero returns. Raw output retains window indexes/dates, valid/expected counts, zero-return count, and both metric and base-entry reason codes.

The manual states `Std` but does not set a degrees-of-freedom convention. This DEV implementation uses sample standard deviation (`ddof=1`) for `VOL252`; it is an explicit technical convention requiring confirmation before formal evidence freeze, not a silent economic-parameter choice.

## Preprocessing and non-goals

Preprocessing occurs only within `US × signal_date × metric`: frozen 1%/99% winsorization, z-score, then direction normalization. The frozen minimum cross-section is 50; smaller eligible cross-sections retain raw inputs but have missing preprocessing fields and `insufficient_cross_section_lt_50`.

For reproducibility, quantiles use deterministic linear interpolation and the z-score uses population cross-sectional standard deviation. These technical conventions are disclosed, not optimized. Missing values never become zero. Momentum keeps its sign; `VOL252` reverses after z-scoring for Low Risk.

No Momentum or Low Risk neutralization is produced because the frozen framework marks it `Test`; current GICS is also not historical PIT GICS. IVOL is deferred: SPY is available, but the manual does not uniquely freeze market proxy, return convention, regression window, or regression minimum observations. No IVOL proxy or regression has been selected; `ivol_status_dev.md` records the issue.

## Audit and Formal PIT boundary

The output directory provides weekly coverage, factor availability, missing-history/eligibility, preprocessing, and anomaly carry-forward reports. The latter tracks all 29 source `review` securities, signal-date zero-volume observations, and invalid OHLC records with `retained_with_provenance_no_automatic_deletion` treatment.

Current S&P 500 survivorship bias, current non-PIT GICS, Yahoo adjustment limitations, missing RDQ timing, and missing historical member/delisting data remain. This layer cannot be called survivorship-bias-free, historical PIT GICS, final OOS, or formal Gate A evidence. After historical constituents/security history, historical GICS, and approved CRSP/Compustat/equivalent inputs arrive, the same pipeline must rerun as `FORMAL_PIT_UNIVERSE`.
