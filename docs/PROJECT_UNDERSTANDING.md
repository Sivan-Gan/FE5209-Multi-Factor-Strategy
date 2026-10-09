# Project Understanding: Source-Data Foundation

## Status and evidence boundary

This document summarizes only the frozen research framework and the
`US_source_data_2026-10-08` package. The project has completed its DEV source
intake, canonical price-input, and signed SEC PIT fundamental-input stages:
canonical market data, weekly signal dates, the three authorized price
metrics/preprocessing fields, normalized signed SEC facts, and a weekly PIT
availability/provenance panel exist. No financial ratio, fundamental-factor
construction, factor-effectiveness research, strategy construction, backtest,
configuration freeze, or research-performance result has been created from
this package.

The package is stored unchanged in `raw_package/US_source_data_2026-10-08/`.
It is source-layer evidence, not a finished monthly PIT panel or a directly
tradable factor dataset.

The project uses two evidence layers. Current work may proceed as
`DEV_CURRENT_UNIVERSE`; it uses the supplied current S&P 500 universe, current
GICS, Yahoo daily data, and SEC Company Facts. A later rerun based on
historical constituents and historical GICS is `FORMAL_PIT_UNIVERSE`. Results
from the current layer are preliminary evidence and cannot close the formal
Gate A factor-set freeze.

## Research objective

The framework first develops a weekly, long-only US multi-factor stock
selection strategy with a factor-momentum tilt. It selects the final factor
set from the candidate pool only through US validation, freezes the US core
strategy, and then transfers the same reusable engine to Singapore for
out-of-market validation. The subsequent work is to compare the US and
Singapore outcomes, diagnose differences in signals, portfolio translation,
market structure, implementation, risk, currency, and regimes, and permit
only diagnosed, independently validated adaptations.

Value, Quality, Momentum, Low Risk, Investment, Dividend, and Size are
candidate factors, not a decided final factor set.

## US–Singapore architecture

The reusable pipeline is:

`Data & PIT → Factor Engineering → Factor Research → Strategy Engine → Backtest & Risk`.

It is developed and frozen in the US, then applied with a `market` parameter
to Singapore rather than reimplemented as a separate pipeline. Singapore has
two distinct transfer objects:

- **SG Strict Transfer**: preserves the US-frozen rules and portfolio settings
  wherever mechanically feasible; it is the primary portability benchmark.
- **SG Feasibility-Adjusted Base**: uses only market-feasibility rules frozen
  before any SG strategy performance is reviewed, such as breadth, industry,
  liquidity, or REIT treatment rules.

Later adaptations are neither of these objects. They require an observed
problem, a documented mechanism, a targeted change, and independent
validation.

## Sample periods and gates

The framework specifies the following US research schedule:

| Stage | Period | Purpose |
| --- | --- | --- |
| Price/factor warm-up | 2006–2010 | Build required historical windows; not for selecting strategy performance. |
| Factor selection | 2011–2015 | Evaluate candidate factors and freeze the final factor set at Gate A. |
| Configuration selection | 2016–2020 | Select permitted FMOM/configuration choices and freeze them at Gate B. |
| Historical OOS | 2021–2026.09 | Evaluate the frozen US Base strategy after Gate C. |
| Live/operational test | 2026-10 | Operational update/execution testing only after an actual freeze. |

Gate 0 (Data Ready) must precede factor construction for each evidence layer.
It requires, among other things, immutable raw inputs, fixed calendars,
traceable signal/information/execution dates, PIT checks, coverage, industry
coverage, and reconstructible universe entry/exit. The current package supports
building and testing the DEV version of this data contract; it does not make
the historical-current-universe limitations disappear. The remaining gates are
A (final factor set), B (FMOM and portfolio configuration), C (US Base), D
(SG Strict and Feasible transfer specifications), E (validated adaptation),
and F (final untouched test).

The supplied price stage ends on 2026-09-18. It does not itself provide a
complete October 2026 operational-test feed.

## Team and workstream architecture

