# FE5209 Adaptive Multi-Factor Strategy
## 策略研究总结与团队交接报告（Research Handoff Report）

**项目分支：** `feature/adaptive-factor-strategy`
**研究对象：** US Equity Long-Only Multi-Factor Strategy
**当前主模型：** Adaptive 3Y Top-3 Inverse Volatility（IV）
**历史回测：** 2016–2025
**近期扩展：** 2026 年，39 个完整执行周
**项目状态：** V1 Pipeline 已完成，等待最终 Git Handoff

---

# 1. Executive Summary（研究总结）

本研究旨在改进原始的固定多因子股票投资策略，探索是否能够通过动态选择因子以及风险调整权重，提高投资组合在不同市场环境中的适应能力。

我们的核心假设是：

> 不同因子的有效性会随市场环境发生变化。相比始终使用固定因子集合，根据历史表现动态选择因子，可能改善组合的收益和风险调整后表现。

为检验这一假设，我们构建了一套 Adaptive Multi-Factor Strategy。

策略从七个候选因子中，每年根据过去三年的历史表现选择 Top-3，并利用历史波动率确定因子权重。

每周重新计算股票综合评分，构建 Long-Only 股票组合。

我们比较了三种因子权重方法：

- Equal Weight（EW）
- Inverse Volatility（IV）
- Minimum Variance（MV）

使用 2016–2020 年作为模型选择训练期，最终按照 Sharpe Ratio 选择 **Top-3 IV**。

在 2021–2025 年验证期中，我们进一步构建了 Fixed Top-3 IV Benchmark，用于检验动态因子选择是否创造增量价值。

### 主要研究发现

1. Adaptive Top-3 IV 在控制变量实验中优于 Fixed Top-3 IV。
2. 动态选因子改善了 CAGR、Sharpe、Sortino 和 Calmar。
3. 策略在部分市场下跌年份表现出较好的防御性。
4. 策略在 2023–2024 年明显跑输 S&P 500，说明其市场适应能力仍存在局限。
5. 2026 年近期扩展表现较好，但数据来源和历史回测不同，应独立报告。

**总体结论：**

当前结果支持 Dynamic Factor Selection 的研究价值，但尚不能证明策略在所有市场环境下都优于固定多因子策略或市场指数。

---

# 2. Research Motivation（研究动机）

## 2.1 原始策略的局限

传统多因子策略通常使用固定的因子集合，例如：

- Quality
- Momentum
- Low Risk
- Investment

这类策略具有清晰的经济学解释，但存在一个潜在问题：

**不同因子的历史表现并不稳定。**

某些因子可能在特定市场环境中表现良好，而在其他时期失效。

如果始终使用固定的因子集合，组合可能长期暴露于近期表现较弱的因子。

因此，我们希望研究：

1. 是否应该动态调整因子集合？
2. 是否应该根据因子风险调整配置权重？
3. 动态策略是否能够优于固定因子组合？
4. 动态策略是否能够改善回撤和风险调整后收益？

## 2.2 研究假设

本研究包含两个核心假设。

**Hypothesis 1：Adaptive Factor Selection**

根据历史因子表现动态选择 Top-3，可能优于始终持有固定的三个因子。

**Hypothesis 2：Risk-Aware Factor Weighting**

相比因子等权配置，根据历史风险调整权重可能改善组合的风险调整后收益。

为了区分这两个机制的作用，我们分别构建因子选择和权重配置的对照实验。

---

# 3. Strategy Construction（策略构建）

## 3.1 Candidate Factor Universe

策略从七类因子中进行选择：

| Factor | 经济学含义 |
|---|---|
| Quality | 盈利能力与财务质量 |
| Momentum | 历史价格趋势 |
| Low Risk | 历史低风险特征 |
| Investment | 公司投资扩张行为 |
| Value | 相对基本面估值 |
| Dividend | 股息相关特征 |
| Size | 公司市值规模 |

因子数据来自项目已有的 Factor Research Pipeline。

