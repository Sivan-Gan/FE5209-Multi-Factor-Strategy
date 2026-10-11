# Gate A0 Low Risk Family Review

This is a descriptive 2011–2015 evidence sheet. It does not record a family-representative outcome.

## 1. Frozen specification definitions

| specification_id | incumbent | role | economic_rationale_note | implementation_note |
| --- | --- | --- | --- | --- |
| LOWRISK_CURRENT | TRUE | INCUMBENT | Existing equal-weight standardized Low Risk composite | NO_BASELINE_NEUTRALIZATION |
| LOWRISK_VOL | FALSE | CHALLENGER | Frozen negative Volatility252 only | NO_BASELINE_NEUTRALIZATION |
| LOWRISK_IVOL | FALSE | CHALLENGER | Frozen negative IVOL252 only | NO_BASELINE_NEUTRALIZATION |

## 2. Primary 1W IC

| specification_id | primary_1w_mean_ic | primary_1w_icir | primary_1w_nw_t | primary_1w_raw_p | primary_1w_bh_q |
| --- | --- | --- | --- | --- | --- |
| LOWRISK_CURRENT | 0.0144 | 0.4482 | 0.9803 | 0.3269 | 0.3410 |
| LOWRISK_VOL | 0.0155 | 0.4318 | 0.9522 | 0.3410 | 0.3410 |
| LOWRISK_IVOL | 0.0126 | 0.4631 | 1.0097 | 0.3127 | 0.3410 |

## 3. Primary 4W IC

| specification_id | primary_4w_mean_ic | primary_4w_icir | primary_4w_nw_t | primary_4w_raw_p | primary_4w_bh_q |
| --- | --- | --- | --- | --- | --- |
| LOWRISK_CURRENT | 0.0230 | 0.7390 | 0.9747 | 0.3297 | 0.3372 |
| LOWRISK_VOL | 0.0252 | 0.7301 | 0.9669 | 0.3336 | 0.3372 |
| LOWRISK_IVOL | 0.0191 | 0.7300 | 0.9597 | 0.3372 | 0.3372 |

## 4. Yearly IC

See the annual evidence CSV; sign counts appear in the master table.

## 5. Rolling IC

| specification_id | rolling_52w_positive_fraction_1w | rolling_52w_positive_fraction_4w |
| --- | --- | --- |
| LOWRISK_CURRENT | 0.5721 | 0.5220 |
| LOWRISK_VOL | 0.5721 | 0.5220 |
| LOWRISK_IVOL | 0.5577 | 0.5073 |

## 6. Primary portfolio evidence

| specification_id | top_weekly_mean | bottom_weekly_mean | spread_weekly_mean | spread_gross_sharpe |
| --- | --- | --- | --- | --- |
| LOWRISK_CURRENT | 0.0029 | 0.0021 | 0.0008 | 0.2910 |
| LOWRISK_VOL | 0.0029 | 0.0021 | 0.0007 | 0.2565 |
| LOWRISK_IVOL | 0.0029 | 0.0022 | 0.0006 | 0.2540 |

## 7. Yearly portfolio evidence

See the annual portfolio evidence CSV; no mechanical judgement is encoded.

## 8. Common-family sample evidence

| specification_id | common_sample_1w_mean_ic | common_sample_4w_mean_ic | common_sample_spread_sharpe |
| --- | --- | --- | --- |
| LOWRISK_CURRENT | 0.0144 | 0.0230 | 0.2910 |
| LOWRISK_VOL | 0.0155 | 0.0252 | 0.2565 |
| LOWRISK_IVOL | 0.0126 | 0.0191 | 0.2540 |

## 9. Coverage

| specification_id | mean_coverage | minimum_coverage |
| --- | --- | --- |
| LOWRISK_CURRENT | 0.9941 | 0.9860 |
| LOWRISK_VOL | 0.9941 | 0.9860 |
| LOWRISK_IVOL | 0.9941 | 0.9860 |

## 10. Turnover / break-even cost

| specification_id | top_turnover | bottom_turnover | break_even_oneway_cost |
| --- | --- | --- | --- |
| LOWRISK_CURRENT | 0.0292 | 0.0369 | 0.0467 |
| LOWRISK_VOL | 0.0290 | 0.0370 | 0.0467 |
| LOWRISK_IVOL | 0.0304 | 0.0371 | 0.0432 |

## 11. Correlation / redundancy

| specification_id | highest_same_family_score_corr | highest_same_family_spread_corr |
| --- | --- | --- |
| LOWRISK_CURRENT | 0.9800 | 0.9951 |
| LOWRISK_VOL | 0.9800 | 0.9951 |
| LOWRISK_IVOL | 0.9757 | 0.9920 |

## 12. Sector breadth

| specification_id | positive_sector_count | valid_sector_count |
| --- | --- | --- |
| LOWRISK_CURRENT | 6 | 9 |
| LOWRISK_VOL | 7 | 9 |
| LOWRISK_IVOL | 6 | 9 |

## 13. Component diagnostics where relevant

Components are diagnostic-only and use their natural domains. See the component diagnostics evidence CSV.

## 14. Economic interpretation

The registry definitions and stated conceptual concerns are retained unchanged; numerical evidence alone does not establish an economic conclusion.

## 15. Open questions

Human reviewers should consider coverage, common-sample sensitivity, stability, implementation constraints, and the incumbent-default governance before any later human review record.
