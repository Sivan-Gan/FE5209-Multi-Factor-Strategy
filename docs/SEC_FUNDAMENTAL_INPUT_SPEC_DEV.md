# SEC Fundamental Input Specification — DEV

## Status and non-goals

- Evidence stage: `DEV_CURRENT_UNIVERSE`.
- Scope: raw SEC Company Facts coverage analysis, PIT-safe input-selection proposal, and recorded DEV sign-offs where explicitly approved.
- Not produced: canonical fundamental values, E/P, B/P, FCF Yield, ROE, GP/A, Leverage, ROA, Asset Growth, market capitalization, factor scores, IC, portfolios, FMOM, or strategy results.

This document is a specification proposal. It does not amend the frozen research manual and does not silently freeze choices the manual leaves open. `raw_package/US_source_data_2026-10-08/` remains immutable.

## Raw facts and audit coverage

The scan read 497/497 issuer archives and 12,709,510 raw observations. Each Company Facts observation retains CIK, taxonomy, tag, unit, start/end, FY/FP, filed date, form, accession number, value and, where supplied, frame. The raw source contains 32 observed forms and material duplicate-period/amendment complexity.

The audit files are [sec_tag_coverage_dev.csv](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/dev_legacy/sec_pit/sec_tag_coverage_dev.csv), [sec_unit_audit_dev.csv](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/dev_legacy/sec_pit/sec_unit_audit_dev.csv), [sec_form_audit_dev.csv](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/dev_legacy/sec_pit/sec_form_audit_dev.csv), [sec_period_audit_dev.csv](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/dev_legacy/sec_pit/sec_period_audit_dev.csv), and [sec_duplicate_amendment_audit_dev.csv](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/dev_legacy/sec_pit/sec_duplicate_amendment_audit_dev.csv). Lexically discovered tags are a search surface only; they are not fallbacks.

## Economic concept dictionary and proposed priority

All priority entries below are `PROPOSED_FOR_SIGNOFF`, not frozen rules. A tag is usable only when it passes the relevant form, unit, period, PIT, and duplicate checks below.

| Economic concept | Main actual candidate coverage | Proposed primary / fallback logic | Status |
| --- | ---: | --- | --- |
| Net income | `us-gaap:NetIncomeLoss`: 494/497 issuers (99.4%) | Primary: `NetIncomeLoss` in USD. Conditional fallback: `us-gaap:ProfitLoss` after issuer-level semantic review. Do not substitute common-share or parent-attributable variants automatically. | Proposed; fallback semantics need sign-off. |
| Common equity | `us-gaap:StockholdersEquity`: 490/497 (98.6%) | Primary: `StockholdersEquity` in USD, instant only. Including-NCI, partner/member equity are conditional fallbacks; capital components are not total equity. | Proposed; NCI treatment open. |
| Operating cash flow | `NetCashProvidedByUsedInOperatingActivities`: 496/497 (99.8%) | Primary: that tag in USD, duration only. Continuing-operations variant is conditional only. | Proposed. |
| CapEx input | `PaymentsToAcquirePropertyPlantAndEquipment`: 394/497 (79.3%) | DEV FCF primary: that tag in USD, duration only. No weak-semantic fallback; acquisitions and combined PP&E/intangible tags are not fallbacks. | Approved for DEV FCF input; negative-value exception is specified below. |
| Total assets | `us-gaap:Assets`: 497/497 (100.0%) | Primary: `Assets` in USD, instant only. Current/non-current assets are not substitutes. | Proposed and comparatively clear. |
| Gross profit | `us-gaap:GrossProfit`: 256/497 (51.5%) | DEV baseline uses only this exact USD duration tag where it passes controls. No universal fallback, including `SalesRevenueNet - CostOfGoodsAndServicesSold`. | Approved DEV baseline; coverage limitation remains. |
| Revenue | `Revenues`: 400/497 (80.5%); contract revenue excluding tax: 355/497 (71.4%); net sales: 227/497 (45.7%) | No universal primary/fallback is frozen. These tags can differ in tax inclusion, goods/services scope, and financial-issuer economics. | `OPEN_DECISION`. |
| Total debt / leverage input | `LongTermDebt`: 421/497 (84.7%), plus current/non-current/lease/borrowing components | No generic Total Debt canonical value is proposed. The targeted review recommends `Liabilities / Assets` only for the DEV ordinary non-Financial, non-RE Quality branch; it does not infer construction authorization. | See `LEVERAGE_DEFINITION_TARGETED_AUDIT_DEV.md`. |

The coverage figures describe issuer presence, not economic interchangeability. The candidate report separately shows taxonomy, units, forms, first/last end dates, period classes, value-sign distribution, amendment counts, and duplicate counts.

## FCF input boundary

The approved DEV source-interface formula is `FCF = OCF - CapEx`, with `NetCashProvidedByUsedInOperatingActivities` as OCF and `PaymentsToAcquirePropertyPlantAndEquipment` as CapEx. This does not create any FCF value in this stage. Positive reported CapEx payment amounts are deducted; zero remains zero; a negative primary-CapEx observation is `SIGN_EXCEPTION`/unavailable. Do not apply an absolute value or a sign inversion, and do not use a weaker-semantic fallback. Acquisition cash flows and combined PP&E/intangible tags remain excluded.

