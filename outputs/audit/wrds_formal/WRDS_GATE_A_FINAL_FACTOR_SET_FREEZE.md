# WRDS Gate A Final Factor Set Freeze

## 1. Executive Freeze Verdict

**GATE A FINAL FACTOR SET FREEZE — PASS WITH WARNINGS.** The one inherited non-blocking warning is the refreshed evidence package's disclosure that no frozen transaction-cost grid exists; it does not alter the human Gate A decision. Blockers: **0**.

## 2. Research Lineage

This human decision follows the frozen Gate A0 representative decision and the immutable refreshed Gate A evidence package, both bounded to the 2011–2015 Factor Selection sample. Freeze parent commit: `c9054c42bdf75ada808a1f27427facfd47aa301f`.

## 3. Gate A Purpose

Gate A determines which frozen family representatives enter the US Base Strategy Final Factor Set. It does not construct scores, strategy weights, FMOM, stock portfolios, or a strategy backtest.

## 4. Gate A0 Frozen Representatives

`VALUE_CURRENT`, `QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, `INVESTMENT_CURRENT`, `DIVIDEND_CURRENT`, and `SIZE_CURRENT` remain the exact Gate A0 representative map. No Gate A0 specification is changed.

## 5. Human Gate A Decision Framework

This is an approved human, multi-dimensional decision considering economic rationale, Forward IC, stability, single-factor portfolio evidence, tradability, coverage, sector breadth, and complementarity. It is not an automated rank, score, p-value, or BH-q-value threshold decision.

## 6. Final Factor Set

```
FINAL_FACTOR_SET_US = {
    QUALITY_CURRENT,
    MOMENTUM_12_1,
    LOWRISK_CURRENT,
    INVESTMENT_CURRENT
}
K = 4
```

Canonical order: Quality, Momentum, Low Risk, Investment.

## 7. Quality — KEEP

`QUALITY_CURRENT` is retained as the **WEAKEST_RETAINED_FACTOR**. Its rank-IC evidence is modest, but portfolio evidence, low turnover, common-sample persistence, economic rationale, and complementarity justify retention. Later OOS cannot reopen this decision.

## 8. Momentum — KEEP

`MOMENTUM_12_1` is retained. It has the strongest overall Gate A evidence across 1W/4W IC, yearly consistency, rolling stability, spread evidence, common-seven robustness, sector breadth, and implementation economics. `MOMENTUM_CURRENT` is not reintroduced.

## 9. Low Risk — KEEP

`LOWRISK_CURRENT` is retained for positive 1W/4W IC point estimates, reasonable stability, positive tradable spread evidence, low turnover, and distinct risk information.

## 10. Investment — KEEP

`INVESTMENT_CURRENT` is retained for useful medium-horizon predictive evidence, strong portfolio evidence, favorable formal-sample spread Sharpe, rolling stability, and complementary investment economics. Weaker short-horizon rank IC is not an automatic rejection rule.

## 11. Value — DROP

`VALUE_CURRENT` is excluded from the Base Strategy because its standalone evidence is insufficient: 1W predictive evidence is near zero, mean 4W IC is negative, and tradable spread evidence is comparatively weak. Diversification alone does not override weak standalone evidence. `VALUE_FCF_COMPONENT` remains diagnostic-only.

## 12. Dividend — DROP

`DIVIDEND_CURRENT` is excluded because predictive and spread evidence is weak, while redundancy with Low Risk is material.

## 13. Size — DROP

`SIZE_CURRENT` is excluded. Under the pre-frozen smaller-issuer-is-higher-score direction, formal Gate A mean 1W and 4W IC are negative and portfolio evidence is weak. The direction is not reversed into a large-cap factor.

## 14. Statistical-Evidence Interpretation

No mechanical p-value or BH-q-value threshold is required. The freeze does not claim that all retained factors demonstrate statistically significant predictive alpha; statistical inference is one dimension of the multi-dimensional decision framework.

## 15. Complementarity Interpretation

Complementarity can support an otherwise adequate retained factor, but diversification or negative correlation alone does not promote a weak standalone factor. The retained four span Quality, price-trend Momentum, Low Risk, and Investment economics.

## 16. Treatment of Dropped Factors

Value, Dividend, and Size are excluded from the Base Strategy composite and Base Strategy FMOM universe. Their historical scores, validation evidence, and audit trail are retained for audit, attribution, and authorized robustness research. Drop does not mean deletion.

## 17. Round-2 Challenger Status

**ROUND_2_FACTOR_SEARCH = NOT_AUTHORIZED.** FCF-only Value, GP/A-focused Quality, RMW, RMWc, Trend Momentum, Low Beta, BAB, Shareholder Yield, Net Payout Yield, EV-based Value, and any newly invented specification remain future-research hypotheses only.

## 18. OOS / Contamination Status

The human decision uses 2011–2015 only; that adaptive design period is not labelled untouched. 2016–2020 Configuration Selection, 2021–2025 Formal Historical OOS, and 2026 performance are all `NOT_ACCESSED`. Protected future periods are not contaminated.

## 19. Frozen Downstream Contract

Future Base Strategy documentation must use the exact four-factor universe and canonical order above:

$$ Score_{i,t}=\sum_{k\in\mathcal F^{Final}_{US}}w_{k,t}F_{k,i,t}. $$

This freeze calculates no weights, scores, FMOM, portfolios, or backtest. The eventual FMOM universe contains exactly the four retained families and may not assign Value, Dividend, or Size a Base Strategy weight. $J$, $\lambda$, and all configuration decisions remain pending Gate B.

## 20. Exact Next Permitted Step

Prepare the **Final Factor Handoff Package** from these frozen artifacts. Gate B, FMOM configuration, factor-return calculation, stock-score construction, and strategy testing remain outside this freeze.
