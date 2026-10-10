# Formal US Candidate Factor Construction Report

## 1. Executive verdict

**CANDIDATE FACTOR CONSTRUCTION — BLOCKED.** Frozen-definition blockers prevent trustworthy factor validation for Size, Value, Quality, Investment, and Dividend.

## 2. Canonical freeze point

Canonical logical hash: `de1bf079e6399025ebde6a81883bbad21aec9c5443dbb39f7ca2f00b5a409c6e`.

## 3. Factor architecture

Derived-only SQLite at `data/factors/us_formal/us_candidate_factors.sqlite`, grain `(signal_date, PERMNO)`, 523,909 rows.

## 4. Size

Market-cap and log-market-cap diagnostics are preserved; economic level and candidate direction are not frozen.

## 5. Momentum

MOM12-1 and MOM6-1 use direct CIZ DlyRet, fixed trading-day windows and a 21-day skip. No forward return is attached.

## 6. Low Risk

Trailing native daily Vol and residual IVOL are constructed with >=200 observations; each component retains its own dates and observation count. IVOL uses 1000080 DlyTotRet.

## 7. Value

Blocked: no annual/quarterly PIT precedence is frozen for E/P, B/P, or FCF Yield.

## 8. Quality

Blocked: ROE/ROA time convention and leverage definition are not frozen.

## 9. Investment

Blocked: one-year PIT asset lag logic is not operationally frozen.

## 10. Dividend

Blocked: no canonical cash-dividend event layer exists.

## 11. Winsorization

Implemented for constructible metrics at 1/99 within each signal-date cross-section.

## 12. Z-score standardization

Implemented only after same-date winsorization, with missing metrics retained missing.

## 13. Branch logic

Historical GICS 40/60 identifies Financials/Real Estate; blocked branch factors are not substituted.

## 14. Neutralization

Not run: GICS neutralization level and market-cap control economic level require design confirmation.

## 15. Availability rules

Full fixed Momentum history; >=200 valid daily returns for Vol/IVOL; no missing-to-zero substitution.

## 16. Factor coverage

See candidate_factor_availability_qa.csv and candidate_factor_availability_by_period_qa.csv.

## 17. Metric distribution QA

See candidate_factor_metric_distribution_qa.csv.

## 18. Lookback QA

See candidate_factor_momentum_qa.csv and candidate_factor_lowrisk_qa.csv.

## 19. PIT / no-lookahead checks

No forward returns or future-dated labels are created.

## 20. Unit / issuer-denominator checks

No Value ratio is constructed without frozen numerator precedence; no partial issuer denominator is used.

## 21. Gate-0 warning propagation

Canonical availability and historical-GICS limitations propagate unchanged.

## 22. Determinism

See candidate_factor_determinism.csv.

## 23. Tests

Focused tests cover preprocessing, window construction, availability and prohibited-output contracts.

## 24. Candidate-factor acceptance matrix

See candidate_factor_acceptance.csv.

## 25. Remaining warnings/blockers

The five definition/dependency blockers are explicit; no silent substitutions occurred.

## 26. Exact next permitted step

None until blocked frozen definitions are confirmed.

## 27. Explicitly prohibited next steps before review

Forward IC, Single-Factor Validation, factor portfolios, FMOM, stock selection and all backtests remain prohibited.
