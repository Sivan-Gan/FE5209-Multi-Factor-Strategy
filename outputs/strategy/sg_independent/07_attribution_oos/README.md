# Post-OOS attribution diagnostics

This directory describes the frozen Singapore strategy after OOS performance
has been observed. It must not be used to select a new configuration.

- `01_*` gives calendar-year strategy comparisons.
- `02_*` gives the full OOS summary, including GTR-based costs.
- `03_*` gives realized FMOM factor weights.
- `04_*` gives standalone factor diagnostics.
- `05_*` gives portfolio sector exposures.
- `06_*` through `09_*` are pre-OOS training and internal-holdout checks.

All `turnover` columns are one-way turnover (`GTR / 2`). Costs are calculated
elsewhere from full GTR using the stated per-side cost scenario.
