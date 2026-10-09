# Estimated Historical Market Cap Construction — DEV Acceptance Report

- **Evidence stage:** `DEV_CURRENT_UNIVERSE`
- **Decision:** PASS WITH WARNINGS
- **Canonical object:** `data/canonical/us_weekly_estimated_market_cap_dev.sqlite`
- **Scope:** DEV estimated market-cap construction and validation only. No valuation ratio, factor, IC, portfolio, FMOM, or strategy calculation is included.

## Object contract

The panel is a complete `ticker × signal_date` grid. `price_close` is exact signal-date Yahoo `close` in USD/share; `reported_shares` and `aligned_shares` are source-unit shares; `estimated_market_cap = price_close × aligned_shares` is USD at scale 1. No thousand-share multiplier, adjusted close, price fill, or staleness exclusion is applied.

Grid size is **543,743** rows = 503 securities × 1081 signal dates. Available observations: **343,649**. Every row labels source `SEC_YAHOO_ESTIMATE_DEV` and evidence stage `DEV_CURRENT_UNIVERSE`.

## Construction invariants

| Check | Violations | Status |
| --- | ---: | --- |
| complete-grid row count | 543,743 | PASS |
| duplicate ticker/date key | 0 | PASS |
| available shares filed after signal date | 0 | PASS |
| available price date differs from signal date | 0 | PASS |
| available reported shares <= 1 | 0 | PASS |
| available nonpositive/NaN market cap | 0 | PASS |
| available unresolved multi-class | 0 | PASS |
| available non-standard action | 0 | PASS |
| available missing provenance | 0 | PASS |
| unavailable missing primary reason | 0 | PASS |

## Weekly coverage

| Reference denominator | 2011–2015 mean / median / min / p05 | 2016–2020 mean / median / min / p05 | 2021–2026.09 mean / median / min / p05 |
| --- | --- | --- | --- |
| 500 SEC-mapped reference | 69.74% / 70.20% / 60.20% / 61.00% | 80.28% / 80.20% / 76.40% / 76.80% | 88.60% / 88.40% / 83.60% / 84.40% |
| 503 complete grid | 69.32% / 69.78% / 59.84% / 60.64% | 79.80% / 79.72% / 75.94% / 76.34% | 88.07% / 87.87% / 83.10% / 83.90% |

The SEC-mapped reference should agree with the signed availability audit (approximately 69.74%, 80.28%, and 88.60% mean coverage); the complete-grid denominator also retains the three non-SEC-mapped securities as explicit unavailable rows.

## Primary missingness

| Window (500 SEC-mapped reference) | Leading primary reasons |
| --- | --- |
| 2011–2015 | SHARES_NOT_PIT_AVAILABLE 13.58%; NONSTANDARD_CORPORATE_ACTION 10.13%; NO_SEC_SHARES 6.00% |
| 2016–2020 | SHARES_NOT_PIT_AVAILABLE 7.11%; NONSTANDARD_CORPORATE_ACTION 6.17%; NO_SEC_SHARES 6.00% |
| 2021–2026.09 | NO_SEC_SHARES 6.00%; NONSTANDARD_CORPORATE_ACTION 2.70%; SHARES_NOT_PIT_AVAILABLE 2.24% |

## Split and numerical diagnostics

| Representative event | Construction-level status |
| --- | --- |
| AAPL_2014_7_FOR_1 | PASS |
| AAPL_2020_4_FOR_1 | PASS |
| AMZN_2022_20_FOR_1 | PASS |
| C_2011_1_FOR_10_REVERSE | PASS |

Distribution: 343,649 available observations; min=1442, p01=1.28502e+09, median=2.26923e+10, p99=5.71113e+11, max=1.55077e+17; nonpositive/NaN count=0. No clipping or winsorization occurred.
The jump audit retains the deterministic largest 250 consecutive-week absolute log changes; the shares-update audit records 27,197 SEC-observation switches. Both are diagnostic only and no row is silently corrected.
**DEV warning / downstream blocker:** the report-only extreme-share inspection finds 274 available observations across 13 tickers with raw reported shares above 1000000000000. The largest raw value is 4.81906e+15; examples in the audit include raw SEC values approximately one-million times adjacent disclosures. These observations satisfy the signed `> 1` predicate and therefore remain in this canonical object unchanged. They require a separately authorized semantic share-scale rule before this object can support factor construction; they are neither re-scaled nor silently excluded here.

## Value-input availability recheck (availability only)

| Window | E/P input | B/P input | FCF Yield input | Value >=2/3 |
| --- | ---: | ---: | ---: | ---: |
| 2011–2015 | 62.39% | 64.81% | 47.62% | 63.61% |
| 2016–2020 | 76.11% | 77.37% | 58.41% | 77.83% |
| 2021–2026.09 | 85.31% | 86.90% | 67.08% | 86.81% |

This is a post-construction availability cross-check only. The report contains no E/P, B/P, FCF, FCF Yield, Value score, Size factor, neutralization, IC, portfolio, FMOM, or strategy value.

## Decision labels

- **DEV blocker (downstream use):** the retained raw SEC share-scale anomaly requires a separately authorized semantic validation/rejection rule before any Value, Size, or size-neutralization consumer may use this object. The blocker does not license an automatic re-scale, deletion, fallback, or threshold rule.
- **DEV warnings (construction scope):** periodic shares, staleness retained without cutoff, price-review rows retained, unresolved non-standard corporate actions, and unresolved multi-class issuers reduce or qualify DEV coverage but did not violate the signed construction invariants.
- **Formal PIT limitations:** current-S&P-500 survivorship bias, current GICS, Yahoo retrospective price/action adjustments, absent historical delisting universe, and no RDQ/formal market-cap vendor source mean this output is not Formal PIT evidence.

## Downstream interface and boundaries

Downstream code can read `estimated_market_cap` plus `market_cap_source`, `availability_flag`, and `exclusion_reason` without knowing the SEC/Yahoo implementation. A Formal replacement can preserve this interface while setting `market_cap_source = CRSP_FORMAL` under a separately approved Formal contract.

DEV warnings remain: current-S&P-500 survivorship bias, current GICS, periodic SEC shares, Yahoo retrospective price/action adjustments, unresolved non-standard corporate actions, unresolved multi-class issuers, missing historical delisting universe, and absent RDQ. This object must be called **DEV estimated market cap**, never Formal PIT market cap.
