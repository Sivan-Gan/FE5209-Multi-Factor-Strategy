# Project Understanding: Formal WRDS Transition State

## Status and evidence boundary

This document summarizes the repository's current transition state against the
frozen research SSOT. The formal US raw-data layer now exists at
`wrds_raw/US_WRDS_2026-10-09/`: CRSP + Compustat + CCM + Historical GICS +
CRSP market/S&P 500 indexes + Fama–French/RF. Its formal market-data cutoff is
`2025-12-31`.

Formal Gate 0 has **not** started or passed. Consequently, no formal canonical
dataset, factor construction, factor-effectiveness research, portfolio, FMOM,
strategy backtest, configuration freeze, or formal performance result exists.
Raw WRDS placement alone does not establish `FORMAL_PIT_UNIVERSE` evidence.

The project retains a separate legacy layer:
`raw_package/US_source_data_2026-10-08/` remains unchanged Yahoo + SEC
`DEV_CURRENT_UNIVERSE` evidence, with archived DEV processed data and audits.
It is a retained historical research source and possible 2026 Recent Extension
support, not the formal 2006–2025 historical research stack. DEV evidence
cannot close the formal Gate A factor-set freeze.

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
| Initialization / pre-sample buffer | 2004–2005 | Initialize long-lookback signals; not performance evaluation. |
| Development / warm-up | 2006–2010 | Validate pipeline and signals; not final performance selection. |
| Factor selection | 2011–2015 | Evaluate candidate factors and freeze the final factor set at Gate A. |
| Configuration selection | 2016–2020 | Select permitted FMOM/configuration choices; Gates B/C freeze the US Base strategy. |
| Formal Historical OOS | 2021–2025.12 | `HIST_OOS_BASE`; primary formal US evidence after Gate C. |
| Recent / operational extension | 2026.01–2026.09 | `RECENT_EXTENSION`; separate from formal OOS headline statistics. |
| Live / forward test | 2026.10+ | `LIVE_OPS` only after an actual strategy freeze. |

Formal Gate 0 — WRDS Data Ready must precede formal canonical construction and
factor construction. It requires raw integrity, coverage, PERMNO/GVKEY and
time-valid CCM verification, Historical-GICS timing, Compustat PIT rules, CRSP
DlyCap/CIZ/index semantics, Fama–French unit checks, and reproducible timing
controls. The remaining gates are A (final factor set), B (FMOM and portfolio
configuration), C (US Base), D (SG Strict and Feasible transfer specifications),
E (validated adaptation), and F (final untouched test).

The retained Yahoo price stage ends on 2026-09-18. It does not itself provide
a complete October 2026 operational-test feed.

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

## Formal WRDS raw-data layer

The formal historical source layer is physically present at
`wrds_raw/US_WRDS_2026-10-09/`. Formal US construction will use historical S&P
500 membership with the identifier flow:

`Historical S&P 500 membership → CRSP PERMNO → time-valid CCM → Compustat GVKEY`.

PERMNO is the security key and GVKEY is the company key; ticker/CUSIP are only
diagnostic identifiers. The formal CCM baseline is `LINKTYPE ∈ {LC, LU, LS}`
and `LINKPRIM ∈ {P, C, J}`, subject to `LINKDT <= t <= LINKENDDT`; valid J
links are retained as secondary issues. Historical GICS is effective-date
based (`INDFROM <= t <= INDTHRU`). Formal market cap is CRSP `DlyCap`; the
formal IVOL market proxy is `INDNO 1000080` `DlyTotRet`, and the formal US
benchmark is `INDNO 1000500` `DlyTotRet`. `INDNO 1000502` is a price-return
cross-check only. Fama–French/RF fields are decimal returns.

These are formal data-contract rules, not a claim that their canonical
implementation or Gate 0 validation has been completed.

## Retained DEV Yahoo + SEC source-package inventory

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

## What the retained DEV package can support

For `DEV_CURRENT_UNIVERSE`, the package continues to support source-data
intake, legacy audit, security mapping, a weekly signal calendar, PIT
fundamental parsing, factor-metric preprocessing, and reusable validation,
strategy, and backtest infrastructure. It may also provide expressly labelled
support for the 2026 `RECENT_EXTENSION` bridge. It is not the formal 2006–2025
data stack, and any resulting DEV artifact must carry the appropriate
`DEV_CURRENT_UNIVERSE` or `RECENT_EXTENSION` label.

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

## Archived DEV canonical price-input stage

The retained DEV layer has a 503-security canonical master, 2,392,900 canonical
daily market rows (including 5,210 benchmark SPY rows), and 1,081 weekly signal
dates. It constructs only `MOM12-1`, `MOM6-1`, and 252-day realised volatility,
each with raw availability, base-entry eligibility, reason-code, winsorized,
z-score, and direction-normalized fields. The full contract is
`docs/dev_legacy/PRICE_FACTOR_ENGINEERING_DEV.md`.

