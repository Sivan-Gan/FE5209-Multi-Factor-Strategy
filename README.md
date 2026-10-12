# FE5209 — Formal US Quant Research Delivery Repository

## 1. Project Overview

This repository is the controlled team-delivery workspace for the FE5209 US–Singapore cross-market quantitative-research project. It contains the frozen formal-US research implementation, governance evidence, Final Factor Handoff contracts, and the tests needed to begin the next authorized downstream stage without rebuilding factor research.

## 2. Research Objective

The project develops a weekly, long-only US multi-factor strategy and will later test a strictly transferred version in Singapore. The US is the development market; Singapore is an external cross-market validation market. The authoritative research design is the frozen [Research Manual](docs/US_SG_Cross_Market_Quant_Research.md).

## 3. Current Project Status

**Completed:** Formal Gate 0, canonical dataset, candidate-factor construction, factor validation, Gate A0, Gate A, and the Final Factor Handoff.

**Current frozen stage:** Gate A + Final Factor Handoff.

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
  -> Final Factor Handoff (contracts + local execution data)
  -> future authorized Gate B / Strategy Engine
```

The handoff is an interface export of existing frozen scores. It does not calculate factor returns, FMOM, weights, stock composite scores, holdings, portfolios, or strategy results.

## 6. Repository Structure

| Path | Purpose |
| --- | --- |
| `docs/` | SSOT, project status, team handoff, and audit index. |
| `src/` | Canonical formal-US pipeline code and controlled handoff exporter. |
| `tests/` | Native `unittest` regression and delivery checks. |
| `config/` | Approved query inputs and a portable path example. |
| `handoff/final_factor/` | Frozen final-factor interface contracts, manifests, QA, and deterministic sample. |
| `outputs/audit/wrds_formal/` | Complete formal audit trail, including final delivery QA. |
| `data/` | Local large canonical/research stores; see [data/README.md](data/README.md). |
| `wrds_raw/`, `raw_package/` | Local immutable vendor/source evidence; never upload or alter. |

## 7. Quick Start

```bash
git clone <private-repository-url>
cd FE5209
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m unittest tests.test_github_delivery -v
python scripts/smoke_test_handoff.py
```

Then read [the Final Factor Handoff contract](handoff/final_factor/FINAL_FACTOR_HANDOFF_CONTRACT.md), inspect [the registry](handoff/final_factor/final_factor_registry.csv), and load the deterministic tracked sample at `handoff/final_factor/data/factor_scores_weekly_sample.csv`.

## 8. Environment Setup

Use Python 3.12 or a compatible Python 3.12+ environment. See [ENVIRONMENT.md](ENVIRONMENT.md) for dependency scope, platform notes, and the test command. No WRDS credentials belong in this repository.

## 9. Final Factor Handoff

Start at [handoff/final_factor/README.md](handoff/final_factor/README.md). The receiving interface is:

- `factor_scores_weekly.parquet` — four frozen score columns keyed by `(signal_date, PERMNO)`;
- `factor_availability_weekly.parquet` — availability flags, count, and reasons;
- `universe_weekly.parquet` — frozen identity, membership, GICS, market-cap, and execution support fields.

The three Parquet files are deliberately external local data pending redistribution review. Their schema, logical hashes, transfer manifest, and placement instructions are tracked. The smoke script succeeds without them and reports the documented external-data requirement.

## 10. Data Layers

1. **Licensed raw vendor data:** WRDS/CRSP/Compustat/CCM/Historical GICS downloads in local ignored directories.
2. **Local large derived stores:** canonical, factor, and validation SQLite files required to reproduce upstream research.
3. **Final downstream interface:** the governed handoff Parquet files, availability data, and universe data.
4. **Tracked delivery metadata:** contracts, registries, manifests, QA summaries, and a small score sample.

## 11. What Is Included in Git

Code, tests, SSOT and delivery documents, approved configuration/query inputs, Final Factor Handoff contracts, small deterministic samples, audit summaries, and provenance manifests are included.

## 12. What Is Not Included in Git

Licensed raw vendor files, raw Yahoo/SEC source package files, large local SQLite stores, virtual environments, credentials, machine metadata, and unapproved large handoff Parquet exports are excluded. See [LICENSE_OR_DATA_NOTICE.md](LICENSE_OR_DATA_NOTICE.md) and [data/README.md](data/README.md).

## 13. Reproduction Workflow

Upstream reconstruction requires locally authorized data and follows [reproduction_manifest.csv](handoff/final_factor/reproduction_manifest.csv). Downstream users should not rerun upstream construction merely to start Strategy Engine work: consume the frozen handoff after receiving the separately approved local Parquet files.

## 14. Strategy Engine Starting Point

Read the handoff contract, registry, formula dictionary, and downstream interface contract. Join frozen score, availability, and universe data on `(signal_date, PERMNO)`. Do not calculate anything until Gate B is separately authorized.

## 15. Governance Timeline

| Stage | Period | Current use |
| --- | --- | --- |
| Warm-up | 2006–2010 | Pipeline initialization only. |
| Factor Selection | 2011–2015 | Gate A final-factor selection evidence. |
| Configuration Selection | 2016–2020 | Protected for future Gate B/C only. |
| Formal Historical OOS | 2021–2025.12 | Protected OOS; never tune against it. |
| Recent Extension | 2026.01–2026.09 | Operational extension; separate from formal OOS. |

## 16. Protected OOS Periods

`HIST_OOS_BASE` (2021–2025) is not a tuning sample. Out-of-market is not out-of-time: any Singapore evidence does not permit US OOS retuning. Consult [oos_contamination_register.csv](outputs/audit/wrds_formal/oos_contamination_register.csv) before any future authorized analysis.

## 17. Testing

Run the delivery checks first, then the full native suite:

```bash
python -m unittest tests.test_github_delivery -v
python scripts/smoke_test_handoff.py
python -m unittest discover -s tests -p 'test_*.py'
```

## 18. Data Licensing / Sharing Notice

Raw WRDS/CRSP/Compustat/CCM/Historical-GICS data are licensed third-party materials and are not included. Distribution of derived Parquet handoff files is **DATA_DISTRIBUTION_REVIEW_REQUIRED**: use only an institutionally approved private transfer after the applicable agreement is confirmed. This repository does not make legal conclusions or grant data rights.

## 19. Important Do-Not-Change Rules

- Do not modify the SSOT, Gate A/A0 frozen decisions, factor definitions, timing, or final factor set.
- Do not reopen `MOMENTUM_12_1`, add Value/Dividend/Size, or reconstruct factors casually.
- Do not access 2016+ performance, calculate factor returns/FMOM, or run a strategy backtest without separate authorization.
- Do not add vendor raw data, credentials, or large local stores to Git.
- Do not alter `raw_package/` or `wrds_raw/`.

## 20. Team Handoff Checklist

1. Read [PROJECT_STATUS.md](docs/PROJECT_STATUS.md) and [GITHUB_TEAM_HANDOFF.md](docs/GITHUB_TEAM_HANDOFF.md).
2. Read the handoff contract and final registry.
3. Install the documented environment and run the smoke test.
4. Obtain external Parquet files only through an approved private channel; verify the transfer manifest hashes.
5. Start only the separately authorized Gate B / Strategy Engine workflow.