本研究主要使用已经构建和标准化的股票因子评分，而不是重新从原始财务报表计算所有因子。

## 3.2 Adaptive Factor Selection

每年年初，根据过去三年的历史因子收益进行排序。

对于每个因子：

\[
SelectionScore_i=\frac{\overline{R_i}}{\sigma_i}
\]

其中：

- \(\overline{R_i}\)：历史平均周度因子收益。
- \(\sigma_i\)：历史周度因子收益标准差。

选择 Selection Score 最高的三个因子。

参数设置：

| Parameter | Value |
|---|---|
| Lookback Window | 3 Years |
| Number of Factors | Top-3 |
| Minimum Coverage | 65% |
| Selection Frequency | Annual |
| Historical Information | Completed Returns Only |

注意：这里的 Selection Score 没有扣除无风险收益，因此并非严格意义上的标准 Sharpe Ratio。

### 历史年度因子选择

| Year | Selected Factors |
|---|---|
| 2016 | Momentum, Investment, Low Risk |
| 2017 | Dividend, Quality, Investment |
| 2018 | Quality, Dividend, Low Risk |
| 2019 | Quality, Dividend, Low Risk |
| 2020 | Quality, Low Risk, Momentum |
| 2021 | Quality, Low Risk, Size |
| 2022 | Quality, Size, Dividend |
| 2023 | Size, Dividend, Quality |
| 2024 | Size, Value, Dividend |
| 2025 | Value, Momentum, Dividend |
| 2026 | Value, Investment, Momentum |

可以观察到，策略的因子集合确实随时间发生变化。

例如，2023 年选择 Size、Dividend、Quality，而 2025 年转向 Value、Momentum、Dividend。

这为研究因子轮动与市场环境变化提供了基础。

## 3.3 Risk-Aware Factor Weighting

在选出三个因子后，需要确定因子权重。

我们测试了三种方法。

### Equal Weight（EW）

\[
w_i=\frac{1}{K}
\]

三个因子等权。

### Inverse Volatility（IV）

\[
w_i=\frac{1/\sigma_i}{\sum_j1/\sigma_j}
\]

波动率较低的因子获得更高权重。

### Minimum Variance（MV）

\[
\min_w w^\top\Sigma w
\]

满足：

\[
\sum_iw_i=1
\]

以及：

\[
0.05\leq w_i\leq0.80
\]

MV 使用 10% 的协方差对角收缩。

风险估计使用最近 52 个完整周的历史因子收益。

**因子集合和因子权重每年更新一次，而股票组合每周重新调整。**

## 3.4 Composite Stock Score

对每只股票计算：

\[
Score_{j,t}=\sum_{i\in K_t}w_{i,t}F_{i,j,t}
\]

其中：

- \(K_t\) 为当年选中的因子集合。
- \(w_{i,t}\) 为对应因子权重。
- \(F_{i,j,t}\) 为股票的标准化因子评分。

综合评分越高，股票排名越靠前。

## 3.5 Portfolio Construction

主要组合规则如下：

| Parameter | Setting |
|---|---|
| Portfolio | Long-Only |
| Stock Selection | Top 20% |
| Stock Weighting | Equal Weight |
| Exit Buffer | 30% |
| Sector Cap | 30% |
| Rebalancing | Weekly |
| Transaction Cost | 10 bps One-Way |

Buffer 机制允许已有持仓在跌出 Top 20% 后，只要仍处于允许的退出排名范围内，就可以优先保留，从而减少不必要的换手。

净收益计算：

\[
R_t^{net}=(1-cGTR_t)(1+R_t^{gross})-1
\]

其中：

\[
c=0.001
\]

即单边交易成本 10 bps。

---

# 4. Model Selection（模型选择）

我们比较三种 Adaptive Top-3 模型：

- Top-3 EW
- Top-3 IV
- Top-3 MV

训练期设置为 2016–2020 年。

使用训练期 Sharpe Ratio 选择最终模型。

## 4.1 Training Performance