The manual assigns one primary owner per workstream, with contributors and an
independent reviewer:

| Workstream | Primary responsibility |
| --- | --- |
| M1 — Data & PIT | US/SG universe, PIT alignment, prices, fundamentals, industry, FX, coverage, and data gates. |
| M2 — Factor Engineering | Candidate-factor formulas, field mapping, cleaning, internal aggregation, and neutralization. |
| M3 — Factor Research | Forward IC, single-factor evidence, stability, redundancy, and final-factor proposal. |
| M4 — Strategy Engine | Factor returns, factor momentum, weights, scores, holdings, buffers, and execution logic. |
| M5 — Backtest & Risk | Performance, costs, risk, liquidity/capacity, drawdowns, and strategy profiles. |
| M6 — Cross-Market & Integration | SG transfer specification, matched comparison, diagnosis, adaptation governance, and integration. |

All gate freezes require group confirmation. A workstream owner may not
unilaterally change frozen definitions.

## Current source-data inventory

The immutable US package contains 508 files covered by its supplied checksum
manifest, plus that manifest itself.

| Area | Evidence currently supplied |
| --- | --- |
| Documentation and integrity | `00_READ_FIRST.md`, `QA_SUMMARY.json`, `PACKAGE_SHA256.csv`. |
| Current security reference | A download-date S&P 500 security master with SEC CIK, exchange, current GICS, identifiers, and quality fields. |
| Daily prices and actions | Yahoo/yfinance download-stage table with 2,392,900 rows from 2006-01-03 through 2026-09-18; it includes OHLC, adjusted close, volume, dividends, splits, and download-stage simple return, log return, and dollar volume fields. |
| Price quality record | Per-ticker coverage and quality information, including zero-volume, invalid-OHLC, large-return, and review indicators. |
| SEC facts | 497 compressed SEC EDGAR Company Facts JSON archives, with a 500-security CIK map and download status. The facts retain reported concepts, units, periods, filing dates, forms, and accession numbers. |
| Provenance code | The two scripts used to download/transform the SEC and Yahoo stages; rerunning them may retrieve newer external data and does not reproduce this historic snapshot. |

The package's own QA summary says it does **not** include processed US prices,
the old monthly PIT valuation panel, five-factor signals, normalized selected
SEC fundamentals, or SGX/Hong Kong data.

## What this package can support now

For `DEV_CURRENT_UNIVERSE`, the package supports source-data intake, security
mapping, a weekly signal calendar, PIT fundamental parsing, factor-metric
construction, preprocessing, preliminary Forward IC, preliminary single-factor
portfolios, preliminary stability/redundancy diagnostics, and reusable
validation, strategy, and backtest infrastructure. Every resulting research
artifact must carry the `DEV_CURRENT_UNIVERSE` evidence label.

SEC Company Facts plus `filed_date` supports conservative PIT fundamental
construction now: a fact is available when `filed_date <= signal_date`.
This is sufficient for DEV work and for a conservative PIT availability rule in
later construction. Implementations still need frozen field precedence, units,
period length, amendment handling, and availability rules; these are explicit
data-contract decisions, not a lack of PIT-fundamental source data.

Actual report date/RDQ or earnings-announcement timestamps are not supplied.
Their absence is a timing limitation and possible future enhancement: the
conservative `filed_date` rule may be later than the first public availability
of earnings information. It is not a blocker to DEV coding.

## Current DEV canonical price-input stage

The current DEV layer has a 503-security canonical master, 2,392,900 canonical
daily market rows (including 5,210 benchmark SPY rows), and 1,081 weekly signal
dates. It constructs only `MOM12-1`, `MOM6-1`, and 252-day realised volatility,
each with raw availability, base-entry eligibility, reason-code, winsorized,
z-score, and direction-normalized fields. The full contract is
`docs/PRICE_FACTOR_ENGINEERING_DEV.md`.

