# Formal Factor Research Archive Contract

## Frozen counts

```text
FORMAL_ARCHIVED_SPEC_COUNT = 20
FINAL_ACTIVE_SPEC_COUNT = 4
FINAL_ACTIVE = {
QUALITY_CURRENT,
MOMENTUM_12_1,
LOWRISK_CURRENT,
INVESTMENT_CURRENT
}
```

The remaining 16 specifications are retained only for audit, research reproducibility, factor-selection explanation, robustness where pre-authorized, and historical reporting. They are **not** automatically eligible for Gate B, FMOM, Base Strategy, or a new parameter search.

## Boundary

This archive is a deterministic export of frozen Gate A0 scores and frozen Gate A governance. It does not construct a new score, calculate forward returns or IC, produce a factor return, calculate FMOM, create a portfolio, or alter the Final Factor Handoff. The Strategy Engine must use `handoff/final_factor/`, not this archive.

## Controlled roles

The registry uses the controlled taxonomy `FINAL_ACTIVE`, `GATE_A_DROPPED`, `GATE_A0_SELECTED_REPRESENTATIVE`, `GATE_A0_REPLACED_INCUMBENT`, `GATE_A0_ELIGIBLE_CHALLENGER`, `SINGLE_SPEC_PASSTHROUGH`, and `DIAGNOSTIC_ONLY`. `DATA EXISTS ≠ FACTOR IS ACTIVE`.
