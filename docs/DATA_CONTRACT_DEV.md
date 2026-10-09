# Gate 0 Data Contract — US DEV Source Layer

## Status, scope, and evidence label

- **Evidence stage:** `DEV_CURRENT_UNIVERSE`
- **Gate 0 DEV decision:** PASS WITH WARNINGS
- **Formal Gate A eligibility:** not available from this layer. A rerun labelled
  `FORMAL_PIT_UNIVERSE` is required after historical constituent and GICS inputs
  are available.
- **Scope of this contract:** the delivered US source layer only. It defines
  source grain, identity, availability semantics, and audit acceptance; it does
  not define factor formulas, tag precedence, portfolio rules, IC, returns, or
  strategy logic.

`raw_package/US_source_data_2026-10-08/` is immutable evidence. All audit
outputs are reproducible products in `outputs/audit/`; no raw file, frozen
manual, or canonical dataset is changed by this Gate 0 work.

## Audit evidence

The full machine-readable inventory is
`outputs/audit/source_file_inventory.csv`, with 509 rows: 508 delivered payload
files covered by `PACKAGE_SHA256.csv`, plus the checksum manifest itself.
The payload manifest verified **508/508** files, with no missing, unexpected,
byte-size, or SHA-256 failures. Its own SHA-256 is
`b1cc92f9dc97ad1a5e963136f0b0867c092c4b44bfcbc2e1092abfeedc4cde72`.

Supporting audit artifacts are:

- `outputs/audit/source_inventory.json` — source-level schema and inventory;
- `outputs/audit/source_coverage_report.json` — delivered coverage and mapping
  statistics;
- `outputs/audit/source_exception_report.json` — observed exceptions and raw
  fact complexity;
- `outputs/audit/acceptance_test_report.md` — PASS/WARN/FAIL Gate 0 decision.

## Source objects

| Source object | Delivered file(s) | Grain and candidate key | Actual scope | Key fields | DEV downstream use |
| --- | --- | --- | --- | --- | --- |
| Package integrity | `PACKAGE_SHA256.csv`, `QA_SUMMARY.json`, `00_READ_FIRST.md` | One row per payload file / `archive_path` | 508 payloads plus the manifest | Path, expected bytes, SHA-256, provenance path | Verify immutable evidence before every downstream run. |
| Current security reference | `yahoo_download_stage/sp500_security_master.csv` | Download-date current constituent security / `security_id` | 503 securities, 500 CIKs | `security_id`, canonical `ticker`, `yahoo_ticker`, CIK, security/issuer names, exchange, current GICS, currency, first/last price dates, quality fields | Current DEV identity, vendor-symbol, exchange, and current-GICS reference. |
| Daily price/action stage | `yahoo_download_stage/sp500_daily_prices.csv` | Daily delivered vendor symbol / `(ticker, date)` | 2,392,900 rows; 504 tickers including SPY; 2006-01-03 to 2026-09-18 | OHLC, `adj_close`, volume, dividends, splits, delivery-stage simple/log return and dollar volume | DEV price intake, calendar construction, and corporate-action inspection. |
| Delivered price QA | `yahoo_download_stage/sp500_data_quality.csv` | Ticker-level summary / `ticker` | 503 current securities | Coverage, internal gaps, core-cell, zero-volume, duplicate-date, invalid-OHLC, extreme-return, eligibility, quality fields | Exception register; not an automatic exclusion list. |
| SEC security-to-issuer map | `sec_api_json/security_cik_map.csv` | Eligible current security-to-issuer relationship / `(ticker, cik)` | 500 securities, 497 issuers | CIK, canonical ticker, Yahoo ticker, current GICS, security name | Link eligible securities to issuer-level SEC archives. |
| SEC download status | `sec_api_json/download_status.csv` | Issuer / `cik` | 497 issuers | CIK, primary ticker, status, cache flag, normalized-row count, error | Verify issuer completeness and provenance. |
| SEC Company Facts | `sec_api_json/raw_companyfacts/CIK*.json.gz` | Issuer raw response; fact-observation candidate identity is `(cik, taxonomy, tag, unit, start, end, filed, accn)` | 497 readable issuer archives; 12,709,510 raw observations | Root `cik`, `entityName`, `facts`; tag metadata; unit-keyed observations with dates, filing metadata, forms, accession and values | Conservative PIT fundamental parsing under this contract; no tag or amendment priority is selected here. |
| Stage metadata/provenance | Both `manifest.json` files and `scripts/*.py` | Delivery-stage metadata/script | One SEC and one Yahoo manifest; two scripts | Source URLs, download scope, stated limitations, scripts | Audit provenance only. Scripts must not be run to refresh this snapshot. |

## Price and corporate-action contract

