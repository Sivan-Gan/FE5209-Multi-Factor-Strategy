# WRDS 2026 Recent Extension Downstream Handoff Package

## Result

**TECHNICALLY READY BUT REDISTRIBUTION AUTHORIZATION REQUIRED**.

## Scope

This package is a local mechanical export of the completed 2026 `RECENT_EXTENSION_MIXED_SOURCE` security-level factor library and the minimum daily market/execution interface. It introduces no factor definition, score, raw-data, canonical-data, return, portfolio, FMOM, holding, NAV, Gate B, or Gate C change.

## Contents

- factor-library Parquet: 402420 long-form observations, 20 factors, 40 signal dates, and exact frozen Final K=4.
- market Parquet: 97587 security-day records through 2026-10-09.
- registry CSV: 20 rows; 18 constructible factors and two governed unavailable factors.
- Quality/Investment support SQLite: intentionally internal-only; no full support table is exported.

## Source integrity

- factor-library source SHA-256: `cd43d408990cb4efa35f03d47a6e99620e1ff6df3b5a078ec729f569b27a9edd`.
- market source SHA-256: `53f38434b30ae88a97977bded29fcaff47f58e53e6adea3f94d9ecdb72805c1d`.
- support source SHA-256: `45b5c7640d9522060f130f7f89b91aebe14ced751732a03ad63094a372b262a1`.
- all three sources were byte-identical before and after export.

## Distribution / LFS

`factor_library_2026.parquet` is 16574225 bytes; existing git attribute: `handoff/us_recent_extension_2026/data/factor_library_2026.parquet: filter: unspecified`.
`market_2026.parquet` is 2514326 bytes; existing git attribute: `handoff/us_recent_extension_2026/data/market_2026.parquet: filter: unspecified`.
Neither path has an approved existing Git LFS rule. No `.gitattributes` change was made. A future explicit authorization would need narrow proposed rules:
```text
handoff/us_recent_extension_2026/data/factor_library_2026.parquet filter=lfs diff=lfs merge=lfs -text
handoff/us_recent_extension_2026/data/market_2026.parquet filter=lfs diff=lfs merge=lfs -text
```
The project owner must separately authorize redistribution/delivery before any GitHub staging, commit, or transfer.

## Acceptance

- `MARKET_END_DATE`: PASS — Observed market end is 2026-10-09
- `FACTOR_SIGNAL_END_DATE`: PASS — Observed factor signal end is 2026-10-02
- `SIGNAL_DATE_COUNT`: PASS — Observed distinct signal dates: 40
- `FACTOR_COUNT`: PASS — Observed factors: 20
- `CONSTRUCTIBLE_FACTOR_COUNT`: PASS — Derived from exported registry availability status.
- `FINAL_K4_COUNT`: PASS — Observed Final K=4 count: 4
- `FINAL_K4_IDENTITY`: PASS — Exact frozen final-factor order and identity.
- `UNAVAILABLE_FACTOR_COUNT`: PASS — Observed governed unavailable factors: 2
- `UNAVAILABLE_FACTOR_REASONS`: PASS — Must retain governed reason codes exactly.
- `FACTOR_LIBRARY_ROW_COUNT`: PASS — Observed rows: 402420
- `DUPLICATE_NATURAL_KEYS`: PASS — Factor key is (signal_date, canonical_security_id, factor_name); market key is (date, canonical_security_id).
- `LOOKAHEAD_CONTRACT`: PASS — PIT registry validation must pass; execution cannot precede signal; outcomes/strategy columns are absent.
- `LINEAGE`: PASS — Each exported row retains RECENT_EXTENSION_MIXED_SOURCE lineage.
- `SOURCE_CANONICAL_HASH_MATCH`: PASS — All source canonical SQLite stores are byte-identical before and after export.
- `UNAVAILABLE_NULL_CONTRACT`: PASS — Unavailable score fields remain null with governed reasons; no proxy is exported.
- `NO_STRATEGY_OUTPUTS`: PASS — No factor returns, FMOM, portfolios, holdings, NAV, or strategy output is included.
- `SUPPORT_LAYER_BOUNDARY`: PASS — Support SQLite is internal build support only and is not exported.

## Explicit exclusions

No raw/vendor rows, canonical SQLite, support SQLite, factor portfolio returns, FMOM, strategy weights, holdings, NAV/performance, Gate B, or Gate C artefact is included.
