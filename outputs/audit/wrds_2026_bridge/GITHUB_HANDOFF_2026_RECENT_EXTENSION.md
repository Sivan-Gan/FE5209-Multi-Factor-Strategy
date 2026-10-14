# GitHub Handoff Audit — 2026 Recent Extension Data and Factor Library

## Audit scope and result

**RESULT: BLOCKED — canonical SQLite delivery policy is not yet authorized.**

This is a read-only handoff audit of the completed 2026
`RECENT_EXTENSION_MIXED_SOURCE` market, Quality / Investment support, and
20-factor security-level library work. It does not authorize or perform Gate B,
Gate C, factor-return research, IC analysis, FMOM, holdings, NAV, or strategy
performance work.

The corrected registry validator now reports **20 / 20 PASS** rows. It derives
validation from the complete 20,121 security-date grid, the 20-factor registry,
18 constructible factors, the two governed-unavailable contracts, Final K=4,
the frozen 40-date calendar, and zero PIT violations. The factor-library SQLite
SHA-256 remains `cd43d408990cb4efa35f03d47a6e99620e1ff6df3b5a078ec729f569b27a9edd`.

One delivery blocker remains: all three completed 2026 canonical SQLite
artifacts are intentionally ignored. None is tracked, none has a Git-LFS
attribute, and the approved LFS rules cover only the existing 20 handoff
Parquet paths. There is no authorized GitHub delivery route for the canonical
data.

No files were staged, committed, or pushed. The narrow repair changed only the
registry-validation writer, its focused test, and the regenerated registry
validation CSV; it did not rebuild or modify canonical factor data.

## Confirmed 2026 handoff state

