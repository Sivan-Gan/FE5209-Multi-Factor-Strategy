# Formal WRDS Gate 0 Phase 2B — CRSP Market Semantics Audit

## Scope

This Phase-2B evidence freezes only CRSP CIZ return, delisting, corporate-action, market-cap, share-outstanding, index, and issuer-data-sufficiency semantics. It creates no canonical panel, no factors, no Compustat PIT policy, and no CRSP–Yahoo bridge.

## Canonical Return Rule

`USE_CIZ_DLYRET_DIRECTLY__DO_NOT_ADD_SEPARATE_DELRET`.

CIZ `DlyRet` is documented as security total return and the official WRDS CIZ event-study macro states that delisting return is already incorporated. In the delivered raw data, all 322 delisting events map to a `DlyDelFlg=Y` daily row; 320 numeric `DelRet` values exactly equal that row's `DlyRet`, while two events have no numeric value in either field. The canonical daily return must therefore use `DlyRet` directly and never add standalone `DelRet`.

## Corporate Actions

`DlyRetx` is the price-only counterpart to `DlyRet`. Distribution samples retain raw values and document that dividends, distributions, and splits must not be manually added when `DlyRet` is used.

## Market Capitalization and Shares

CRSP `DlyCap` is the formal **security-level** raw market-cap source. The deterministic constituent-row reconciliation considered 2,527,062 positive observations; candidate scale 1 is the documented relationship `DlyCap = DlyPrc × ShrOut`, with `DlyCap` and `ShrOut` expressed in thousands. No issuer market cap was built.

`ShrOut` history uses inclusive effective-date intervals (`ShrStartDt` through `ShrEndDt`) and is security-level, in thousands. Daily-stock TAR batches do not carry `ShrOut`; the delivered constituent-daily object does, and is used only for this reconciliation.

## Index Semantics

The selected identities and return fields are audited directly in `gate0_phase2b_index_semantics.csv`: 1000080/DlyTotRet for IVOL market proxy, 1000500/DlyTotRet for formal S&P500 total-return benchmark, and 1000502/DlyPrcRet only as a price-return cross-check.

## Issuer-Level Sufficiency

The current raw delivery is a 959-PERMNO historical-S&P500 extraction rather than a full CRSP equity-class universe. It cannot prove complete relevant-equity-class coverage for the 37 Phase-2A groups. The audit therefore requires, but does not execute, the documented supplemental CRSP extraction before issuer-level market-cap construction.

## Carried-Forward Open Items

- 2,907 `GICS_BOUNDARY_GAP` observations remain open.
- 12,118 blank raw `INDTHRU` encodings remain open.
- Supplemental issuer-class extraction remains required before issuer-level Value denominators.

## Verdict

**Gate 0 Phase 2B — PASS WITH WARNINGS**

**Formal Gate 0 overall: PENDING**

Evidence stage: `FORMAL_GATE0_PHASE2B_CRSP_MARKET_SEMANTICS_AUDIT`.