### Delivered schema and checks

The delivered price table contains exactly:

`date`, `ticker`, `open`, `high`, `low`, `close`, `adj_close`, `volume`,
`dividends`, `stock_splits`, `simple_return`, `log_return`, and `dollar_volume`.

The audit found strictly ascending `(ticker, date)` order, which proves
uniqueness in the delivered order: zero invalid dates, blank tickers, duplicate
key/order violations, missing core numeric cells, non-numeric core cells, or
negative delivered price/action values. SPY is present as the benchmark, not a
current constituent-security row.

The actual data has 28,556 positive-dividend rows, 331 non-zero split rows, and
1,998,160 rows where `close` and `adj_close` differ. These fields are therefore
distinct source fields and must never be treated as interchangeable.

### Field semantics and availability

| Field group | Source meaning in this delivery | Availability/use restriction |
| --- | --- | --- |
| `open`, `high`, `low`, `close` | Yahoo delivery-stage daily price fields; package instructions state `close` is split-adjusted. | The future execution/return convention must be frozen separately. |
| `adj_close` | Yahoo delivery-stage adjusted close; package instructions state it also reflects dividends and other adjustments. | Use only with an explicit total-return/corporate-action convention; do not add dividend cashflows to a total-return use. |
| `volume`, `dollar_volume` | Delivered trading activity and delivery-stage notional proxy. | Zero-volume rows require treatment under a later documented eligibility/liquidity rule. |
| `dividends`, `stock_splits` | Delivered corporate-action fields. | Retained for audit and future corporate-action reconciliation; not a licence to mix raw and adjusted return conventions. |
| `simple_return`, `log_return` | Delivery-stage derived columns. | Audit fields only at this stage; no performance, IC, or factor result is produced here. |

The current price layer is suitable for `DEV_CURRENT_UNIVERSE` intake but is not
a vendor HTTP-response archive and may be retrospectively adjusted after later
corporate actions. The later [historical-market-cap DEV specification]
(/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/docs/HISTORICAL_MARKET_CAP_SPEC_DEV.md)
audits a candidate split/share-class/action basis, but it remains pending
human sign-off and has not constructed a market-cap panel. Direct Yahoo-price ×
SEC-share multiplication remains prohibited.

### Price warnings requiring documented later treatment

- The delivered quality table has 29 `review` securities, 3,540 zero-volume
  rows, one internal trading-date gap, and one invalid-OHLC row.
- The direct scan locates the OHLC exception at `HUBB`, 2021-05-05: the delivered
  open (195.9799957) is below the delivered low (196.2100067).
- The quality flag is an exception register, not an automatic exclusion rule.
  A future DEV data-preprocessing rule must state how any affected observation
  is treated before a downstream calculation uses it.

## Identity, issuer, and benchmark contract

The canonical current-universe ticker and Yahoo delivery ticker are different
identity fields. Downstream code must retain both and use `yahoo_ticker` when
joining to the delivered price table.

- 503 current master securities match all 503 non-SPY delivered price tickers
  through `yahoo_ticker`; SPY is the only price ticker outside that mapping.
- Two actual vendor translations are `BF.B → BF-B` and `BRK.B → BRK-B`.
- The master includes 500 CIKs because three issuers have multiple current
  share-class tickers: `NWS/NWSA`, `GOOG/GOOGL`, and `FOX/FOXA`.
- The eligible SEC map contains 500 security rows and 497 CIKs; all 497 map to
  readable Company Facts archives and to successful download-status rows.
- `security_type` is `Common stock` for all 503 master rows. There is no
  separate delivered `share_class` field; share-class identity is represented
  through security/ticker records and the many-security-to-one-CIK cases above.
- Master exchange, current GICS sector, and current GICS sub-industry are
  populated for all 503 rows. These classifications are explicitly current,
  not historical PIT data.

## SEC Company Facts contract

Each archive has the actual root structure `cik`, `entityName`, and `facts`.
The facts map contains observed taxonomies `dei`, `ecd`, `ffd`, `ifrs-full`,
`invest`, `rxp`, `srt`, and `us-gaap`. Actual tag metadata fields are
`description`, `label`, and `units`. Unit-keyed observation records actually
contain `accn`, `end`, `filed`, `form`, `fp`, `fy`, `val`, and, where
applicable, `start` and `frame`.

All 12,709,510 observations contain `end`, `filed`, `form`, and `accn`; the
audit found no malformed populated date values. The raw filing dates span
2009-04-15 to 2026-09-18. Raw `start` and `end` fields have a much wider source
range (1900-01-01 to 2027-01-01 and 1989-12-31 to 2199-12-31 respectively),
and raw observations include multiple filing forms. This demonstrates why
future field extraction must deliberately constrain economic period and form
selection rather than treating every raw record as an accounting input.