| Strategy | CAGR | Sharpe | Max Drawdown |
|---|---:|---:|---:|
| Top-3 EW | 14.36% | 0.777 | 35.19% |
| **Top-3 IV** | 16.80% | **0.930** | 32.26% |
| Top-3 MV | **17.37%** | 0.927 | **32.01%** |

虽然 MV 的 CAGR 和最大回撤略优于 IV，但 IV 的训练期 Sharpe 最高。

因此，Trainer 最终选择：

**WF_3Y_Top3_IV**

这一选择在后续 2021–2025 和 2026 年绩效评估之前冻结。

需要注意，项目研究过程中已经尝试过多种策略规格，因此这种时间顺序划分能够减少直接利用未来收益选择模型的问题，但不能完全消除研究过程中的 Data Snooping。

---

# 5. Historical Performance（历史绩效）

## 5.1 2021–2025 Validation Performance

下表采用从 2016 年连续运行的历史回测版本。

| Strategy | Cumulative Return | CAGR | Sharpe | Max Drawdown |
|---|---:|---:|---:|---:|
| Adaptive Top-3 IV | 65.18% | 10.60% | 0.491 | 22.52% |
| 4F Rolling IV | 60.63% | 9.98% | 0.494 | 19.89% |
| 4F Rolling MV | 68.07% | 10.99% | 0.539 | 20.78% |
| CRSP S&P 500 Universe VW | **95.64%** | **14.42%** | **0.704** | 23.67% |

### 结果分析

Adaptive Top-3 IV 相比原始 4F Rolling IV 获得了略高的 CAGR，但 Sharpe 基本相同。

相比 4F Rolling MV，Adaptive IV 的 CAGR 和 Sharpe 均略低。

相比市场基准，Adaptive IV 的累计收益、CAGR 和 Sharpe 明显落后，但最大回撤略低。

因此：

**不能认为 Adaptive Top-3 IV 已经全面优于原始四因子策略或 S&P 500。**

我们的主要研究价值应放在动态因子选择本身是否创造增量收益，而不是宣称已经稳定战胜市场。

---

# 6. Controlled Benchmark Experiment（控制变量实验）

这是目前最重要的策略有效性实验。

## 6.1 为什么需要 Fixed Top-3 IV？

如果直接比较 Adaptive Top-3 IV 与 Fixed 4F Rolling IV，两个策略同时存在多个区别：

- 因子数量不同。
- 因子集合不同。
- 因子选择方式不同。

因此，很难判断绩效差异究竟来自哪一个机制。

为解决这个问题，我们构建 Fixed Top-3 IV。

它在 2021 年初确定三个因子：

**Quality、Low Risk、Size**

之后保持因子集合固定，但仍然每年重新计算 IV 权重。

其他选股和交易规则与 Adaptive 完全一致。

## 6.2 Fair Initialization

为了保证比较公平：

- Adaptive Top-3 IV 从 2021 年重新建仓。
- Fixed Top-3 IV 同样从 2021 年重新建仓。
- 两者初始状态均为 100% Cash。
- 两者均支付初始建仓交易成本。

2021 年两种策略的周度 Gross Return、GTR 和 Net Return 已通过逐周一致性验证。

最大误差约为：

\[
10^{-16}
\]

说明两种策略在起始年度的交易规则和收益记账完全一致。

## 6.3 Controlled Benchmark Performance

2021–2025：

| Metric | Adaptive Top-3 IV | Fixed Top-3 IV |
|---|---:|---:|
| Cumulative Return | **67.08%** | 47.85% |
| CAGR | **10.86%** | 8.17% |
| Sharpe | **0.505** | 0.374 |
| Sortino | **0.709** | 0.526 |
| Volatility | 16.75% | **15.45%** |
| Max Drawdown | **22.53%** | 23.14% |
| Calmar | **0.482** | 0.353 |

相对于 Fixed Top-3 IV：

- CAGR 提高约 2.69 个百分点。
- Sharpe 从 0.374 提高到 0.505。
- Sortino 从 0.526 提高到 0.709。
- 最大回撤略有下降。

