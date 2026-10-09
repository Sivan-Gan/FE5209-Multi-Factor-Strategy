# Leverage Definition Targeted Audit — DEV

## Scope and status

- Evidence stage: `DEV_CURRENT_UNIVERSE`.
- Purpose: choose the most defensible DEV baseline definition for the non-Financial Quality component `-Leverage`, using source-input availability and semantics only.
- This document records neither individual Leverage, ROE, or GP/A values nor a Quality score, z-score, IC, portfolio, backtest, or strategy result. It does not create a canonical PIT fundamental panel.
- The frozen manual remains unchanged. Its relevant branch rule is preserved: Financials use ROE/ROA rather than `-Leverage`; Real Estate requires a separately pre-registered comparable-liability treatment. Current GICS is a DEV classification, not PIT evidence.

The machine-readable evidence is [definition coverage](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/sec_leverage_targeted_definition_audit_dev.csv), [non-Financial Quality-input availability](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/sec_quality_input_availability_nonfinancial_dev.csv), [approved DEV sign-offs](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/sec_dev_signoff_register.csv), [audit JSON](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/sec_leverage_targeted_audit_dev.json), and [acceptance report](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/sec_leverage_targeted_audit_acceptance_report.md).

## Recorded DEV sign-offs

| Decision | Recorded DEV rule |
| --- | --- |
| Gross Profit | Baseline uses exact `GrossProfit` only. No universal fallback; `SalesRevenueNet - CostOfGoodsAndServicesSold` is excluded. |
| FCF | Use `NetCashProvidedByUsedInOperatingActivities - PaymentsToAcquirePropertyPlantAndEquipment`; no weak-semantic fallback. |
| Negative primary CapEx | Mark `SIGN_EXCEPTION`/unavailable. Do not use an absolute value or reverse its sign. |
| Latest eligible filing-date conflicts | Mark conflicting values `AMBIGUOUS`/missing. Do not choose by magnitude, input order, or final record. |
| Foreign forms | 20-F is conditionally included: US-GAAP facts may use the same controls; IFRS facts require a separately approved mapping and currency policy. No 40-F observations currently exist. |

## Audit method and interpretation

The audit reads the immutable SEC Company Facts archives directly. A compatible record requires the exact same CIK, report end date, form, `filed` date, and accession number across every input. It uses deterministic USD `us-gaap` facts from 10-K, 10-Q, and US-GAAP 20-F families; amendments are present only as their own filed dates. A tag/key with more than one value is excluded rather than resolved.

This is an **input-availability** audit, not a ratio calculation. “ROE availability” means end-aligned availability of `NetIncomeLoss` and non-zero `StockholdersEquity`; it does not select an end-versus-average equity convention. “GP/A availability” means exact `GrossProfit` and non-zero `Assets`. The future Quality missingness rule is not frozen by the 2-of-3 counts below.

## Candidate comparison

The recommendation scope is the Current-GICS **ordinary non-Financial, non-Real-Estate** branch: 391 issuers. It excludes the 76 Financials (manual: ROE/ROA) and 30 Real Estate issuers (manual: a separate comparable-liability rule). Source-period coverage is measured against the 51,279 same-scope `Assets` filings; issuer coverage only means at least one compatible source period.

| Candidate | Exact XBRL inputs | Issuers | Compatible periods / asset filings | Time coverage | Semantic / aggregation / double-counting assessment | Denominator and tail risk | Financials / RE suitability | Status |
| --- | --- | ---: | ---: | --- | --- | --- | --- | --- |
| `(LongTermDebtCurrent + LongTermDebtNoncurrent) / Assets` | `LongTermDebtCurrent`, `LongTermDebtNoncurrent`, `Assets` | 272/391 (69.57%) | 16,912/51,279 (32.98%) | 2007-12-31 to 2026-08-15 | Narrow debt-capital proxy. Sum exactly the two named components once, from one filing. Both descriptions exclude capital leases. Do not add `LongTermDebt`, `DebtCurrent`, or lease tags. | `Assets` is positive for all 16,912 compatible records; comparatively low denominator-tail risk. Scope/coverage is the risk. | Not Financials baseline; RE only under a separate policy. | `ALTERNATIVE_DEBT_ONLY` |
| `Liabilities / Assets` | `Liabilities`, `Assets` | 269/391 (68.80%) | 29,619/51,279 (57.76%) | 2007-12-31 to 2026-08-15 | Broad recognized-liabilities-to-assets proxy. One reported numerator: no component aggregation and no double count. Do not derive liabilities by subtraction or substitute `LiabilitiesAndStockholdersEquity`. | 29,619 positive-Assets records; 7 zero-Assets records are unavailable. Lower tail risk than equity-denominator proxies. | Not Financials baseline; RE only under a separate policy. | `RECOMMENDED_BASELINE` |
| `Assets / StockholdersEquity` | `Assets`, `StockholdersEquity` | 384/391 (98.21%) | 44,805/51,279 (87.37%) | 2007-12-31 to 2026-08-31 | Equity multiplier, not a direct debt measure. No aggregation, but parent equity need not exactly align with all-liabilities presentation. | 11 zero and 2,473 negative equity records; small positive equity can create unbounded tails. | Not Financials baseline; RE treatment not approved. | `NOT_RECOMMENDED_EQUITY_DENOMINATOR` |
| `Liabilities / StockholdersEquity` | `Liabilities`, `StockholdersEquity` | 264/391 (67.52%) | 26,926/51,279 (52.51%) | 2008-06-30 to 2026-08-15 | Broad liabilities/equity multiplier, not debt-only capital structure. No aggregation, but numerator/equity presentation can be misaligned. | 7 zero and 1,519 negative equity records; sign reversal and unbounded-tail risk. | Not Financials baseline; RE treatment not approved. | `NOT_RECOMMENDED_EQUITY_DENOMINATOR` |

