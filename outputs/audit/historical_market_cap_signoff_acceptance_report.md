# Historical Market Cap Sign-off / Coverage Closure Audit — DEV

- **Evidence stage:** `DEV_CURRENT_UNIVERSE`
- **Decision:** PASS WITH WARNINGS
- **Construction authorization:** `AUTHORIZED_FOR_NEXT_ISOLATED_DEV_CONSTRUCTION_TASK_ONLY` — no construction is performed by this audit.
- **Scope:** availability/reason-code evidence only. No market-cap value or panel, ratio, factor, IC, portfolio, FMOM, or strategy output exists.
- **Denominator:** 500 SEC-mapped current-universe securities (497 issuers); this remains `DEV_CURRENT_UNIVERSE`, not a survivorship-unbiased historical universe.

## Scope and controls

The frozen price, PIT, share-source, split-basis, and multi-class rules were tested as specified. This audit selects no new factor rule and does not alter any raw, canonical upstream, or frozen-manual file. Any separately requested construction must retain the name `estimated_market_cap` and source `SEC_YAHOO_ESTIMATE_DEV`; it is not a formal PIT market cap.

## Small shares (`<= 1`) evidence

There are **35 raw SEC observations** at or below one across **27 CIKs**: `<0` 0, `=0` 31, `0<value<1` 0, and `=1` 4. The previously quoted **824** is not a raw-observation count: it is the number of selected ticker-weeks affected by the four `=1` source records.

The 31 zero records are `OBVIOUS_INVALID_ZERO` and none becomes a candidate selection. Two `=1` ICE records and one CSX record have other normal share observations and are `LIKELY_XBRL_CONTEXT_OR_SCALE_ISSUE`. The remaining `=1` record belongs to FOX/FOXA and is `UNRESOLVED_UNIT_VALUE_NEEDS_CONTEXT`; it is independently multi-class unresolved. The rule is not to mutate raw observations, but to require selected primary shares to be strictly `>1`; selected `<=1` cells are unavailable, with their source context preserved in the small-share audit CSV.

## Non-standard corporate-action evidence

The 104 non-standard Yahoo action factors span **75 tickers**: 93 numerical upward factors and 11 numerical downward factors. These are numerical classes only—not asserted economic classifications. Possible interpretations remain unresolved (unusual split, share distribution, spin-off, ticker/corporate-action artifact, or vendor anomaly).

Keeping every non-standard action unresolved removes **29,081 unique ticker-weeks** from candidate availability across the full signal calendar. The loss is **10.13%**, **6.17%**, and **2.70%** in the three research windows, respectively. Individual action rows are non-additive because several actions can affect the same ticker-week. Under the Yahoo delivery-basis convention, a later action can affect an earlier reported-share observation; this is not evidence that the action occurred after that signal date.

## Staleness sensitivity (candidate availability only)

| Scenario | 2011–2015 mean | 2016–2020 mean | 2021–2026.09 mean | Removed ticker-weeks (all signal dates) |
| --- | ---: | ---: | ---: | ---: |
| NO_CUTOFF | 69.74% | 80.28% | 88.60% | 0 |
| 120_DAYS | 65.58% | 75.84% | 84.07% | 18,692 |
| 180_DAYS | 67.21% | 77.05% | 85.26% | 12,695 |
| 270_DAYS | 67.52% | 77.25% | 85.36% | 11,733 |
| 365_DAYS | 67.78% | 77.40% | 85.43% | 11,031 |

Recommendation: **`NO_CUTOFF_WITH_STALENESS_FLAG`** for this DEV hand-off. It preserves the current candidate method while making age observable. A 120-day screen removes 18,692 ticker-weeks and lowers the three means by 4.16, 4.43, and 4.53 percentage points; 180/270/365-day cases remain sensitivity evidence, not silently substituted baselines. The CSV records weekly issuer coverage (distinct CIKs) alongside ticker coverage.

## Signal-date price evidence

Missing exact `close` occurs in 45,090 weekly cells / 108 tickers; zero-volume valid bars occur in 756 / 13; review-security bars in 25,023 / 26; invalid OHLC and internal-gap missing signal dates are both zero. Baseline: exact positive plausible `close` only; retain zero-volume/review bars with `PRICE_REVIEW_FLAG`; mark invalid/missing as unavailable; never fill from another date.

