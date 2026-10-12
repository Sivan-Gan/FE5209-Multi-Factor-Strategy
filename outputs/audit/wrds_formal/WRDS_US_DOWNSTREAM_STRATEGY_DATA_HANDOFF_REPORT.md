# WRDS US Downstream Strategy Data Handoff Report

## 1. Executive verdict

**US DOWNSTREAM STRATEGY DATA HANDOFF — PASS WITH WARNINGS.** Blockers: 0. Warnings: 8.

## 2. Purpose

Prepare governed downstream data interfaces, not a strategy, parameter-selection, or performance-research result.

## 3. Why Final Factor Handoff alone was insufficient

It supplied frozen scores/availability/universe but not executable security-return paths, calendar, benchmark/RF/FF, liquidity/capacity inputs, or a pre-Gate-B seed-return history.

## 4. Frozen factor contract

K=4: `QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, `INVESTMENT_CURRENT`. No Value, Dividend, Size, or `MOMENTUM_CURRENT` active factor is introduced.

## 5. Downstream data architecture

Large derived Parquet are local/Git-ignored. Contracts, manifests, dictionary, samples, QA, code and tests are tracked. See `downstream_source_manifest.csv`.

## 6. Security daily market data

`security_daily_market.parquet` has 3,930,454 date-PERMNO rows. It preserves canonical CIZ market/return/cap fields and adds only raw `DlyVol`/`DlyPrcVol` provenance.

## 7. Execution calendar

`weekly_execution_calendar.parquet` has 1,148 signal dates and preserves S_t, next-trading-day E_t, and E_t+1 fields.

## 8. Weekly execution returns

`security_weekly_execution_returns.parquet` has 523,909 signal-PERMNO rows. It compounds direct CIZ `DlyRet` over `(E_t,E_t+1]` only.

## 9. Liquidity / ADV

`execution_liquidity_weekly.parquet` has 523,909 rows. ADV20/ADV60 are backward-looking `DlyPrcVol` proxies; no impact model or liquidity threshold is set.

## 10. Benchmark

Primary daily/weekly benchmark is CRSP INDNO 1000500 `DlyTotRet`. INDNO 1000502 remains only a price-return diagnostic.

## 11. Risk-free rate

Daily and execution-aligned weekly Fama-French RF are exported in decimal units; no divide-by-100 transformation occurs.

## 12. FF regression factors

Daily and monthly decimal FF factors are exported. No strategy regression or weekly FF aggregation specification is calculated.

## 13. Final-factor return seed

`final_factor_returns_seed_weekly.parquet` has 2,088 K=4 factor-signal rows only within 2006–2015. The rowwise 2011–2015 reproduction QA is in `final_factor_return_seed_qa.csv`; it is not a performance ranking.

## 14. Universe / sector support

The frozen Final Factor Handoff universe supplies signal-date Historical GICS, final-factor availability and issuer-cap support. No current-GICS backfill is used.

## 15. Return semantics

`USE_CIZ_DLYRET_DIRECTLY__DO_NOT_ADD_SEPARATE_DELRET`; terminal confirmed-delisting CIZ return is followed by cash. No manual corporate-action add-on is applied.

## 16. Delisting / corporate-action semantics

Path status and delisting flags are explicit. The construction does not modify CRSP corporate-action return semantics.

## 17. Join integrity

Score/orphan=0; return/orphan=0; universe/orphan=0; liquidity/orphan=0; calendar conflicts=0.

## 18. Gate B data readiness

Pre-2016 seed and 2016–2020 configuration market data are physically available under a stage-aware access policy. Gate B itself has not started.

## 19. US Base data readiness

The data interfaces can support a future US Base build, but no composite score, holdings, cost grid, or US Base is constructed or frozen.

## 20. Historical OOS data readiness

2021–2025 market data are materialized as a protected physical partition. No strategy outcome is calculated or summarized; loader access is blocked pending future Gate B/C freeze artifacts.

## 21. Backtest & Risk capability

Calendar, execution returns, caps, liquidity, benchmark, RF, FF and historical-sector interfaces are data-ready. The capability matrix records still-pending parameters.

## 22. Remaining non-data decisions

See `downstream_non_data_decisions.csv`, including FMOM J, risk adjustment, lambda, sector cap, buffer, missing-factor policy, cost grid, inference and regime rules.

## 23. Stage-aware OOS governance

`GATE_B` loader access excludes Historical OOS. `HIST_OOS` requires future valid Gate-B and Gate-C acceptance artifacts; no force override exists in normal usage.

## 24. Redistribution / GitHub policy

Full derived Parquet are `LOCAL_LARGE_DATA` and `DATA_DISTRIBUTION_REVIEW_REQUIRED`. They are not force-added to Git.

## 25. Determinism

All exported local datasets have two-build logical equality status: True.

## 26. Upstream immutability

Raw, SSOT, canonical, factor stores, validation store and final handoff immutability status: True.

## 27. Tests

Focused downstream tests and the native unittest suite are required verification steps; their execution is recorded at the authorized handoff run, not used as research evidence.

## 28. Warnings / blockers

Non-blocking governance/distribution warnings: `TRANSACTION_COST_GRID_NOT_YET_FROZEN`, `FORMAL_REGIME_RULE_NOT_YET_FROZEN`, and `DATA_DISTRIBUTION_REVIEW_REQUIRED`.

## 29. Exact next permitted step

After separate authorization, conduct Gate B Configuration Selection using 2016–2020 only through the stage-aware loader. Do not start it automatically from this handoff.
