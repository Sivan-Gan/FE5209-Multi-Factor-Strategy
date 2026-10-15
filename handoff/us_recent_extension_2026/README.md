# 2026 Recent Extension Downstream Handoff

## Scope and lineage

This package is a controlled downstream interface for the completed 2026
`RECENT_EXTENSION_MIXED_SOURCE` security-level factor library and minimum market/execution data.
It is separate from the Formal 2006–2025 handoffs and does not replace,
extend, or merge into them.

Market data end: **2026-10-09**. Completed factor signal dates:
**40**, from **2026-01-02** through **2026-10-02**.

The registry has 20 factors: 18 constructible factors and two governed
unavailable factors. The frozen Final K=4 is exactly
`QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, and
`INVESTMENT_CURRENT`.

`LOWRISK_IVOL` remains unavailable with
`CRSP_1000080_2026_SOURCE_UNAVAILABLE`; `DIVIDEND_CURRENT` remains
unavailable with `CRSP_DISTRIBUTION_TAXONOMY_NOT_REPRODUCIBLE`. Their rows
are retained in the factor-library interface with null score fields and their
explicit governed missing reasons. No proxy or replacement is supplied.

## Files and joins

- `data/factor_library_2026.parquet`: long-form weekly-security-factor data,
  naturally keyed by `(signal_date, canonical_security_id, factor_name)`.
- `data/market_2026.parquet`: minimum daily security market/execution data,
  naturally keyed by `(date, canonical_security_id)`.
- `data/benchmark_daily_returns_2026.parquet`: daily Compustat S&P 500
  Comp-Ltd total-return benchmark, naturally keyed by `date`. Its identity is
  `GVKEYX=000003` / `I0003`; its daily return is
  `PRCCDDIV_t / PRCCDDIV_(t-1) - 1`; and it runs through
  **2026-10-09**. It is independent of the security-level
  `market_2026.parquet` interface and is not a constituent aggregation or
  proxy benchmark.
- `factor_registry_2026.csv`: exactly 20 governed factor entries and 2026
  availability status.
- `acceptance.csv`: artifact-derived contract validation.
- `handoff_manifest.csv`: file-level hashes, sizes, rows, schemas, lineage,
  and canonical-source provenance for all payload/document files other than
  the self-referential manifest itself. Its physical hash is recorded in the
  companion audit validation artifact.

Downstream researchers join factor observations to the market interface via
the canonical security identity. `execution_date` on each factor observation
defines the first eligible execution date. Daily `return_available` and
`return_method` remain explicit; no return is filled or substituted.

## Factor-library schema

- `signal_date` — `string`
- `execution_date` — `string`
- `canonical_security_id` — `string`
- `membership_security_id` — `string`
- `gvkey` — `string`
- `ticker` — `string`
- `issuer_key` — `string`
- `issuer_identity_class` — `string`
- `identity_class` — `string`
- `temporal_identity_rule` — `string`
- `history_source` — `string`
- `gics_status` — `string`
- `gsector` — `string`
- `branch` — `string`
- `issuer_market_cap` — `double`
- `factor_name` — `string`
- `factor_family` — `string`
- `final_k4` — `int8`
- `raw_signal` — `double`
- `winsorized_signal` — `double`
- `processed_signal` — `double`
- `neutralized_signal` — `double`
- `final_score` — `double`
- `rank_or_percentile` — `double`
- `rank_status` — `string`
- `component_count` — `int16`
- `availability` — `int8`
- `missing_reason` — `string`
- `components_json` — `string`
- `provenance_json` — `string`
- `source_lineage` — `string`
- `factor_version` — `string`

## Market-interface schema

- `date` — `string`
- `canonical_security_id` — `string`
- `membership_security_id` — `string`
- `gvkey` — `string`
- `iid` — `string`
- `ticker` — `string`
- `cusip` — `string`
- `company_name` — `string`
- `member_flag` — `int8`
- `membership_provenance` — `string`
- `prccd` — `double`
- `ajexdi` — `double`
- `trfd` — `double`
- `cshoc` — `double`
- `daily_return` — `double`
- `market_cap` — `double`
- `return_available` — `int8`
- `return_method` — `string`
- `market_cap_available` — `int8`
- `market_cap_reason` — `string`
- `temporal_identity_rule` — `string`
- `identity_provenance` — `string`
- `source_dataset` — `string`
- `data_lineage` — `string`

## Intentionally not included

The Quality/Investment support SQLite is internal build support only: all
needed score, availability, reason, provenance and lineage outputs are already
present in `factor_library_2026.parquet`. This package contains no raw vendor
rows, canonical SQLite databases, PIT event layer, factor portfolio returns,
FMOM results, strategy weights, holdings, NAV/performance, Gate B, or Gate C.

## Redistribution and Git LFS boundary

Classification: **DERIVED_DATA_PRIVATE_TEAM_GITHUB_APPROVED**.
The project owner explicitly authorizes the following observation-level
derived Parquets for **private team GitHub/LFS delivery only**:

- `data/factor_library_2026.parquet`
- `data/market_2026.parquet`
- `data/benchmark_daily_returns_2026.parquet`

This authorization is not permission for public redistribution. Raw/vendor
data, canonical SQLite databases, and other source artifacts remain excluded.