The source forms are predominantly 10-Q and 10-K. The scope includes 20-F/20-F/A only where the facts are US-GAAP and USD; no IFRS mapping is made. The later 2026 end dates are delivered source coverage, not a forward-looking availability assertion; any future implementation must apply `filed_date <= signal_date`.

## Non-Financial joint-input availability

The requested broad non-Financial view includes 421 current-GICS issuers, including Real Estate. This is a DEV-only reporting partition.

| Input set | Available issuers | Compatible periods / asset filings | At least 2 of ROE-input, exact GP/A-input, and this leverage candidate |
| --- | ---: | ---: | ---: |
| End-aligned ROE inputs: `NetIncomeLoss` + `StockholdersEquity` | 407/421 (96.67%) | 21,608/55,147 (39.18%) | — |
| Exact GP/A inputs: `GrossProfit` + `Assets` | 218/421 (51.78%) | 9,373/55,147 (17.00%) | — |
| Debt components / Assets | 278/421 (66.03%) | 17,372/55,147 (31.50%) | 319/421 (75.77%); 10,781/55,147 (19.55%) |
| Liabilities / Assets | 297/421 (70.55%) | 33,100/55,147 (60.02%) | 344/421 (81.71%); 13,496/55,147 (24.47%) |
| Assets / Equity | 414/421 (98.34%) | 48,399/55,147 (87.76%) | 410/421 (97.39%); 17,046/55,147 (30.91%) |
| Liabilities / Equity | 292/421 (69.36%) | 30,258/55,147 (54.87%) | 343/421 (81.47%); 13,042/55,147 (23.65%) |

For the recommendation scope (391 ordinary non-Financial, non-RE issuers), `Liabilities / Assets` has 316/391 issuers (80.82%) and 12,410/51,279 filings (24.20%) with at least two of the three input sets. Its three-input intersection is 4,762 filings. These are availability counts only; they do not establish a future missing-component policy or calculate any component.

## RECOMMENDED_BASELINE

**`Liabilities / Assets`, using exact same-filing `us-gaap:Liabilities` over `us-gaap:Assets`, is the recommended DEV baseline for the Current-GICS ordinary non-Financial, non-Real-Estate Quality `-Leverage` branch.**

Reasons:

- It uses two direct balance-sheet concepts, needs no component aggregation, and has no debt-component overlap or lease double-counting decision.
- It avoids the unstable zero/negative/small-equity denominator that makes the two equity-denominator proxies unsuitable as a baseline.
- It covers 57.76% of eligible asset filings—well above the 32.98% of the narrowly defined debt-components alternative—while preserving a broad, interpretable book-leverage meaning.
- The lower issuer count than the debt-components alternative (269 versus 272) is explicitly retained as a DEV limitation; issuer presence is not substituted for time-series availability.

This recommendation does **not** apply to Financials or Real Estate, does not claim PIT sector classification, and does not define a Quality score or a neutralization/missingness rule. Any future construction must also retain the approved `filed_date <= signal_date`, amendment, ambiguity, unit, and 20-F/IFRS controls.

## Alternative and rejected candidates

- **Alternative for a deliberately narrow debt-capital question:** `(LongTermDebtCurrent + LongTermDebtNoncurrent) / Assets`. It is appropriate only if the team explicitly wants a debt-excluding-capital-leases construct. Its controlled aggregation must never be broadened by adding other debt/lease tags, and it has materially lower filing coverage.
- **Not a baseline:** `Assets / StockholdersEquity` and `Liabilities / StockholdersEquity`. Their high apparent coverage does not offset the economic difference from a direct debt/balance-sheet burden and the documented zero/negative/small-equity instability.

## Remaining boundaries

- No Canonical PIT Fundamental Construction has begun or is authorized by this review.
- Current S&P 500 membership and Current GICS remain survivorship-biased/non-PIT DEV inputs.
- RDQ/earnings-announcement timing is unavailable; `filed_date` remains the conservative availability basis.
- Historical constituents, identifier history, historical GICS, CRSP delistings/prices, Compustat/GVKEY/PERMNO links, and Formal PIT evidence remain future work.
