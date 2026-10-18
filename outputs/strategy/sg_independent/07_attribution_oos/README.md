# OOS attribution — market-cap and sector neutralized rerun

These files are descriptive diagnostics for the frozen 2021–latest Singapore
run. They do not select a new factor, parameter, turnover threshold, or
portfolio construction rule after observing OOS performance.

- `01_oos_yearly_strategy_comparison.csv`: yearly returns and turnover by strategy.
- `02_oos_overall_strategy_comparison.csv`: OOS aggregate metrics.
- `03_oos_factor_weight_attribution.csv`: realized FMOM factor weights.
- `04_oos_single_factor_attribution.csv`: standalone long–short diagnostics.
- `05_oos_fmom_sector_exposure.csv`: realized six-sector portfolio exposures.
- `06_training_top10_configuration_grid.csv`: selection-period grid evidence.
- `07_training_dynamic_vs_static.csv`: configuration-period dynamic/static comparison.
- `08_internal_walkforward_model_test.csv` and `09_internal_holdout_dynamic_vs_static.csv`: pre-OOS internal split diagnostics.

All four underlying scores are cross-sectionally residualized against the
lagged weekly market-cap proxy and six-sector dummies before composition. The
market-cap input is a documented proxy, not vendor-confirmed strict PIT.
