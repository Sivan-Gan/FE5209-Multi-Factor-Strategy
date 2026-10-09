# SEC Fundamental Design Review — DEV

## Status and boundary

- Evidence stage: `DEV_CURRENT_UNIVERSE`.
- This is a read-only design review of the SEC Company Facts package. It does not amend the frozen research manual.
- Not produced: a canonical PIT fundamental panel; ROE, GP/A, Leverage, ROA, Asset Growth, E/P, B/P, FCF Yield, historical market cap, preprocessing, IC, portfolios, FMOM, backtests, or strategy results.
- The source package remains immutable. The post-review package verification is 508/508 payload hashes passing.

The frozen manual specifies the economic concepts—non-financial Quality includes GP/A and `-Leverage`, Financials Quality uses ROE/ROA, and ordinary-company Value includes FCF Yield—but does not freeze SEC Revenue/Cost tags, a Total Debt definition, lease treatment, or a tag-priority chain. This review provides evidence and recommendations; it does not silently fill those choices.

The supporting machine-readable evidence is in [the SEC design-review audit report](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/dev_legacy/sec_pit/sec_fundamental_design_review_acceptance_report.md), [tag inventory](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/dev_legacy/sec_pit/sec_design_tag_inventory_dev.csv), [Gross Profit audit](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/dev_legacy/sec_pit/sec_gross_profit_fallback_audit_dev.csv), [leverage audit](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/dev_legacy/sec_pit/sec_leverage_definition_audit_dev.csv), [FCF input audit](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/dev_legacy/sec_pit/sec_fcf_input_audit_dev.csv), [foreign-form audit](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/dev_legacy/sec_pit/sec_foreign_form_audit_dev.csv), and [same-filed conflict audit](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/dev_legacy/sec_pit/sec_same_filed_conflict_audit_dev.csv).

Five subsequent DEV decisions are now recorded in the [DEV sign-off register](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/dev_legacy/sec_pit/sec_dev_signoff_register.csv). The targeted Leverage audit is separate evidence for the remaining Leverage-definition choice; it does not authorize canonical construction.

## Audit controls

All Gross Profit and FCF compatibility checks use only deterministic, raw USD `us-gaap` facts from original 10-K/10-Q filings. A matched source period requires the same issuer, start date, end date, form, `filed` date, and accession number. A tag/key with more than one raw value is excluded from that pairing rather than resolved by order.

The leverage coverage checks use deterministic USD `us-gaap` instant facts from the same issuer, balance-sheet date, form, `filed` date, and accession number. They count compatible source inputs only; they do not calculate a debt amount or leverage ratio.

The narrower audit scope explains why its direct-Gross-Profit count is 227 issuers, while the prior raw tag inventory reports 256/497 issuers (51.5%) for `us-gaap:GrossProfit`: the design-comparison sample excludes foreign forms, amendments, 8-K facts, non-USD facts, irregular durations, and ambiguous same-tag keys.

## 1. Gross Profit fallback

The raw package confirms that `GrossProfit` itself describes aggregate revenue less cost of goods/services or direct revenue-generation operating expense. It is directly present for 256/497 issuers in raw source coverage. The relevant cost tags are not interchangeable:

- `CostOfRevenue` is aggregate cost of goods produced/sold and services rendered.
- `CostOfGoodsAndServicesSold` explicitly excludes financial-services and other revenue-generating activities.
- `Revenues` explicitly can include interest income, investment income, and sales/trading gains.
- `SalesRevenueNet` is deprecated and ends in this source on 2019-03-31.

