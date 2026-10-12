# Formal Factor Research Archive

## 1. Purpose

This is the complete governed archive of the 20 formally researched US factor specifications. It preserves frozen score history, availability, decisions, and already-produced 2011–2015 evidence.

## 2. Why the archive exists

It makes the Gate A0 and Gate A decision chain reproducible without treating every historical specification as a live strategy input.

## 3. Difference from Final Factor Handoff

`handoff/final_factor/` is the sole K=4 Strategy Engine interface. This directory is a research and audit archive. **DATA EXISTS ≠ FACTOR IS ACTIVE.**

## 4. The 20 formal specifications

See `all_factor_spec_registry.csv`. There are five Value, six Quality, three Momentum, three Low Risk, and one each Investment, Dividend, and Size specifications.

## 5. Gate A0 eligible vs diagnostic-only

Four component diagnostics are preserved: `VALUE_FCF_COMPONENT`, `QUALITY_GPA_COMPONENT`, `QUALITY_NEG_LEVERAGE_COMPONENT`, and `QUALITY_ROA_COMPONENT`. They were not whole-market family-representative candidates.

## 6. Gate A0 selections

The frozen representatives are `VALUE_CURRENT`, `QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, `INVESTMENT_CURRENT`, `DIVIDEND_CURRENT`, and `SIZE_CURRENT`. Round 2 is not authorized.

## 7. Gate A decisions

Quality, Momentum 12-1, Low Risk, and Investment are KEEP. Value, Dividend, and Size are DROP. See `gate_a_decision_registry.csv`.

## 8. Final K=4

Only `QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, and `INVESTMENT_CURRENT` are final active factors, Base Strategy eligible, and FMOM-universe eligible after Gate B authorization.

## 9. Score history

`data/all_factor_scores_weekly.parquet` is keyed by `(signal_date, PERMNO)` and contains frozen scores only. It has no forward-return, factor-return, portfolio, FMOM, or strategy columns.

## 10. Evidence history

`factor_research_evidence_2011_2015.csv` normalizes existing frozen Gate A0/Gate A evidence only. Empty metrics mean no frozen standalone metric existed; they are not newly calculated.

## 11. How to load archive data

Use `src.handoff.load_factor_research_archive.load_factor_research_archive()`. Use `load_final_factor_scores()` for strategy-facing K=4 data instead.

## 12. Allowed research uses

Audit, reproducibility, factor-selection explanation, historical reporting, and only pre-authorized robustness checks.

## 13. Prohibited strategy uses

The archive must not automatically feed a Base Strategy, Gate B, FMOM, parameter search, new factor selection, or a strategy backtest.

## 14. Reproduction lineage

Source scores come read-only from `data/factors/us_formal/us_gate_a0_factor_specifications.sqlite`; decisions come from the frozen Gate A0/Gate A artifacts listed in `factor_archive_manifest.csv`.

## 15. Relationship to Gate B

Gate B remains `NOT_STARTED`. This archive neither authorizes it nor supplies an automatic all-20-factor strategy path.
