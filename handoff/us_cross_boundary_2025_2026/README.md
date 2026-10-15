# 2025–2026 Cross-Boundary Execution Bridge

## Sole purpose

This package supplies the sole completed weekly execution interval between the
frozen Formal 2006–2025 handoff and the governed 2026 Recent Extension. It
does not redefine or replace Formal history.

- Historical Formal last signal: `2025-12-26`
- Bridge execution interval: `(2025-12-29, 2026-01-05]`
- 2026 successor signal: `2026-01-02`

The holding-date return path contains exactly `2025-12-30`, `2025-12-31`,
`2026-01-02`, and `2026-01-05`. The 2025-12-29 execution-date return is
excluded. The 2026-01-01 market holiday and 2026-01-03/04 weekend are not
missing observations.

## Downstream concatenation order

1. `handoff/us_downstream`
2. `handoff/us_cross_boundary_2025_2026`
3. `handoff/us_recent_extension_2026`

## Delivered artifacts

- `data/weekly_execution_calendar_bridge.parquet`: one completed bridge calendar row.
- `data/security_weekly_execution_returns_bridge.parquet`: 503 approved
  historical-identity security rows.
- `data/benchmark_weekly_execution_return_bridge.parquet`: Compustat S&P 500
  Comp-Ltd (`GVKEYX=000003`, `I0003`) total return from `PRCCDDIV`.
- `data/risk_free_weekly_return_bridge.parquet`: Fama–French decimal RF.
- `identity_mapping_audit.csv`: approved historical CCM identity evidence.
- `acceptance.csv`: delivery validation checks.
- `handoff_manifest.csv`: hashes, schemas, provenance, and redistribution boundary.

## Data rules

Historical membership is taken only from the Formal 2025-12-26 eligible
universe. Identity uses approved historical CCM evidence, never ticker-only
inference, current-identifier backfill, arbitrary IID selection, or
name-based matching. Security total returns preserve the frozen
`PRCCD / AJEXDI * TRFD` method and its pre-authorized structural-null
recovery only. The benchmark is not a proxy or constituent aggregation. RF
is a decimal daily series compounded over the same four holding dates with no
`/100` conversion.

## Delivery boundary

Classification: **DERIVED_DATA_PRIVATE_TEAM_GITHUB_APPROVED**. These derived
artifacts are authorized for private team GitHub/LFS delivery only; this is
not authorization for public redistribution. No raw/vendor file, SQLite
database, or unrelated 2006–2026 duplicate interface is included.

## Validation summary

- Requested and exact approved identities: 503
- Ambiguous identities: 0
- Unmapped identities: 0
- Available security returns: 503
- Benchmark bridge return: `-0.0002199650711303125`
- Risk-free bridge return: `0.0008002400320015202`
- Acceptance: `22/22` PASS
