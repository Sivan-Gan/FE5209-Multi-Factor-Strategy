# Formal WRDS Canonical Dataset Construction Report

## 1. Executive verdict

**FORMAL CANONICAL DATASET PASS WITH WARNINGS**. There are 0 blockers and seven carried-forward non-blocking Gate 0 warnings.

## 2. Formal Gate-0 lineage / freeze point

Built only after the committed Formal Gate 0 integrated sign-off and its frozen contracts; no Gate 0 semantic was changed.

## 3. Canonical architecture

One ignored SQLite store contains seven logical canonical tables; tracked schema, QA, manifest, acceptance, warning implementation, and this report provide the audit interface.

## 4. Raw inputs

All 19 immutable WRDS raw files were rehashed before construction and after construction: PASS.

## 5. Historical universe construction

`membership_daily` contains 2,527,140 exact historical CRSP S&P 500 PERMNO-date records.

## 6. Security daily layer

`security_daily` contains 3,930,454 original-959 PERMNO-date CIZ records from 2004-01-02 through 2025-12-31; CIZ `DlyRet` is direct.

## 7. Identifier / CCM layer

Strict time-valid CCM maps are retained; 5 weekly market rows remain unmapped rather than filled.

## 8. Historical GICS layer

Only strict Historical GICS intervals are used; 604 weekly rows are explicitly unavailable for industry-dependent use, with no current-GICS fallback.

## 9. Supplemental share-class treatment

2 supplemental PERMNOs appear only in issuer denominator coverage and never enlarge historical S&P 500 investability.

## 10. Issuer market-cap construction

`issuer_market_cap_daily` has 3,891,965 available and 6,347 unavailable rows. The sum is present only when every active included common-equity class has CRSP DlyCap.

## 11. Market-index layer

`market_index_daily` has 16,605 rows for frozen index IDs 1000080, 1000500, and 1000502.

## 12. Compustat standard-record construction

`pit_fundamental_events` contains 82,820 unique standard USD annual/quarterly records; unresolved duplicate/conflict cases remain unavailable.

## 13. PIT availability implementation

Quarterly next-local-trading-day eligible events: 64,117; annual exact-Q4-RDQ eligible events: 15,763.

## 14. Numeric-unit normalization

Raw values are preserved. Only NI/NIQ, CEQ/CEQQ, OANCF/OANCFY, and CAPX/CAPXY receive documented USD-millions × 1000 companion values.

## 15. YTD / TTM data transformations

Same-fiscal-year YTD derivation is recorded on 66,299 quarterly events. TTM is capability metadata only (60,247 eligible endpoints); no TTM factor panel is built.

## 16. Weekly research calendar

`weekly_calendar` has 1,148 signals from 2004-01-02 to 2025-12-26. Each execution date is the next actual local trading day.

## 17. Weekly signal panel

`weekly_signal_panel` has 523,909 unique `(signal_date, PERMNO)` rows, with market data, identifier/GICS status, issuer cap, and as-of annual/quarterly event references.

## 18. Formal sample labels

{"CONFIGURATION_AND_STRATEGY_FREEZE": 131789, "DEV_WARMUP": 130494, "FACTOR_SELECTION": 130707, "HIST_OOS_BASE": 130919}

## 19. Missing-data / unavailable rules

Missing raw values, identifiers, GICS, Q4/RDQ timing, active issuer class caps, and unresolved conflicts remain explicit unavailable observations; no fill or proxy is used.

## 20. Leakage tests

Availability references never exceed signal date; annual datadate is never used as publication time; 2026 is excluded from formal layers.

## 21. Known Gate-0 warnings carried forward

All seven non-blocking disclosures are mapped in `canonical_gate0_warning_implementation.csv`, including CCM/GICS coverage, RDQ/Q4 timing, vintage boundary, raw IN DTHRU blanks, and issuer-class limitations.

## 22. New canonical warnings, if any

No new material contradiction with Gate 0 was identified. The supplemental exact-duplicate policy was implemented as exact-equality-only; a non-identical duplicate would fail construction.

## 23. QA summary

All 23 required canonical acceptance checks pass, with six checks labeled PASS_WITH_WARNING solely for carried-forward coverage/timing limitations.

## 24. Reproducibility / determinism

First-run logical hash: `de1bf079e6399025ebde6a81883bbad21aec9c5443dbb39f7ca2f00b5a409c6e`. SQLite artifact SHA256: `35413a5a0aa4092207a48c99039faacc5c456f83ed9ba4f4593461b2b42bcb55`. The deterministic reconstruction comparison is recorded in `canonical_determinism.csv`.

## 25. Canonical acceptance matrix

See `canonical_acceptance.csv` for each required check, blocking field, evidence, action, and note.

## 26. Exact next permitted step

Candidate Factor Construction

## 27. Exact prohibited steps before review

Forward IC, factor portfolios, FMOM, stock ranking/selection, strategy execution, costs, capacity, and backtests remain prohibited until the next authorized stage.

Candidate Factor Construction is now permitted.
