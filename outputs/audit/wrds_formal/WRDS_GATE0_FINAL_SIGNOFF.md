# Formal WRDS Gate 0 — Integrated Final Sign-off

## 1. Executive verdict

**FORMAL GATE 0 — BLOCKED**

The evidence audit is complete and reproducible, but Formal Canonical Dataset Construction is **not permitted**. The single blocker is unresolved authoritative numeric-unit and scale alignment between Compustat accounting fields and CRSP issuer market capitalization. No multiplier, currency conversion, or other scale assumption has been inferred.

## 2. Scope

This sign-off reconciles Phase 1, 2A, 2B, 2B.1, 2B.2, and 2C. It creates audit contracts only. It creates no canonical dataset, issuer market cap, factor, IC, portfolio, or backtest.

## 3. Evidence lineage by phase

- Phase 1: raw integrity and schemas.
- Phase 2A: historical universe, CCM linkage, and historical GICS.
- Phase 2B: CIZ returns, delisting/distribution semantics, DlyCap, shares, and benchmark identities.
- Phase 2B.1/2B.2: supplemental issuer share-class coverage.
- Phase 2C: Compustat timing, restatement boundary, flow semantics, TTM capability, and raw-field contract.

## 4. Raw integrity status

All 19 expected raw files rehash to their approved manifests: original=16, supplemental=3, failures=0.

## 5. Universe, CCM, and historical GICS

The historical constituent-to-CCM rule is strict and deterministic. Twenty-nine observations remain unmapped, including DVMT/PERMNO 16267; they remain unavailable. Accepted link types/priorities and raw LINKENDDT `E` stay frozen. Historical GICS has 2,907 boundary gaps and 12,118 blank raw INDTHRU endpoints. No current-GICS backfill is permitted; industry-dependent transforms are unavailable where no single time-valid historical GICS interval exists.

## 6. CRSP returns and delisting

The frozen return rule is `USE_CIZ_DLYRET_DIRECTLY__DO_NOT_ADD_SEPARATE_DELRET`. The Phase 2B delisting, distribution, and split review found no reason for a manual add-on or separate corporate-action return adjustment.

## 7. Market cap and issuer share classes

DlyCap remains the future security-level market-cap input. A later issuer cap may sum only economically relevant included active common-equity DlyCap values. The 23 issuer limitations are now classified deterministically: 22 are post-effective-end diagnostics; PERMCO 47011 has five active-class dates that must be unavailable. No active share class may be silently omitted.

## 8. Compustat PIT timing and annual availability

Quarterly records require a valid RDQ and become usable only on the next local trading day. Annual records require exact defensible Q4 RDQ evidence; annual datadate is never a publication date. Missing, invalid, conflicting, or non-trading-day timing evidence yields unavailable, not a convenience reporting lag.

## 9. Restatement boundary, quarterly flows, and TTM

Ordinary delivered Compustat is labelled publication-date-aligned historical fundamentals, not vintage-perfect PIT. OANCFY and CAPXY may be differenced only within a fiscal year; TTM requires four consecutive eligible single quarters. Neither rule authorizes filling missing predecessors or source rows.

## 10. Compustat conflict policy

Annual duplicate groups=25437 and quarterly duplicate groups=451. The final rule retains a row only if the standard USD predicate leaves exactly one candidate. The raw profile records that POPSRC is not delivered. All remaining no/multiple-candidate observations are unavailable, never selected by dataframe order.

## 11. Numeric units and cross-source scaling

The delivered raw headers do not supply authoritative Compustat item units. Same-source ratios can be scale-invariant, but E/P, B/P, FCF Yield, and dividend yield combine Compustat accounting values with CRSP issuer market capitalization and therefore require explicit cross-source alignment. The required authoritative item dictionary is absent from the project and from the bundled Phase 2C sources. This is a blocker, not a warning.

## 12. Formal sample boundaries

- 2004–2005: initialization / pre-sample buffer.
- 2006–2010: DEV / warm-up.
- 2011–2015: factor selection.
- 2016–2020: configuration selection and strategy freeze.
- 2021–2025.12: Formal Historical OOS.
- 2026.01–2026.09: RECENT_EXTENSION only.
- 2026.10 onward: Live / Forward.

The formal market-data cutoff is **2025-12-31**. No 2026 mixed-source data may enter the formal historical panel or any 2025 signal.

## 13. Remaining-warning table

| Issue | Canonical treatment |
|---|---|
| CCM_UNMATCHED_OBSERVATIONS | No fundamental/GICS dependent value for the observation. |
| GICS_BOUNDARY_GAP | Industry-neutral data may remain; industry-dependent transforms are unavailable. |
| GICS_RAW_INDTHRU_BLANK_ENCODING | If no single time-valid interval remains, industry-dependent transforms are unavailable. |
| ISSUER_MARKET_CAP_SUPPLEMENTAL_EXTRACTION | Unavailable only for affected active issuer-date(s); no active class is omitted. |
| COMPUSTAT_ANNUAL_AVAILABILITY_Q4_RDQ | No Q4 evidence means annual observation unavailable. |
| COMPUSTAT_HISTORICAL_VINTAGE_RESTATEMENT_BOUNDARY | Do not claim vintage-perfect PIT. |
| COMPUSTAT_RDQ_MISSINGNESS | Observation unavailable; no timing fill. |

## 14. Blocking-issue count

**1**: `COMPUSTAT_RAW_FIELD_UNITS`.

## 15. Canonical construction readiness

All non-unit contracts now specify deterministic source, timing, conflict, availability, and missing-data behavior. Nevertheless, a factor-ready formal canonical layer cannot be trusted until the required cross-source numeric scale is evidenced.

## 16. Exact permitted next step

Obtain and retain authoritative Compustat item-unit documentation and explicit Compustat-to-CRSP scale-alignment evidence, then rerun this integrated final sign-off.

## 17. Exact prohibited next steps before review

Do not construct the formal canonical dataset, issuer market cap, factors, IC, portfolio, backtest, Gate A evidence, or any mixed 2026 formal-history output. Do not modify raw files or the frozen SSOT.

## 18. Machine-readable evidence

The issue register, issuer assessment, numeric-units contract, conflict policy, PIT contract, canonical construction contract, and acceptance matrix in this directory are the controlling audit artifacts for this decision.
