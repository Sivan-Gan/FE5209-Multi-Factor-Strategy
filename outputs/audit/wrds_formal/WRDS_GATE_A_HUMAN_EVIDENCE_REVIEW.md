# Formal Gate A — Human Evidence Review

## 1. Purpose

This package reorganizes frozen candidate-factor validation evidence for human Gate-A discussion. It does not make an automated or final factor decision.

## 2. Governance / performance firewall

Formal review evidence is 2011–2015 only. DEV 2006–2010 is separate context. No 2016–2025 outcome is calculated, accessed, or used.

## 3. Evidence dimensions

Economic rationale; forward IC; portfolio evidence; persistence; sector breadth; complementarity; turnover/cost; coverage and implementation limitations.

## 4. Aggregate factor comparison

| Factor | 1W mean IC | 4W mean IC | Spread | Spread Sharpe | Top turnover |
| --- | --- | --- | --- | --- | --- |
| Size | -0.0059 | -0.0078 | 0.0001 | 0.0461 | 0.0314 |
| Momentum | 0.0215 | 0.0278 | 0.0009 | 0.4454 | 0.1213 |
| Low Risk | 0.0144 | 0.0230 | 0.0008 | 0.2910 | 0.0255 |
| Value | 0.0011 | -0.0015 | 0.0001 | 0.1173 | 0.0466 |
| Quality | 0.0029 | 0.0017 | 0.0003 | 0.3615 | 0.0262 |
| Investment | 0.0032 | 0.0105 | 0.0005 | 0.7488 | 0.0471 |
| Dividend | 0.0012 | 0.0030 | 0.0000 | 0.0015 | 0.0347 |

## 5. IC comparison

See `gate_a_yearly_ic_review.csv`, `gate_a_ic_stability_review.csv`, and the factor review sheets. All IC statistics retain their original construction and inference conventions.

## 6. Year-by-year IC stability

Each calendar-year row is retained without an aggregate stability score. Rolling windows end no later than 2015-12-31.

## 7. Portfolio comparison

Annual portfolio tables compound existing weekly gross evidence only. No net-cost scenario is created.

## 8. Year-by-year portfolio stability

`maximum_year_contribution_share` is max(abs(calendar-year compounded spread)) divided by sum(abs(calendar-year compounded spread)); it is descriptive concentration context only.

## 9. Sector breadth

Sector evidence uses the historical GICS evidence already validated at signal dates. It does not make a sector-neutrality assertion.

## 10. Score complementarity

The fixed-order 7×7 matrix reports mean same-date cross-sectional Spearman correlations for 2011–2015 only.

## 11. Spread-return complementarity

The fixed-order 7×7 matrix reports Pearson correlations of overlapping weekly gross spreads for 2011–2015 only.

## 12. Common-sample robustness

Common-7 results are diagnostic comparisons and never overwrite primary factor-specific evidence.

## 13. Turnover / break-even cost

Break-even values are gross-wealth diagnostics. No frozen cost grid exists, so no illustrative net scenario is shown.

## 14. Coverage / limitations

Coverage remains descriptive. Factor-specific structural limitations are carried forward from the frozen rationale ledger.

## 15. Factor-by-factor evidence summary

See `gate_a_factor_review/` for seven fixed-format sheets in canonical factor order.

## 16. Open questions for human Gate-A decision

See `gate_a_open_questions.csv`; questions identify evidence tensions without embedding an answer or a threshold.

## 17. Explicit prohibited next steps before human decision

Do not alter factor definitions, create factor weights, configure Factor Momentum, build a multi-factor strategy, or evaluate 2016–2025 outcomes.