This remains `DEV_CURRENT_UNIVERSE` input engineering. It is not Forward IC,
single-factor portfolio, FMOM, neutralization, strategy, or backtest evidence;
it does not select a final factor set or alter any factor's frozen economic
direction. `SPY` is a benchmark/calendar row only, never a stock cross-section
member. IVOL remains unimplemented because its regression specification is not
uniquely frozen.

## Current DEV SEC canonical fundamental-input stage

All 497 SEC Company Facts archives have been profiled, and the signed exact-tag
construction now produces 469,705 normalized source facts plus a separately
classed weekly PIT availability/provenance layer. The selector enforces
`filed_date <= signal_date`, keeps instant/annual/quarterly/YTD/irregular
classes separate, applies amendment availability only from its own filing date,
and preserves unresolved conflicts as missing. Details, coverage, age, foreign
form, sign-exception, and open-provenance evidence are in
`docs/CANONICAL_PIT_FUNDAMENTALS_DEV.md`.

This is an input/provenance layer, not a Value, Quality, Investment, FCF, or
market-cap calculation. A future factor-definition step still must determine
how permitted period classes are used, without retroactively changing the
source layer.

## Current DEV estimated-historical-market-cap stage

The signed construction is complete and its acceptance is `PASS WITH
WARNINGS`. `data/canonical/us_weekly_estimated_market_cap_dev.sqlite` retains
the complete 503-security × 1,081-signal-date grid, not only usable values. It
uses an exact signal-date Yahoo `close`, PIT-available primary DEI shares,
standard/reverse-split delivery-basis alignment, and explicit price/shares/split
provenance. The canonical field is `estimated_market_cap`; the source is
`SEC_YAHOO_ESTIMATE_DEV`; the evidence stage is `DEV_CURRENT_UNIVERSE`.

Construction checks find zero available-value PIT filing-date violations, exact
price-date violations, nonpositive/nonfinite values, unresolved multi-class
uses, non-standard-action uses, or incomplete provenance records. The named
AAPL 7:1 and 4:1, AMZN 20:1, and C 1:10 reverse cases pass the mechanical
split-discontinuity audit. This is still only a DEV estimated market cap, never
Formal PIT market cap.

The object is not yet cleared for Fundamental Factor Construction. The
subsequent extreme-share source audit identifies 20 source facts behind 274
available ticker-weeks and supports a candidate relative transient-run rule;
however, its market-cap semantic closure decision is `FAIL`. After simulated
rejection, VTRS and other securities still show million-level shares-update
jumps. No values were rescaled, clipped, or silently excluded, the candidate
rule is not formally signed off, and no canonical patch/rebuild is recommended.
No Value/Size factor, neutralization, IC, portfolio, FMOM, or strategy work
has begun.

## Known limitations

- **Current-S&P-500 survivorship bias:** the universe is the download-date
  current S&P 500 membership projected backwards. It cannot support a claim of
  an unbiased historical investable S&P 500 universe. Any current DEV evidence
  remains conditional on that research universe.
- **Non-PIT GICS:** the supplied GICS sectors and sub-industries are current,
  not historical point-in-time classifications. They can be used to develop
  and test the neutralization pipeline under `DEV_CURRENT_UNIVERSE`, but cannot
  be final PIT sector-neutralization evidence.
- **Historical-market-cap limitations remain material:** the signed DEV object
  is built, but Yahoo delivery prices are retrospectively action-normalized and
  reported SEC shares are periodic. Narrow validation of the `>1e12` source
  set did not close the broader source-share semantic issue: million-level
  update jumps remain after simulation. Current value is `estimated_market_cap`,
  never Formal PIT market cap.
- **Price-quality review remains open:** zero-volume and anomalous OHLC rows
  remain in the download stage, and AMCR, FERG, and VRT require identity/listing
  history review. A `review` status is not an automatic exclusion rule.
- **SEC facts require continuing selection governance:** multiple XBRL tags,
  units, comparative periods, amendments, and fiscal periods are present. The
  constructed signed-tag layer enforces `filed_date <= signal_date` and does
  not overwrite prior history retroactively with amended filings; it still does
  not freeze a future factor-specific period hierarchy or resolve the disclosed
  non-unique-provenance issue.
