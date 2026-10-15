# SG independent strategy: post-OOS attribution

This folder is a descriptive attribution of the frozen Singapore strategy. It
does not change the factor set, the configuration selected in 2016--2020, or
the reported 2021+ out-of-sample result.

## Main finding

The selected dynamic FMOM configuration was better than the static control in
the 2016--2020 selection window (Sharpe 1.047 vs. 0.865). It also remained
better in an internal 2016--2018 / 2019--2020 stability check (validation
Sharpe 0.962 vs. 0.787). It nevertheless lost to the static equal-factor
strategy in the frozen 2021+ period (annualized net return 15.38% vs. 17.72%;
Sharpe 1.09 vs. 1.41; maximum drawdown 20.92% vs. 17.39%).

The dynamic strategy lagged static in 2021 (4.35% vs. 19.37%), 2022 (-9.69%
vs. -3.67%), and 2023 (-0.69% vs. 5.22%). It was ahead in 2024 and 2026, and
approximately tied in 2025. Dynamic mean one-way turnover was 20.61% per week,
versus 8.43% for static; at the provisional 20 bp one-way cost assumption this
is a mean weekly cost of 4.13 bp versus 1.69 bp.

## Interpretation

The evidence supports the static equal-factor portfolio as the main Singapore
specification. Dynamic factor timing remains a pre-specified extension, not a
demonstrated OOS improvement. The standalone-factor files are signal
diagnostics only: they cannot causally allocate the nonlinear top-20% stock
portfolio return among individual factors.

## Files

- `01_oos_yearly_strategy_comparison.csv`: realized annual returns, risk and turnover.
- `02_oos_overall_strategy_comparison.csv`: whole-OOS comparison.
- `03_oos_factor_weight_attribution.csv`: actual dynamic factor weights.
- `04_oos_single_factor_attribution.csv`: standalone IC and long-short diagnostics.
- `05_oos_fmom_sector_exposure.csv`: realized FMOM sector exposures.
- `06_training_top10_configuration_grid.csv`: selection-window context.
- `07_training_dynamic_vs_static.csv`: selection-window dynamic/static comparison.
- `08_internal_walkforward_model_test.csv`: the two-model pre-OOS stability check.
- `09_internal_holdout_dynamic_vs_static.csv`: summary of that check.

Do not select a new configuration using these OOS files. Any revised model must
be declared as an exploratory post-hoc experiment and tested on a genuinely
unseen later period.
