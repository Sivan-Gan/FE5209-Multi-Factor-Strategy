# FE5209 — Formal US Quant Research Delivery Repository

## 1. Project Overview

This repository is the controlled team-delivery workspace for the FE5209 US–Singapore cross-market quantitative-research project. It contains the frozen formal-US research implementation, governance evidence, Final Factor Handoff contracts, and the tests needed to begin the next authorized downstream stage without rebuilding factor research.

## 2. Research Objective

The project develops a weekly, long-only US multi-factor strategy and will later test a strictly transferred version in Singapore. The US is the development market; Singapore is an external cross-market validation market. The authoritative research design is the frozen [Research Manual](docs/US_SG_Cross_Market_Quant_Research.md).

## 3. Current Project Status

**Completed:** Formal Gate 0, canonical dataset, candidate-factor construction, factor validation, Gate A0, Gate A, the Final Factor Handoff, the Formal Factor Research Archive, and the governed US downstream Strategy/Backtest data handoff.

**Current frozen stage:** Gate A + Final Factor Handoff; downstream data are mechanically materialized and stage-governed.

**Next research stage:** Gate B / Strategy Engine, only after separate authorization. Gate B, FMOM configuration, base strategy construction, 2016–2020 configuration evaluation, 2021–2025 formal OOS evaluation, Singapore transfer, and adaptation are not started in this delivery task.

## 4. Frozen Final Factor Set

```text
FINAL_FACTOR_SET_US = {
  QUALITY_CURRENT,
  MOMENTUM_12_1,
  LOWRISK_CURRENT,
  INVESTMENT_CURRENT
}
K = 4
```

Canonical order: **Quality**, **Momentum**, **Low Risk**, **Investment**. `VALUE_CURRENT`, `DIVIDEND_CURRENT`, and `SIZE_CURRENT` are retained only as historical research evidence and are not active Base Strategy factors. The governing registry is [gate_a_final_factor_registry.csv](outputs/audit/wrds_formal/gate_a_final_factor_registry.csv).

## 5. Project Architecture

```text
Licensed WRDS raw data (local, not Git)
  -> Gate 0 / canonical data (local large stores)
  -> candidate factors / Gate A0 / Gate A (frozen evidence)
  -> Final Factor Handoff (frozen K=4 interface)
  -> Formal Factor Research Archive (frozen 20-spec research/audit interface)
  -> US downstream data handoff (calendar / return / liquidity / benchmark interfaces)
  -> future authorized Gate B / Strategy Engine
```

The handoffs are governed interfaces, not Strategy Engine research. The downstream data package contains only a frozen 2006–2015 final-factor L/S seed return history; it makes no Gate B decision and calculates no FMOM, weights, stock composite scores, holdings, portfolios, NAV, or strategy results.

## 6. Repository Structure

| Path | Purpose |
| --- | --- |
| `docs/` | SSOT, project status, team handoff, and audit index. |
| `src/` | Canonical formal-US pipeline code and controlled handoff exporter. |
| `tests/` | Native `unittest` regression and delivery checks. |
| `config/` | Approved query inputs and a portable path example. |
| `handoff/final_factor/` | Frozen final-factor interface contracts, manifests, QA, and deterministic sample. |
| `handoff/factor_research_archive/` | Frozen 20-specification research/audit archive; never an automatic Strategy Engine input. |
| `handoff/us_downstream/` | Governed local-US calendar, return, liquidity, benchmark, RF/FF, reference, and pre-Gate-B seed data contract. |
| `outputs/audit/wrds_formal/` | Complete formal audit trail, including final delivery QA. |
| `data/` | Local large canonical/research stores; see [data/README.md](data/README.md). |
| `wrds_raw/`, `raw_package/` | Local immutable vendor/source evidence; never upload or alter. |

## 7. Quick Start

```bash
git clone <private-repository-url>
cd FE5209
git lfs install
git lfs pull
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m unittest tests.test_github_delivery -v
python scripts/smoke_test_handoff.py
```

Then read [the Final Factor Handoff contract](handoff/final_factor/FINAL_FACTOR_HANDOFF_CONTRACT.md), inspect [the registry](handoff/final_factor/final_factor_registry.csv), and load the deterministic tracked sample at `handoff/final_factor/data/factor_scores_weekly_sample.csv`.

For private-team delivery after Git LFS is installed and the approved derived-data commit exists, `git lfs pull` receives the Final Factor, Formal Factor Research Archive, and US Downstream Parquet interfaces. No GitHub URL is implied before a remote is created.

## 8. Environment Setup

Use Python 3.12 or a compatible Python 3.12+ environment. See [ENVIRONMENT.md](ENVIRONMENT.md) for dependency scope, platform notes, and the test command. No WRDS credentials belong in this repository.

## 9. Final Factor Handoff

Start at [handoff/final_factor/README.md](handoff/final_factor/README.md). The receiving interface is:

- `factor_scores_weekly.parquet` — four frozen score columns keyed by `(signal_date, PERMNO)`;
- `factor_availability_weekly.parquet` — availability flags, count, and reasons;
- `universe_weekly.parquet` — frozen identity, membership, GICS, market-cap, and execution support fields.