## PIT availability and amendment proposal

The DEV availability invariant is frozen:

```text
filed_date <= signal_date
```

For a compatible issuer/tag/unit/economic-period group, the proposed selector first discards every later-filed observation, then selects the latest eligible filing date. A 10-K/A or 10-Q/A may only replace an original filing after its own `filed_date`; it must not overwrite earlier signal dates. Conflicting observations with the same latest eligible filing date are returned as `AMBIGUOUS`/missing, never arbitrarily ordered.

This is conservative because RDQ and intraday announcement time are absent. It is intentionally not a claim that the filing date is the first public availability time.

## Form, unit, and period proposal

| Area | Proposal | Status |
| --- | --- | --- |
| 10-K | Conditional candidate for annual duration and instant facts after concept/unit/period controls. | Proposed for sign-off. |
| 10-Q | Conditional candidate for quarterly/YTD duration and instant facts after controls. | Proposed for sign-off. |
| 10-K/A / 10-Q/A | The PIT proposal keeps an amendment effective only after its own filing date; unresolved same-latest-filed-date conflicts are `AMBIGUOUS`/missing. | Conflict disposition approved in the current DEV sign-off; amendment timing remains the documented PIT proposal. |
| 20-F / 40-F | 20-F is conditionally included when the facts are US-GAAP and pass the same controls; IFRS requires separately approved mapping and currency policy. No 40-F observations exist in the delivered package. | Approved DEV boundary. |
| 8-K / 8-K/A | Present in the source (209,386 / 1,247 raw observations respectively); canonical use is not frozen. | `OPEN_DECISION`. |
| Other observed forms | Excluded from the initial proposal pending form-specific sign-off, not silently deleted from raw evidence. | Proposed exclusion only. |
| Units | `USD` is the proposed canonical unit. No unit conversion occurs. `EUR`, `AFN`, `CAD`, shares, ratios, and other units are excluded or `UNRESOLVED_UNIT` as shown by the unit audit. | USD proposal ready for sign-off; conversion is open. |

Duration classification is a proposal based on `end - start`, base form, and FY/FP together: annual candidate = 330–380 days with 10-K/20-F/40-F and FY/blank FP; quarterly candidate = 75–110 days with 10-Q and Q1/Q2/Q3/blank FP; YTD candidate = 150–300 days with 10-Q and Q2/Q3/blank FP. A fact without `start` is instant and uses its balance-sheet end date. Every other duration is irregular and is not silently reclassified.

## Duplicate, missingness, and identity rules

Duplicate audit keys are issuer + taxonomy + tag + unit + start + end. The audit retains multi-filed groups, same-filed ambiguous groups, amendments, comparative facts, and repeated forms; it does not globally keep the last record. Missingness is retained as unavailable with a reason, never converted to zero and never solved by a cross-tag assumption.

Current identity mapping links 500 securities to 497 issuer archives, including three current multi-share-class issuers. Current GICS identifies 76 Financials and 30 Real Estate issuer CIKs for DEV coverage inspection only. It is not historical PIT GICS and does not authorize a historical branch rule.

The frozen framework states that Financials/Real Estate Value should not use FCF Yield and that Financials Quality uses ROE/ROA rather than non-financial GP/A/-Leverage. This stage creates neither branch factor nor formula; it only preserves the input-interface requirement.

## Recorded DEV rules and remaining open decisions

The current DEV sign-off records: exact `GrossProfit` only (no universal fallback); the primary FCF input formula and negative-CapEx exception; unresolved same-latest-eligible-filed-date conflicts as `AMBIGUOUS`/missing; and conditional 20-F inclusion for US-GAAP facts only. IFRS mapping and currency policy are not approved. Separately, the existing specification retains `filed_date <= signal_date`, no silent unit conversion, instant-versus-duration separation, dates-plus-form/FY/FP classification, and amendment timing as the documented PIT controls/proposals; this sign-off does not silently relabel any unapproved proposal as a new decision.

The following remain `OPEN_DECISION`: universal Revenue chain; any generic Total Debt/lease/component aggregation; 8-K inclusion; NCI-equity fallback; continuing-operations OCF fallback; amendment timing; and whether the remaining proposed 10-K/10-Q primary chains are approved as canonical rules. The targeted audit recommends, but does not authorize construction of, a `Liabilities / Assets` baseline for the stated ordinary non-Financial, non-RE DEV Quality branch. Historical constituents, historical GICS, RDQ, delisting coverage, and market-cap split-basis alignment remain Formal PIT issues.

## Construction gate

Canonical PIT Fundamental Construction remains **on hold by current instruction**. The recorded DEV controls and targeted Leverage recommendation do not authorize it. Any later construction must still exclude market-cap construction and all factor/ratio/research logic until separately authorized. A later Formal PIT rerun must replace current universe/GICS inputs while retaining the documented PIT selector discipline.
