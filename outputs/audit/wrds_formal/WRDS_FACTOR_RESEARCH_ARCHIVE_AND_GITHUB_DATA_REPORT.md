# WRDS Factor Research Archive and GitHub Data Report

## 1. Executive Verdict

**FACTOR ARCHIVE + GITHUB DATA DELIVERY — BLOCKED.** Acceptance: 48 PASS, 1 BLOCKED, 0 FAIL.

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

## 7. Receiver Validation

Isolated local LFS receiver validation: BLOCKED. The procedure creates a temporary non-protected reference, clones it with `--no-local`, verifies all 20 actual Parquet hashes, runs the receiver smoke script, and removes its temporary artifacts.

## 8. Test Boundary

This delivery step does not calculate IC, returns, factor returns, FMOM, portfolios, or strategy results; it does not begin Gate B.

## 9. Remote / Push

Remote created: no. Push performed: no.
