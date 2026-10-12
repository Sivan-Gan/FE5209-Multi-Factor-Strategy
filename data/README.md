# Local Data Layers

This directory intentionally separates local large research data from Git-delivered contracts and source code.

## Not distributed through Git

- `wrds_raw/` contains licensed WRDS/CRSP/Compustat/CCM/Historical-GICS raw inputs and is outside `data/`.
- `raw_package/` contains the immutable Yahoo/SEC DEV source package.
- `data/canonical/`, `data/factors/`, and `data/validation/` may contain large local SQLite stores used to reproduce frozen upstream stages.

None of those datasets is bundled in a fresh Git clone. Do not commit them or add credentials.

## Final Factor Handoff placement

After approved private transfer, place these three files exactly here:

```text
handoff/final_factor/data/factor_scores_weekly.parquet
handoff/final_factor/data/factor_availability_weekly.parquet
handoff/final_factor/data/universe_weekly.parquet
```

They remain ignored by Git. Verify bytes and SHA256 against [the external transfer manifest](../handoff/final_factor/EXTERNAL_DATA_TRANSFER_MANIFEST.csv). The full interface is defined in [the handoff contract](../handoff/final_factor/FINAL_FACTOR_HANDOFF_CONTRACT.md); the deterministic tracked sample is `handoff/final_factor/data/factor_scores_weekly_sample.csv`.

## Reproduction and limitations

The upstream sequence and frozen source hashes are documented in `handoff/final_factor/reproduction_manifest.csv` and `source_manifest.csv`. Reproduction requires authorized local data access; downstream Strategy Engine work should consume the frozen handoff rather than reconstruct factor research.
