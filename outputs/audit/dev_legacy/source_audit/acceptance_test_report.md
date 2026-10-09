# Gate 0 — Source Data Intake Acceptance Report

- Evidence stage: `DEV_CURRENT_UNIVERSE`
- Generated at (UTC): `2026-10-08T10:05:54+00:00`
- DEV Gate 0 decision: **PASS WITH WARNINGS — DEV Factor Engineering may begin**
- Formal Gate A decision: **not eligible from this evidence layer**; it requires the `FORMAL_PIT_UNIVERSE` rerun.

| Status | Check | Evidence | Disposition |
| --- | --- | --- | --- |
| PASS | Raw package manifest/hash integrity | Verified 508/508 declared payload files; hash failures=0, missing=0, unexpected=0. | FAIL blocks DEV intake until the supplied package is restored exactly. |
| WARN | SEC raw form and period-date scope | raw forms=32; start range=1900-01-01 to 2027-01-01; end range=1989-12-31 to 2199-12-31; invalid parsed dates={}. | Raw Company Facts is intentionally unfiltered. Later PIT parsing must freeze included forms, tag/unit/period rules, and treatment of non-economic/outlying period observations. |
| PASS | Expected source files readable | All expected delivered tables and metadata files are present. | FAIL blocks DEV intake. |
| PASS | Yahoo daily `(ticker, date)` primary-key and date validity | rows=2392900, strict_key_order=True, invalid_dates=0, blank_tickers=0. | FAIL blocks DEV price intake until keys/dates are repaired or excluded by a documented rule. |
| WARN | Yahoo OHLC/action plausibility and delivered quality flags | stream_invalid_ohlc=1, negative_values=0, zero_volume_rows=3540, quality_review_tickers=29. | WARN requires documented ticker/row treatment before a downstream object relies on affected observations; it does not invalidate the full DEV package. |
| PASS | Security identity and CIK mapping consistency | price/master-yahoo intersection=503; price-only=['SPY']; map CIK without status=0; SEC-map CIK outside master=0. | FAIL blocks DEV identity-dependent intake until reconciled. |
| PASS | SEC Company Facts readability and issuer coverage | readable_archives=497/497, archive/map gaps=0, missing root metadata={}. | FAIL blocks DEV PIT-fundamental intake until the delivered source/mapping is reconciled. |
| WARN | SEC multi-unit, duplicate-period, and amendment facts | tags_with_multiple_units=2427, duplicate_period_groups=202372, amendment_observations=114700. | Expected source complexity. A later data contract must freeze tag/unit/period/amendment precedence; no priority is chosen by this audit. |
| WARN | Current S&P 500 survivorship bias | The supplied universe is current-constituent based. | Permitted for `DEV_CURRENT_UNIVERSE`; historical constituents are required before formal Gate A evidence. |
| WARN | Current GICS is not historical PIT GICS | Security master GICS is download-date reference data. | Permitted for DEV pipeline testing only; historical GICS is required for formal PIT sector-neutralization evidence. |
| WARN | RDQ/earnings-announcement availability unavailable | SEC facts retain `filed_date`; actual RDQ/timestamp is not supplied. | Use conservative `filed_date <= signal_date` for DEV; RDQ is a timing limitation/future enhancement. |
| WARN | Yahoo historical adjustment limitation | Delivered `close`, `adj_close`, dividend, and split fields need a frozen future corporate-action basis. | Do not infer historical market cap by directly multiplying price and SEC shares; formal data contract work remains required. |
| WARN | Historical member and delisting coverage pending | The delivered package is not a historical index-membership or delisting reconstruction. | Required for `FORMAL_PIT_UNIVERSE`, not a blocker for DEV intake. |

## DEV blockers

- None found by this Gate 0 DEV audit.

## DEV limitations that may proceed

- **SEC raw form and period-date scope** — Raw Company Facts is intentionally unfiltered. Later PIT parsing must freeze included forms, tag/unit/period rules, and treatment of non-economic/outlying period observations.
- **Yahoo OHLC/action plausibility and delivered quality flags** — WARN requires documented ticker/row treatment before a downstream object relies on affected observations; it does not invalidate the full DEV package.
- **SEC multi-unit, duplicate-period, and amendment facts** — Expected source complexity. A later data contract must freeze tag/unit/period/amendment precedence; no priority is chosen by this audit.
- **Current S&P 500 survivorship bias** — Permitted for `DEV_CURRENT_UNIVERSE`; historical constituents are required before formal Gate A evidence.
- **Current GICS is not historical PIT GICS** — Permitted for DEV pipeline testing only; historical GICS is required for formal PIT sector-neutralization evidence.
- **RDQ/earnings-announcement availability unavailable** — Use conservative `filed_date <= signal_date` for DEV; RDQ is a timing limitation/future enhancement.
- **Yahoo historical adjustment limitation** — Do not infer historical market cap by directly multiplying price and SEC shares; formal data contract work remains required.
- **Historical member and delisting coverage pending** — Required for `FORMAL_PIT_UNIVERSE`, not a blocker for DEV intake.

## Deferred to Formal PIT / WRDS or equivalent

- Historical S&P 500 membership and security/identifier history.
- Historical GICS/industry classification.
- Historical-member return, delisting, and corporate-action coverage if CRSP or an equivalent source is adopted.
- Optional PERMNO/GVKEY linking and any approved external fundamental cross-checks.
- RDQ/announcement timestamp enrichment, if acquired; the conservative DEV availability rule remains `filed_date <= signal_date`.

## Scope guard

This audit did not calculate factors, IC, portfolio returns, FMOM, or strategy results. It did not write to `data/canonical/` or modify `raw_package/`.
