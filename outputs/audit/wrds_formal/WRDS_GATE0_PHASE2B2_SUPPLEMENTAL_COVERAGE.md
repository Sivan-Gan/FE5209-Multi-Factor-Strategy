# Formal WRDS Gate 0 Phase 2B.2 — Supplemental CRSP Issuer / Share-Class Coverage Audit

## Verdict

**PASS WITH WARNINGS**

Formal Gate 0 remains **PENDING**. This audit establishes raw issuer/share-class coverage evidence only. It does not construct issuer market capitalization, canonical data, factors, IC, portfolios, or backtests.

## Content-based raw identification

The supplemental files were identified from their actual schemas and exact approved-45-PERMCO content, not trusted merely because of their filenames. The raw files were acquired manually from the official WRDS CRSP CIZ web-query interface; no WRDS PostgreSQL connection was attempted.

| Semantic type | Filename | Rows | Columns | SHA256 pre/post match |
|---|---:|---:|---:|---:|
| DAILY_SECURITY_DATA | `CRSP_Supplemental_Issuer_DailySecurityData_2004-01-01_2025-12-31.csv.gz` | 168,624 | 8 | true |
| SECURITY_INFORMATION_HISTORY | `CRSP_Supplemental_Issuer_SecurityInfoHist_FullHistory.csv.gz` | 504 | 24 | true |
| SHARE_OUTSTANDING_HISTORY | `CRSP_Supplemental_Issuer_ShareOutstanding_FullHistory_to_2025-12.csv.gz` | 11,204 | 11 | true |

## Integrity and scope

- Original Phase-1 raw files: 16 expected; integrity: **PASS**.
- Current vendor raw files: 19 (= 16 original + 3 supplemental); non-vendor filesystem metadata: 1 (`.DS_Store`, not treated as raw data). The Phase-1 manifest remains historical 16-file evidence and was not rewritten.
- Reconciliation: 37 Phase-2B evidence groups → 45 query PERMCOs → 55 mappings; no unexplained query PERMCO was accepted.
- Relevant Phase-2B.1 common-equity classes: 58 PERMNOs. Supplemental data are denominator-coverage evidence only and do not expand the historical S&P 500 investable universe.
- Daily raw duplicate finding: 34 exact duplicate rows across the natural key; raw values remain unchanged and no nonexact key conflict was found.

## Share-history FullHistory check

**FULL_HISTORY_LABEL_CONFIRMED**. The audit checks share intervals active at the formal sample start against 2004-01-01 and checks every observed daily date; it never fabricates a pre-2004 share interval.

## Issuer coverage classification

- `ISSUER_COVERAGE_COMPLETE`: 22
- `ISSUER_COVERAGE_COMPLETE_WITH_LIMITATION`: 23
- `ISSUER_COVERAGE_UNRESOLVED`: 0

## Carried-forward warnings

- 2,907 `GICS_BOUNDARY_GAP` observations remain open.
- 12,118 blank raw `INDTHRU` encodings remain open.
- No current-GICS backfill was applied.

## Explicit boundaries

- CRSP `DlyCap` remains a security-level raw field in thousands; no issuer-level market-cap aggregation was calculated.
- No raw files were renamed, decompressed, modified, or combined.
- No canonical dataset, factor, Forward IC, portfolio, strategy, or Phase 2C artifact is created by this module.
