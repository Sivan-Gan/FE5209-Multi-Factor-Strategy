# DEV Price-Factor Engineering Acceptance Report

- **Evidence stage:** `DEV_CURRENT_UNIVERSE`
- **Decision:** PASS WITH WARNINGS — DEV price-input engineering may proceed; this is not Formal PIT or Gate A evidence.
- **Scope:** canonical market inputs, weekly signal dates, MOM12-1, MOM6-1, 252-day realised volatility, and cross-sectional preprocessing only.

| Check | Status | Evidence / disposition |
| --- | --- | --- |
| Raw package integrity after build | PASS | Verified 508/508 payload files. |
| Canonical daily `(ticker, date)` generation | PASS | Generated 2392900 rows; duplicate count=0. |
| Canonical identity map and benchmark separation | PASS | Master-driven Yahoo-to-canonical mapping is retained; SPY is tagged benchmark-only. |
| Weekly signal calendar | PASS | Signal date is final actual SPY trading date in each ISO natural week; no Friday assumption. |
| Final delivered-week next trading date | WARN | The final signal has no later delivered SPY date, so `next_trading_date` is intentionally blank rather than inferred. |
| MOM12-1 / MOM6-1 timing | PASS | Adjusted-price offsets use 252/21 and 126/21 delivered market trading days and do not access post-signal prices. |
| Realised volatility rule | WARN | 252 daily-return window, minimum 200 valid adjacent-price returns, `sqrt(252)` annualisation; sample standard deviation (`ddof=1`) is explicitly documented as a DEV technical convention requiring confirmation before formal evidence freeze. |
| Cross-sectional preprocessing | PASS | 1%/99% winsorisation then z-score within US × signal date × metric only; no missing-to-zero conversion. |
| Neutralisation / IVOL | PASS (not performed) | Momentum/Low Risk neutralisation is Test; IVOL specification remains open and is not implemented. |

## Blocking assessment

No DEV coding blocker was identified by these acceptance checks when raw integrity passes. Current-S&P-500 survivorship bias, current (non-PIT) GICS, Yahoo adjustment limitations, RDQ absence, and missing historical membership/delisting inputs remain DEV limitations and Formal PIT requirements, not a failure of this DEV price-input phase.
