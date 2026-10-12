# WRDS Factor Research Archive and GitHub Data Report

## 1. Executive Verdict

**FACTOR ARCHIVE + GITHUB DATA DELIVERY — COMPLETE.** Acceptance: 49 PASS, 0 BLOCKED, 0 FAIL.

## 2. Frozen Research Boundary

The archive preserves 20 frozen specifications. The active Strategy Engine interface remains K=4: `QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, `INVESTMENT_CURRENT`. Archive/research-only and diagnostic specifications remain ineligible for strategy use.

## 3. Git LFS Configuration

Version: `git-lfs/3.8.0 (GitHub; darwin arm64; go 1.27.0)`. `.gitattributes` contains 20 explicit approved-file rules; no wildcard or directory rule is used.

## 4. Approved Derived Data

Files: 20; total bytes: 422615107; largest: `handoff/us_downstream/data/security_daily_market.parquet` (133677806 bytes); >100 MB: 1; >1 GB: 0.

## 5. Pointer and Scope Verification

All approved staged objects are valid LFS pointers with matching SHA-256/size: PASS. Unapproved LFS file count: 0.

## 6. Safety / Immutability

Raw vendor paths, canonical/source SQLite files, SSOT, and protected upstream inputs are excluded from the staged delivery scope. The inherited upstream immutability evidence remains 12/12 PASS. Secret audit: PASS.

## 7. Real GitHub Receiver Validation

Validated read-only against the supplied fresh clone of private GitHub repository `Sivan-Gan/FE5209-Multi-Factor-Strategy`, branch `formal-wrds-rebuild`, commit `cfb02adb015278b25738924e3680bad7da5636c6`. Git LFS version: `git-lfs/3.8.0 (GitHub; darwin arm64; go 1.27.0)`.

The clone was clean and recovered the exact approved LFS scope: 20/20 files, no missing paths, and no unexpected paths. Every receiver working-tree object was real Parquet content (not pointer text) and matched the authoritative delivery manifest's byte size and SHA-256. Aggregate inventory: 422615107 bytes; largest `security_daily_market.parquet` at 133677806 bytes; one object over 100 MB; none over 1 GB.

Receiver smoke: PASS. Research loader exposes exactly 20 archived specifications. Strategy loader exposes exactly K=4: `QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, `INVESTMENT_CURRENT`; no research-only or diagnostic factor enters the Strategy Engine. Receiver focused archive/GitHub/LFS delivery tests: 30/30 PASS.

## 8. Test Boundary

This delivery step does not calculate IC, returns, factor returns, FMOM, portfolios, or strategy results; it does not begin Gate B.

## 9. Evidence-Round Remote Boundary

This validation round did not authenticate to GitHub, create or reconfigure a remote, push, or create a new clone. It used only the already-supplied receiver clone and recorded the successful result locally.
