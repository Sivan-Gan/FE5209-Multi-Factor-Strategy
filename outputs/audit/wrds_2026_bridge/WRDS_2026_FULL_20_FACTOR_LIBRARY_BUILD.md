# WRDS 2026 Full 20-Factor Research Library Build

## Result

**2026 FULL 20-FACTOR LIBRARY PASS WITH EXPECTED UNAVAILABLE FACTORS**.

## Scope

This is a separate `RECENT_EXTENSION_MIXED_SOURCE` 2026 security-level research library. It does not modify or merge the frozen Formal 2006–2025 factor store.

## Registry and timing

The complete frozen 20-factor registry is retained at every approved weekly signal date. The completed calendar has 40 dates from 2026-01-02 through 2026-10-02; the incomplete 2026-10-09 week is excluded.

Final K=4 is exactly `QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, and `INVESTMENT_CURRENT`.

## Preserved semantics

The build reuses Formal same-date 1%/99% winsorization, z-scoring, branch rules, component minima, historical-GICS and issuer-cap neutralization, and PIT source precedence. Fundamental source selection retains RDQ-to-next-local-trading-day timing and annual exact-Q4 availability. Market-history inputs use approved Formal-CRSP-to-extension identity evidence only; no ticker/GVKEY inference or return fill is used.

## Governed unavailable factors

`LOWRISK_IVOL` is retained with null raw/processed/neutralized/final/rank fields at every observation and reason `CRSP_1000080_2026_SOURCE_UNAVAILABLE`. `DIVIDEND_CURRENT` is retained identically with reason `CRSP_DISTRIBUTION_TAXONOMY_NOT_REPRODUCIBLE`. No proxy, replacement source, or heuristic is used.

## Scores and rank

`final_score` is the frozen standardized score or residual-z. `rank_or_percentile` is deliberately null: Formal semantics contain no rank field, so a rank is not silently substituted for the score.

## Validation

- `REGISTRY_EXACT_20`: PASS
- `FINAL_K4_EXACT_4`: PASS
- `SIGNAL_CALENDAR_40`: PASS
- `NO_DUPLICATE_NATURAL_KEYS`: PASS
- `NO_UNSUPPORTED_IDENTITY`: PASS
- `MISSING_REASON_COMPLETE`: PASS
- `GOVERNED_UNAVAILABLE_NULL`: PASS
- `K4_EXPECTED_COVERAGE`: PASS
- `PIT_AVAILABILITY`: PASS
- `PIT_LINEAGE`: PASS
- `NO_IMPLICIT_RANK_SUBSTITUTION`: PASS
- `NO_OUTCOME_OR_STRATEGY_COLUMNS`: PASS
- `OBSERVATION_COUNT`: PASS

## Explicitly not performed

No factor-return analysis, IC, portfolio construction, FMOM/RA-FMOM, Gate B/C decision, holdings, transaction cost, NAV, or strategy-performance calculation was run.