此外：

| Relative Risk Metric | Value |
|---|---:|
| Tracking Error | 4.57% |
| Information Ratio | 0.586 |
| Annualized Arithmetic Active Return | +2.67% |

### 结论

在相同的因子数量、IV 配置方法、股票池和交易规则下，动态更新因子集合改善了该验证期的收益和风险调整后表现。

这为 Adaptive Factor Selection 的有效性提供了直接的历史证据。

但我们尚未证明这种改善具有统计显著性。

---

# 7. Annual Performance Attribution（逐年表现）

## 7.1 Adaptive vs Fixed Top-3 IV

以下采用双方从 2021 年现金重新建仓的公平比较口径。

| Year | Adaptive IV | Fixed IV | Relative Performance |
|---|---:|---:|---|
| 2021 | +24.65% | +24.65% | 相同 |
| 2022 | -7.86% | -9.17% | Adaptive 较好 |
| 2023 | +9.55% | +12.07% | Fixed 较好 |
| 2024 | +11.96% | +11.76% | 基本持平 |
| 2025 | +18.60% | +4.26% | Adaptive 明显较好 |

### 主要发现

动态因子选择并不是每年都创造正的超额收益。

2023 年 Fixed Top-3 IV 反而优于 Adaptive。

2024 年两者表现基本相同。

**Adaptive 相对于 Fixed 的累计优势主要来自 2025 年。**

这说明策略可能在某些因子表现发生明显变化的时期受益，但这种机制仍然需要进一步归因。

后续应重点研究：

- 2025 年因子集合变化。
- Value、Momentum、Dividend 的收益贡献。
- 不同因子组合的行业暴露。
- 股票选择与行业配置对超额收益的贡献。

---

# 8. Comparison with S&P 500（市场基准比较）

虽然 Adaptive Top-3 IV 优于 Fixed Top-3 IV，但与 S&P 500 的比较显示了策略的局限。

## 8.1 2021–2025 市场相对表现

| Metric | Adaptive Top-3 IV | S&P 500 Benchmark |
|---|---:|---:|
| CAGR | 10.60% | **14.42%** |
| Sharpe | 0.491 | **0.704** |
| Max Drawdown | **22.52%** | 23.67% |

主策略在该时期的回撤略低，但收益和 Sharpe 均落后于市场。

## 8.2 2023–2024 的相对弱势

| Year | Adaptive IV | S&P 500 | Return Difference |
|---|---:|---:|---:|
| 2023 | 9.55% | 24.50% | -14.95 pp |
| 2024 | 11.96% | 27.64% | -15.68 pp |

这两个年份是目前策略相对市场表现最值得研究的时期。

可能的解释方向包括：

1. 市场收益集中于少数大型成长股。
2. 动态因子集合没有充分捕捉主导市场的股票特征。
3. 行业上限限制了组合对强势行业的配置。
4. 组合对市场 Beta 的暴露随时间变化。
5. Top-3 因子选择可能存在历史表现滞后。

这些目前只是待检验假设，不能直接当作已经完成的归因结论。

## 8.3 Market Risk Attribution

2021–2025 年主策略的市场风险归因结果：

| Metric | Adaptive Top-3 IV |
|---|---:|
| Market Beta | 0.881 |
| Annualized Arithmetic Alpha Estimate | -1.97% |
| Tracking Error | 8.67% |
| Information Ratio | -0.386 |

这说明在该验证期内，策略对市场的 Beta 小于 1，但相对市场的主动收益为负。

当前 Alpha 只是回归截距的估计值，尚未完成统计显著性检验。

---

# 9. 2026 Recent Extension

为了考察策略在近期市场中的表现，我们进一步使用 2026 年扩展数据进行回测。

2026 年选择的三个因子为：

- Value
- Investment
- Momentum

当前冻结模型仍采用 IV 权重方法。

## 9.1 2026 Performance

