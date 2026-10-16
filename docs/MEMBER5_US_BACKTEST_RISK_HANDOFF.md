# Member 5 — US 回测与风险分析正式交接记录

**负责人：** M5（Backtest & Risk）  
**研究市场：** 美国（US）  
**研究区间：** 2021–2025 Historical OOS  
**状态：** 周度回测与主要风险分析已完成；部分正式验收事项待关闭  
**冻结配置：** `J26_lam0.5_ra1_min4_buf1`

## 1. 研究范围与冻结策略

本阶段负责美国市场冻结策略的样本外绩效评估、基准比较、统计推断、风险分析、交易成本、流动性容量及压力测试。

本次研究不修改 Gate A/B/C 已冻结的因子定义和策略参数，也不利用 OOS 结果重新选择配置。

冻结策略使用四个因子：

- `QUALITY_CURRENT`
- `MOMENTUM_12_1`
- `LOWRISK_CURRENT`
- `INVESTMENT_CURRENT`

主要参数为 J=26、λ=0.5、Risk Adjustment 开启、min4、Buffer 开启。

股票组合采用 Top 20% 综合评分选股、Long-only、等权配置及 30% Sector Cap。

每周最后交易日收盘产生信号，下一交易日收盘执行，持有至下一执行日收盘。

单边交易成本为 10bp，现金收益为零。

**冻结运行信息：**

| 项目 | 内容 |
|---|---|
| Strategy ID | `US_BASE_FMOM_K4` |
| Run ID | `US_BASE_hist_oos_20261013T141725Z` |
| Code Commit | `61eedb3630ddfc186b351f5496edf09b571565ff` |
| Gate Status | ACCEPTED |
| OOS 完成周期 | 259 周 |
| Frozen Acceptance | 15 项 PASS |

策略从 2016 年连续运行，2021 年第一周不是从现金重新建仓。

冻结持仓有 260 个信号周，但 2025-12-26 的持有期跨入 2026，因此正式绩效只采用 259 个已完成周期。

## 2. 核心绩效结果

| 指标 | Dynamic FMOM | Static MF | Universe EW | S&P 500 |
|---|---:|---:|---:|---:|
| CAGR | 6.28% | 9.51% | 10.65% | 14.42% |
| Annual Volatility | 15.53% | 14.72% | 15.92% | 16.45% |
| Sharpe | 0.260 | 0.470 | 0.511 | 0.704 |
| Sortino | 0.358 | 0.667 | 0.717 | 1.012 |
| Max Drawdown | 21.17% | 19.86% | 19.45% | 23.67% |
| Calmar | 0.297 | 0.479 | 0.548 | 0.609 |
| Annual Turnover | 4.084 | 1.874 | 0.712 | — |

**主要发现：** Dynamic FMOM 的 CAGR 和 Sharpe 均低于 Static Multi-Factor、Universe EW 和 S&P 500。Static Multi-Factor 也未能跑赢 Universe EW。

不过，S&P 500 的最大回撤高于三条股票策略，因此不能仅根据收益率判断所有风险维度。

## 3. FMOM 增量价值与统计检验

Dynamic − Static 的配对周度净收益检验结果：

| 指标 | 结果 |
|---|---:|
| Annualized Mean Increment | -2.8729% |
| HAC t-stat | -1.5344 |
| HAC p-value | 0.124936 |
| HAC 95% CI | [-6.5427%, 0.7969%] |
| Sharpe Difference | -0.2097 |
| Bootstrap Sharpe 95% CI | [-0.4491, 0.0069] |
| Gross Increment | -2.4309% |
| Gross HAC p-value | 0.194211 |

虽然 Dynamic 的增量收益点估计为负，但 HAC 检验在 5% 显著性水平下未拒绝零增量假设。

因此不能断言 Factor Momentum 的增量价值显著为负。

## 4. Market Regression 与 FF6 Regression

### Market Regression

| Model | Annualized Alpha | HAC p-value | Market Beta | R² |
|---|---:|---:|---:|---:|
| Dynamic | -5.820% | 0.04467 | 0.8513 | 0.8122 |
| Static | -2.442% | 0.36775 | 0.8077 | 0.8144 |
| Dynamic − Static | -3.378% | 0.08331 | 0.0436 | 0.0259 |

