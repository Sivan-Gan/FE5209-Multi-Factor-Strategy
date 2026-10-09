# Leverage Definition Targeted Audit — DEV Acceptance Report

- **Evidence stage:** `DEV_CURRENT_UNIVERSE`
- **Decision:** PASS WITH WARNINGS — a DEV baseline recommendation is evidence-supported, but it is not a canonical panel or a Quality-score implementation.
- **Recommended baseline:** `Liabilities / Assets` for the Current-GICS ordinary non-financial branch excluding Real Estate, subject to the documented Financials/Real Estate boundary.

| Check | Status | Evidence / disposition |
| --- | --- | --- |
| Raw package integrity | PASS | Verified 508/508 payload files. |
| Required candidate definitions | PASS | Compared debt-components/assets, liabilities/assets, assets/equity, and liabilities/equity using exact same-filing source inputs. |
| Baseline source coverage | PASS WITH WARNINGS | In Current-GICS ordinary non-financials excluding Real Estate, Liabilities/Assets has 269 issuers and 29619 same-filing source periods; exact Liabilities has lower raw issuer coverage than Assets. |
| Debt-only alternative | PASS WITH WARNINGS | Current/non-current long-term-debt components have 272 issuers and require controlled aggregation; they explicitly exclude capital leases. |
| Equity-denominator alternatives | WARN | Zero/negative StockholdersEquity observations create undefined/sign-reversing and unbounded-tail risk. |
| Financials/RE branch | WARN | The frozen manual uses ROE/ROA for Financials; Real Estate requires a separately documented comparable-liability decision. Current GICS is DEV-only. |
| Prohibited outputs | PASS | No individual ratio, Quality score, z-score, IC, portfolio, backtest, or strategy result was written. |