| Contract item | Read-only result |
|---|---|
| Market daily coverage | 97,587 member-date rows; security and benchmark dates 2026-01-02 through 2026-10-09. |
| Weekly library calendar | 40 dates, 2026-01-02 through 2026-10-02; incomplete 2026-10-09 excluded. |
| Library cardinality | 402,420 observations = 20,121 member-date rows × 20 factors. |
| Registry | Exactly 20 factors. |
| Constructible | 18 with governed missingness. |
| Governed unavailable | `LOWRISK_IVOL`: `CRSP_1000080_2026_SOURCE_UNAVAILABLE`; `DIVIDEND_CURRENT`: `CRSP_DISTRIBUTION_TAXONOMY_NOT_REPRODUCIBLE`. |
| Final K=4 | `QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, `INVESTMENT_CURRENT`. |
| PIT interface | 41,520 support as-of records; zero source-availability-after-signal records. |
| Scope boundary | Security-level factor library only; no factor portfolio returns, FMOM, holdings, NAV/performance, Gate B, or Gate C. |

## Canonical artifacts and Git/LFS status

| Artifact | Bytes | SHA-256 | Git / attribute / LFS | Handoff decision |
|---|---:|---|---|---|
| `data/canonical/wrds_recent_extension/us_recent_extension_market_2026.sqlite` | 77,238,272 | `53f38434b30ae88a97977bded29fcaff47f58e53e6adea3f94d9ecdb72805c1d` | Ignored; attribute unspecified; absent from Git LFS. | Review required: no approved delivery route. |
| `data/canonical/wrds_recent_extension/us_recent_extension_quality_investment_support_2026.sqlite` | 96,604,160 | `45b5c7640d9522060f130f7f89b91aebe14ced751732a03ad63094a372b262a1` | Ignored; attribute unspecified; absent from Git LFS. | Review required: no approved delivery route. |
| `data/canonical/wrds_recent_extension/us_recent_extension_factor_library_2026.sqlite` | 709,926,912 | `cd43d408990cb4efa35f03d47a6e99620e1ff6df3b5a078ec729f569b27a9edd` | Ignored; attribute unspecified; absent from Git LFS. | Review required: no approved delivery route. |

Combined completed-extension canonical size: **883,769,344 bytes**. The
existing `.gitattributes` list is an explicit allow-list for 20 existing
handoff Parquet objects; it provides no SQLite delivery convention. This audit
does not alter `.gitignore`, `.gitattributes`, or LFS policy.

The library also depends on the ignored 2,384,883,712-byte Formal canonical
store `data/canonical/wrds_formal/us_wrds_formal_canonical.sqlite`, SHA-256
`35413a5a0aa4092207a48c99039faacc5c456f83ed9ba4f4593461b2b42bcb55`.
That dependency is outside this 2026 extension commit and needs the existing
formal-data delivery/rebuild pathway.

## Source, tests, documentation, and provenance

Proposed 2026-specific source files are
`src/data/wrds_recent_extension_canonical.py` and
`src/data/wrds_recent_extension_quality_investment_support.py`; their focused
tests are `tests/test_wrds_recent_extension_canonical.py` and
`tests/test_wrds_recent_extension_quality_investment_support.py`.
`docs/formal_wrds/WRDS_2026_RECENT_EXTENSION_CANONICAL_MARKET_LAYER.md`, the
nine `exports/wrds_2026_bridge/` bridge exports, and the 109
`outputs/audit/wrds_2026_bridge/` artifacts are the narrow project-created
provenance/evidence set.

All 109 bridge-audit files are eligible 2026 evidence. The regenerated
registry-validation CSV has 20 / 20 `PASS` rows, and the factor-library
builder plus its focused test are now eligible. The unchanged committed Formal
factor builder `src/factors/build_candidate_factors.py` remains a baseline
dependency; it would not need staging in a 2026-only commit.

The exact file-by-file decision, including every current working-tree file,
the ignored canonical dependencies, and raw sources, is in
`github_handoff_2026_commit_manifest.csv`.

## Licensed raw-data policy

No licensed WRDS or CRSP raw download is required in GitHub. All listed raw
files remain local under `/wrds_raw/`, which current repository policy ignores.
The five market/raw and three Compustat source hashes are also recorded in the
local canonical SQLite `source_manifest` tables; the dividend and CRSP controls
are recorded in the final semantic-closure evidence. A future authorized
handoff should retain the following manifest/hash evidence, not redistribute
the raw files.

| Raw source | Bytes | SHA-256 | Policy |
|---|---:|---|---|
| `wrds_raw/US_WRDS_2026-10-09/compustat/Compustat_Historical_GICS_FullHistory.csv.gz` | 380095 | `3b46fe1099385501d12074e20eb9fba07b9bd2ff2401822eebb06468a31b3a23` | Local-only licensed raw; manifest/hash only |
| `wrds_raw/US_WRDS_2026-10-09/compustat/Compustat_NA_Fundamentals_Annual_2004-01_2026-10.csv.gz` | 13883026 | `10b56a2956626bbcf0b9c70d97632916d1a33cac934023b46d8ab39a0c9aa7b9` | Local-only licensed raw; manifest/hash only |
| `wrds_raw/US_WRDS_2026-10-09/compustat/Compustat_NA_Fundamentals_Quarterly_2004-01_2026-10.csv.gz` | 73136872 | `be7a144c6a23d64f00934a863785795198d6766f8e409687398caee9372a72f3` | Local-only licensed raw; manifest/hash only |
| `wrds_raw/US_WRDS_2026-10-09/crsp/CRSP_Distribution_CIZ_2004-01-01_2025-12-31_959_SP500_PERMNOs.csv.gz` | 669901 | `2cce09c967f1474733b6a0ff691099f6a7083c42cacb036dba13ca18fd046245` | Local-only licensed raw; manifest/hash only |
| `wrds_raw/US_WRDS_2026-10-13/compustat_recent_extension/index_daily/compustat_sp500_index_daily_20250101_20261009_raw.csv.gz` | 14194 | `9a3f1696de7ade505f013a664b60090cf6224cda6f771247364f5493da646d59` | Local-only licensed raw; manifest/hash only |
| `wrds_raw/US_WRDS_2026-10-13/compustat_recent_extension/security_daily/compustat_security_daily_ctva_postspin_20261001_20261009_raw.csv.gz` | 892 | `9b112b7cbe01cd2267c9da9a96edfffb1a25e9b9cca495a7b5833e420c85baa6` | Local-only licensed raw; manifest/hash only |
| `wrds_raw/US_WRDS_2026-10-13/compustat_recent_extension/security_daily/compustat_security_daily_sp500_bridge_20250101_20261011_raw.csv.gz` | 11320560 | `6f3482a72bcf985c1ccdde92eeb8e00d65ec668d102ae012aef6c71a98dffb1b` | Local-only licensed raw; manifest/hash only |
| `wrds_raw/US_WRDS_2026-10-13/compustat_recent_extension/security_daily/compustat_security_daily_vmrk_transition_20260818_20261009_raw.csv.gz` | 1532 | `11fb8ca4fc465fb2adcd124158f3d0cfb3e1899e4af425a307bcbd7f8020de11` | Local-only licensed raw; manifest/hash only |
| `wrds_raw/US_WRDS_2026-10-13/compustat_recent_extension/security_daily/compustat_security_daily_vylr_supplement_20261001_20261009_raw.csv.gz` | 880 | `a58884b8198faa4b9d09e38233c0c423b3cff567a1cecef1c45a2e6d513f6106` | Local-only licensed raw; manifest/hash only |
| `wrds_raw/US_WRDS_2026-10-14/compustat_dividend_validation/compustat_security_daily_caremark_special_followup_raw.csv.gz` | 1736 | `043aca5556fc544ad6fe7657b1f21b29ae8aeb7db449fd7ecd11eeb3903ed53e` | Local-only licensed raw; manifest/hash only |
| `wrds_raw/US_WRDS_2026-10-14/compustat_dividend_validation/compustat_security_daily_dell_special_followup_raw.csv.gz` | 1676 | `946ebc875a3196f7d29525ade5157829c26aa680a11586e33c2353e9c2c04ab0` | Local-only licensed raw; manifest/hash only |
| `wrds_raw/US_WRDS_2026-10-14/compustat_dividend_validation/compustat_security_daily_dividend_validation_4stocks_2024_2025_raw.csv.gz` | 59354 | `8882a762360ac1ba84a8fa42668a91226e97cbc9cdee21a0dfb12a2b0cc361a3` | Local-only licensed raw; manifest/hash only |

## Reproducibility boundary

After pulling a future approved GitHub handoff, a team member can inspect the
source code, focused tests, 2026 membership/provenance exports, and audit
records. They cannot reconstruct or use the security-level 2026 canonical
stores from GitHub alone under the present policy, because those stores are
ignored and not LFS-managed.

With separately authorized local WRDS/CRSP raw inputs, the three 2026 builders
can reconstruct their governed layers subject to the frozen source hashes,
prior Formal canonical dependency, and the audited identity/missingness
artifacts. Licensed raw data itself remains local-only. This handoff ends at
the security-level 2026 factor library and explicitly does not provide 2026
factor portfolio returns, FMOM strategy, holdings, NAV/performance, Gate B,
or Gate C.

## Files excluded from this handoff

The manifest excludes 43 Gate B / Step16 / RA104 and historical
factor-return/downstream files, plus 6 unrelated pre-existing
working-tree files (including `.gitignore`, the frozen SSOT modification, and
other handoff/governance work). None is objectively required to build the
isolated 2026 market, support, or factor-library layers.

## Proposed commit status

| Classification | File count |
|---|---:|
| `INCLUDE — 2026 RECENT EXTENSION` | 125 |
| `REVIEW REQUIRED` | 6 |
| `EXCLUDE — GATE B / STEP16 / RA104` | 43 |
| `EXCLUDE — UNRELATED PRE-EXISTING` | 6 |
| `EXCLUDE — LICENSED RAW` | 12 |

No `git add .` is appropriate. A data-library feature commit is blocked until
the canonical-data delivery policy is resolved under separate authorization.

## Required validation immediately before a future commit/push

Run these after the canonical-data delivery decision:

1. `python -m unittest discover -s tests -p 'test_wrds_recent_extension_*.py'`
2. Rebuild or read-only validate each local 2026 SQLite store, including
   `PRAGMA integrity_check`, canonical source-manifest SHA-256 equality, the
   2026-01-02 to 2026-10-09 market range, and the 40-week / 20-factor
   factor-library contract.
3. Assert the per-factor registry-validation artifact has 20 `PASS` rows;
   assert the two governed-unavailable factors have the exact frozen reasons
   and null score fields; assert Final K=4 has exactly four named factors.
4. If a future authorized Git-LFS route is chosen, run LFS tracking checks and
   a fresh-clone / `git lfs pull` receiver validation for the three SQLite
   artifacts before push. Do not add raw WRDS/CRSP files.
5. Run `git diff --check`, inspect the staged path list against this manifest,
   and confirm that no Gate B / Step16 / RA104, raw, SSOT, or unrelated files
   are staged.

## Suggested future commit message

`feat: add governed 2026 recent-extension factor library`

This message is only appropriate after the canonical-data delivery policy is authorized.
