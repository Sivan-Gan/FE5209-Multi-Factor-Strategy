# Canonical PIT Fundamental Inputs — DEV Contract

## Status and scope

- **Evidence stage:** `DEV_CURRENT_UNIVERSE`
- **Construction decision:** `PASS WITH WARNINGS`
- **Purpose:** transform the immutable SEC Company Facts archives into auditable,
  point-in-time (PIT) source inputs and a weekly availability/provenance panel.
- **Out of scope:** market capitalization, FCF, ROE, ROA, GP/A, Leverage, Asset
  Growth, all valuation/factor calculations, preprocessing, IC, portfolios,
  FMOM, backtests, and strategy outputs.

This contract implements only the user-signed DEV source rules. It does not
freeze a factor definition, an annual/quarterly/TTM hierarchy, or any
cross-sectional eligibility rule. The frozen research manual remains unchanged
and is the methodological single source of truth.

## Sources and identity boundary

The raw evidence is
`raw_package/US_source_data_2026-10-08/`; it remains immutable. The post-build
manifest check passed **508/508** payload-file hashes, with manifest SHA-256
`b1cc92f9dc97ad1a5e963136f0b0867c092c4b44bfcbc2e1092abfeedc4cde72`.

The DEV security master has 503 current securities. The SEC map links 500
security rows to 497 issuer CIKs and all 497 issuer archives are readable.
Issuer facts are intentionally fanned out to mapped current securities,
including current share-class relationships. Three current-master securities
have no SEC map and are included in the complete weekly universe grid with
`SEC_MAPPING_UNAVAILABLE`, but are outside the 500-security SEC coverage
denominator.

The denominator is explicitly a current-universe DEV denominator. Current GICS
is not used to determine availability, and neither current S&P 500 membership
nor current GICS is historical PIT evidence.

## Canonical outputs

SQLite is used because the project environment does not include Parquet support.
Numeric source values remain text, avoiding implicit rounding or conversion
before a separately authorized factor-engineering stage.

| Object | Grain / key | Rows | Purpose |
| --- | --- | ---: | --- |
| `data/canonical/us_pit_fundamental_facts_dev.sqlite` → `canonical_fundamental_facts` | `(ticker, source_observation_id)` | 469,705 | Normalized signed-tag SEC observations, including unavailable/excluded observations and their reason. |
| `data/canonical/us_weekly_pit_fundamentals_dev.sqlite` → `weekly_security_universe` | `(ticker, signal_date)` | 543,743 = 503 × 1,081 | Complete DEV ticker/week universe and SEC-map status. |
| Same weekly database → `weekly_pit_fundamental_snapshot` | `(ticker, signal_date, economic_concept, period_class)` | 5,088,161 | Sparse available PIT facts with full source provenance and age fields. |
| Same weekly database → `weekly_pit_fundamental_unavailability` | Same as snapshot key | 4,027 | Explicit `AMBIGUOUS`, `SIGN_EXCEPTION`, or `OPEN_IMPLEMENTATION_ISSUE` slots. |

The sparse fact table covers 500 mapped tickers and 910 signal dates with at
least one available signed fact. Absence from that table means unavailable; it
does not mean an absent security/week because the universe table carries every
503 × 1,081 pair.

Every nonmissing snapshot record retains ticker/Yahoo ticker, CIK, issuer,
economic concept, period class, source taxonomy/tag/unit, source dates, period
length, FY/FP, form, filed date, accession, frame, amendment flag,
`source_observation_id`, and both age measures. Snapshot rows join to exactly
one fact-layer record by `(ticker, source_observation_id)`.

## Signed input surface and source controls

| Economic concept | Exact allowed tag | Source kind | Unit / taxonomy |
| --- | --- | --- | --- |
| Net Income | `us-gaap:NetIncomeLoss` | Duration | USD / `us-gaap` |
| Common Equity | `us-gaap:StockholdersEquity` | Instant | USD / `us-gaap` |
| Assets | `us-gaap:Assets` | Instant | USD / `us-gaap` |
| Liabilities | `us-gaap:Liabilities` | Instant | USD / `us-gaap` |
| Gross Profit | `us-gaap:GrossProfit` | Duration | USD / `us-gaap` |
| Operating Cash Flow | `us-gaap:NetCashProvidedByUsedInOperatingActivities` | Duration | USD / `us-gaap` |
| Capital Expenditure | `us-gaap:PaymentsToAcquirePropertyPlantAndEquipment` | Duration | USD / `us-gaap` |

