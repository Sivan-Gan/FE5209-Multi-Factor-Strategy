# Singapore independent factor research — market-cap and sector neutralized rerun

## Purpose and time split

This rerun keeps the Singapore workflow independent from the United States:

| Stage | Period | Use |
| --- | --- | --- |
| Factor selection | 2011–2015 | Test the four pre-defined factor families only. |
| Configuration selection | 2016–2020 | Select the factor-momentum configuration from the pre-declared grid. |
| OOS reporting | 2021–latest | Report only; do not retune. |

## Factor processing

At each weekly signal date, each raw Quality, Momentum, LowRisk and Investment
signal is winsorized at the cross-sectional 1st/99th percentile and z-scored.
It is then residualized by the joint regression

`z(raw signal) = intercept + beta × log(weekly market-cap proxy) + six-sector dummies + residual`.

Six-sector buckets with fewer than ten eligible securities are pooled before
dummy creation. Residuals are winsorized and z-scored again. A missing or
non-positive market-cap proxy makes the score unavailable; no current value,
zero, or interpolation is substituted.

The weekly market-cap input is a **proxy**: lagged annual shares outstanding
times weekly adjusted close. Its source metadata and `PIT_status` are retained
in the private factor panel. It is not a vendor-confirmed point-in-time market
capitalization series.

## Current rerun summary

- 2011–2015: all four pre-defined families satisfy the project’s operational
  selection rule.
- 2016–2020 selected configuration: `J52_lam1_ra0_buf1`.
- 2021–latest OOS FMOM: 20.12% annualized net return; 1.28 weekly zero-rate
  Sharpe; 15.96% maximum drawdown.
- OOS static equal-factor control: 15.29% annualized net return; 1.28 Sharpe.
- OOS same-scoreable-universe equal-weight control: 11.84% annualized net
  return; 1.00 Sharpe.
- OOS STI Total Return comparison: 17.15% annualized return; 1.36 Sharpe.

The strategy’s return is higher than these controls in this run, but the STI
benchmark has a higher Sharpe and lower drawdown. The strategy must therefore
not be described as conclusively superior or as confirmed alpha.

## Material limitations

1. Fundamental information uses fiscal period end + 183 days as a conservative
   availability proxy; actual SGX filing timestamp and as-reported restatement
   version are not verified.
2. The stock universe is a current screened universe and does not establish
   historical delisted-company coverage.
3. The 20 bp per-side trading-cost scenario is charged as `20 bp × GTR`; it is
   a scenario rather than a transaction-cost estimate for every stock.
4. The supplied `.TRISTI` file is Total Return but its gross/net dividend-tax
   convention must be documented before a formal alpha claim.
5. The LSEG/private source data are intentionally excluded from public Git.

## Reproduction

With authorised private sources present:

```bash
python -m src.strategy.run_sg_independent_research --force
python -m src.strategy.add_sg_required_baselines
python -m src.strategy.add_sti_total_return_benchmark /path/to/TRISTI.xlsx --header 18 --date-col "Exchange Date" --value-col Close
python -m src.strategy.analyze_sg_attribution
python -m src.strategy.add_sg_execution_robustness
```