| Revenue − cost pair | Compatible issuer count | Exact GP overlap | Exact-match rate | Incremental issuers vs. strict direct GP | Decision |
| --- | ---: | ---: | ---: | ---: | --- |
| Contract revenue excluding assessed tax − Cost of Revenue | 82 | 2,937 periods | 93.33% | +31 | Do not approve: error P95 is 120.29%; it continues to 2026 but is not numerically stable. |
| Sales Revenue Net − Cost of Revenue | 47 | 2,474 periods | 97.66% | +10 | Do not approve as universal: deprecated revenue tag stops in 2019. |
| Sales Revenue Net − Cost of Goods and Services Sold | 90 | 5,637 periods | 99.59% | +18 | Best numerical historical pair, but not a universal fallback: the cost tag excludes financial-service economics and the revenue tag ends in 2019. |
| Revenues − Cost of Revenue | 107 | 5,148 periods | 97.82% | +39 | Do not approve: `Revenues` has broader financial/investment scope; individual errors remain material. |
| Revenues − Cost of Goods and Services Sold | 158 | 3,923 periods | 88.58% | +68 | Do not approve: direct semantic mismatch and materially non-zero errors. |

The detailed audit records absolute and relative error statistics without writing reconstructed Gross Profit observations. For the two sales-revenue pairs, overlap P95 relative error is zero, but that does not cure the non-universal economics or their post-2019 absence.

**Recommendation: do not approve a universal Gross Profit fallback.** The evidence supports retaining exact `GrossProfit` as the only universal DEV input. If the team later wants a tightly bounded historical, non-financial fallback, `SalesRevenueNet - CostOfGoodsAndServicesSold` is the only pair with sufficiently strong numerical overlap to bring back for a separate issuer-/period-scope sign-off. It must never be silently combined with `Revenues`, Financials, Real Estate, or post-2019 observations.

## 2. Leverage and Total Debt alternatives

There is no raw `LongTermDebtAndFinanceLeaseObligations` tag family in this package. The actual related family is `LongTermDebtAndCapitalLeaseObligations`. It must not be treated as the same name or scope without an explicit rule.

Relevant raw coverage includes `LongTermDebt` for 421 issuers (but its source definition excludes capital leases), `LongTermDebtCurrent` for 330, `LongTermDebtNoncurrent` for 343, capital-lease combined current/non-current components for 147/187, and the reported capital-lease total including current maturities for 92. Finance-lease liability is separately present for 258 issuers and operating-lease liability for 484; neither should be added to another debt tag without a lease-scope policy because overlap is not established.

| Candidate numerator over `Assets` | Compatible issuers / source periods | Complexity | Double-counting / scope assessment | Recommendation |
| --- | ---: | --- | --- | --- |
| `LongTermDebt` | 421 / 21,027 | Low | No aggregation, but excludes capital leases and is not demonstrated Total Debt. | A long-term-debt proxy only; do not call it `TotalDebt/Assets`. |
| `LongTermDebtCurrent + LongTermDebtNoncurrent` | 302 / 18,374 | Medium | Exact current/non-current components both exclude capital leases; do not add `LongTermDebt`, `DebtCurrent`, or lease tags. | **Recommended conditional DEV definition** if human sign-off chooses debt excluding capital leases. Label it accordingly, not as universal total debt. |
| `LongTermDebtAndCapitalLeaseObligationsCurrent + LongTermDebtAndCapitalLeaseObligations` | 142 / 9,517 | Medium | Exact current/non-current pair; do not add finance- or operating-lease tags. | Broader historical debt/capital-lease candidate, but lower coverage and finance-lease treatment remain open. |
| `LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities` | 91 / 2,883 | Low | One reported amount: no component aggregation. | Safest reported-total-style source where present, but insufficient coverage for a broad universe rule. |
| `DebtCurrent + LongTermDebtNoncurrent` | 138 / 6,864 | Medium | Current debt includes current capital leases while non-current tag excludes them; scope is inconsistent. | Human review only; not recommended as a default. |
| `ShortTermBorrowings + LongTermDebtCurrent + LongTermDebtNoncurrent` | 123 / 5,219 | High | Short-term borrowings and current debt may overlap in issuer presentation. | Do not approve without a dedicated overlap-control policy. |

As a source reconciliation diagnostic—not a constructed debt series—the reported capital-lease total and its exact current/non-current components coexist in 1,916 same-filing source records and reconcile exactly in 1,719 (89.7%). The remaining 197 do not support an automatic switch or cross-tag substitution.

