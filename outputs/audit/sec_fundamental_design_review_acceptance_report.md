# SEC Fundamental Design Review — DEV Acceptance Report

- **Evidence stage:** `DEV_CURRENT_UNIVERSE`
- **Decision:** PASS WITH WARNINGS — the source evidence supports the documented recommendations, but it does not itself freeze a Total Debt definition, Revenue/Cost fallback chain, foreign-form policy, or ambiguity policy.
- **Canonical PIT construction:** NOT YET AUTHORIZED automatically. It requires explicit human sign-off on the documented recommendations and remaining choices.

| Check | Status | Evidence / disposition |
| --- | --- | --- |
| Raw package integrity after design review | PASS | Verified 508/508 payload files; failures=0. |
| Gross Profit fallback evidence | WARN | Five named Revenue/Cost pairs were tested against exact GrossProfit only where the raw filing/period identity matched. Semantic approval remains pair-specific. |
| Leverage / Total Debt alternatives | WARN | Coverage and double-counting risks are documented, but the frozen manual does not choose the debt/lease scope. |
| OCF minus CapEx input compatibility | PASS WITH EXCEPTIONS | 41736 matched source periods; 73 matched negative CapEx observations remain exceptions and are not inverted. |
| 20-F / 40-F source impact | WARN | Measured by audited concept and taxonomy; no automatic exclusion/inclusion rule is inferred. |
| Latest same-filed conflicting values | WARN | 12 / 380233 same-tag latest groups conflict; future construction must retain them as ambiguous/missing unless a supported deterministic rule is approved. |
| Prohibited outputs | PASS | No canonical panel, financial factors/ratios, FCF Yield, market cap, preprocessing, IC, portfolio, FMOM, backtest, or strategy result was written. |

## Remaining construction conditions

1. Select and approve the leverage numerator/lease scope; do not infer it from coverage.
2. Approve or reject each Gross Profit fallback pair only within its stated semantic scope.
3. Approve FCF treatment of the rare negative CapEx-source observations (the recommendation is exception/missing, never silent absolute-value conversion).
4. Approve 20-F/40-F use on the measured issuer/concept evidence.
5. Adopt the proposed safe rule that unresolved latest-same-filed conflicts are `ambiguous/missing`.
