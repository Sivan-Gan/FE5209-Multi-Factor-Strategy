# Residual Extreme Market-Cap Jump Root-Cause Audit — DEV

- **Evidence stage:** `DEV_CURRENT_UNIVERSE`
- **Scope:** read-only canonical-source audit and non-mutating simulations only.
- **Decision:** `FAIL` for safe SEC + Yahoo DEV market-cap governance closure.
- **Stop-loss decision:** `STOP_DEV_MARKET_CAP_REPAIR`.

## Scope and fixed boundaries

This audit does not modify the frozen manual, raw package, canonical market-cap object, share scale, tag selection, split chain, or price history. It performs no factor, Value, Size, neutralization, IC, portfolio, FMOM, or strategy calculation. A threshold is only an audit trigger; it is not itself a rejection rule.

## Complete event population and decomposition

There are **85** available, exact-consecutive-week events with `J^MCap > 10` or `< 0.1` across **41** tickers. Cumulative trigger counts are 85 at 10x, 79 at 100x, 61 at 1,000x, and 17 at 1,000,000x. Exclusive maximum bands are: >10x only 6; >100x 18; >1,000x 44; >1,000,000x 17. The detailed threshold summary is preserved in the simulation CSV's baseline row.

Every triggered event is decomposed as `J^MCap = J^Price × J^AlignedShares`. Dominant-component count: price-driven 0; shares-driven 85; both 0. This is accounting decomposition, not a causal assertion.

## Root-cause evidence

Classifications are: `SHARES_SEMANTIC_ANOMALY` 68; `SECURITY_HISTORY_BREAK` 0; `UNRESOLVED_EXTREME_JUMP` 17; `VALID_PRICE_MOVE` 0; `VALID_STANDARD_SPLIT` 0; `VALID_CORPORATE_EVENT` 0.

The source-sequence audit records 89 fact observations in confirmed two-sided local semantic-break segments and 72 facts in one-sided discontinuity segments. A confirmed segment has normal-scale source evidence on both sides and no matching delivered standard split. One-sided cases remain unresolved: CompanyFacts does not preserve original XBRL context/dimensions, and the package has no historical identifier-link or transaction dataset.

## Mandatory cases

- **VTRS:** 2021-02-26 → 2021-03-05; market-cap 11306746.6×; price 0.936700303×; aligned shares 12070826.2×; classification `UNRESOLVED_EXTREME_JUMP` (LOW). The preceding DEI fact is 100 shares from `0001193125-20-290294`, filed/as-of 2020-11-10, form 10-Q, unit shares. The next fact is 1,207,082,624 shares from `0001792044-21-000009`, filed 2021-03-01/as-of 2021-02-22, form 10-K. Same-accession US-GAAP diagnostics also show 100 for the old filing; there is no automatic alternate fallback. No delivered matching split is present. Current data cannot distinguish an issuer-transition/history issue from an unresolved source semantic issue, so it is **not safe to reject only that ticker by hand**.
- **CTVA:** 2019-08-02 → 2019-08-09; market-cap 7529398.03×; price 1.00540369×; aligned shares 7488930.17×; classification `UNRESOLVED_EXTREME_JUMP` (LOW).
- **ETN:** 2013-02-22 → 2013-03-01; market-cap 4940439.49×; price 1.04870293×; aligned shares 4711000×; classification `UNRESOLVED_EXTREME_JUMP` (LOW).
- **SW:** 2024-08-02 → 2024-08-09; market-cap 4843035.04×; price 0.932503595×; aligned shares 5193583.23×; classification `UNRESOLVED_EXTREME_JUMP` (LOW). Delivered price history starts 2024-07-08 and does not show a pre-listing price splice in the current master; no `PRICE_HISTORY_START_OVERRIDES` configuration exists to claim an independent solution.
- **PSKY:** 2025-11-07 → 2025-11-14; market-cap 1112830.34×; price 1.03841059×; aligned shares 1071666.98×; classification `UNRESOLVED_EXTREME_JUMP` (LOW).
- **CRM:** 2011-09-02 → 2011-09-09; market-cap 1008838.32×; price 0.996925837×; aligned shares 1011949.22×; classification `SHARES_SEMANTIC_ANOMALY` (HIGH).

## Security identity / history boundary

The current security master supplies one current ticker/CIK mapping only. It cannot verify historical CIK stability, ticker reuse, predecessor/successor relationships, merger/spin-off treatment, or listing migration. Therefore this audit confirms **zero** `SECURITY_HISTORY_BREAK` classifications and recommends no history truncation. Potential mismatches are documented as unresolved candidates rather than inferred corporate history.

## Uniform-rule candidates and simulations

