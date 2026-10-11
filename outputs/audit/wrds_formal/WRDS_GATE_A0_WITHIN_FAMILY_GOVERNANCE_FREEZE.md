# Formal Gate A0 — Within-Family Specification Governance Freeze

## 1. Why Gate A0 was introduced

The initial equal-weight family composites can provide weak family-level evidence for two distinct reasons: the economic family may be weak, or heterogeneous constituent signals may dilute one another when equally aggregated. Gate A0 separates these questions before any cross-family Final Factor Set decision.

## 2. Historical framework state

The historical Candidate Factor Pool contains seven economic families: Value, Quality, Momentum, Low Risk, Investment, Dividend, and Size. Multi-component families previously used equal-weight internal aggregation. The initial seven-family validation and the Gate-A human evidence package evaluated those incumbent definitions and remain preserved research artifacts.

Commit `d19485a58ae5fc60520c167e13e11a458cf9c596` is the freeze point for **INITIAL INCUMBENT FAMILY-LEVEL GATE-A EVIDENCE**. This governance record neither overwrites those outputs nor claims that their prior review included Gate A0 comparison.

## 3. Adaptive-research disclosure

Gate A0 was introduced after incumbent family-level 2011–2015 evidence had been reviewed. It is therefore an adaptive research-design refinement inside the designated Factor Selection sample, not a fully pre-performance specification search. The candidate pool is deliberately small, discrete, limited to already frozen constituent definitions, and does not permit continuous internal-weight optimization.

## 4. Protected later-period samples

No 2016–2020, 2021–2025, or 2026 performance outcome is accessed for this governance freeze or may be used for Gate A0 selection. Later Configuration Selection and Formal Historical OOS firewalls remain intact. The 2011–2015 Factor Selection sample must not now be described as untouched for the Gate A0 design question.

## 5. Constituent metric versus factor family

A constituent metric is an economic input such as E/P, ROE, MOM12-1, or -IVOL252. A factor family is the broader economic style such as Value, Quality, Momentum, or Low Risk. A domain-specific component diagnostic is not automatically an eligible whole-market family representative.

## 6. Gate A0 versus Gate A

Gate A0 asks: **How should an economic style be measured?** It freezes at most one representative specification per family. Gate A asks: **Should that economic style enter the final multi-factor strategy?** It compares the seven family representatives and freezes the Final Factor Set. No Gate A0 or Gate A selection is made by this artifact.

## 7. Frozen specification pool

The complete row-level contract is `gate_a0_within_family_specification_registry.csv`.

- Value: `VALUE_CURRENT`, `VALUE_EP`, `VALUE_BP`, `VALUE_EP_BP`; `VALUE_FCF_COMPONENT` is diagnostic only.
- Quality: `QUALITY_CURRENT`, `QUALITY_ROE`, `QUALITY_PROFITABILITY`; `QUALITY_GPA_COMPONENT`, `QUALITY_NEG_LEVERAGE_COMPONENT`, and `QUALITY_ROA_COMPONENT` are diagnostic only.
- Momentum: `MOMENTUM_CURRENT`, `MOMENTUM_12_1`, `MOMENTUM_6_1`.
- Low Risk: `LOWRISK_CURRENT`, `LOWRISK_VOL`, `LOWRISK_IVOL`.
- Single-specification families: `INVESTMENT_CURRENT`, `DIVIDEND_CURRENT`, `SIZE_CURRENT`.

All registry rows are `EXISTING_CONSTITUENT_ROUND` only.

## 8. Eligible candidate versus component diagnostic

Eligible family representatives can participate in the later within-family comparison. Component diagnostics are retained to understand branch-specific inputs but have `eligible_family_representative = FALSE`. They cannot become standalone cross-family factors in Round 1. This prevents a Financials / Real Estate-only ROA or a nonfinancial-only FCF Yield diagnostic from being silently promoted to a whole-market factor.

## 9. Score-construction inheritance rules

Future Gate A0 construction must reuse the corresponding incumbent source data, PIT rules, timing, economic level, direction, winsorization, standardization, availability, and source precedence. It must not recompute accounting concepts or change definitions. Value and Quality retain mandatory historical-GICS Sector plus log issuer-market-cap neutralization. Momentum and Low Risk retain no baseline neutralization. Component diagnostics retain their natural domain.

## 10. Common-within-family sample rule

Each multi-specification family must later receive two views: (i) its natural factor-specific PRIMARY sample and (ii) a `COMMON_WITHIN_FAMILY_SAMPLE` on which all eligible representatives of that family are available. The common view is diagnostic and cannot silently replace primary evidence.

## 11. Future validation contract

When separately authorized, every eligible representative receives the same 2011–2015-only validation engine: economic rationale, coverage, executable 1W and 4W IC, ICIR, positive IC ratio, HAC / Newey-West and 4W block-bootstrap inference, yearly and rolling stability, Top/Bottom 30% gross portfolio evidence, turnover, break-even cost, yearly portfolio stability, applicable sector breadth, common-sample robustness, and within-family redundancy diagnostics. The execution convention remains `S_t → E_t → E_{t+1}`.

## 12. Human decision rule

No fixed IC or Sharpe threshold, weighted score, automated rank, or machine-selected winner is permitted. Human reviewers must jointly weigh integrity, economics, predictive evidence, stability, portfolio separation, turnover / break-even economics, coverage, common-sample robustness, redundancy, interpretability, and multiple-testing evidence.

## 13. Incumbent-default rule

If challenger evidence does not provide a clear, economically coherent, documented reason to replace an incumbent, retain the incumbent. A single higher historical point estimate is insufficient. No numeric `clear superiority` threshold is introduced.

## 14. One-representative-per-family rule

After Gate A0, an economic family has at most one formal representative entering Gate A. Momentum cannot enter Gate A separately as both MOM12-1 and MOM6-1. Component diagnostics never receive a separate cross-family vote in this round.

## 15. Multiple-testing governance

Future Gate A0 evidence must record family identifier, eligible-specification count, component-diagnostic count, IC-horizon count, raw p-values, and Benjamini–Hochberg FDR-adjusted q-values. It must distinguish within-family testing from later cross-family Gate A testing. The smallest p-value cannot mechanically choose a specification.

## 16. Explicitly excluded literature challengers

Round 1 excludes RMW / Operating Profitability, Cash Profitability / RMWc, Trend Momentum, Low Beta / BAB, Shareholder Yield / Net Payout Yield, EV-based Value, and any unlisted challenger. A Round-2 literature-motivated pool requires separate governance authorization after Round-1 review.

## 17. Exact next permitted step

**Gate A0 existing-constituent factor construction, using only the frozen registry, may begin after this governance freeze passes.** Construction must be followed by the separately authorized identical Gate A0 validation contract before any human representative decision.

## 18. Explicit prohibited actions

This governance freeze does not authorize a Gate A0 representative choice, Gate A Final Factor Set decision, continuous internal-weight optimization, a new literature challenger, FMOM configuration, multi-factor strategy construction, or access to 2016–2025 performance. It does not change `wrds_raw/`, `raw_package/`, canonical data, candidate-factor data, validation data, dividend extension, prior factor-validation outputs, or existing Gate-A review outputs.

Gate A0 existing-constituent factor construction is now permitted.
