# Formal US Factor Validation — Pre-Gate-A Report

## 1. Executive validation verdict

**FACTOR VALIDATION EVIDENCE — PASS WITH WARNINGS**. Blockers: 0. Warnings: 1.

## 2. Frozen input lineage

Canonical logical hash: `de1bf079e6399025ebde6a81883bbad21aec9c5443dbb39f7ca2f00b5a409c6e`. Candidate-factor logical hash: `4282b50f398ee5746420db264d154298f01167b31e03b1feb9e6c5244a65fb64`. Dividend extension logical hash: `f5e1edcf58e28a96cdc4e1cb24ec2d38bc4917a71058fbeccd5d7e1a9228d82d`. The validation database is separate from all three inputs.

## 3. Sample firewall

Performance evidence accessed only 2006-01-01 to 2015-12-31; formal Gate-A evidence only 2011-01-01 to 2015-12-31. No candidate score was joined to post-2015 return data.

## 4. Signal / execution timing

Scores are observed at weekly S_t after close; E_t is the next actual trading-day close. Return compounding begins after E_t, not on E_t.

## 5. Forward-return construction

CIZ DlyRet is compounded directly over `(E_t, E_{t+h}]`. No separate DelRet, dividend, distribution, or split adjustment is added.

## 6. Delisting handling

A confirmed delisting uses its terminal CIZ return and thereafter cash at zero return; unexplained path termination is unavailable. See `factor_validation_forward_return_qa.csv`.

## 7. Forward IC methodology

Same-date Spearman IC uses pairwise valid observations, n>=30 overall and n>=10 inside a historical GICS sector. 1W uses NW lag 4; overlapping 4W uses NW lag 3 and moving-block bootstrap (block 4, 2,000 replications, seed 5209).

## 8. 1-week IC results — Factor Selection (2011–2015)

| Factor | Weeks | Mean IC | Annualized ICIR | Positive ratio | NW t (lag 4) |
| --- | --- | --- | --- | --- | --- |
| Size | 259 | -0.0059 | -0.3630 | 0.4749 | -0.9142 |
| Momentum | 259 | 0.0215 | 0.7866 | 0.5637 | 1.7764 |
| Low Risk | 259 | 0.0144 | 0.4482 | 0.5212 | 0.9803 |
| Value | 259 | 0.0011 | 0.0717 | 0.5212 | 0.1568 |
| Quality | 259 | 0.0029 | 0.2842 | 0.5097 | 0.6467 |
| Investment | 259 | 0.0032 | 0.3698 | 0.4942 | 0.9031 |
| Dividend | 259 | 0.0012 | 0.0565 | 0.4440 | 0.1184 |

## 9. 4-week IC results — Factor Selection (2011–2015)

| Factor | Weeks | Mean IC | Annualized ICIR | NW t (lag 3) | Bootstrap 2.5% | Bootstrap 97.5% |
| --- | --- | --- | --- | --- | --- | --- |
| Size | 256 | -0.0078 | -0.5652 | -0.7935 | -0.0282 | 0.0105 |
| Momentum | 256 | 0.0278 | 1.1276 | 1.5426 | -0.0091 | 0.0628 |
| Low Risk | 256 | 0.0230 | 0.7390 | 0.9747 | -0.0246 | 0.0690 |
| Value | 256 | -0.0015 | -0.1057 | -0.1410 | -0.0228 | 0.0199 |
| Quality | 256 | 0.0017 | 0.1635 | 0.2162 | -0.0149 | 0.0169 |
| Investment | 256 | 0.0105 | 1.3149 | 1.8588 | -0.0004 | 0.0213 |
| Dividend | 256 | 0.0030 | 0.1327 | 0.1734 | -0.0291 | 0.0357 |

## 10. IC inference

The reported HAC and bootstrap statistics are descriptive inference/stability evidence only; no automatic factor threshold or decision rule is applied.

## 11. Yearly / rolling stability

Calendar-year and rolling-52-week results (only through 2015) are recorded in `factor_validation_ic_stability.csv`; portfolio year-by-year results are in `factor_validation_portfolio_stability.csv`.