| Strategy | Cumulative Return | Volatility | Zero-RF Sharpe | Max Drawdown |
|---|---:|---:|---:|---:|
| Top-3 EW | **23.30%** | 14.09% | 2.055 | 6.63% |
| Top-3 IV | 21.53% | **12.47%** | **2.151** | **6.38%** |
| Top-3 MV | 21.55% | 13.17% | 2.044 | 6.78% |
| S&P 500 Compustat TR | 13.60% | 13.05% | 1.368 | 8.87% |

2026 年 Top-3 IV：

- 累计收益高于市场约 7.93 个百分点。
- Sharpe 高于其他两种 Top-3 权重方法。
- 最大回撤低于市场。
- 年化波动率略低于市场。

这些结果说明策略在 2026 年已覆盖期间具有较好的风险调整后表现。

但需要强调：

**2026 年只有 39 个完整执行周，并非完整自然年。**

## 9.2 Data Quality

2026 年使用 Mixed Source 数据。

Top-3 IV 的数据质量指标：

| Metric | Value |
|---|---:|
| Mean Recovery Weight | 6.31% |
| Mean Recovery Contribution | +0.0041% / week |
| Mean Unpriced Weight | 0.0523% |
| Maximum Unpriced Weight | 1.0204% |

此外，2026 年 Dividend 因子不可用。

2025 年最后一个历史执行周期结束于 2025-12-29，而 2026 年首个执行周期开始于 2026-01-05。

因此存在一个未覆盖执行周。

这意味着 2016–2025 与 2026 不能直接解释为完全连续、完全同口径的实盘回测。

当前 2026 模块读取已经生成的周度策略收益并复现绩效，而不是从原始 2026 数据重新构建全部股票持仓。

---

# 10. Current Strengths（当前优势）

## 10.1 清晰的经济学动机

策略建立在因子表现具有时变性的假设上。

动态因子选择具有明确的研究动机，而不是完全依赖黑箱优化。

## 10.2 可解释的策略结构

策略可以分解为：

Factor Selection → Factor Weighting → Composite Score → Portfolio Construction → Return Accounting

每个环节都有明确的参数和可审计输出。

## 10.3 动态因子选择具有正向历史证据

在控制变量实验中，Adaptive Top-3 IV 的 CAGR、Sharpe、Sortino 和 Calmar 均高于 Fixed Top-3 IV。

## 10.4 一定的风险控制能力

策略包含：

- IV 风险配置。
- 因子权重上下限。
- Sector Cap。
- Buffer。
- 股票等权。
- 交易成本。

在部分下跌年份，策略表现出一定防御性。

## 10.5 工程复现能力

目前项目已经实现：

- 2016–2025 年 520 周历史回测复现。
- EW / IV / MV 收益和交易记账审计。
- Model Selection Trainer。
- Fixed Top-3 Benchmark。
- Controlled Benchmark Reporting。
- 2026 Recent Extension Reporting。
- 七阶段 Unified Runner。

最终集成测试已成功运行。

---

# 11. Current Limitations（当前问题）

## 11.1 尚未稳定跑赢市场

2021–2025 年，策略的 CAGR 和 Sharpe 均低于市场 Benchmark。

这是目前最重要的绩效局限。

## 11.2 2023–2024 相对市场表现较弱

策略在这两个年份明显落后于市场。

需要进一步判断是因子选择、行业约束还是个股选择导致。

## 11.3 Adaptive 优势集中于部分年份

相对 Fixed Top-3 IV 的优势主要来自 2025 年。

因此还不能认为动态因子选择在所有市场环境中都有效。

## 11.4 Data Snooping Risk

研究过程中已经测试过：

- 不同因子数量。
- EW、IV、MV。
- 不同训练窗口。
- Fixed 4F 和 Adaptive Top-3。
- 不同 Benchmark 设计。

因此，历史绩效可能受到研究过程中的模型选择偏差影响。

## 11.5 统计显著性尚未充分验证

目前尚未完成：