**Recommendation:** do not freeze a generic `TotalDebt/Assets` label now. The most implementable broad definition is `LongTermDebtCurrent + LongTermDebtNoncurrent` divided by `Assets`, provided the team explicitly approves a **debt-excluding-capital-leases** definition. If the label must be true reported total debt including current maturities and capital leases, use only the single reported total tag and accept its 91-issuer coverage. Operating leases remain outside the definition pending an explicit economic policy.

## 3. FCF input rule

The candidate definition is valid as a source-interface rule:

\[
FCF = OCF - CapEx
\]

with `NetCashProvidedByUsedInOperatingActivities` as OCF and `PaymentsToAcquirePropertyPlantAndEquipment` as CapEx. The CapEx tag is explicitly a cash outflow/payment for PP&E, so a positive payment amount is deducted from OCF. OCF itself can correctly be negative; no sign transformation is needed.

The strict same-filing audit finds 41,736 compatible duration periods across 391 issuers, from 2007-09-28 through 2026-08-31. Of the matched CapEx facts, 41,629 (99.7436%) are positive, 34 are zero, and 73 are negative. Across all raw primary-CapEx facts, the signs are similarly dominated by positive values (49,346 positive, 34 zero, 89 negative).

**Recommendation: approve the formula and the two primary tags for DEV, with one explicit exception rule.** Deduct a positive reported payment; retain zero as reported; send a negative CapEx-source observation to `SIGN_EXCEPTION`/missing for review. Do not take an absolute value, invert the sign, or use a weaker CapEx fallback. This review writes no FCF values and no FCF Yield; historical market-cap work remains out of scope.

## 4. 20-F and 40-F

The current universe has two issuers with relevant 20-F-family facts: CRH (CIK 849395) and NXPI (CIK 1413447). No 40-F or 40-F/A observation exists in the delivered package.

- CRH's relevant 20-F facts are primarily `ifrs-full`, including IFRS `GrossProfit`, `Revenue`, `CostOfSales`, `Assets`, and borrowing/lease concepts.
- NXPI's relevant 20-F/20-F/A facts are primarily `us-gaap`, including Gross Profit, net-sales/cost, assets, OCF, CapEx, net income, equity, and debt-support tags.
- Neither issuer lacks *all* 10-K/10-Q history for a reviewed concept. However, 20-F provides foreign-only report periods: for Gross Profit, Revenue, Cost, and Assets, 14 of 18 foreign period identities have no same-concept 10-K/10-Q observation; for NXPI OCF/CapEx/Net Income/Common Equity, 8 of 10 such period identities are foreign-only.

Therefore, **20-F is not an exclusion candidate**: omitting it would create period-level gaps. It needs an issuer-/taxonomy-aware canonical policy. In particular, this review does not approve an IFRS-to-US-GAAP fallback or a USD/EUR conversion for CRH. 40-F needs no operational rule until such source facts are delivered.

## 5. Same latest-filed-date conflicting facts

The conflict audit covers the named concepts under review, not every raw Company Facts tag. For each issuer/taxonomy/tag/unit/start/end group, it identifies the maximum valid `filed` date and tests whether that latest-date set contains multiple values. It does not select a value.

| Source classification | Groups | Issuers | Share of 380,233 latest same-tag groups |
| --- | ---: | ---: | ---: |
| Actual same-tag latest-date conflicting values | 12 | 2 | 0.003156% |
| Different accession number | 12 | 2 | 0.003156% |
| Same accession number | 0 | 0 | 0.000000% |
| Includes an amendment | 1 | 1 | 0.000263% |
| Different FY/FP or frame surrogate | 4 | 2 | 0.001052% |
| Other/unexplained within available fields | 0 | 0 | 0.000000% |
| Different taxonomy/tag for same economic concept and latest filing date | 29,199 | 488 | 7.679239% |

The classifications other than the first and last are non-exclusive source attributes. Company Facts does not preserve a context identifier, so FY/FP/frame is only a surrogate; the audit already fixes the reported start/end dates. The 29,199 taxonomy/tag rows are **semantic competition**, not same-tag ties: different Revenue or debt tags must not be coalesced merely because they share a period.