## 12. Sector IC

Historical-GICS sector diagnostics and cross-sector dispersion are recorded in `factor_validation_sector_ic.csv` and `factor_validation_sector_ic_summary.csv`.

| Factor | Horizon | Valid sectors | Cross-sector dispersion |
| --- | --- | --- | --- |
| Dividend | 1W | 9 | 0.0112 |
| Dividend | 4W | 9 | 0.0156 |
| Investment | 1W | 9 | 0.0100 |
| Investment | 4W | 9 | 0.0197 |
| Low Risk | 1W | 9 | 0.0162 |
| Low Risk | 4W | 9 | 0.0320 |
| Momentum | 1W | 9 | 0.0149 |
| Momentum | 4W | 9 | 0.0253 |
| Quality | 1W | 9 | 0.0194 |
| Quality | 4W | 9 | 0.0342 |
| Size | 1W | 9 | 0.0157 |
| Size | 4W | 9 | 0.0258 |
| Value | 1W | 9 | 0.0159 |
| Value | 4W | 9 | 0.0317 |

## 13. Single-factor Top/Bottom methodology

Each factor is ranked independently on the factor-specific valid S_t cross-section. Top and Bottom each receive floor(30%*N), with score then PERMNO ascending tie-break. Targets are selected before E_t; an E_t execution failure is excluded and the same leg is renormalized without replacement. Legs are equal weight, unhedged, and unscaled.

## 14. Single-factor portfolio results — Factor Selection (2011–2015)

| Factor | Top mean/wk | Bottom mean/wk | Spread mean/wk | Spread gross Sharpe | Spread NW t | Top turnover | Bottom turnover | Top break-even 1-way cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Size | 0.0027 | 0.0027 | 0.0001 | 0.0461 | 0.1216 | 0.0314 | 0.0205 | 0.0360 |
| Momentum | 0.0028 | 0.0019 | 0.0009 | 0.4454 | 1.0307 | 0.1213 | 0.1194 | 0.0099 |
| Low Risk | 0.0029 | 0.0021 | 0.0008 | 0.2910 | 0.7039 | 0.0255 | 0.0331 | 0.0539 |
| Value | 0.0027 | 0.0025 | 0.0001 | 0.1173 | 0.2686 | 0.0466 | 0.0440 | 0.0244 |
| Quality | 0.0028 | 0.0024 | 0.0003 | 0.3615 | 0.8543 | 0.0262 | 0.0296 | 0.0459 |
| Investment | 0.0028 | 0.0024 | 0.0005 | 0.7488 | 1.8164 | 0.0471 | 0.0430 | 0.0270 |
| Dividend | 0.0027 | 0.0027 | 0.0000 | 0.0015 | 0.0034 | 0.0347 | 0.0254 | 0.0350 |

## 15. Turnover

Turnover is 0.5 times GTR computed against drifted pre-trade weights on the union of prior and new holdings; the preceding table reports the 2011–2015 means.

## 16. Cost / break-even diagnostics

Top-leg break-even one-way cost solves the exact product `Π((1-c*GTR_t)*(1+R_top,t)) = 1`. No cost grid is frozen, so `COST_SENSITIVITY_SCENARIO_PENDING_GATE_A_REVIEW` remains a warning.

## 17. Score complementarity

Pairwise same-date score correlations, separately labelled DEV and FACTOR_SELECTION, are in `factor_validation_score_complementarity.csv`; they are descriptive only.

## 18. Factor-return complementarity

Pairwise weekly gross spread-return correlations for 2011–2015 only are in `factor_validation_return_complementarity.csv`; low correlation is not a factor decision rule.

## 19. Common-sample robustness

The following diagnostic repeats core ICs only where all seven scores exist; it does not replace factor-specific primary evidence.