This remains archived `DEV_CURRENT_UNIVERSE` input engineering under
`data/dev_legacy/` and `outputs/factor_research/dev_legacy/`. It is not formal
Forward IC, single-factor portfolio, FMOM, neutralization, strategy, or
backtest evidence; it does not select a final factor set or alter any factor's
frozen economic direction. `SPY` is a benchmark/calendar row only, never a
stock cross-section member. Formal IVOL will use the SSOT's CRSP index rule
after Gate 0.

## Archived DEV SEC canonical fundamental-input stage

All 497 SEC Company Facts archives have been profiled, and the signed exact-tag
construction now produces 469,705 normalized source facts plus a separately
classed weekly PIT availability/provenance layer. The selector enforces
`filed_date <= signal_date`, keeps instant/annual/quarterly/YTD/irregular
classes separate, applies amendment availability only from its own filing date,
and preserves unresolved conflicts as missing. Details, coverage, age, foreign
form, sign-exception, and open-provenance evidence are in
`docs/dev_legacy/CANONICAL_PIT_FUNDAMENTALS_DEV.md`.

This is an archived DEV input/provenance layer, not a Value, Quality,
Investment, FCF, or formal-market-cap calculation. It does not settle the
formal Compustat PIT data contract or future factor definitions.

## Retired DEV historical-market-cap repair route

The old Yahoo price × SEC shares route, including its estimated historical
market-cap, share-scale, extreme-jump, and sign-off artifacts, is retired from
the active formal branch. The formal decision is
`STOP_DEV_MARKET_CAP_REPAIR`. Retired artifacts remain recoverable from the
DEV freeze history and are documented by the active retirement manifest; they
are not active canonical inputs and must not be revived as Formal PIT market
cap.

The formal market-cap baseline is CRSP `DlyCap`, subject to Formal Gate 0
verification of its unit and semantic contract. Retained Yahoo price / SEC PIT
infrastructure remains available only for explicitly labelled DEV audit work or
possible 2026 `RECENT_EXTENSION` support.

## Known limitations

- **Current-S&P-500 survivorship bias:** the universe is the download-date
  current S&P 500 membership projected backwards. It cannot support a claim of
  an unbiased historical investable S&P 500 universe. Any current DEV evidence
  remains conditional on that research universe.
- **Non-PIT GICS:** the supplied GICS sectors and sub-industries are current,
  not historical point-in-time classifications. They can be used to develop
  and test the neutralization pipeline under `DEV_CURRENT_UNIVERSE`, but cannot
  be final PIT sector-neutralization evidence.
- **Retired Yahoo × SEC market-cap route:** retrospective Yahoo action handling
  and periodic SEC shares remain limitations of the legacy evidence. They are
  not an unresolved active formal market-cap task; the formal baseline is CRSP
  `DlyCap` after Gate 0.
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

## Formal WRDS readiness remains pending Gate 0

The WRDS source layer is available; the pending work is its formal data-contract
validation, not source acquisition. Formal Gate 0 must verify raw hashes and
schemas; historical universe coverage; duplicate keys; PERMNO/GVKEY and CCM
coverage; Historical-GICS timing; Compustat quarterly/annual PIT rules; CRSP
`DlyCap`, CIZ return/delisting, and index semantics; Fama–French/RF units; and
the `2025-12-31` formal cutoff. It must also document the annual PIT-availability
rule and validate any 2025 CRSP–Yahoo bridge before using Yahoo market data for
the 2026 extension.

No raw source, including WRDS, silently settles methodology. Versions, field
definitions, coverage, timing, merge rules, and formal exception handling remain
subject to the SSOT and Gate 0 acceptance evidence.

## Retained DEV evidence vs formal WRDS workflow

| Retained Yahoo + SEC DEV layer | Formal WRDS workflow after Gate 0 |
| --- | --- |
| Preserve and audit `DEV_CURRENT_UNIVERSE` evidence, explicitly retaining current-universe/current-GICS limitations. It may support legacy audits and a clearly labelled 2026 bridge. | Build reproducible interim and canonical datasets from the historical universe; label formal inputs `FORMAL_PIT_UNIVERSE`. |
| Do not describe DEV outputs as unbiased historical investable performance, formal PIT sector-neutralization evidence, Final OOS, or evidence eligible to close formal Gate A. | Run formal factor-selection evidence and, only under the formal stack, support the Gate A decision. |
| Keep the legacy package immutable; do not revive the retired Yahoo × SEC market-cap route. | Use CRSP `DlyCap` as the formal market-cap baseline once its contract is verified. |

## Immediate next steps

1. Keep both `raw_package/US_source_data_2026-10-08/` and
   `wrds_raw/US_WRDS_2026-10-09/` immutable and retain their integrity evidence.
2. Treat Formal Gate 0 — WRDS Data Ready as the next formal prerequisite; raw
   placement is not a Gate 0 pass.
3. Resolve only the Gate 0 data-contract items identified in the SSOT before
   constructing formal interim/canonical data or factor inputs.
4. Preserve retained Yahoo price / SEC PIT infrastructure for DEV audit work or
   possible `RECENT_EXTENSION` support, with the required evidence labels and
   2025 bridge validation.
5. Do not restart `STOP_DEV_MARKET_CAP_REPAIR`; use the SSOT's CRSP `DlyCap`
   baseline for formal work after Gate 0.