No fallback tag, `Revenue − CostOfRevenue` reconstruction, sign inversion,
absolute-value conversion, currency conversion, or silent unit conversion is
used. Available records are limited to 10-K, 10-Q, and conditionally eligible
US-GAAP 20-F families, including their `/A` forms. Other forms and IFRS facts
remain source records with an exclusion reason; they never become canonical
available values.

## PIT, amendment, conflict, and period semantics

A fact enters a weekly information set only when:

```text
filed_date <= signal_date
```

An amendment enters only on or after its own filing date. It never overwrites
an earlier weekly record. Within a given period class, the selector holds the
latest eligible report-period end; for that period, it uses the latest eligible
filing state only when that state is uniquely supported under this contract.

Duration facts are retained separately as `annual_candidate`,
`quarterly_candidate`, `ytd_candidate`, or `irregular_duration`; balance-sheet
facts are `instant`. These classes are never merged, and the snapshot does not
claim a final annual/quarterly/TTM selection hierarchy.

If the same latest eligible source group has differing values, it is
`AMBIGUOUS` and missing. If matching values have more than one non-identical
provenance and the signed rule does not uniquely choose one, it is
`OPEN_IMPLEMENTATION_ISSUE_NONUNIQUE_PROVENANCE` and missing. Neither case is
resolved by value magnitude, row order, last row, or accession ordering.

For each available weekly fact:

```text
age_since_period_end_days = signal_date - end_date
age_since_filing_days     = signal_date - filed_date
```

Both fields are descriptive only. No maximum staleness cutoff has been
invented or applied.

## Construction counts and exception controls

| Fact-layer outcome | Count | Treatment |
| --- | ---: | --- |
| Available signed source facts | 462,059 | Eligible for class-specific weekly selection. |
| Same-latest-filed conflicts | 14 rows / 7 source groups | `AMBIGUOUS`, unavailable. |
| Negative primary CapEx | 89 rows across 22 tickers | `SIGN_EXCEPTION`, unavailable; no sign change. |
| IFRS facts (CRH) | 44 | `UNSUPPORTED_TAXONOMY`, unavailable. |
| Excluded forms | 7,491 | `FORM_REJECTED`, unavailable. |
| Period-class mismatches | 6 | `PERIOD_CLASS_MISMATCH`, unavailable. |
| Future period end after filing | 2 | `OPEN_IMPLEMENTATION_ISSUE_FUTURE_PERIOD_END`, unavailable. |

At weekly level, 34 source-class slots are `AMBIGUOUS`, 1,518 are negative
CapEx `SIGN_EXCEPTION`, and 2,475 are non-unique-provenance open issues. The
class slots are conservatively missing. At concept coverage level, ambiguity
does not reduce coverage because another permitted class is available; negative
CapEx accounts for 360 unavailable CapEx concept/date observations; open
provenance issues account for 470 unavailable concept/date observations. The
complete, aggregated exception record is
`outputs/audit/sec_canonical_pit_open_implementation_issues_dev.csv`.

## Weekly coverage in the formal US research windows

Each percentage is weekly availability among 500 SEC-mapped current DEV
securities. It is descriptive source coverage only—not a factor-selection or
factor-usability decision.

| Concept | Window | Mean | Median | Minimum | 5th percentile | First-to-last change |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Assets | 2011–2015 | 83.8% | 84.6% | 74.8% | 75.4% | +13.8 pp |
| Assets | 2016–2020 | 91.7% | 91.4% | 88.6% | 88.6% | +6.2 pp |
| Assets | 2021–2026.09 | 98.0% | 97.8% | 94.8% | 95.4% | +5.2 pp |
| CapEx | 2011–2015 | 61.5% | 61.6% | 52.2% | 53.0% | +14.8 pp |
| CapEx | 2016–2020 | 70.4% | 69.8% | 67.0% | 67.2% | +7.0 pp |
| CapEx | 2021–2026.09 | 77.3% | 77.2% | 74.0% | 74.6% | +5.4 pp |
| Common Equity | 2011–2015 | 78.5% | 79.8% | 66.2% | 67.6% | +18.4 pp |
| Common Equity | 2016–2020 | 88.6% | 88.2% | 84.6% | 84.6% | +8.0 pp |
| Common Equity | 2021–2026.09 | 96.4% | 96.4% | 92.6% | 93.2% | +6.0 pp |
| Gross Profit | 2011–2015 | 39.2% | 40.0% | 28.4% | 32.0% | +14.8 pp |
| Gross Profit | 2016–2020 | 45.7% | 46.0% | 43.2% | 43.4% | +4.8 pp |
| Gross Profit | 2021–2026.09 | 49.7% | 49.4% | 48.0% | 48.4% | +3.2 pp |
| Liabilities | 2011–2015 | 57.1% | 58.0% | 49.8% | 50.4% | +11.0 pp |
| Liabilities | 2016–2020 | 64.7% | 64.2% | 60.8% | 61.2% | +7.6 pp |
| Liabilities | 2021–2026.09 | 72.5% | 72.6% | 68.4% | 69.2% | +6.2 pp |
| Net Income | 2011–2015 | 79.4% | 80.6% | 67.6% | 69.4% | +17.4 pp |
| Net Income | 2016–2020 | 88.9% | 88.6% | 85.0% | 85.4% | +7.8 pp |
| Net Income | 2021–2026.09 | 96.2% | 95.8% | 92.8% | 93.2% | +6.2 pp |
| OCF | 2011–2015 | 80.9% | 81.6% | 72.2% | 72.6% | +13.2 pp |
| OCF | 2016–2020 | 89.8% | 89.4% | 85.4% | 85.4% | +8.8 pp |
| OCF | 2021–2026.09 | 97.7% | 97.4% | 94.2% | 95.0% | +5.6 pp |