### FF6 Regression

| Model | Annualized Alpha | p-value | R² |
|---|---:|---:|---:|
| Dynamic | -7.185% | 0.00277 | 0.8624 |
| Static | -3.744% | 0.11901 | 0.8536 |
| Dynamic − Static | -3.442% | 0.06102 | 0.0817 |

FF6 包括 Market、SMB、HML、RMW、CMA 和 Momentum。

回归结果说明 Dynamic 的市场风险暴露较高，且模型调整后的 Alpha 为负。但回归 Alpha 不等于直接可实现的交易收益。

## 5. 年度绩效与回撤

| Signal Year | Dynamic | Static | S&P 500 |
|---|---:|---:|---:|
| 2021 | 18.15% | 18.83% | 24.29% |
| 2022 | -10.27% | -6.92% | -15.37% |
| 2023 | 5.84% | 9.63% | 24.50% |
| 2024 | 16.93% | 17.46% | 27.64% |
| 2025 | 3.23% | 10.40% | 17.05% |

这些是按 Signal Year 归组的复合收益，并非严格日历年收益。

Dynamic 最大回撤为 21.17%，对应：

- Peak：2021-12-27
- Trough：2023-10-30
- Recovery：2024-06-17

Static 最大回撤为 19.86%，对应：

- Peak：2021-12-27
- Trough：2022-09-26
- Recovery：2024-01-22

Dynamic 从高点到完全恢复的时间较长，但从低点到恢复的时间较短，两者不能混淆。

## 6. Transaction Cost Analysis

| 单边成本 | Dynamic CAGR | Static CAGR | Universe EW CAGR |
|---|---:|---:|---:|
| 0bp | 7.153% | 9.923% | 10.804% |
| 10bp | 6.282% | 9.512% | 10.647% |
| 20bp | 5.417% | 9.102% | 10.489% |
| 30bp | 4.558% | 8.694% | 10.332% |

Break-Even One-Way Cost：

- Dynamic：84.47bp
- Static：252.13bp
- Universe EW：719.32bp

Dynamic 对交易成本最敏感，但即使在零交易成本下，其收益仍低于 Static。

成本压力测试固定历史持仓和交易路径，不代表真实交易成本变化后策略会保持相同的交易行为。

## 7. Liquidity & Capacity

| 指标 | Dynamic | Static | Universe EW |
|---|---:|---:|---:|
| ADV Trade Coverage | 99.964% | 99.955% | 99.885% |
| Trade-Weight Coverage | 99.800% | 99.509% | 97.102% |
| 5% ADV Capacity | $53.66M | $68.74M | $3,029.23M |
| $50M AUM P95 Participation | 0.149% | 0.074% | 0.004% |

三条策略的历史容量瓶颈均涉及 PERMNO 13964，但发生日期不同。

当 ADV 下降 50% 时，相同交易规模下的 Participation Rate 翻倍。

容量结果基于有有效 ADV 的交易和单日执行假设，未纳入完整 Market Impact 模型，不能视为已验证的最大实际管理规模。

## 8. Portfolio Concentration

| 指标 | Dynamic | Static | Universe EW |
|---|---:|---:|---:|
| Stock HHI | 0.01004 | 0.01004 | 0.00199 |
| Sector HHI | 0.14035 | 0.13287 | 0.10845 |
| Average Max Sector Weight | 21.55% | 20.53% | 15.19% |
| Weighted Median Market Cap | $43.86B | $41.41B | $32.47B |
| Mega Cap Weight | 13.13% | 13.41% | 7.44% |

Dynamic 与 Static 的个股权重集中度基本相同，但 Dynamic 的行业集中度略高。

三条策略的历史市值数据覆盖率均超过 99.7%，行业覆盖率为 100%。

Dynamic 和 Static 未发现 30% Sector Cap 违反情况。

## 9. Regime & Stress Testing

### Market Regime

| Regime | Weeks | Dynamic | Static |
|---|---:|---:|---:|
| Bull | 204 | 10.409% | 11.982% |
| Bear | 55 | -4.199% | 3.495% |
| High Vol | 160 | 4.341% | 6.590% |
| Low Vol | 99 | 12.100% | 15.981% |

