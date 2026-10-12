# Project Status — Formal US Research Delivery

## Completed

- Formal WRDS Gate 0 sign-off and data-contract evidence.
- Formal canonical dataset construction.
- Candidate-factor definition freeze and construction.
- Pre-Gate-A factor validation.
- Gate A0 within-family governance, construction, validation, and representative freeze.
- Gate A human review and Final Factor Set freeze.
- Final Factor Handoff interface export and acceptance.

## Current Frozen Factors

| Order | Family | Specification ID |
| --- | --- | --- |
| 1 | Quality | `QUALITY_CURRENT` |
| 2 | Momentum 12-1 | `MOMENTUM_12_1` |
| 3 | Low Risk | `LOWRISK_CURRENT` |
| 4 | Investment | `INVESTMENT_CURRENT` |

The frozen Final Factor Set is K=4. Value, Dividend, and Size are not active Base Strategy factors.

## Current Delivery Boundary

The governed final-factor signals, availability flags, and universe interface are prepared for a future Strategy Engine. This does not authorize calculation of returns, factor returns, FMOM, factor weights, composite scores, portfolio holdings, or strategy outcomes.

## Not Started

- Gate B / FMOM configuration.
- Base Strategy construction.
- 2016–2020 configuration evaluation.
- 2021–2025 Formal Historical OOS evaluation.
- Singapore external transfer.
- Adaptation, final revalidation, attribution, capacity, and stress testing.

## Governance Reminder

The SSOT remains [US_SG_Cross_Market_Quant_Research.md](US_SG_Cross_Market_Quant_Research.md). `HIST_OOS_BASE` is protected; it may not be used for parameter tuning. See [GITHUB_TEAM_HANDOFF.md](GITHUB_TEAM_HANDOFF.md) before starting any subsequent stage.
