# GitHub Team Handoff Guide

## 1. Clone and set up

```bash
git clone <private-repository-url>
cd FE5209-Multi-Factor-Strategy
git lfs install
git lfs pull
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Read [ENVIRONMENT.md](../ENVIRONMENT.md) for scope and platform notes. Never commit `.venv/`, secrets, credentials, or local data.

## 2. Repository map

- `docs/US_SG_Cross_Market_Quant_Research.md`: frozen SSOT.
- `docs/PROJECT_STATUS.md`: completed and prohibited stages.
- `handoff/final_factor/`: final K=4 downstream interface.
- `handoff/factor_research_archive/`: complete frozen 20-specification research/audit archive.
- `handoff/us_downstream/`: governed formal-US market-data interface for the future Strategy Engine and Backtest/Risk workflow.
- `outputs/audit/wrds_formal/`: complete formal audit record.
- `src/`: upstream formal pipeline plus controlled handoff exporter.
- `tests/`: native regression and delivery checks.

## 3. First files to read

1. Root [README](../README.md).
2. The SSOT.
3. [Final Factor Handoff contract](../handoff/final_factor/FINAL_FACTOR_HANDOFF_CONTRACT.md).
4. [Final factor registry](../handoff/final_factor/final_factor_registry.csv).
5. [Downstream interface contract](../handoff/final_factor/downstream_interface_contract.csv).
6. [US downstream data contract](../handoff/us_downstream/US_DOWNSTREAM_DATA_CONTRACT.md).
7. [Factor Research Archive contract](../handoff/factor_research_archive/FACTOR_RESEARCH_ARCHIVE_CONTRACT.md).

## 4. Final factor data location

The final factor interface is under `handoff/final_factor/data/`. The Formal Factor Research Archive is under `handoff/factor_research_archive/data/`. The US downstream interface is under `handoff/us_downstream/data/`. Exactly 20 approved project-derived Parquets across these three interfaces are delivered through explicit Git LFS rules in the PRIVATE team repository. After cloning, a teammate runs `git lfs pull` and verifies the delivery manifest; real GitHub receiver validation recovered all 20/20 approved LFS objects. Raw vendor data and source/canonical SQLite databases remain excluded.

Strategy Engine work reads `handoff/final_factor/` plus `handoff/us_downstream/`. Research/audit work may read `handoff/factor_research_archive/`. Use `src/handoff/load_us_downstream_data.py` with an explicit stage—there is no normal OOS bypass. Archive existence does not create an all-20 Strategy Engine path.

## 5. Canonical factor definitions

The only active final specifications are, in order:

1. `QUALITY_CURRENT`
2. `MOMENTUM_12_1`
3. `LOWRISK_CURRENT`
4. `INVESTMENT_CURRENT`

`MOMENTUM_CURRENT`, Value, Dividend, and Size must not be silently substituted or reactivated. Definitions, directions, PIT rules, availability rules, neutralization, timing, and transformations are frozen.

## 6. Canonical code paths

| Responsibility | Authoritative path | Status |
| --- | --- | --- |
| WRDS canonical build | `src/data/wrds_canonical_build.py` | Frozen upstream. |
| Candidate-factor construction | `src/factors/build_candidate_factors.py` | Frozen upstream. |
| Gate A0 score construction | `src/factors/build_gate_a0_factor_specifications.py` | Frozen upstream. |
| Factor validation | `src/validation/build_formal_factor_validation.py` | Frozen upstream. |
| Gate A freeze | `src/validation/freeze_gate_a_final_factor_set.py` | Frozen. |
| Final handoff export | `src/handoff/prepare_final_factor_handoff.py` | Controlled export. |
| Factor research archive export | `src/handoff/prepare_factor_research_archive.py` | Controlled frozen 20-spec archive export. |
| Final/archive loaders | `src/handoff/load_factor_research_archive.py` | Explicit K=4 versus research-only separation. |
| Downstream data export | `src/handoff/prepare_us_downstream_data.py` | Controlled mechanical export. |
| Downstream stage loader | `src/handoff/load_us_downstream_data.py` | Gate-B/OOS governed access. |
| Future Strategy Engine | `src/strategy/` | Not implemented; separate authorization required. |

DEV/Yahoo/SEC code and artifacts remain archived historical evidence; they are not the formal 2006–2025 research path.

## 7. What not to change

Do not modify `raw_package/`, `wrds_raw/`, the SSOT, frozen Gate A/A0 output, factor formulas, factor timing, the final K=4 set, or audit evidence. Do not add licensed raw data or credentials to Git. Do not regenerate upstream factors merely to begin downstream work.

## 8. Next permitted research stage

The next potential stage is a governed Gate B / Strategy Engine workflow, but only after explicit authorization. This delivery task does not authorize any Gate B calculation.

## 9. OOS governance warning

2016–2020 is the future configuration-selection period. 2021–2025 (`HIST_OOS_BASE`) is protected formal OOS. Out-of-market evidence is not out-of-time evidence. Consult [oos_contamination_register.csv](../outputs/audit/wrds_formal/oos_contamination_register.csv) before any authorized use.

## 10. Git LFS delivery troubleshooting

`python scripts/smoke_test_handoff.py` and `python scripts/smoke_test_complete_delivery.py` validate the interfaces. The approved full derived Parquets are already delivered through explicit Git LFS rules; after cloning the PRIVATE repository, run `git lfs pull` and verify the relevant manifest/hash records. This is a delivery operation, not a reconstruction instruction.

The downstream package behaves the same way: use its requirement and transfer manifests, then run its focused test after `git lfs pull`. Never force-add Parquet files or add raw vendor/source data.

## 11. Tests

```bash
python -m unittest tests.test_github_delivery -v
python scripts/smoke_test_handoff.py
python scripts/smoke_test_complete_delivery.py
python scripts/smoke_test_us_downstream.py
python -m unittest discover -s tests -p 'test_*.py'
```

## 12. Responsibility boundaries

The upstream data/PIT, factor engineering, validation, and Gate A responsibilities are frozen and evidenced. The receiving downstream member owns only the separately authorized Strategy Engine work; they must consume the final interface and escalate any required change to the research-governance owner.
