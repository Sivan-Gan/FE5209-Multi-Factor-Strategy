# GitHub Delivery Repository Inventory

## Scope

This inventory classifies physical delivery-relevant files and collapsed local environment/planning directories. `.git/` is intentionally excluded. No vendor data are copied, opened for research, or staged by this inventory.

## Classification summary

| Classification | Paths |
| --- | ---: |
| A. REQUIRED_FOR_GITHUB_DELIVERY | 156 |
| B. REQUIRED_FOR_REPRODUCTION_BUT_LOCAL_DATA | 12 |
| C. HISTORICAL_AUDIT_KEEP | 319 |
| D. TEMPORARY_OR_REGENERABLE | 6 |
| E. PROPRIETARY_RAW_DATA_DO_NOT_UPLOAD | 530 |

Tracked physical paths at audit time: **421**. Ignored physical paths at audit time: **602**.

## Delivery decision

Licensed WRDS/CRSP/Compustat/CCM/Historical-GICS raw data and the legacy raw source package remain local-only. Final handoff Parquet files are derived interfaces, but no explicit redistribution approval was found in project documentation; they remain Git-ignored with status **DATA_DISTRIBUTION_REVIEW_REQUIRED**. Their separate-transfer hashes are in `handoff/final_factor/EXTERNAL_DATA_TRANSFER_MANIFEST.csv`.

See `github_delivery_inventory.csv` for one row per inventoried path.
