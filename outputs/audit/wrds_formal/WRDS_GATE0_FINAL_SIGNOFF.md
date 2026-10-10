# Formal WRDS Gate 0 — Integrated Final Sign-off

## 1. Executive verdict

**FORMAL GATE 0 — PASS WITH WARNINGS**

Formal Canonical Dataset Construction is now permitted.

## 2. Sign-off history and re-evaluation

Commit `f1831e3011892c8c7e73e2a97143b28a0ab7b813` first recorded Formal Gate 0 as blocked by `COMPUSTAT_RAW_FIELD_UNITS`. That historical conclusion remains preserved: generic WRDS wording that most items are in millions was correctly insufficient because it did not identify exact fields or annual/quarterly scope.

This dated re-evaluation adds authorized official WRDS Compustat North America Data Dictionary item-detail evidence for the exact delivered mnemonics `ni`, `niq`, `ceq`, `ceqq`, `oancf`, `oancfy`, `capx`, and `capxy`. It does not alter raw data, the frozen research manual, timing, factor definitions, or market-cap aggregation rules.

## 3. Frozen scope and evidence lineage

This sign-off reconciles Phase 1, 2A, 2B, 2B.1, 2B.2, and 2C. It creates audit contracts only. It creates no canonical dataset, issuer market cap, factor, IC, portfolio, or backtest.

- Phase 1: raw integrity and schemas.
- Phase 2A: historical universe, CCM linkage, and historical GICS.
- Phase 2B: CIZ returns, delisting/distribution semantics, DlyCap, shares, and benchmark identities.
- Phase 2B.1/2B.2: supplemental issuer share-class coverage.
- Phase 2C: Compustat timing, restatement boundary, flow semantics, TTM capability, and raw-field contract.

## 4. Raw integrity and frozen CRSP denominator

All 19 approved raw files rehash to the existing manifests: original=16, supplemental=3, failures=0.

The frozen CRSP CIZ rule remains: `DlyCap` is security-level market capitalization in thousand USD for the formal USD population. A future issuer denominator may sum only eligible active common-equity security `DlyCap` values in the same thousand-USD scale; this audit constructs no issuer market cap.

## 5. Previously signed universe, return, market-cap, and PIT boundaries

The historical constituent-to-CCM rule is strict and deterministic. Twenty-nine observations remain unmapped, including DVMT/PERMNO 16267; they remain unavailable. Historical GICS has 2,907 boundary gaps and 12,118 blank raw INDTHRU endpoints. No current-GICS backfill is permitted; industry-dependent transforms are unavailable where no single time-valid historical GICS interval exists.

The frozen return rule is `USE_CIZ_DLYRET_DIRECTLY__DO_NOT_ADD_SEPARATE_DELRET`. The Phase 2B delisting, distribution, and split review found no reason for a manual add-on or separate corporate-action return adjustment. The 23 issuer limitations remain deterministically classified: 22 are post-effective-end diagnostics; PERMCO 47011 has five active-class unavailable dates. No active share class may be silently omitted.

Quarterly fundamental records require valid RDQ and become usable only on the next local trading day. Annual records require exact defensible Q4 RDQ evidence; annual datadate is never a publication date. Ordinary delivered Compustat remains labelled publication-date-aligned historical fundamentals, not vintage-perfect PIT.

## 6. Compustat field-level unit conclusion

The new exact item-detail evidence resolves numeric scale for the Gate-0 cross-source numerator scope: `ni` and `niq`, `ceq` and `ceqq`, `oancf` and `oancfy`, and `capx` and `capxy` are each **Millions**. The annual and quarterly/YTD counterparts therefore have authoritatively identical numeric scale for this scope.

The USD record predicate (`INDL + STD + C + curcd/curcdq = USD`) establishes currency denomination, while the WRDS item pages establish numeric scale. These are distinct facts. The formal quarterly-flow rule remains intact: `oancfy` and `capxy` are differenced only within the same fiscal year, and TTM requires four PIT-eligible single quarters. Both transformations preserve Millions.

## 7. Cross-source ratios

### E/P

- Numerator fields: ni (annual); niq (quarterly)
- Denominator: `DlyCap` in thousand USD
- Required conversion: Multiply NI or valid PIT NIQ in USD millions by 1000 to obtain USD thousands.
- Common-unit formula: E/P = ((NI or valid PIT earnings measure) × 1000) / IssuerMarketCap_in_CRSP_thousands.
- Status: **RESOLVED_AUTHORITATIVELY**