**Safe recommendation:** after applying `filed_date <= signal_date`, choose the latest eligible filing date only when the chosen tag/key has one deterministic value. If multiple values remain at that latest date, emit `ambiguous/missing`. Do not use value magnitude, raw row order, or a final-record rule. The practical coverage impact in this audit is small, but the policy is required for PIT integrity.

## 6. Pre-sign-off table

| Proposed rule | Recommendation | Reason |
| --- | --- | --- |
| `NetIncomeLoss` → Net Income primary | `RECOMMEND_SIGNOFF` | 494/497 issuer raw coverage; retain USD, duration, PIT, and ambiguity checks. |
| `StockholdersEquity` → Common Equity primary | `RECOMMEND_SIGNOFF` | 490/497 issuer coverage; exact instant tag avoids automatic NCI/partner/member substitutions. |
| `Assets` → Total Assets primary | `RECOMMEND_SIGNOFF` | 497/497 issuer coverage; exact instant total-assets tag. |
| `NetCashProvidedByUsedInOperatingActivities` → OCF primary | `RECOMMEND_SIGNOFF` | 496/497 issuer coverage; no continuing-operations fallback required. |
| `PaymentsToAcquirePropertyPlantAndEquipment` → CapEx primary | `RECOMMEND_SIGNOFF` | 394/497 raw issuer coverage; retain sign exceptions and do not use acquisition/intangible fallbacks. |
| `filed_date <= signal_date` | `RECOMMEND_SIGNOFF` | Conservative, source-observable DEV availability rule; RDQ/intraday timing remains unavailable. |
| Amendments only effective from their own filing date | `RECOMMEND_SIGNOFF` | Prevents future information from backfilling an earlier signal date. |
| No silent unit conversion | `RECOMMEND_SIGNOFF` | The source includes non-USD facts; no FX/conversion rule is approved. |
| Instant/duration separation | `RECOMMEND_SIGNOFF` | Balance-sheet and flow facts use different economic-period structures. |
| Classify periods using dates + form + FY/FP, not FP alone | `RECOMMEND_SIGNOFF` | The source contains irregular and comparative periods; FP alone is insufficient. |

The machine-readable equivalent is [sec_design_signoff_table_dev.csv](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/dev_legacy/sec_pit/sec_design_signoff_table_dev.csv).

## Historical pre-sign-off questions and their current status

1. Leverage economic scope was the remaining core definition. It is now assessed in [the targeted audit](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/docs/dev_legacy/LEVERAGE_DEFINITION_TARGETED_AUDIT_DEV.md), which makes a recommendation but does not claim a construction authorization.
2. **Resolved — DEV sign-off:** exact `GrossProfit` is the baseline; no universal fallback, including `SalesRevenueNet - CostOfGoodsAndServicesSold`, enters the baseline.
3. **Resolved — DEV sign-off:** primary FCF is OCF minus primary CapEx; negative CapEx is `SIGN_EXCEPTION`/unavailable, without absolute-value or sign inversion.
4. **Resolved — DEV sign-off:** 20-F is conditionally included. US-GAAP facts use the same controls; IFRS facts require separately approved mapping and currency policy. There are no 40-F observations.
5. **Resolved — DEV sign-off:** unresolved same-latest-eligible-filed-date conflicts are `AMBIGUOUS`/missing, without an arbitrary tie-break.

## Construction gate

The design-review evidence is **PASS WITH WARNINGS**. Its five non-Leverage sign-offs are now recorded, and the separate targeted audit proposes a Leverage baseline. Nevertheless, Canonical PIT Fundamental Construction remains **on hold by current instruction**; no construction authorization is inferred from either document. Any future construction remains `DEV_CURRENT_UNIVERSE`: Current S&P 500 membership and Current GICS are not historical PIT evidence; RDQ is unavailable; and historical constituents, historical GICS, CRSP/delisting coverage, and any formal market-cap construction remain future Formal PIT work.
