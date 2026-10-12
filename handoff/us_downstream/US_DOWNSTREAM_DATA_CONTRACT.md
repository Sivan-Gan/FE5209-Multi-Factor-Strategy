# US Downstream Strategy & Backtest Data Contract

## 1. Purpose

This governed package supplies the formal-US market, return, benchmark, RF, FF, liquidity, identity, calendar and pre-Gate-B final-factor-return interfaces required by a later Strategy Engine and Backtest/Risk workflow. It is an export layer, not a strategy implementation.

## 2. Frozen upstream factor contract

The only active final factors are, in canonical order: `QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, and `INVESTMENT_CURRENT` (K=4). Value, Dividend, Size and `MOMENTUM_CURRENT` are excluded. Gate A remains frozen.

## 3. Dataset architecture

Large Parquet files under `data/` are local, Git-ignored derived data. Their schemas, logical hashes, source paths and redistribution status are recorded in `downstream_source_manifest.csv`. Small pre-2016 samples, contracts, QA, manifests, code and tests are tracked.

## 4. Security return semantics

Security returns use `USE_CIZ_DLYRET_DIRECTLY__DO_NOT_ADD_SEPARATE_DELRET`. CIZ `DlyRet` is compounded directly. No separate DelRet, dividend return, distribution or split adjustment is added.

## 5. Execution timing

Signals are observed at weekly `S_t` close. Execution is at the next actual trading-day close `E_t`. A formal weekly holding return is compounded over `(E_t, E_{t+1}]`; signal-close-to-signal-close is prohibited as a substitute.

## 6. Sample partitions

Source labels are preserved exactly: `INITIALIZATION` (2004–2005), `DEV_WARMUP` (2006–2010), `FACTOR_SELECTION` (2011–2015), `CONFIGURATION_AND_STRATEGY_FREEZE` (2016–2020), and `HIST_OOS_BASE` (2021–2025). Physical daily-market partitions enforce pre-Gate-B, configuration, and protected Historical-OOS boundaries.

## 7. Benchmark semantics

The primary benchmark is CRSP `INDNO=1000500`, `DlyTotRet`, the CRSP Value-Weighted Index of the S&P 500 Universe. `INDNO=1000502` remains a price-return cross-check only and never replaces it.

## 8. RF semantics

Fama-French `rf` is a released decimal return. It is never divided by 100. Weekly RF compounds the same daily `(E_t,E_{t+1}]` path used for strategy returns.

## 9. FF regression factors

`mktrf`, `smb`, `hml`, `rmw`, `cma`, `rf`, and `umd` are supplied at daily and monthly (`dateff`) frequency in decimal units. They support future external regression; no strategy regression is performed here. A weekly FF transformation is deliberately deferred to M5 because it would require a separate regression-aggregation specification.

## 10. Liquidity / ADV semantics

`DlyVol` is raw shares traded. `DlyPrcVol` is `DlyPrc × DlyVol`, a dollars-traded proxy rather than actual transaction-by-transaction dollar volume. ADV20/ADV60 use only calendar trading days through `S_t`; valid-day counts are explicit. The interface selects no liquidity threshold, participation rate, impact model, or transaction-cost value.

## 11. Historical GICS semantics

Signal-date Historical GICS is reused from the frozen final-factor universe. It is time-valid Historical GICS only; current-GICS backfill is prohibited. This supports future sector caps, concentration and attribution without recomputing a universe.

## 12. Final-factor return seed

`final_factor_returns_seed_weekly.parquet` is the only factor-return output here. It contains the frozen K=4 Top 30% minus Bottom 30%, equal-weight, executable gross L/S construction for 2006–2015 only. Any seed path reaching 2016 is explicitly unavailable. The 2011–2015 rows are reproduced against frozen prior evidence. The seed neither selects FMOM parameters nor generates FMOM.

## 13. Stage-aware access

Use `src/handoff/load_us_downstream_data.py` with an explicit stage. `GATE_B` may load pre-2016 seed data and 2016–2020 configuration data, but not 2021–2025 OOS data. `HIST_OOS` requires future valid Gate-B and Gate-C/US-Base freeze evidence and has no normal force bypass.

## 14. Transaction-cost input boundary

The one-way all-in-cost formula is governed upstream. Its value/grid is not frozen here. The package supplies returns, execution prices, caps and ADV inputs only.

## 15. What is already data-complete

The requirement and capability matrices identify all formal US market/return/calendaring/benchmark/RF/FF/liquidity/identity interfaces as data-ready through 2025-12-31, subject to documented formal-source limitations.

## 16. What remains a configuration decision

`downstream_non_data_decisions.csv` distinguishes no-longer-missing data from still-pending governance: FMOM J, risk adjustment, lambda, sector cap, buffer, missing-factor policy, transaction-cost grid, inference rules, regime definitions and Gate-C readiness.

## 17. What Gate B may access

Gate B may access the 2006–2015 seed and 2016–2020 configuration market data only after separate authorization. It may not load the protected Historical-OOS partition using the normal loader.

## 18. What Historical OOS may access

2021–2025 material is physically present but protected. It is available only after both Gate B and Gate C/US Base are frozen according to their future governance artifacts.

## 19. Prohibited uses

Do not reopen Gate A/A0; add factors; select J/lambda/risk adjustment/sector cap/buffer; calculate FMOM, composite scores, strategy holdings, trades, NAV, Sharpe, CAGR, alpha, or any 2016–2025 strategy-performance summary from this package alone.

## 20. Downstream workflow

Read the Final Factor Handoff Contract, load frozen scores/universe, load this calendar and the pre-Gate-B seed, then—only after separate authorization—construct completed-period 2016–2020 final-factor returns and conduct Gate B. Freeze Gate B, construct/freeze US Base at Gate C, and only then unlock Historical OOS.
