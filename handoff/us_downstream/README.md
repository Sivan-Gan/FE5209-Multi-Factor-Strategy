# US Downstream Strategy & Backtest Data Handoff

This is the governed US market-data companion to `handoff/final_factor/`. It contains no Gate-B decision, FMOM, holdings, strategy backtest, strategy NAV, or strategy-performance summary.

## Quick start

1. Read `../final_factor/FINAL_FACTOR_HANDOFF_CONTRACT.md` and this package's `US_DOWNSTREAM_DATA_CONTRACT.md`.
2. Load the frozen final factor scores and weekly universe from `handoff/final_factor/data/`.
3. Load `data/weekly_execution_calendar.parquet` for exact `S_t → E_t → E_{t+1}` timing.
4. Load `data/final_factor_returns_seed_weekly.parquet` for only the completed pre-Gate-B 2006–2015 K=4 seed history.
5. At separately authorized Gate B, use the stage-aware loader to load only the 2016–2020 configuration partition.
6. Construct completed-period final-factor returns for 2016–2020 only after that authorization.
7. Run Gate B only under its separate governance; do not tune against 2021–2025.
8. Freeze Gate B.
9. Construct/freeze the US Base at Gate C.
10. Only then use the `HIST_OOS` loader stage to unlock the 2021–2025 market-data partition.

## Delivery and source-data policy

The approved downstream Parquets are part of the 20 approved project-derived Git LFS delivery artifacts. Authorized teammates receive them from the PRIVATE GitHub repository with `git lfs pull` and must verify hashes and sizes against the existing delivery/transfer manifests. Raw vendor/source data remain excluded. GitHub delivery does not alter any Gate-B or OOS access restriction, and deterministic small pre-2016 schema samples remain tracked under `data/samples/`.

## Core files

- `US_DOWNSTREAM_DATA_REQUIREMENT_MATRIX.csv`: object-level requirement and source map.
- `downstream_data_dictionary.csv`: every exported field.
- `downstream_access_contract.csv`: Gate-B/OOS access boundary.
- `downstream_non_data_decisions.csv`: data-ready versus governance-pending decisions.
- `US_DOWNSTREAM_CAPABILITY_MATRIX.csv`: readiness of later M4/M5 capabilities.
- `downstream_acceptance.csv`: construction acceptance evidence.