- **Not vendor-response byte backups:** the compressed SEC JSON files are
  reserialized/gzipped API responses; the price file is the earliest retained
  download-stage table, not a Yahoo HTTP response. The original Yahoo,
  Wikipedia, and SEC identity-response payloads were not preserved.

## Pending WRDS/CRSP/Compustat or equivalent inputs

No WRDS, CRSP, or Compustat data is present in the package, and the framework
does not select a vendor. These inputs are not blockers for
`DEV_CURRENT_UNIVERSE`; they are required to replace the current-universe
foundation and produce `FORMAL_PIT_UNIVERSE` evidence for the formal Gate A
decision. The documented pending inputs from those sources or an approved
equivalent are:

- historical point-in-time S&P 500 membership and security identity history;
- historical point-in-time GICS/industry classifications;
- PIT security/return history needed to handle delistings, ticker migrations,
  corporate actions, shares, and market-cap construction under a single frozen
  basis;
- historical-member price and delisting coverage if CRSP is adopted, and
  PERMNO/GVKEY linking if those identifiers are available;
- any approved fundamental cross-check/enhancement data. SEC Company Facts plus
  `filed_date` already supplies a conservative PIT-fundamental availability
  rule; RDQ/announcement timing remains an enhancement rather than a missing
  prerequisite;
- any required benchmark, risk-free, transaction-cost, and Singapore data
  inputs required by later gates.

Acquiring a source does not silently settle methodology: its versions, field
definitions, coverage, timing, and merge rules must enter a data contract and
be evaluated at Gate 0.

## DEV work now vs Formal PIT work later

| Current DEV work using the package | Future Formal PIT work after historical constituents/GICS are available |
| --- | --- |
| Label every dataset, diagnostic, and result `DEV_CURRENT_UNIVERSE`. Preserve/audit the package; perform source intake, mapping, PIT parsing using `filed_date <= signal_date`, weekly calendars, metrics, preprocessing, and preliminary IC/single-factor/stability/redundancy research. | Build a historical universe with reconstructible PIT membership, security identity, and sector classification; label every rerun `FORMAL_PIT_UNIVERSE`. |
| Develop reusable validation, strategy, and backtest infrastructure, while retaining current-universe and current-GICS limitations explicitly. | Rebuild the input layer using the formal universe and any approved CRSP/Compustat or equivalent inputs under frozen corporate-action, linking, and coverage rules. |
| Do not describe outputs as unbiased historical investable performance, formal PIT sector-neutralization evidence, final OOS, or evidence eligible to close formal Gate A. | Re-run the data-ready checks and factor-selection evidence with formal inputs. Only this layer may support the formal Gate A factor-set freeze. |
| Do not treat the package as a prebuilt factor panel or combine it directly into a tradeable monthly panel. | Version and document approved external sources in `data/external/`; create reproducible interim/canonical datasets outside `raw_package/`. |

## Immediate next steps

1. Keep `raw_package/US_source_data_2026-10-08/` immutable and use its checksum
   manifest for every future integrity check.
2. Review the canonical market and price-factor input audits, including the
   zero-volume/review-security carry-forward records and IVOL open issue.
3. Any future DEV factor research must use these labelled inputs and retain the
   documented current-universe/current-GICS limitations; it may not close
   formal Gate A.
4. Review the canonical PIT fundamental-input audit, including coverage,
   staleness, amendments, explicit missingness, and the non-unique-provenance
   open issue; do not treat it as factor construction.
5. Review and explicitly sign (or revise) the historical-market-cap decisions:
   share-value validity, standard/nonstandard action handling, multi-class
   disposition, price-quality treatment, staleness, form scope, and economic
   level. Do not construct a market-cap object until then.
6. Review the documented price-quality, ticker/issuer-history, and split-basis
   issues for the DEV data-ready contract.
7. Obtain and version historical constituents/GICS and other approved PIT
   inputs before the `FORMAL_PIT_UNIVERSE` rerun and formal Gate A decision.