- Alpha t-statistic。
- HAC / Newey-West Robust Standard Errors。
- 完整的主动收益显著性检验。

## 11.6 2026 数据口径存在差异

2026 年与历史回测的数据来源和可用性规则不同。

还存在一个未覆盖执行周。

因此，2026 结果应独立展示。

---

# 12. Recommended Next Steps（后续研究建议）

## Priority 1：完成团队交付

当前最紧要的工作不是继续优化绩效，而是完成稳定的策略框架交付。

包括：

- 完成 README。
- 完成自动化验收测试。
- 检查数据共享权限。
- 整理 Git 提交内容。
- 将代码提交到团队开发分支。

## Priority 2：2023–2025 Performance Attribution

重点解释：

- 为什么 2023–2024 跑输市场？
- 为什么 2025 年 Adaptive 明显优于 Fixed？
- 哪些因子贡献了主要收益？
- 哪些行业或股票贡献了超额收益？

建议开展：

**Factor Attribution、Sector Attribution、Stock Selection Attribution。**

## Priority 3：Robustness Tests

在不根据未来绩效事后选择参数的前提下，测试：

- Alternative Lookback Windows。
- Top-3 vs Top-4。
- Transaction Cost Sensitivity。
- Alternative Factor Weighting。
- Different Market Regimes。

## Priority 4：2026 Data Integration

未来可以将 2026 原始数据构建逻辑迁移到正式 Pipeline，实现从原始因子评分、股票收益到组合回测的完整重建。

---

# 13. Project Reproduction（项目复现）

当前分支：

`feature/adaptive-factor-strategy`

统一运行命令：

```bash
python -m src.adaptive_strategy.run \
    --config configs/adaptive_strategy.json
```

主要输出：

| Output | Description |
|---|---|
| `historical_reproduction/` | 历史策略收益和因子决策 |
| `model_selection/` | 模型排名和冻结配置 |
| `fixed_top3_benchmark/` | 固定三因子对照策略 |
| `benchmark_validation/` | 公平初始条件比较 |
| `benchmark_reporting/` | 控制变量绩效报告 |
| `reporting/` | 历史市场及四因子 Benchmark 报告 |
| `recent_extension/` | 2026 年近期扩展报告 |

历史 Pipeline 已通过逐周复现验证。

2026 扩展已通过收益复现验证，但目前属于结果级报告模块。

项目仍需确认 WRDS、CRSP 和 Compustat 衍生数据在团队仓库中的共享权限。

---

# 14. Final Assessment（最终评价）

当前 Adaptive Multi-Factor Strategy 已经形成一套较完整的研究和工程框架。

从研究角度看，最重要的发现是：

**在控制因子数量、权重方法、交易规则和初始状态后，动态因子选择在 2021–2025 年取得了优于固定三因子的收益和风险调整后表现。**

这为 Adaptive Factor Selection 提供了有价值的历史证据。

但与此同时：

- 策略没有在整个验证期跑赢 S&P 500。
- 2023–2024 年存在明显的市场相对弱势。
- 动态策略的优势主要集中于部分年份。
- 研究仍存在模型选择偏差和数据口径限制。

因此，现阶段不应将其描述为已经得到充分验证的最优投资策略。

更准确的定位是：

> **A reproducible and interpretable adaptive multi-factor investment framework that demonstrates promising improvements over a fixed-factor baseline, while leaving meaningful room for further attribution, robustness testing, and risk optimization.**

即：

**一个可复现、可解释的动态多因子投资框架。它相对于固定因子基准展示了具有研究价值的改进，同时仍有进一步归因分析、稳健性检验和风险优化的空间。**

这也应当成为团队后续共同优化的起点，而不是研究的终点。

---

**数据说明：** 本报告的绩效数值依据已经运行的 Historical Pipeline、Model Selection、Controlled Benchmark Reporting 和 2026 Recent Extension 输出整理。不同回测视图的初始持仓条件、无风险利率口径和数据来源可能不同，跨表比较时应遵循各章节的说明。
