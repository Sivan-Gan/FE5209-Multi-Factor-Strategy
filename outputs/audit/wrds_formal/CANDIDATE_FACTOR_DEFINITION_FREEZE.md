# Formal US Candidate-Factor Definition Freeze

**Decision date:** 2026-10-11
**Historical predecessor:** `cd0151bd1df80b1834ce4cfe04b7e967adc6cbde` — `Record blocked formal US candidate-factor construction`

## Purpose

This record freezes the previously unresolved Formal US candidate-factor design questions.  It is a research-design decision register, not a factor-selection result.

**No performance evidence was consulted in freezing these definitions.** No IC, return, Sharpe, alpha, portfolio, factor-selection, or backtest result was used to choose them.

## Resolved prior blockers

| Prior blocker | Frozen decision |
|---|---|
| Size orientation | `SIZE_RAW = -log(positive canonical issuer market cap)`; no Size neutralization. |
| Value source precedence | Quarterly-derived TTM is primary for flows; quarterly point-in-time is primary for stocks; whole-metric annual fallback only. |
| Quality timing and Leverage | Paired quarterly TTM / latest quarterly denominator primary; paired same-observation annual fallback; Leverage is debt divided by assets and Quality uses its negative. |
| Investment lag | Exact same-fiscal-quarter prior-year quarterly asset match; annual same-issuer prior-year fallback only. |
| Neutralization controls | Positive issuer-level log market cap plus all historical GICS Sector dummies, with no intercept. |
| Dividend definition | Security-level trailing 365-calendar-day ordinary cash-dividend yield on an audited split-normalized per-share basis. |

## Operational boundary

The economic Dividend definition is frozen here.  Its canonical event extension and split-normalization semantics remain a separate Stage-B operational acceptance condition.  If those exact semantics cannot be established from audited CRSP fields, Dividend remains unavailable rather than receiving a substitute definition.

The complete field-level register is in `candidate_factor_definition_freeze.csv`; the superseding frozen rules are in the SSOT section **Formal US Candidate-Factor Definition Freeze — 2026-10-11**.
