# Formal US Candidate Factor Construction Report

## Historical record

`cd0151bd1df80b1834ce4cfe04b7e967adc6cbde` recorded the earlier blocked construction. It remains preserved; this is a later re-evaluation after definition freeze `d68aaaec585a85fe00879d192f7c5a221e297b0e` and the separate cash-dividend extension.

## Revised verdict

**CANDIDATE FACTOR CONSTRUCTION — PASS WITH WARNINGS**. Blockers: 0. Warnings: 4.

## Frozen inputs

Base canonical logical hash: `de1bf079e6399025ebde6a81883bbad21aec9c5443dbb39f7ca2f00b5a409c6e`. Dividend extension logical hash: `f5e1edcf58e28a96cdc4e1cb24ec2d38bc4917a71058fbeccd5d7e1a9228d82d`.

## Construction

Derived factor database: `data/factors/us_formal/us_candidate_factors.sqlite`; rows: 523,909; natural key: `(signal_date, PERMNO)`.

## Definitions and PIT

Size is `-log` issuer market cap. Value, Quality and Investment use the Stage-A quarterly-primary / whole-annual-fallback contract, historical GICS sector branches, and no-intercept all-sector-dummy issuer-log-cap neutralization. Dividend uses ordinary CRSP ex-date events, 365 calendar days, standard split-basis normalization and signal-date security price.

## QA

See size, value, quality, investment, dividend, branch, source-precedence, neutralization, availability, look-ahead, winsorization, acceptance and determinism artifacts.

## Integrity

Raw 19-file integrity: `True`. Canonical immutable: `True`. Determinism: `PASS`.

## Prohibited work

No outcome calculation, predictive statistic, portfolio, selection, strategy, cost, capacity or Singapore analysis was run.


## Remaining warnings

1. Scores begin in 2006 after required history and PIT inputs become usable; no historical score is backfilled.
2. The 604 observations without historical GICS remain branch-dependent Value and Quality unavailable; no current-GICS fallback is used.
3. Annual fundamental fallback is retained where the required quarterly TTM or point-in-time chain is unavailable; it is the frozen precedence rule, not a coverage optimization.
4. Dividend observations remain unavailable when the complete-window or approved split-basis contract is not satisfied; no alternative dividend source is substituted.
