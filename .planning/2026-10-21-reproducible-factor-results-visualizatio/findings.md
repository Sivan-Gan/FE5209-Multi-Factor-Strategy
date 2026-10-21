# Findings & Decisions

## Requirements
- Draw from frozen results only; do not rerun four analytical modules or change the panel/CSV contents.
- Create `src/plot_factor_results.py`, `figures/<module>/`, and `figures/figure_index.csv`.
- Support all-module, module-specific, both-sample, individual-sample, and output-directory CLI controls with root-relative defaults.
- Produce English report-ready 300-DPI PNG and matching PDF files, fixed factor colors, correct units, and source traceability.

## Research Findings
- Formal CSV inventory: performance 12 files, robustness 3 CSVs, correlation 6 CSVs, validation 6 CSVs; all weekly date fields span 2011-01-07 through 2015-12-31.
- Performance supports mean Rank IC, ICIR, weekly 30% long-short compounding, quintile profiles, weekly Top-20 excess, and score coverage.
- Robustness supports annual mean Rank IC, annual mean weekly long-short and Top-20 excess, plus positive-IC weekly frequency—not annual compounded long-short.
- Correlation supports mean weekly cross-sectional Spearman relationships, complete-case coverage, composite weekly returns and summary risk/return fields, and fixed-setting incremental contributions.
- Validation supports HAC mean, standard error, t-statistic, p-value, annual compounded 30% long-short return, screening fields, and paired-vs-price-eligible comparisons.
- Matplotlib 3.11.2 was installed as the one required additional package and added to `requirements.txt`.
- Generated 21 indexed figures: performance 6, robustness 5, correlation 5, validation 5. Each has one 300-DPI PNG and one same-content PDF, for 43 files including `figure_index.csv`.
- Visual PDF/PNG review covered representative performance, robustness, correlation, and validation figures. The time-axis padding and footnote layout were corrected before final regeneration.
- Full validation passed: each index source file and field exists, plotted date fields fall within 2011–2015, all PDFs are renderable, fixed factor colors/order are present in the module, and all expected figure IDs have status `GENERATED`.
- Frozen data SHA256 remains `ad7f37af6700917ce4f76b312cbc5a5328001498be0937c229a3fadadd71525c`; frozen result-inventory SHA256 remains `b4edfcf80e27cdf0d1217ba9c04928dd500a1f3c77866180452fc261a808b115`.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Label robustness R02 as annual mean weekly long-short return | This is the actual field (`mean_ls_weekly`); it is not an annual compounded return. |
| Use HAC standard errors directly for V02 | `top20pct_excess_hac_se` exists in the current validation summary, so confidence intervals are supported without reconstructing assumptions. |

## Issues Encountered
| Issue | Resolution |
|-------|------------|

## Resources
- Formal panel and all existing results are immutable inputs to plotting.