### B/P

- Numerator fields: ceq (annual); ceqq (quarterly)
- Denominator: `DlyCap` in thousand USD
- Required conversion: Multiply CEQ or valid PIT CEQQ in USD millions by 1000 to obtain USD thousands.
- Common-unit formula: B/P = (CEQ × 1000) / IssuerMarketCap_in_CRSP_thousands.
- Status: **RESOLVED_AUTHORITATIVELY**

### FCF Yield

- Numerator fields: oancf - capx (annual); oancfy and capxy after frozen same-fiscal-year differencing and eligible four-quarter TTM aggregation
- Denominator: `DlyCap` in thousand USD
- Required conversion: Compute FCF = OANCF - CAPX in USD millions, then multiply the result by 1000 to obtain USD thousands.
- Common-unit formula: FCF Yield = ((OANCF - CAPX) × 1000) / IssuerMarketCap_in_CRSP_thousands; quarterly/TTM uses valid YTD-derived OANCFY and CAPXY in Millions before ×1000.
- Status: **RESOLVED_AUTHORITATIVELY**

## 8. Annual versus quarterly and currency-versus-scale boundaries

For the eight exact raw fields in the newly supplied evidence, annual/quarterly/YTD numeric scale is Millions. In a same-source ratio whose numerator and denominator are both these Compustat monetary fields in Millions, the common scale cancels. That property does not replace the explicit cross-source conversion required for E/P, B/P, or FCF Yield: their Compustat numerator must be multiplied by 1000 before division by CRSP `DlyCap` in USD thousands.

## 9. Other frozen Gate 0 evidence

Annual duplicate groups=25437 and quarterly duplicate groups=451. The standard USD predicate, PIT timing contracts, CIZ return semantics, historical membership, CCM, historical GICS, and issuer-share-class coverage decisions remain as recorded in the existing Phase 1–2C artifacts. No canonical dataset, issuer market cap, factor, IC, portfolio, or backtest has been created.

## 10. Formal sample boundaries

- 2004–2005: initialization / pre-sample buffer.
- 2006–2010: DEV / warm-up.
- 2011–2015: factor selection.
- 2016–2020: configuration selection and strategy freeze.
- 2021–2025.12: Formal Historical OOS.
- 2026.01–2026.09: RECENT_EXTENSION only.
- 2026.10 onward: Live / Forward.

The formal market-data cutoff is **2025-12-31**. No 2026 mixed-source data may enter the formal historical panel or any 2025 signal.

## 11. Remaining warnings

| Issue | Canonical treatment |
|---|---|
| CCM_UNMATCHED_OBSERVATIONS | No fundamental/GICS dependent value for the observation. |
| GICS_BOUNDARY_GAP | Industry-neutral data may remain; industry-dependent transforms are unavailable. |
| GICS_RAW_INDTHRU_BLANK_ENCODING | If no single time-valid interval remains, industry-dependent transforms are unavailable. |
| ISSUER_MARKET_CAP_SUPPLEMENTAL_EXTRACTION | Unavailable only for affected active issuer-date(s); no active class is omitted. |
| COMPUSTAT_ANNUAL_AVAILABILITY_Q4_RDQ | No Q4 evidence means annual observation unavailable. |
| COMPUSTAT_HISTORICAL_VINTAGE_RESTATEMENT_BOUNDARY | Do not claim vintage-perfect PIT. |
| COMPUSTAT_RDQ_MISSINGNESS | Observation unavailable; no timing fill. |

## 12. Blocking-issue count

**0: no outstanding Formal Gate 0 blocker.**

## 13. Exact next permitted step

Formal Canonical Dataset Construction is now permitted. The next authorized stage must implement the existing frozen contracts; no canonical data, market cap, factor, IC, portfolio, or backtest was created by this re-signoff.

## 14. Authoritative sources examined

