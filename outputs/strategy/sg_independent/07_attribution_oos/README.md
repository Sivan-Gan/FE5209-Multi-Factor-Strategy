# Post-OOS attribution diagnostics

These files describe the frozen Singapore strategy after OOS performance has
been observed. They must not be used to select a new configuration.

- `01_*`: calendar-year strategy comparisons.
- `02_*`: full OOS strategy comparison using GTR-based costs.
- `03_*`: realized FMOM factor weights.
- `04_*`: standalone factor diagnostics.
- `05_*`: sector exposures.
- `06_*` through `09_*`: pre-OOS training and internal-holdout diagnostics.

`turnover` is one-way turnover (`GTR / 2`); transaction cost is calculated
from full GTR. The weekly calendar excludes partial provider-outage dates.