Rule A (conservative candidate) marks only source observations inside confirmed two-sided `normal → scale-discontinuous → normal` segments unavailable. It is cross-ticker and source-provenance-based; it never rescales a value or substitutes another tag. Rule B cannot be simulated because no delivered evidence validates a historical identity break or a valid start date. Rule C would mark every source-observation range touching an unresolved >10x event unavailable. It is deterministic but **not safe for sign-off**: it would turn unresolved corporate/identity questions into blanket exclusions without the required evidence.

Rule A leaves 5 million-level, 10 thousand-level, and 11 hundred-level events; its largest remaining weekly absolute jump is 11306746.6×. Rule A + Rule C mechanically removes all direct triggered pairs in the current simulation, but this does not validate Rule C and therefore cannot justify a canonical patch/rebuild.

## Coverage impact

Coverage rows report baseline and simulated results against the 500 SEC-mapped reference denominator. They are input-availability joins only; no E/P, B/P, FCF Yield, Value score, or Size value is calculated.

| Scenario | Measure | Window | Before | After | Change (pp) | Removed available ticker-weeks |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| RULE_A_CONFIRMED_SHARES_SEMANTIC_BREAK | market_cap_input | factor_selection_2011_2015 | 69.7387% | 69.2031% | -0.5356 | 699 |
| RULE_A_CONFIRMED_SHARES_SEMANTIC_BREAK | value_two_of_three_input | factor_selection_2011_2015 | 63.6107% | 63.0874% | -0.5234 | 683 |
| RULE_A_CONFIRMED_SHARES_SEMANTIC_BREAK | market_cap_input | configuration_selection_2016_2020 | 80.2751% | 80.1134% | -0.1617 | 211 |
| RULE_A_CONFIRMED_SHARES_SEMANTIC_BREAK | value_two_of_three_input | configuration_selection_2016_2020 | 77.8322% | 77.6705% | -0.1617 | 211 |
| RULE_A_CONFIRMED_SHARES_SEMANTIC_BREAK | market_cap_input | historical_oos_2021_2026_09 | 88.5953% | 88.4678% | -0.1275 | 190 |
| RULE_A_CONFIRMED_SHARES_SEMANTIC_BREAK | value_two_of_three_input | historical_oos_2021_2026_09 | 86.8101% | 86.6826% | -0.1275 | 190 |
| RULE_A_PLUS_RULE_C_UNRESOLVED_SOURCE_RANGES | market_cap_input | factor_selection_2011_2015 | 69.7387% | 69.1655% | -0.5732 | 748 |
| RULE_A_PLUS_RULE_C_UNRESOLVED_SOURCE_RANGES | value_two_of_three_input | factor_selection_2011_2015 | 63.6107% | 63.0674% | -0.5433 | 709 |
| RULE_A_PLUS_RULE_C_UNRESOLVED_SOURCE_RANGES | market_cap_input | configuration_selection_2016_2020 | 80.2751% | 80.0000% | -0.2751 | 359 |
| RULE_A_PLUS_RULE_C_UNRESOLVED_SOURCE_RANGES | value_two_of_three_input | configuration_selection_2016_2020 | 77.8322% | 77.6330% | -0.1992 | 260 |
| RULE_A_PLUS_RULE_C_UNRESOLVED_SOURCE_RANGES | market_cap_input | historical_oos_2021_2026_09 | 88.5953% | 88.3805% | -0.2148 | 320 |
| RULE_A_PLUS_RULE_C_UNRESOLVED_SOURCE_RANGES | value_two_of_three_input | historical_oos_2021_2026_09 | 86.8101% | 86.6322% | -0.1779 | 265 |

## Stop-loss conclusion

The evidence supports a limited, portable Rule A for confirmed local source semantic breaks, but it does not safely govern every severe event. A broader Rule C would require treating unresolved issuer-transition, corporate-event, class/context, and possible history-identity cases as interchangeable missingness without validating them. That is not an auditable closure standard. Accordingly, retain the existing `SEC_YAHOO_ESTIMATE_DEV` object only as a diagnostic artifact; do not patch/rebuild it; allow only market-cap-independent Momentum/Volatility development to proceed under separately authorized work; and defer Value, Size, and size-neutralization research to an approved WRDS/CRSP-style formal identity/market-equity source.

## Formal limitations

Current-S&P-500 survivorship bias, current GICS, periodic SEC shares, unavailable RDQ, Yahoo retrospective price/action adjustments, missing historical delisting universe, absent historical security identifiers, and absence of formal market equity remain. This evidence must never be described as Formal PIT market cap.

## Artefacts

- `residual_extreme_market_cap_jump_root_cause_dev.csv`
- `residual_extreme_market_cap_jump_decomposition_dev.csv`
- `security_history_break_audit_dev.csv`
- `residual_shares_semantic_break_audit_dev.csv`
- `residual_jump_rule_simulation_dev.csv`
- `residual_jump_coverage_impact_dev.csv`
- `residual_extreme_market_cap_acceptance_report.md`