## Input-level joint coverage (no ratios calculated)

| Window | MCap input | E/P input | B/P input | FCF Yield input | Value >=2/3 | Mean eligible count for Value >=2/3 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2011–2015 | 69.74% | 62.39% | 64.81% | 47.62% | 63.61% | 318.1 |
| 2016–2020 | 80.28% | 76.11% | 77.37% | 58.41% | 77.83% | 389.2 |
| 2021–2026.09 | 88.60% | 85.31% | 86.90% | 67.08% | 86.81% | 434.1 |

The companion CSV gives mean, median, minimum, p05, and weekly eligible count for every availability set. It performs no E/P, B/P, FCF, FCF Yield, Value score, or Size calculation.

## Size / neutralization and missingness diagnostics

Candidate market-cap cross-sections average 348.7, 401.4, and 443.0 of 500 securities in the three windows. Because all multi-class issuers are unavailable, available ticker and issuer counts are equivalent in these candidate cells; staleness output separately reports the 497-issuer denominator. Annual and current-GICS diagnostics appear in the selection-bias CSV. Current-GICS availability ranges from 28.21%–82.11%, 30.56%–90.37%, and 40.74%–98.15%. This is a material selection-bias warning, not a sector-neutralization result; GICS is current, not historical PIT.

The familiar all-calendar 63.59% coverage includes the pre-2011 warm-up and is not comparable to the three formal-window figures. Its dominant mutually-exclusive reason is `SHARES_NOT_PIT_AVAILABLE` early in the period. Within the formal windows, leading losses are: 2011–2015 `SHARES_NOT_PIT_AVAILABLE` 13.58%, `NONSTANDARD_CORPORATE_ACTION` 10.13%; 2016–2020 `SHARES_NOT_PIT_AVAILABLE` 7.11%, `NONSTANDARD_CORPORATE_ACTION` 6.17%; 2021–2026.09 `NO_SEC_SHARES` 6.00%, `NONSTANDARD_CORPORATE_ACTION` 2.70%. The reason table is a mutually-exclusive priority decomposition, so it must not be summed with independent price-quality counts.

## Decision table

| Decision | Recommendation | Evidence | Status |
| --- | --- | --- | --- |
| signal-date price | exact positive `close`; retain zero-volume/review only with review flag; invalid/missing unavailable | exact-date audit and no-fill control | RECOMMEND_SIGNOFF |
| primary shares | DEI EntityCommonStockSharesOutstanding, unit shares, PIT selector | direct tag / zero future filing selections | RECOMMEND_SIGNOFF |
| shares <= 1 | selected candidate must be `>1`; preserve raw context | 824 selected ticker-weeks from four `=1` records | RECOMMEND_SIGNOFF |
| standard split chain | standard/reverse only, provenance-relative to share as-of | standard event validation | RECOMMEND_SIGNOFF |
| non-standard actions | retain unresolved and unavailable | 29,081 unique baseline-loss ticker-weeks | RECOMMEND_SIGNOFF |
| multi-class | unresolved/missing | no class-specific shares supplied | RECOMMEND_SIGNOFF |
| zero-volume | retain only with positive plausible close and review flag | signal-date-only audit | RECOMMEND_SIGNOFF |
| invalid price | unavailable | no future/prior fill permitted | RECOMMEND_SIGNOFF |
| staleness | `NO_CUTOFF_WITH_STALENESS_FLAG` for DEV | five sensitivity scenarios; no silent cutoff substitution | RECOMMEND_SIGNOFF |
| Value market-cap level | issuer-level / one-security issuer only | avoids duplicate issuer numerator/denominator | RECOMMEND_SIGNOFF |
| Size market-cap level | issuer-level / one-security issuer only | avoids duplicated multi-class issuer exposure | RECOMMEND_SIGNOFF |

## Remaining warnings and guardrails

All coverage figures use availability flags only. The output does not contain a price × shares result or a Value/Size calculation. Current-universe survivorship bias, current GICS, periodic shares, Yahoo retrospective adjustments, and missing RDQ remain DEV limitations. The authorization above is limited to a separately requested construction task; it does not authorize factors, IC, portfolio, FMOM, strategy, or a formal PIT claim.
