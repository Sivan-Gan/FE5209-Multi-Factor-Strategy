# Final Factor Handoff Package

## 1. What this package is

This is the governed M2 / M3 -> M4 interface for the frozen US final-factor signals. It exports existing signal data only; it is not a strategy or performance package.

## 2. What has already been frozen

Gate A0 representatives and Gate A Final Factor Set are frozen. The authoritative decision is `outputs/audit/wrds_formal/gate_a_final_factor_set.csv`.

## 3. Final K=4 factor set

`QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, `INVESTMENT_CURRENT`; K=4.

## 4. Exact factor definitions

Read `factor_formula_dictionary.csv` and `final_factor_registry.csv`. Do not redefine any formula, direction, neutralization, PIT rule, or availability rule.

## 5. Canonical factor order

Quality, Momentum, Low Risk, Investment.

## 6. What files to load first

Load `final_factor_registry.csv`, then the three files under `data/`: scores, availability, and universe.

## 7. factor_scores_weekly schema

The natural key is `(signal_date, PERMNO)`. Four active score fields are `quality_score`, `momentum_score`, `lowrisk_score`, and `investment_score`.

## 8. availability schema

`factor_availability_weekly.parquet` records the four availability flags, count, all-four flag, and frozen unavailable reasons.

## 9. universe schema

`universe_weekly.parquet` supplies existing identity, historical-membership, GICS, issuer-market-cap, execution-date, and availability support fields.

## 10. signal timing

Signals are weekly cutoff-date objects. The frozen execution convention is signal `S_t` then next-trading-day execution `E_t`; this package does not calculate a return.

## 11. PIT / no-lookahead summary

All fields come from frozen formal stores. No WRDS query, current-GICS backfill, future return, or post-signal outcome is added.

## 12. dropped-factor treatment

Value, Dividend, and Size remain in historical/audit evidence but are not active Base Strategy score columns.

## 13. what NOT to recompute

Do not reconstruct factors, rerun Gate A0/Gate A, replace Momentum, or alter frozen transformations.

## 14. what is allowed next

The receiving member may load and join the frozen signals and universe, then prepare the governed Strategy Engine handoff process when separately authorized.

## 15. what is NOT yet allowed

No factor returns, FMOM, factor weights, stock composite score, holdings, portfolio return, or strategy backtest.

## 16. Gate B / OOS firewall

Gate B is not started. 2016–2020 is the future configuration period; 2021–2025 is protected Formal Historical OOS. Do not use 2021–2025 to tune Gate B.

## 17. large-data files not stored in Git

The three Parquet files are `LOCAL_LARGE_DATA` and ignored by Git. Generate them with this exporter from the local frozen stores; do not commit them. CSV/Markdown contracts, manifests, and the deterministic small sample are tracked.

## 18. how to verify hashes

Check `source_manifest.csv`, `factor_score_manifest.csv`, `handoff_determinism.csv`, reproduction QA, and the upstream acceptance artifacts.

## 19. code entry points

Use `src/handoff/prepare_final_factor_handoff.py` for export; see `code_manifest.csv` for upstream lineage.

## 20. reproduction lineage

Follow `reproduction_manifest.csv` in order. The handoff starts only after Gate A is frozen.

## 21. known warnings / limitations

The inherited Gate A warning is no frozen transaction-cost grid. Formal PIT limitations remain those disclosed in Gate 0. This handoff contains no performance conclusion.

## 22. receiving-member checklist

1. Read `FINAL_FACTOR_HANDOFF_CONTRACT.md` and the final registry. 2. Load scores and availability by `(signal_date, PERMNO)`. 3. Confirm `all_four_available` before any later authorized strategy step.

**DO NOT replace `MOMENTUM_12_1` with `MOMENTUM_CURRENT`.**

**DO NOT add Value / Dividend / Size to the Base Strategy.**

**DO NOT use 2021–2025 to tune Gate B. DO NOT reopen Gate A.**
