# Canonical PIT Fundamental Construction — DEV Acceptance Report

- **Evidence stage:** `DEV_CURRENT_UNIVERSE`
- **Decision:** PASS WITH WARNINGS
- **Scope:** canonical SEC source inputs and weekly PIT availability/provenance only; no ratio, factor, score, IC, portfolio, strategy, or historical market-cap output is created.

| Check | Status | Evidence / disposition |
| --- | --- | --- |
| Raw package integrity | PASS | 508/508 payload hashes verified. |
| SEC archive/mapping coverage | PASS | 497 raw issuer archives; 497 mapped CIKs; 3 current-master securities without SEC mapping are explicitly outside the SEC coverage denominator. |
| PIT no-look-ahead | PASS | Snapshot rows with filed_date > signal_date: 0. |
| Provenance uniqueness | PASS | Non-unique snapshot keys: 0; each available row carries one source observation ID and full source provenance. |
| Unit/taxonomy/form controls | PASS | Only USD `us-gaap` signed tags in 10-K/10-Q/US-GAAP-20-F families are available; no unit conversion or IFRS mapping occurs. |
| Amendment timing | PASS | Amendments can enter only on or after their own filed date; amendment switches are reported separately. |
| Conflict handling | PASS WITH WARNINGS | Same-latest-filed-value conflicts remain `AMBIGUOUS`/missing; no value/order/accession tie-break occurs. |
| Open implementation issues | PASS WITH WARNINGS | 2475 slot/date instances lack a signed deterministic provenance rule and remain missing; the issue audit is aggregated by ticker/concept/class. |
| Negative CapEx | PASS WITH WARNINGS | Negative primary CapEx remains `SIGN_EXCEPTION`/unavailable; no sign correction occurs. |
| Period separation | PASS | Instant, annual, quarterly, YTD, and irregular duration facts remain independently stored; no final hierarchy is imposed. |
| Prohibited downstream results | PASS | No ROE, ROA, GP/A, Leverage, Asset Growth, FCF, valuation, factor, IC, portfolio, or strategy output is written. |

## DEV blockers

No selector-integrity DEV blocker is present when the PASS checks above hold. A future factor-engineering step must still specify how the separately stored period classes are used; this construction deliberately does not decide that hierarchy.

## DEV warnings

Current-S&P-500 survivorship bias, non-PIT Current GICS, missing RDQ/timestamps, direct-Gross-Profit coverage limits, sign/ambiguity/open-provenance missingness, and current-master securities without an SEC mapping remain disclosed DEV warnings.

## Formal PIT limitations

Historical S&P 500 membership, historical security/identifier history, historical GICS, CRSP delistings and returns, Compustat/GVKEY/PERMNO links, and historical market-cap basis are absent. They require a later `FORMAL_PIT_UNIVERSE` rerun and do not by themselves cause this DEV construction to fail.