The delivery contains 2,427 tags with multiple units, 202,372 duplicate-period
groups, 230,723 additional records within those groups, 195,088 groups with
multiple filed dates, and 114,700 amended-form observations. These are source
facts, not data corruption and not decisions. This contract intentionally does
not select a taxonomy, tag, unit, fiscal-period, restatement, or amendment
priority.

### Conservative PIT availability semantics

For the current DEV layer, a SEC fact is eligible for a signal date only when:

```text
filed_date <= signal_date
```

`filed_date` is a conservative availability proxy. Actual RDQ/earnings
announcement date and intraday publication timestamp are unavailable. That is a
known timing limitation and possible future enhancement, not a lack of a
current PIT-fundamental source or a DEV coding blocker.

## Acceptance criteria and permitted uses

| Criterion | DEV Gate 0 result | Downstream consequence |
| --- | --- | --- |
| Raw manifest, file set, and hashes | PASS | Package may be read; every future run must re-check integrity. |
| Expected source files readable | PASS | Intake code may rely on the delivered package layout. |
| Daily-price schema, key uniqueness, and date validity | PASS | The delivered price grain may be used for DEV source/interim pipeline work. |
| Identity/CIK coverage | PASS | Use the explicit canonical-to-Yahoo mapping; retain CIK many-to-one relations. |
| SEC archive readability and map coverage | PASS | Conservative DEV PIT parsing is implemented using `filed_date`; see the canonical PIT extension. |
| OHLC/volume review flags | WARN | Preserve exceptions and document treatment before affected observations feed downstream metrics. |
| SEC multi-unit, duplicate, amendment, form, and period complexity | WARN | The signed-tag canonical PIT parser records form/taxonomy/period controls, provenance, and explicit missingness; do not add an unapproved tag, unit, or restatement rule. |

The completed canonical PIT source-input extension is defined in
`docs/CANONICAL_PIT_FUNDAMENTALS_DEV.md`. This Gate 0 contract does **not**
authorize a claim of Formal PIT evidence, a formal Gate A factor-set freeze, or
any modification to the raw package.

## DEV canonical market extension

The accepted downstream price-input objects are
`data/canonical/us_security_master_dev.csv`,
`data/canonical/us_daily_market_dev.csv.gz`, and
`data/canonical/us_weekly_signal_calendar_dev.csv`. They are derived products,
not raw evidence, and all carry `DEV_CURRENT_UNIVERSE`.

- The master is the sole canonical identity map, retaining canonical `ticker`
  and vendor `yahoo_ticker`; it retains current GICS as `*_current`, never as
  PIT classification.
- The daily data has one canonical `(ticker, date)` record and preserves source
  OHLC, adjusted close, volume, dividends, splits, quality fields, benchmark
  marker, and base-entry provenance. SPY is benchmark-only.
- The weekly calendar uses the final delivered SPY trading date in each ISO
  natural week as `signal_date`, without assuming Friday. The source's final
  week intentionally has no inferred next trading date because it ends on
  2026-09-18.

`docs/PRICE_FACTOR_ENGINEERING_DEV.md` records the 252/21 and 126/21 momentum
indexes, 252-return volatility, metric-specific availability, preprocessing,
and the explicit non-implementation of IVOL and neutralization. These DEV
objects do not change any source limitation or create Formal PIT evidence.

## DEV SEC fundamental-input specification extension

`docs/SEC_FUNDAMENTAL_INPUT_SPEC_DEV.md` and the accompanying `outputs/audit`
SEC reports profile all 497 Company Facts archives without selecting or writing
canonical fundamental values. They retain the frozen conservative rule
`filed_date <= signal_date`, audit tags/units/forms/periods/amendments, and
make proposal/open-decision status explicit. No tag priority, unit conversion,
revenue/debt/FCF semantic chain, or amendment tie-break is silently frozen.

## Known DEV limitations and Formal PIT requirements

- Current S&P 500 membership projected backward creates survivorship bias.
- Current GICS is not historical PIT GICS; it is usable only for DEV pipeline
  testing, not final PIT sector-neutralization evidence.
- RDQ and announcement timestamps are unavailable; `filed_date` is a
  conservative substitute.
- Yahoo price history may be retrospectively adjusted and is not a preserved
  raw HTTP response.
- Historical index membership, delistings, security history, and historical
  member coverage are not present.

Historical S&P 500 constituents, identity history, historical GICS, and, if
adopted, CRSP/Compustat or equivalent linking/return/delisting inputs belong in
the future `FORMAL_PIT_UNIVERSE` intake. That layer must rerun the same data
checks and only then can provide formal Gate A evidence.
