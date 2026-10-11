# Gate A0 Value Family Review

This is a descriptive 2011–2015 evidence sheet. It does not record a family-representative outcome.

## 1. Frozen specification definitions

| specification_id | incumbent | role | economic_rationale_note | implementation_note |
| --- | --- | --- | --- | --- |
| VALUE_CURRENT | TRUE | INCUMBENT | Existing branch-aware Value composite | MANDATORY_HISTORICAL_GICS_SECTOR_PLUS_LOG_ISSUER_MCAP |
| VALUE_EP | FALSE | CHALLENGER | Frozen E/P only | MANDATORY_HISTORICAL_GICS_SECTOR_PLUS_LOG_ISSUER_MCAP |
| VALUE_BP | FALSE | CHALLENGER | Frozen B/P only | MANDATORY_HISTORICAL_GICS_SECTOR_PLUS_LOG_ISSUER_MCAP |
| VALUE_EP_BP | FALSE | CHALLENGER | Equal-weight standardized E/P and B/P | MANDATORY_HISTORICAL_GICS_SECTOR_PLUS_LOG_ISSUER_MCAP |

## 2. Primary 1W IC

| specification_id | primary_1w_mean_ic | primary_1w_icir | primary_1w_nw_t | primary_1w_raw_p | primary_1w_bh_q |
| --- | --- | --- | --- | --- | --- |
| VALUE_CURRENT | 0.0011 | 0.0717 | 0.1568 | 0.8754 | 0.8754 |
| VALUE_EP | 0.0019 | 0.1442 | 0.3239 | 0.7460 | 0.8754 |
| VALUE_BP | -0.0057 | -0.3750 | -0.8000 | 0.4237 | 0.8754 |
| VALUE_EP_BP | -0.0020 | -0.1274 | -0.2720 | 0.7856 | 0.8754 |

## 3. Primary 4W IC

| specification_id | primary_4w_mean_ic | primary_4w_icir | primary_4w_nw_t | primary_4w_raw_p | primary_4w_bh_q |
| --- | --- | --- | --- | --- | --- |
| VALUE_CURRENT | -0.0015 | -0.1057 | -0.1410 | 0.8879 | 0.8879 |
| VALUE_EP | -0.0020 | -0.1557 | -0.2118 | 0.8323 | 0.8879 |
| VALUE_BP | -0.0055 | -0.3709 | -0.4843 | 0.6282 | 0.8879 |
| VALUE_EP_BP | -0.0053 | -0.3476 | -0.4609 | 0.6448 | 0.8879 |

## 4. Yearly IC

See the annual evidence CSV; sign counts appear in the master table.

## 5. Rolling IC

| specification_id | rolling_52w_positive_fraction_1w | rolling_52w_positive_fraction_4w |
| --- | --- | --- |
| VALUE_CURRENT | 0.6923 | 0.6585 |
| VALUE_EP | 0.7837 | 0.6878 |
| VALUE_BP | 0.4423 | 0.5561 |
| VALUE_EP_BP | 0.6635 | 0.6049 |

## 6. Primary portfolio evidence

| specification_id | top_weekly_mean | bottom_weekly_mean | spread_weekly_mean | spread_gross_sharpe |
| --- | --- | --- | --- | --- |
| VALUE_CURRENT | 0.0027 | 0.0025 | 0.0001 | 0.1173 |
| VALUE_EP | 0.0027 | 0.0026 | -0.0000 | -0.0190 |
| VALUE_BP | 0.0025 | 0.0026 | -0.0001 | -0.0940 |
| VALUE_EP_BP | 0.0026 | 0.0027 | -0.0001 | -0.0499 |

## 7. Yearly portfolio evidence

See the annual portfolio evidence CSV; no mechanical judgement is encoded.

## 8. Common-family sample evidence

| specification_id | common_sample_1w_mean_ic | common_sample_4w_mean_ic | common_sample_spread_sharpe |
| --- | --- | --- | --- |
| VALUE_CURRENT | 0.0011 | -0.0015 | 0.1173 |
| VALUE_EP | 0.0019 | -0.0020 | -0.0190 |
| VALUE_BP | -0.0058 | -0.0057 | -0.1053 |
| VALUE_EP_BP | -0.0020 | -0.0053 | -0.0499 |

## 9. Coverage

| specification_id | mean_coverage | minimum_coverage |
| --- | --- | --- |
| VALUE_CURRENT | 0.9958 | 0.9940 |
| VALUE_EP | 0.9958 | 0.9940 |
| VALUE_BP | 0.9960 | 0.9940 |
| VALUE_EP_BP | 0.9958 | 0.9940 |

## 10. Turnover / break-even cost

| specification_id | top_turnover | bottom_turnover | break_even_oneway_cost |
| --- | --- | --- | --- |
| VALUE_CURRENT | 0.0502 | 0.0476 | 0.0226 |
| VALUE_EP | 0.0552 | 0.0505 | 0.0207 |
| VALUE_BP | 0.0372 | 0.0353 | 0.0286 |
| VALUE_EP_BP | 0.0526 | 0.0465 | 0.0211 |

## 11. Correlation / redundancy

| specification_id | highest_same_family_score_corr | highest_same_family_spread_corr |
| --- | --- | --- |
| VALUE_CURRENT | 0.9161 | 0.9488 |
| VALUE_EP | 0.7570 | 0.8698 |
| VALUE_BP | 0.7485 | 0.8591 |
| VALUE_EP_BP | 0.9161 | 0.9488 |

## 12. Sector breadth

| specification_id | positive_sector_count | valid_sector_count |
| --- | --- | --- |
| VALUE_CURRENT | 6 | 9 |
| VALUE_EP | 7 | 9 |
| VALUE_BP | 2 | 9 |
| VALUE_EP_BP | 5 | 9 |

## 13. Component diagnostics where relevant

Components are diagnostic-only and use their natural domains. See the component diagnostics evidence CSV.

## 14. Economic interpretation

The registry definitions and stated conceptual concerns are retained unchanged; numerical evidence alone does not establish an economic conclusion.

## 15. Open questions

Human reviewers should consider coverage, common-sample sensitivity, stability, implementation constraints, and the incumbent-default governance before any later human review record.