| Factor | Horizon | Primary mean IC | Common mean IC | Primary weeks | Common weeks |
| --- | --- | --- | --- | --- | --- |
| Size | 1W | -0.0059 | -0.0056 | 259 | 259 |
| Size | 4W | -0.0078 | -0.0076 | 256 | 256 |
| Momentum | 1W | 0.0215 | 0.0216 | 259 | 259 |
| Momentum | 4W | 0.0278 | 0.0286 | 256 | 256 |
| Low Risk | 1W | 0.0144 | 0.0147 | 259 | 259 |
| Low Risk | 4W | 0.0230 | 0.0232 | 256 | 256 |
| Value | 1W | 0.0011 | 0.0007 | 259 | 259 |
| Value | 4W | -0.0015 | -0.0026 | 256 | 256 |
| Quality | 1W | 0.0029 | 0.0023 | 259 | 259 |
| Quality | 4W | 0.0017 | 0.0007 | 256 | 256 |
| Investment | 1W | 0.0032 | 0.0033 | 259 | 259 |
| Investment | 4W | 0.0105 | 0.0105 | 256 | 256 |
| Dividend | 1W | 0.0012 | 0.0014 | 259 | 259 |
| Dividend | 4W | 0.0030 | 0.0032 | 256 | 256 |

## 20. Coverage

Weekly and yearly coverage is in `factor_validation_coverage.csv`; the following are 2011–2015 primary weekly averages.

| Factor | Members | Score available | Execution eligible | IC 1W | IC 4W |
| --- | --- | --- | --- | --- | --- |
| Size | 500.7931 | 499.7854 | 499.6398 | 495.7816 | 489.9732 |
| Momentum | 500.7931 | 498.6782 | 498.5249 | 494.7011 | 488.9540 |
| Low Risk | 500.7931 | 497.8582 | 497.7050 | 493.8851 | 488.1341 |
| Value | 500.7931 | 498.6820 | 498.5364 | 494.6935 | 488.9080 |
| Quality | 500.7931 | 498.6820 | 498.5364 | 494.6935 | 488.9080 |
| Investment | 500.7931 | 497.8851 | 497.7395 | 493.9042 | 488.1379 |
| Dividend | 500.7931 | 500.6322 | 500.4866 | 496.6207 | 490.8008 |

## 21. DEV versus Factor Selection separation

All DEV 2006–2010 outcomes are labelled diagnostic. The tables above use only FACTOR_SELECTION 2011–2015. No combined statistic is used for Gate-A evidence.

## 22. Post-2015 firewall proof

IC after 2015: 0; portfolio rows after 2015: 0; 2016 returns used: 0; rolling summaries entering 2016: 0; Gate-A evidence using post-2015 outcomes: 0.

## 23. Contamination-register update

`oos_contamination_register.csv` records FACTOR_SELECTION evidence as 2011–2015 only and both 2016–2020 and 2021–2025 as NOT_EVALUATED.

## 24. Determinism

Two independent atomic builds yielded the same validation logical hash: `8cacc0549f74cc4bcb75a658f158283318ba67f90e4115107b4620671100f5db`. See `factor_validation_determinism.csv`.

## 25. Tests

Focused tests cover timing, return semantics, delisting, IC thresholds/inference, deterministic Top/Bottom construction, drifted turnover, exact cost accounting, sample firewall, schema, and input-integrity requirements. Full native-suite results are reported separately in the repository test run.

## 26. Gate-A evidence matrix

`factor_validation_gate_a_evidence_matrix.csv` is descriptive by design; it contains no Keep/Drop/rank/final-factor field.

## 27. Remaining warnings / blockers

Blockers: none after determinism confirmation. Warning: no pre-specified transaction-cost sensitivity scenario exists; only exact break-even cost is shown. Prior candidate-construction warnings are retained context, not performance decisions.

## 28. Exact next permitted step

Human Gate A review of the evidence matrix; this implementation does not freeze a Final Factor Set.

## 29. Explicitly prohibited next steps

Do not automatically Keep/Drop a factor, freeze a Final Factor Set, calculate FMOM, choose FMOM lambda, build a multi-factor strategy, evaluate 2016–2025 performance, or start Singapore analysis from this report.