| Organization | Title | URL / local path | Section / field scope |
|---|---|---|---|
| Wharton Research Data Services | WRDS conditional-query support (Compustat demo) | https://wrds-www.wharton.upenn.edu/demo/step_support/conditional/ | Conditional statements; examples AT and SALE |
| S&P Global Market Intelligence | Extract: Getting Started Overview | https://www.support.marketplace.spglobal.com/content/dam/spglobal/mi/en/documents/marketplace/extract/extract-getting-started-202207.pdf | Pages 3 and 7: Dictionary_Compustat.xlsx and finding item mnemonics/descriptions |
| Wharton Research Data Services | Compustat North America data dictionary access page | https://wrds-www.wharton.upenn.edu/data-dictionary/comp_na_daily_all/ | Access-control response |
| WRDS / Compustat North America | WRDS Compustat North America Data Dictionary — CAPX item detail | WRDS authenticated Data Dictionary: comp_na_daily_all.funda / CAPX item-detail page (authoritative field-level evidence received 2026-10-10) | Product: Compustat North America daily updates; schema: comp_na_daily_all; table: funda; item: CAPX; periodicity: Annual; format: Number; units: Millions. |
| WRDS / Compustat North America | WRDS Compustat North America Data Dictionary — CAPX item detail | WRDS authenticated Data Dictionary: comp_na_daily_all.funda / CAPX item-detail page (authoritative field-level evidence received 2026-10-10) | Product: Compustat North America daily updates; schema: comp_na_daily_all; table: funda; item: CAPX; periodicity: Year-to-Date; format: Number; units: Millions. |
| WRDS / Compustat North America | WRDS Compustat North America Data Dictionary — CEQ item detail | WRDS authenticated Data Dictionary: comp_na_daily_all.funda / CEQ item-detail page (authoritative field-level evidence received 2026-10-10) | Product: Compustat North America daily updates; schema: comp_na_daily_all; table: funda; item: CEQ; periodicity: Annual; format: Number; units: Millions. |
| WRDS / Compustat North America | WRDS Compustat North America Data Dictionary — CEQ item detail | WRDS authenticated Data Dictionary: comp_na_daily_all.funda / CEQ item-detail page (authoritative field-level evidence received 2026-10-10) | Product: Compustat North America daily updates; schema: comp_na_daily_all; table: funda; item: CEQ; periodicity: Quarterly; format: Number; units: Millions. |
| WRDS / Compustat North America | WRDS Compustat North America Data Dictionary — NI item detail | WRDS authenticated Data Dictionary: comp_na_daily_all.funda / NI item-detail page (authoritative field-level evidence received 2026-10-10) | Product: Compustat North America daily updates; schema: comp_na_daily_all; table: funda; item: NI; periodicity: Annual (North America); format: Number; units: Millions. |
| WRDS / Compustat North America | WRDS Compustat North America Data Dictionary — NI item detail | WRDS authenticated Data Dictionary: comp_na_daily_all.funda / NI item-detail page (authoritative field-level evidence received 2026-10-10) | Product: Compustat North America daily updates; schema: comp_na_daily_all; table: funda; item: NI; periodicity: Quarterly (North America); format: Number; units: Millions. |
| WRDS / Compustat North America | WRDS Compustat North America Data Dictionary — OANCF item detail | WRDS authenticated Data Dictionary: comp_na_daily_all.funda / OANCF item-detail page (authoritative field-level evidence received 2026-10-10) | Product: Compustat North America daily updates; schema: comp_na_daily_all; table: funda; item: OANCF; periodicity: Annual; format: Number; units: Millions. |
| WRDS / Compustat North America | WRDS Compustat North America Data Dictionary — OANCF item detail | WRDS authenticated Data Dictionary: comp_na_daily_all.funda / OANCF item-detail page (authoritative field-level evidence received 2026-10-10) | Product: Compustat North America daily updates; schema: comp_na_daily_all; table: funda; item: OANCF; periodicity: Year-to-Date; format: Number; units: Millions. |
| CRSP / Morningstar Indexes | CRSP US Stock & Indexes Database Guide File Format 2.0 (CIZ) | https://indexes.morningstar.com/docs/guide/crsp-us-stock-databases-guide-for-flat-file-format-2-0?isRdp=true | DlyCap; DlyPrc; ShrOut; PERMNO |

## 15. Machine-readable evidence

The issue register, authoritative-evidence table, field-level numeric-units contract, cross-source scale-alignment table, acceptance matrix, and earlier Gate 0 artifacts in this directory are the controlling evidence for this re-evaluation.
