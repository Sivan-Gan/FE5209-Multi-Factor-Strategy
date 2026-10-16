# Singapore independent factor-research module

## Scope and frozen design

This module is independent from the US model.  It uses a six-sector SGX
universe and follows the agreed split:

| Stage | Dates | Purpose |
| --- | --- | --- |
| Factor selection | 2011--2015 | Standalone IC and long-short validation |
| Configuration selection | 2016--2020 | Select a Member-4-style FMOM configuration |
| Frozen evaluation | 2021--latest available | Run only; do not tune on these results |

The selected factors are Quality, Momentum, LowRisk, and Investment. The
configuration selected from the specified 2016--2020 grid is
`J13_lam1_ra0_buf0`, with a provisional 20 bp **per-side** transaction-cost
scenario. Cost uses `GTR = sum(abs(target weight - drifted pre-trade weight))`
and `cost = 20 bp × GTR`; reported one-way turnover is `GTR / 2`.

## What is committed versus omitted

Committed files include all SG processing, backtest, validation, static
benchmark, attribution and PIT-fundamental handoff code, together with small,
derived research outputs.  The repository deliberately omits raw daily price
files and the LSEG Workspace annual-export workbooks.  They are licensed data,
large, and include user-specific extraction paths.  No raw vendor data should
be committed without confirming the relevant redistribution rights.

Expected local inputs (not versioned):

```
SGX_factor_research/output_lseg_rebuilt_for_sg_strategy/data/prices_daily_2006.csv
SGX_factor_research/output_lseg_rebuilt_for_sg_strategy/data/fundamentals_lseg_panel_unverified_pit.csv
```

`build_lseg_fundamentals_handoff.py` constructs the latter from locally
exported LSEG annual workbooks.  It preserves source fields and creates an
availability-date proxy of fiscal-period end plus 183 days.  This is not an
actual SGX announcement timestamp and must not be represented as verified PIT.

## Run order

From the repository root, with the two local input files available at the
paths expected by the script or with its path constants adjusted locally:

```bash
python -m src.strategy.run_sg_independent_research --force
python -m src.strategy.add_sg_required_baselines
python -m src.strategy.add_sti_price_benchmark
python -m src.strategy.audit_sg_independent_research
python -m src.strategy.analyze_sg_attribution
python -m src.strategy.test_sg_fmom_improvement
python -m src.strategy.add_sg_execution_robustness
python -m src.strategy.export_sg_stock_factor_panel
```

`add_sti_price_benchmark` also requires a locally exported `.STI` price-history
spreadsheet.  The output is a **price-index** comparison, not a total-return
benchmark, so it must not be labelled as final alpha.

For a formal STI comparison, export an authorised STI **total-return** series
from LSEG Workspace/FTSE, document whether it is gross or net of withholding
tax, then run `add_sti_total_return_benchmark` with its date and index-level
column names. The supplied `.TRISTI` LSEG Price History export has its header
at Excel row 19 and uses `Exchange Date` and `Close`, e.g.
`python -m src.strategy.add_sti_total_return_benchmark <export.xlsx> --header 18 --date-col "Exchange Date" --value-col Close --return-type unspecified`.

`export_sg_stock_factor_panel` writes a private, Git-ignored panel of every
covered ticker by weekly signal date. It contains the four scores, current
eligibility inputs, execution and next-execution dates, and realised next-week
return. It is the appropriate input for portfolio-construction experiments;
it does not authorise retuning the frozen OOS model.

## Results and interpretation

All performance tables are generated locally from the run scripts. Preliminary
results must be regenerated whenever the cost scenario or data inputs change.
The static equal-factor benchmark is the key control for whether FMOM adds
value; FMOM must not be called an out-of-sample improvement unless it
outperforms that benchmark after identical GTR-based costs.

The audit output records mechanical checks and limitations.  Main limitations
are the 183-day accounting-availability proxy, potential later restatements,
current-universe survivorship bias, provisional 20 bp trading cost, no formal
capacity filter, and the lack of verified historical point-in-time market cap,
shares and dividends for Value/Size/Dividend.

## Output guide

- `01_*`: 2011--2015 single-factor evidence.
- `02_*`: 2016--2020 configuration grid.
- `03_*`: continuous strategy weights, holdings and weekly returns.
- `04_*`: selected-period versus frozen-OOS summary.
- `06_*`: static multifactor, price-eligible-universe equal weight, and
  scoreable-universe equal-weight benchmarks. The last uses the exact weekly
  universe with all selected factor scores available and is the direct
  stock-selection control.
- `07_attribution_oos/`: calendar-year, factor-weight, sector, turnover and
  internal pre-OOS stability diagnostics.  This folder must never be used to
  re-select the frozen OOS configuration.
- `audit/`: source/portfolio mechanical checks and limitations.
- `08_execution_robustness/`: 0--60 bp cost scenarios, a weekly Newey-West
  FMOM-minus-static test, and a 10%-ADV capacity proxy.
