# Formal WRDS Gate 0 Phase 1 — Raw Integrity, Inventory, Schema, and Basic Coverage

## Scope

This report covers physical raw-source integrity, compressed-container readability, schema, and basic raw coverage for `wrds_raw/US_WRDS_2026-10-09/`. It does not construct formal canonical data or settle any Phase-2/3 data-contract semantics.

## Methodology

- Captured SHA256 and byte-size evidence for every physical raw file before the audit.
- Streamed gzip CSVs to EOF and inspected TAR members without permanent extraction.
- Recorded headers, row counts, identifiable date ranges, raw identifiers, and only unambiguous duplicate checks.
- Recomputed the physical raw snapshot after audit-output generation and compared every path, size, and SHA256.

## Raw Inventory Summary

- Physical raw files audited: **16**.
- Required files: **16**; present: **16**; missing: **0**.
- Unexpected physical files: **0**.
- Optional metadata file `CRSP_Selected_Index_Metadata.csv.gz`: **absent (allowed)**.
- TAR gzip batch members: **10** (expected 10).

## Integrity and Schema Findings

- Raw pre/post SHA256 and size equality: **PASS** for all physical raw files.
- Gzip / TAR readability: **PASS**.
- Schema rows recorded: **26**; basic-coverage rows recorded: **4**.
- Query-input PERMNO lines / unique values: **959 / 959**.
- Observed formal raw facts are reported in the CSV inventories; no contrary result was normalized to an expected value.

## Key Raw Coverage

- CRSP daily stock TAR: rows **3930464**; primary identifiers **959**; dates **2004-01-02** to **2025-12-31**; unambiguous-key duplicates **0**.
- CRSP S&P 500 constituents: rows **2527140**; primary identifiers **959**; dates **2006-01-03** to **2025-12-31**; unambiguous-key duplicates **0**.
- CRSP market / S&P 500 indexes: rows **16605**; primary identifiers **3**; dates **2004-01-02** to **2025-12-31**; unambiguous-key duplicates **0**.
- Historical PERMNO query input: rows **959**; primary identifiers **959**; dates **** to ****; unambiguous-key duplicates **0**.
- CCM raw `LINKTYPE` values observed: **LC, LS, LU**.
- Historical-GICS and Fama–French headers/date coverage are recorded in `raw_schema_inventory.csv`; this phase records field presence only and does not freeze their downstream semantics.

## Warnings

- Nonblank raw date cells outside the plain YYYY-MM-DD/YYYYMMDD parser: 652 cells across 1 object(s); no coercion or semantic interpretation was applied (deferred to a later Gate 0 phase)

## Explicitly Deferred Beyond Phase 1

- CRSP CIZ return, corporate-action, and delisting semantics.
- DlyCap / ShrOut economic unit interpretation and issuer-level market-cap construction.
- CCM mapping-policy verification and Historical-GICS effective-date merge validation.
- Compustat annual PIT availability, vintage/restatement semantics, quarterly YTD flow differencing, and field precedence.
- CRSP–Yahoo bridge acceptance thresholds.

## Verdict

**Gate 0 Phase 1 — Raw Integrity & Inventory: PASS WITH WARNINGS**
**Formal Gate 0 overall: PENDING**

Evidence stage for this output: `FORMAL_GATE0_PHASE1_RAW_AUDIT`.
