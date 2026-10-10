# Formal WRDS Gate 0 Phase 2C — Compustat PIT and Fundamental-Semantics Audit

## Verdict

**Gate 0 Phase 2C — PASS WITH WARNINGS**

Formal Gate 0 overall remains **PENDING**. This evidence set audits timing, raw semantics, conflicts, and a future fundamental-field contract. It creates no canonical dataset, factor, IC, portfolio, or backtest.

## Delivered raw coverage

- Annual: 281,884 raw rows, 38 columns, 29,294 GVKEYs, datadate 2004-01-31 to 2026-09-30.
- Quarterly: 1,058,806 raw rows, 36 columns, 31,460 GVKEYs, datadate 2004-01-31 to 2026-09-30.
- Both inputs were hashed before and after the audit; Phase-1 original raw integrity was rechecked rather than rewriting its historical manifest.

## PIT timing conclusion

Quarterly `rdq` is treated as date-only publication evidence. A quarterly observation becomes usable only on the **next delivered local trading day** after `rdq`; blank, invalid (`rdq < datadate`), duplicate/conflicting, or cutoff-exceeding observations are not PIT eligible. No same-day assumption, date fill, or future observation use is permitted.

Annual `datadate` is never treated as publication timing. The annual availability audit uses a matching unique, valid Q4 `rdq` and the same next-local-trading-day convention. It identifies evidence-supported records and separately labels no-Q4, invalid, ambiguous, calendar-unavailable, raw-conflict, and 2026-outside-formal-cutoff cases. No convenience lag was invented.
- Annual rows resolved through Q4-RDQ evidence: 201,901.
- Annual rows blocked by absent/invalid/ambiguous timing evidence: 78,824.

## Revision/restatement claim boundary

The delivered inputs are ordinary Compustat history, not Compustat Snapshot. Provider evidence states that North America values that are overwritten do not remain in the ordinary product, while Snapshot retains iterations with effective/thru dates. Results may therefore be called **publication-date-aligned historical fundamentals** only; they must not be called vintage-perfect PIT accounting data. Residual revision/restatement bias remains an explicit limitation.

## Quarterly flow and TTM capability

`oancfy` and `capxy` are audited as fiscal-YTD cash-flow variables. Single-quarter derivation is constrained to Q1=YTD, Q2/Q3=current YTD minus the immediate prior fiscal-quarter YTD, and Q4=current Q4 YTD minus Q3 YTD. The audit never differences across fiscal years, fills missing quarters, clips negative flows, or resolves conflicts by row order.
- `oancfy`: 63,487 valid linked-GVKEY single-quarter audit observations; 60,452 four-consecutive-quarter PIT-capable windows.
- `capxy`: 63,350 valid linked-GVKEY single-quarter audit observations; 60,244 four-consecutive-quarter PIT-capable windows.

## Duplicate/conflict evidence

- Annual `(GVKEY, datadate)` conflict groups: 25,437; exact-duplicate groups: 0.
- Quarterly `(GVKEY, datadate)` conflict groups: 451; keys with conflicting `rdq` values: 51; exact-duplicate groups: 0.
- Conflicting raw logical observations remain unresolved. This audit defines no dataframe-order, first-row, or latest-row selection rule.

## Data-contract limitations carried forward

- The delivered CSV headers do not encode authoritative raw-field units. The field contract preserves no silent unit multiplier; an authoritative Compustat item dictionary is required before numeric factor scaling.
- The delivered quarterly schema has no `gpq`. Revenue minus COGS is not adopted as a universal quarterly gross-profit substitute.
- Raw observations extending into 2026 are retained in the immutable delivery but are labelled outside the frozen 2025-12-31 formal market-data cutoff.

## Explicit phase boundary

No canonical data, issuer market-cap construction, factor score, Forward IC, portfolio, strategy, or Phase 3 work is performed by this audit.
