# Formal WRDS Gate 0 Phase 2B.1 — Supplemental CRSP Issuer / Share-Class Extraction Preparation

## Status

**SUPPLEMENTAL_EXTRACTION_READY_FOR_USER_EXECUTION**

This is a preparation-only Gate-0 artifact. It does not open WRDS, download data, construct issuer market capitalization, create canonical data, or begin factor work.

## Why this exists

Phase 2B found that the current 959-PERMNO historical-S&P500 delivery cannot establish that all economically relevant equity share classes are present for its 37 group-level issuer/share-class warnings. The frozen manual requires complete relevant-class coverage before a company-level denominator can be aggregated. The 37 evidence rows coalesce to 45 unique PERMCO query keys; PERMCO is primary and ticker is diagnostic only.

## Confirmed CIZ tables and library

- `crsp.stksecurityinfohist`: security identity, PERMCO, time-valid classification, exchange, and trading-status history.
- `crsp.stkdlysecuritydata`: PERMNO/date security-level daily `DlyCap`, price, return, delisting flag, and volume.
- `crsp.stkshares`: PERMNO shares history, including effective dates, `ShrOut`, source/factor flags, and `ShrAdrFlg`.

Table/library names are supported by the official WRDS CIZ macros and CRSP CIZ reference material recorded in the plan CSV; no table name is inferred from a local filename.

## Formal period and selection boundary

- Coverage requested: `2004-01-01` through `2025-12-31` inclusive.
- The security-history query uses `PERMCO`, time-valid `SecInfoStartDt/SecInfoEndDt`, `SecurityType=EQTY`, `SecuritySubType=COM`, `ShareType ∈ {NS, CE, SB}`, and `IssuerType ∈ {ACOR, CORP, REIT}`.
- `ShareType=AD` (ADR), `UG` (unit), non-EQTY/COM securities, funds, derivatives, and unlisted non-common classifications are excluded at extraction. `USIncFlg` is intentionally not a filter because the documented common-share map includes foreign-incorporated corporate common equity.
- `SecurityActiveFlg` and `TradingStatusFlg` are retrieved but not used as a present-day filter: a class that is now inactive can still have been economically relevant inside the historical period. The later coverage audit must apply time-valid status and record each inclusion/exclusion.
- `ShrAdrFlg` is retained in the raw shares export. A `Y` value is a future coverage-audit rejection / exception condition, never an issuer-market-cap input.

## Exact future raw outputs

1. `wrds_raw/US_WRDS_2026-10-09/crsp/CRSP_Supplemental_Issuer_SecurityInfoHist_2004-01-01_2025-12-31.csv.gz`
2. `wrds_raw/US_WRDS_2026-10-09/crsp/CRSP_Supplemental_Issuer_DailySecurityData_2004-01-01_2025-12-31.csv.gz`
3. `wrds_raw/US_WRDS_2026-10-09/crsp/CRSP_Supplemental_Issuer_Shares_2004-01-01_2025-12-31.csv.gz`

The three provider tables remain separate raw exports rather than a flattened or aggregated derived table. On a future authorized execution, the script writes an audit metadata JSON with exact SQL text, parameters, table/library names, row counts, date coverage, schemas, and SHA256 values.

## Execution control

`python -m src.data.wrds_supplemental_issuer_shareclass_extract` is preparation-only. The user must explicitly authorize and run `--execute` in an authenticated WRDS environment. Existing raw outputs cause a failure rather than overwrite. No credentials are stored in code or configuration.

## Explicitly deferred

- No issuer-level market-cap aggregation or market-cap formula.
- No Value, Size, neutralization, Compustat PIT, factor, IC, portfolio, FMOM, or backtest work.
- No `ISSUER_COVERAGE_COMPLETE` claim. After acquisition, a dedicated coverage audit must classify each issuer as `ISSUER_COVERAGE_COMPLETE`, `ISSUER_COVERAGE_WITH_LIMITATION`, or `ISSUER_COVERAGE_UNRESOLVED` before any aggregation policy is considered.

## References

- [WRDS_CIZ_TO_SIZ_MACRO](https://wrds-www.wharton.upenn.edu/pages/wrds-research/macros/crsp-ciztosiz-macro/): Uses crsp.StkSecurityInfoHist with SecInfoStartDt/SecInfoEndDt and gives the CIZ-to-common-share-code mapping.
- [WRDS_CIZ_IDVOL_MACRO](https://wrds-www.wharton.upenn.edu/pages/wrds-research/macros/wrds-macro-idvol-ciz/): Queries crsp.StkDlySecurityData by PERMNO and DlyCalDt.
- [CRSP_CIZ_SHARE_CODE_REFERENCE](https://www.crsp.org/wp-content/uploads/ShareCode.html): Defines CIZ SecurityType, SecuritySubType, ShareType, IssuerType, and USIncFlg combinations for common-equity codes.
- [CRSP_CIZ_STKSHARES_GUIDE](https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_%26_Indexes_Database_Guide_Flat_File_Format_2.0.pdf): Documents StkShares effective dates, ShrOut, ShrSource, ShrFacType, and ShrAdrFlg.