The three Parquet files are approved project-derived private-team delivery data and are tracked through explicit Git LFS pointers. Their schema, logical hashes, transfer manifest, and placement instructions are tracked. Raw vendor/source data remain excluded.

## 10. Formal Factor Research Archive

Start at [the archive README](handoff/factor_research_archive/README.md). It contains all 20 frozen formal specifications, score/availability history, Gate A0/Gate A decisions, and only already-produced 2011–2015 evidence. It is for audit and reproducibility. **Only `handoff/final_factor/` defines the K=4 Base Strategy universe.**

## 11. Data Layers

1. **Licensed raw vendor data:** WRDS/CRSP/Compustat/CCM/Historical GICS downloads in local ignored directories.
2. **Local large derived stores:** canonical, factor, and validation SQLite files required to reproduce upstream research.
3. **Final-factor interface:** frozen score, availability, and universe Parquet files.
4. **Formal factor-research archive:** 20-spec score/availability Parquet plus frozen decisions and evidence.
5. **US downstream interface:** local daily/weekly market, liquidity, benchmark, RF/FF, reference, and 2006–2015 seed Parquet files.
6. **Tracked delivery metadata:** contracts, registries, manifests, QA summaries, and deterministic samples.

## 12. What Is Included in Git

Code, tests, SSOT and delivery documents, approved configuration/query inputs, Final Factor Handoff contracts, Formal Factor Research Archive contracts, small deterministic samples, audit summaries, and provenance manifests are included. Exactly 20 approved project-derived Parquet artifacts are included only through explicit narrow Git LFS rules.

## 13. What Is Not Included in Git

Licensed raw vendor files, raw Yahoo/SEC source package files, large local SQLite stores, virtual environments, credentials, and machine metadata are excluded. Derived handoff Parquets must never be committed through ordinary Git; only the explicit approved paths are Git-LFS tracked. See [LICENSE_OR_DATA_NOTICE.md](LICENSE_OR_DATA_NOTICE.md) and [data/README.md](data/README.md).

## 14. Reproduction Workflow

Upstream reconstruction requires locally authorized data and follows [reproduction_manifest.csv](handoff/final_factor/reproduction_manifest.csv). Downstream users should not rerun upstream construction merely to start Strategy Engine work: consume the frozen handoffs after receiving the separately approved local Parquet files.

## 15. Strategy Engine Starting Point

Read both handoff contracts, registry, formula dictionary, and the downstream access contract. Join frozen score, availability, and universe data on `(signal_date, PERMNO)`. The stage-aware loader permits only pre-2021 data for Gate B; it blocks Historical OOS until future Gate-B and Gate-C freeze evidence exists. Do not calculate anything until Gate B is separately authorized.

## 16. Governance Timeline

| Stage | Period | Current use |
| --- | --- | --- |
| Warm-up | 2006–2010 | Pipeline initialization only. |
| Factor Selection | 2011–2015 | Gate A final-factor selection evidence. |
| Configuration Selection | 2016–2020 | Protected for future Gate B/C only. |
| Formal Historical OOS | 2021–2025.12 | Protected OOS; never tune against it. |
| Recent Extension | 2026.01–2026.09 | Operational extension; separate from formal OOS. |

## 17. Protected OOS Periods

`HIST_OOS_BASE` (2021–2025) is not a tuning sample. Out-of-market is not out-of-time: any Singapore evidence does not permit US OOS retuning. Consult [oos_contamination_register.csv](outputs/audit/wrds_formal/oos_contamination_register.csv) before any future authorized analysis.

## 18. Testing

Run the delivery checks first, then the full native suite:

```bash
python -m unittest tests.test_github_delivery -v
python scripts/smoke_test_handoff.py
python scripts/smoke_test_complete_delivery.py
python scripts/smoke_test_us_downstream.py
python -m unittest discover -s tests -p 'test_*.py'
```

## 19. Data Licensing / Sharing Notice

Raw WRDS/CRSP/Compustat/CCM/Historical-GICS data are licensed third-party materials and are not included. Project-generated derived handoff data are **DERIVED_DATA_PRIVATE_TEAM_GITHUB_APPROVED** only for the private team repository and only via Git LFS after it is installed/configured. **RAW_VENDOR_DATA_DISTRIBUTION_NOT_AUTHORIZED** remains in force. This repository does not make legal conclusions or grant vendor-data rights.

## 20. Important Do-Not-Change Rules

- Do not modify the SSOT, Gate A/A0 frozen decisions, factor definitions, timing, or final factor set.
- Do not reopen `MOMENTUM_12_1`, add Value/Dividend/Size, or reconstruct factors casually.
- Do not access 2016+ performance, calculate factor returns/FMOM, or run a strategy backtest without separate authorization.
- Do not add vendor raw data, credentials, or large local stores to Git.
- Do not alter `raw_package/` or `wrds_raw/`.

## 21. Team Handoff Checklist

1. Read [PROJECT_STATUS.md](docs/PROJECT_STATUS.md) and [GITHUB_TEAM_HANDOFF.md](docs/GITHUB_TEAM_HANDOFF.md).
2. Read the handoff contract and final registry.
3. Install Git LFS, clone, and run `git lfs pull` only after the approved derived-data LFS commit exists.
4. Install the documented environment and run the smoke tests.
5. Start only the separately authorized Gate B / Strategy Engine workflow.