The detailed weekly report, including reason counts, is
`outputs/audit/sec_weekly_fundamental_coverage_dev.csv`; the window aggregation
is `outputs/audit/sec_weekly_fundamental_coverage_by_window_dev.csv`.

## Staleness evidence

The full descriptive distributions are in
`outputs/audit/sec_fundamental_staleness_dev.csv`. Instant facts have modest
central ages but long tails because no cutoff has been applied:

| Instant concept | Snapshot rows | Median period-end age | 95th pct period-end age | Median filing age | 95th pct filing age |
| --- | ---: | ---: | ---: | ---: | ---: |
| Assets | 395,229 | 81 days | 126 days | 43 days | 93 days |
| Common Equity | 379,370 | 83 days | 150 days | 45 days | 115 days |
| Liabilities | 279,872 | 82 days | 139 days | 45 days | 106 days |

For annual duration classes, median period-end ages are 241 days (Net Income),
248 days (Gross Profit), 246 days (OCF), and 243 days (CapEx); their 95th
percentiles range from 1,123 to 1,764 days. The quarterly, YTD, and irregular
classes are separately reported rather than interpreted as interchangeable
observations. Long tails are an audit finding, not a new exclusion rule.

## Amendments and foreign forms

There are 42,924 weekly snapshot rows sourced from an `/A` form, spanning 172
tickers and all seven concepts. The first-selection transition audit has 1,493
events: 512 `SAME_PERIOD_AMENDMENT`, 551 `LATER_PERIOD_AMENDMENT`, 427 with no
prior available selected fact, and 3 other-period changes. Every transition is
on or after the amendment filing date. See
`outputs/audit/sec_fundamental_amendment_switches_dev.csv` and its summary.

Conditional 20-F inclusion behaved as signed:

- **NXPI:** 81 available `20-F` and 26 available `20-F/A` fact rows, producing
  1,583 and 490 weekly provenance rows respectively; these are USD US-GAAP
  facts and use the same controls as domestic forms.
- **CRH:** 44 IFRS `20-F` fact rows remain unavailable as
  `UNSUPPORTED_TAXONOMY`; no IFRS mapping or currency policy was introduced.
- **40-F:** no observations were found.

## Acceptance and boundaries

The acceptance report is `PASS WITH WARNINGS`. It passes raw integrity, archive
readability/mapping, PIT no-look-ahead (**0** `filed_date > signal_date` rows),
unique snapshot keys, traceable provenance, unit/taxonomy/form controls,
amendment timing, and period separation.

DEV warnings are current-S&P-500 survivorship bias; non-PIT current GICS;
missing RDQ/announcement timestamps; direct Gross-Profit, Liabilities, and
CapEx coverage limits; explicit sign/conflict/open-provenance missingness; and
three current-master securities without SEC mapping. The new non-unique
provenance category requires governance review before anyone chooses a record;
its conservative missing treatment is not a selector-integrity blocker.

Historical S&P 500 membership, historical security/identifier history,
historical GICS, CRSP delisting/member-price coverage, Compustat and
PERMNO/GVKEY links, and an approved historical market-cap basis remain Formal
PIT limitations. Their absence does not change this DEV acceptance result and
requires a later `FORMAL_PIT_UNIVERSE` rerun.

The input/provenance layer is ready for the separately scoped **Historical
Market Cap Specification** discussion. It is not authorization to construct
market cap, ratios, fundamental factors, IC, portfolios, or strategy results.