这里报告的是条件年化算术平均收益，不是 CAGR。

### Historical Stress

| Regime | Weeks | Dynamic Mean Return | Static Mean Return |
|---|---:|---:|---:|
| Market Crash | 26 | -3.686% | -3.276% |
| Market Rally | 26 | 3.498% | 3.584% |
| Normal | 207 | 0.199% | 0.206% |

Crash 和 Rally 根据样本内实际市场收益的最低及最高 10% 定义，是事后压力分析，不是预测信号。

Dynamic 在测试的所有主要 Regime 下平均表现均弱于 Static。

## 10. 补充因子与股票诊断

因子层面完整样本为 256 周：

- Mean Rank IC：-0.0297
- Winner Hit Rate：21.48%
- Annualized Factor Timing Increment：-2.28%

股票层面：

- Average Holdings Overlap：68.25%
- Average Jaccard：52.42%
- 2025 Gross Increment 算术合计：-5.727 个百分点
- 2025 Cost Effect 算术合计：-0.510 个百分点

这些结果属于补充诊断，不构成严格因果归因。

## 11. Robustness Evidence

Member 4 的 Gate B 决策文档记录了 2016–2020 年 28 组配置的重新测试。

覆盖 J、λ、Risk Adjustment 和 Buffer 等参数。

其结论支持选择 J=26、λ=0.5、RA On 和 Buffer On。

但是这些结果属于配置期，不能作为独立 OOS Robustness。

当前本地没有找到完整的 `gate_b_config_summary.csv`。

已完成 OOS Cost Sensitivity、Factor Availability、Regime 和 Stress Analysis；其他 Mandatory Robustness 仍需按 SSOT 核对。

## 12. Validation & Open Items

| 项目 | 状态 |
|---|---|
| Frozen OOS Acceptance | ACCEPTED |
| Static Benchmark | PASS — 22 checks |
| Universe EW Benchmark | PASS — 7 checks |
| Risk Map Implemented Checks | PASS — 7 checks |
| Independent PIT / Return Audit | PENDING |
| Liquidity Missing-Data QA | PENDING |
| Market Impact Calibration | PENDING |
| High/Low Rate Regime | PENDING |
| Mandatory Robustness | PENDING |
| Daily NAV vs Weekly NAV | TEAM APPROVAL REQUIRED |
| Gate B Full Configuration Grid | NOT FOUND LOCALLY |
| Final Gate B/C Sign-Off | REQUIRES CONFIRMATION |

正式 US Strategy Diagnostic Gate 不能在以上必要事项尚未关闭时标记为全部通过。

## 13. 接收人 Checklist

- [ ] 阅读 `src/risk_analysis/README.md`。
- [ ] 确认 Frozen Strategy Config 和 Run Manifest。
- [ ] 检查所有本地输出是否已交接。
- [ ] 核对 `four_strategy_comparison.csv`。
- [ ] 核对 `us_strategy_risk_map.csv`。
- [ ] 核对 `us_risk_map_validation.csv`。
- [ ] 确认周度 NAV 的正式使用口径。
- [ ] 获取缺失的 Gate B 配置网格结果。
- [ ] 分配剩余 QA 和 Robustness 工作。
- [ ] 保留冻结 OOS，不根据结果重新调参。

## 14. Final Conclusion

在 2021–2025 年美国市场 OOS 中，冻结 Dynamic FMOM 策略实现了 6.28% CAGR 和 0.260 Sharpe，低于 Static Multi-Factor、Universe EW 和 S&P 500。

Dynamic 的交易换手更高，行业集中度略高，历史流动性容量瓶颈更严格，且在所测试的市场压力类别中平均表现较弱。

虽然 FMOM 增量收益的点估计为负，但配对 HAC 检验的 p-value 为 0.125，因此不能认定其负向增量在 5% 水平上显著。

**交接状态：美国市场周度回测与风险分析的主要结果已经完成并整理，可供团队使用。正式 Gate 关闭仍需完成开放验证事项和团队审批。**
