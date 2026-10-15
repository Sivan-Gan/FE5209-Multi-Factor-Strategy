# Adaptive Multi-Factor Strategy
## 自适应多因子选股策略 — 项目说明

## 1. 项目概述（Overview）

本项目是 FE5209 Multi-Factor Strategy 的一个扩展研究分支，旨在构建一套完整、可复现的美股多因子量化投资策略。

策略的核心思想是：

**通过历史因子表现动态选择有效因子，并利用风险调整后的因子权重构建股票组合，以适应不同市场环境下的因子表现变化。**

与传统固定多因子策略不同，本策略不会始终使用相同的因子集合，而是根据历史因子收益，每年重新选择表现最好的三个因子。

策略主要包括五个部分：

1. **Factor Construction**：构建和处理股票因子信号。
2. **Adaptive Factor Selection**：根据历史表现动态选择 Top-3 因子。
3. **Risk-Aware Factor Weighting**：利用历史风险估计确定因子权重。
4. **Portfolio Construction**：根据综合因子评分构建股票组合。
5. **Backtesting & Risk Evaluation**：进行回测、绩效评价和风险分析。

本项目提供：

- 2016–2025 年完整历史 Walk-Forward 回测。
- 2016–2020 年模型选择及 2021–2025 年时间顺序验证。
- Adaptive 与 Fixed Top-3 的控制变量对照实验。
- 2026 年近期市场扩展回测报告。
- 自动化绩效分析、Benchmark 比较和图表生成。
- 统一运行入口及复现测试。

---

## 2. 策略核心逻辑（Strategy Methodology）

### 2.1 候选因子集合

策略从七个因子类别中选择：

| 因子 | 英文名称 | 主要经济学含义 |
|---|---|---|
| 质量 | Quality | 衡量公司的盈利能力与财务质量 |
| 动量 | Momentum | 捕捉过去收益趋势的延续性 |
| 低风险 | Low Risk | 倾向于历史风险较低的股票 |
| 投资 | Investment | 衡量公司投资扩张行为 |
| 价值 | Value | 衡量股票相对于基本面指标的估值 |
| 股息 | Dividend | 衡量股息相关特征 |
| 规模 | Size | 衡量公司市值规模 |

这些因子由项目现有的 Factor Research 数据模块提供。

本策略复用已经处理和标准化的因子评分，而不是在 Adaptive Strategy 模块中重新从原始财务报表计算所有因子。

### 2.2 动态因子选择（Adaptive Factor Selection）

策略每年年初重新选择三个因子。

对于每个候选因子，使用过去三年已经完成的因子收益，计算：

\[
SelectionScore_i=\frac{\overline{R_i}}{\sigma_i}
\]

其中：

- \(\overline{R_i}\)：历史平均周度因子收益。
- \(\sigma_i\)：历史周度因子收益标准差。

按照 Selection Score 从高到低排序，选择前三个因子。

主要参数：

| 参数 | 设置 |
|---|---|
| Lookback Window | 3 年 |
| Selected Factors | Top-3 |
| Minimum Coverage | 65% |
| Selection Frequency | 每年一次 |
| Look-Ahead Control | 只使用决策时点已完成的收益 |

需要注意：Selection Score 是基于原始因子收益均值与波动率计算的排序指标，并非扣除无风险利率后的标准 Sharpe Ratio。

### 2.3 风险调整因子权重（Risk-Aware Factor Weighting）

确定 Top-3 因子后，需要决定三个因子在股票综合评分中的权重。

本项目实现三种方法：

**Equal Weight（EW）**

\[
w_i=\frac{1}{K}
\]

三个因子平均分配权重。

**Inverse Volatility（IV）**

\[
w_i=\frac{1/\sigma_i}{\sum_{j=1}^{K}1/\sigma_j}
\]

历史波动率较低的因子获得更高权重。

**Minimum Variance（MV）**

\[
\min_w w^\top\Sigma w
\]

满足：

\[
\sum_iw_i=1
\]

\[
0.05\leq w_i\leq0.80
\]

其中 \(\Sigma\) 为历史因子收益协方差矩阵。

MV 使用 10% 的对角协方差收缩，以降低协方差估计噪声。

主要参数：

| 参数 | 设置 |
|---|---|
| Risk Estimation Window | 52 周 |
| Minimum Factor Weight | 5% |
| Maximum Factor Weight | 80% |
| Covariance Shrinkage | 10% |
| Factor Weight Update | 每年一次 |

**重要：本项目中的因子权重每年重新计算，而不是每周重新计算。**

每周更新的是股票综合评分、持仓选择和组合权重。

### 2.4 股票综合评分（Composite Score）

对于每只股票，将选中的三个因子评分进行加权求和：

\[
Score_{j,t}=\sum_{i\in K_t}w_{i,t}F_{i,j,t}
\]

其中：

- \(K_t\)：当年选中的因子集合。
- \(w_{i,t}\)：当年对应的因子权重。
- \(F_{i,j,t}\)：股票 \(j\) 在因子 \(i\) 上的标准化评分。

综合评分越高，股票在当周的排序越靠前。

历史回测采用共同可用股票池（Common Eligible Universe），要求所需因子评分满足数据可用性条件。

### 2.5 股票组合构建（Portfolio Construction）

根据综合评分构建 Long-Only 股票组合。

主要规则：

| 参数 | 设置 |
|---|---|
| Portfolio Type | Long-Only |
| Stock Selection | Top 20% |
| Stock Weighting | Equal Weight |
| Exit Buffer | 30% 排名阈值 |
| Sector Cap | 30% |
| Rebalancing | Weekly |
| Transaction Cost | 单边 10 bps |

**Buffer 机制**

为降低不必要的换手率，已经持有的股票即使跌出 Top 20%，只要仍处于允许的退出排名范围内，就可以优先保留。

该规则同时受到行业配置上限等组合约束。

**交易成本**

每次调仓根据实际交易权重计算成本：

\[
Cost_t=c\times GTR_t
\]

其中：

- \(c=0.001\)，即单边 10 bps。
- \(GTR_t\) 为股票交易权重绝对变化之和。

净收益按以下方式计算：

\[
R_t^{net}=(1-cGTR_t)(1+R_t^{gross})-1
\]

---

## 3. 模型选择（Model Selection）

本项目将策略内部的年度因子选择与策略模型选择区分开来。

### 3.1 策略内部 Walk-Forward

每年根据过去三年的因子收益选择 Top-3，并根据最近 52 周风险重新确定权重。

这一过程贯穿整个历史回测。

### 3.2 权重方法选择

Model Selection Trainer 比较：

- Adaptive Top-3 EW
- Adaptive Top-3 IV
- Adaptive Top-3 MV

使用以下时间划分：

| 阶段 | 时间 |
|---|---|
| Training | 2016–2020 |
| Validation | 2021–2025 |

选择标准为训练期 Sharpe Ratio。

当前结果：

| 模型 | 训练期 CAGR | 训练期 Sharpe |
|---|---:|---:|
| Top-3 EW | 14.36% | 0.777 |
| **Top-3 IV** | 16.80% | **0.930** |
| Top-3 MV | 17.37% | 0.927 |

因此当前冻结模型为：

**Adaptive 3Y Top-3 IV**

对应配置保存在：

`model_selection/frozen_model.json`

需要说明：虽然训练与验证在时间上分离，但项目研究过程中已经考察过多种策略规格，因此不能将验证期描述为完全未接触过的独立盲测样本。

---

## 4. Benchmark 设计

为了判断策略的有效性，本项目使用多种 Benchmark。

### 4.1 Fixed Top-3 IV

这是检验动态因子选择价值的核心对照。

Fixed Top-3 IV 在 2021 年第一个信号日确定三个因子：

- Quality
- Low Risk
- Size

之后在 2021–2025 年保持因子集合不变，但仍然每年重新估计 IV 权重。

与 Adaptive Top-3 IV 相比：

| 项目 | Adaptive | Fixed |
|---|---|---|
| 因子数量 | 3 | 3 |
| 因子集合 | 每年变化 | 固定 |
| 因子权重 | IV | IV |
| 权重更新 | 每年 | 每年 |
| 股票调仓 | 每周 | 每周 |
| 交易成本 | 相同 | 相同 |
| 初始状态 | 100% Cash | 100% Cash |

为保证公平，两个策略均从 2021 年初重新建仓。

2021 年两者的 Gross Return、GTR 和 Net Return 已通过逐周一致性验证。

### 4.2 Controlled Benchmark 结果

2021–2025 年：

| 指标 | Adaptive Top-3 IV | Fixed Top-3 IV |
|---|---:|---:|
| 累计收益 | 67.08% | 47.85% |
| CAGR | 10.86% | 8.17% |
| Volatility | 16.75% | 15.45% |
| Sharpe | 0.505 | 0.374 |
| Sortino | 0.709 | 0.526 |
| Max Drawdown | 22.53% | 23.14% |
| Calmar | 0.482 | 0.353 |

相对于 Fixed Top-3 IV：

- CAGR 高约 2.69 个百分点。
- Sharpe 从 0.374 提高至 0.505。
- Information Ratio 为 0.586。

结果表明，在该样本期内，动态调整因子集合改善了组合的收益及风险调整后表现。

但这不意味着动态策略每年都优于固定策略，也不构成统计显著性的证明。

### 4.3 其他 Benchmark

还包括：

- Adaptive Top-3 EW
- Adaptive Top-3 MV
- Original Fixed 4F Rolling IV/MV
- S&P 500 市场基准

历史市场基准为 CRSP S&P 500 Universe Value-Weighted Index，并不完全等同于官方 S&P 500 Composite Total Return Index。

Fixed 4F 用于与原始策略框架比较，但由于因子数量和因子集合不同，不适合作为单独检验动态选因子价值的唯一对照。

---

## 5. 历史回测（Historical Backtesting）

### 5.1 Full Walk-Forward Backtest

历史回测覆盖 2016–2025 年，共 520 个执行周。

每个年度决策时点只使用已经完成的历史因子收益。

项目已经完成对原始实验的逐周复现：

- Gross Return
- Gross Traded Weight
- Net Return

EW、IV、MV 共九项收益与交易记账检查全部通过，最大误差约为 \(2.22\times10^{-16}\)。

### 5.2 历史验证与控制变量实验

项目提供两个不同的绩效视图：

**Full Historical Walk-Forward**

从 2016 年连续运行，保留历史持仓与 Buffer 状态。

**Controlled Benchmark Validation**

2021–2025 年从现金重新建仓，公平比较 Adaptive 与 Fixed Top-3 IV。

由于初始持仓状态不同，两者的 2021–2025 年累计收益可能略有差异，不能在同一张表中混用。

---

## 6. 2026 年近期扩展（Recent Extension）

项目另外提供 2026 年近期扩展结果。

覆盖 39 个完整执行周，最后一个执行周期结束于 2026 年 10 月 5 日。

### 6.1 2026 年因子选择

2026 年选择：

- Value
- Investment
- Momentum

当前冻结模型继续使用 IV 权重配置，不根据 2026 年结果重新选择 EW 或 MV。

### 6.2 2026 年绩效

| 策略 | 累计收益 | 年化波动率 | Zero-RF Sharpe | 最大回撤 |
|---|---:|---:|---:|---:|
| Top-3 EW | 23.30% | 14.09% | 2.055 | 6.63% |
| Top-3 IV | 21.53% | 12.47% | 2.151 | 6.38% |
| Top-3 MV | 21.55% | 13.17% | 2.044 | 6.78% |
| S&P 500 | 13.60% | 13.05% | 1.368 | 8.87% |

2026 年 Sharpe 使用零无风险利率，不能直接与历史使用实际 RF 的 Sharpe 完全同口径比较。

### 6.3 数据限制

2026 年扩展使用 Compustat 及 Mixed Source 数据。

主要限制：

- Dividend 因子评分不可用。
- 部分股票收益使用授权 Recovery 方法。
- 少量持仓收益不可定价。
- 股票数据来源和历史时期不同。
- 2025-12-29 至 2026-01-05 的执行周期未覆盖。

因此，2026 年应作为独立 Recent Extension 报告，而不是无缝拼接到 2016–2025 的连续实盘模拟中。

此外，当前 Unified Runner 的 2026 模块读取已经计算好的策略周度收益，并重新计算绩效及进行复现验证，尚未实现从 2026 原始数据重新构建完整持仓的端到端回测。

---

## 7. 项目代码结构

```text
FE5209-Adaptive-Strategy/
├── configs/
│   └── adaptive_strategy.json
│
├── src/
│   ├── strategy/
│   └── adaptive_strategy/
│       ├── factor_selection.py
│       ├── factor_weighting.py
│       ├── composite_score.py
│       ├── pipeline.py
│       ├── model_selection.py
│       ├── fixed_top3_benchmark.py
│       ├── benchmark_validation.py
│       ├── benchmark_reporting.py
│       ├── reporting.py
│       ├── recent_extension.py
│       ├── benchmark_2026.py
│       ├── combined_reporting.py
│       └── run.py
│
├── data/
│   └── adaptive_strategy/
│
├── tests/
│   └── test_adaptive_acceptance.py
│
└── outputs/
    └── adaptive_strategy/
```

项目复用原始 `src/strategy/` 中的 Portfolio Construction 和 Return Accounting 模块，避免重复实现底层交易逻辑。

---

## 8. 环境配置（Installation）

建议使用 Python 3.12。

主要 Python 依赖包括：

```text
numpy
pandas
pyarrow
scipy
matplotlib
```

请优先使用仓库已有的依赖配置文件安装环境。

历史 Parquet 数据通过 Git LFS 管理。

首次克隆仓库后，需要确保已安装 Git LFS，并执行：

```bash
git lfs pull
```

---

## 9. 统一运行（Unified Runner）

项目的统一配置文件：

`configs/adaptive_strategy.json`

当所有必需数据都位于当前仓库时，设置：

```json
"data_root": "."
```

在项目根目录执行：

```bash
python -m src.adaptive_strategy.run \
    --config configs/adaptive_strategy.json
```

如需将结果保存到独立目录：

```bash
python -m src.adaptive_strategy.run \
    --config configs/adaptive_strategy.json \
    --output-root outputs/adaptive_strategy/integration_test
```

Unified Runner 包含七个阶段：

1. Historical Adaptive Backtest
2. Model Selection
3. Fixed Top-3 Benchmark
4. Controlled Benchmark Validation
5. Controlled Benchmark Reporting
6. Historical Performance Reporting
7. 2026 Recent Extension Reporting（可选）

当前版本已通过七阶段集成运行。

---

## 10. 自动化验收测试（Acceptance Tests）

运行：

```bash
python -m unittest discover \
    -s tests \
    -p "test_adaptive_acceptance.py" \
    -v
```

验收测试主要检查：

- 历史回测收益复现。
- 520 周回测数据完整性。
- Trainer 模型选择结果。
- Fixed Top-3 Benchmark。
- 2021 年两种策略的逐周一致性。
- 2026 年收益复现。
- Reporting 输出文件。

当前测试脚本默认检查 `final_integration_test` 输出目录，因此首次克隆仓库后应先运行对应的 Unified Runner，再执行测试。

---

## 11. 输出文件（Outputs）

所有生成结果默认保存在：

`outputs/adaptive_strategy/`

主要输出包括：

| 文件 | 用途 |
|---|---|
| `historical_reproduction/weekly_paths.csv` | 历史策略周度收益 |
| `model_selection/frozen_model.json` | 最终冻结模型 |
| `fixed_top3_benchmark/weekly_paths.csv` | Fixed Benchmark 周度收益 |
| `benchmark_validation/fair_comparison.csv` | 公平比较结果 |
| `benchmark_reporting/overall_comparison.csv` | 完整 Benchmark 绩效 |
| `reporting/overall_performance.csv` | 历史绩效报告 |
| `recent_extension/performance_comparison.csv` | 2026 年扩展绩效 |

还会生成累计净值、年度收益和回撤图表。

`outputs/adaptive_strategy/` 已通过 `.gitignore` 排除，不作为源代码提交。

---

## 12. 数据依赖与使用限制

历史 Canonical Data 位于仓库的 `handoff/` 目录。

Adaptive Strategy 的额外输入文件位于：

`data/adaptive_strategy/`

主要包括：

- Historical Factor Returns
- Fixed 4F Benchmark Paths
- Historical Reproduction Reference
- 2026 Strategy Weekly Returns
- 2026 Benchmark Weekly Returns
- 2026 Performance Reference

部分数据来自 WRDS、CRSP、Compustat 或其衍生计算结果。

**在将数据提交至 GitHub 或分发给组员之前，必须确认相关数据许可允许该范围内的共享。**

Git LFS 仅用于文件版本管理，不代表数据自动获得再分发许可。

---

## 13. 当前局限与未来优化方向

当前策略已经具备完整的历史研究流程，但仍存在以下限制：

**模型选择偏差**

策略研究过程中已经测试多种因子数量、权重方法及训练窗口，历史结果可能受到 Data Snooping 影响。

**市场相对表现**

策略并非在所有年份都优于 S&P 500，尤其在 2023–2024 年表现相对较弱。

**统计显著性**

当前 Alpha 和 Information Ratio 主要用于描述历史绩效，尚未完成完整的统计显著性检验。

**2026 数据一致性**

2026 年使用不同的数据来源与可用性规则，且与历史回测之间存在一个未覆盖执行周。

**后续可优化方向**

- Factor Return Attribution
- Sector Allocation Attribution
- Alternative Lookback Windows
- Transaction Cost Sensitivity
- Nested Walk-Forward Model Selection
- Full 2026 Raw-Data Backtest
- Market Regime Analysis

---

## 14. 当前版本总结

**当前主策略：Adaptive 3Y Top-3 IV**

本项目已经实现：

- 基于历史因子收益的年度动态因子选择。
- 基于风险估计的年度因子权重配置。
- 每周股票综合评分与组合构建。
- 交易成本、行业约束和 Buffer 风险控制。
- 完整历史 Walk-Forward 回测。
- 模型选择及冻结配置。
- Fixed Top-3 控制变量 Benchmark。
- 绩效、风险和市场相对表现报告。
- 2026 年近期扩展报告。
- 统一运行入口和结果复现机制。

项目当前版本的主要目标是提供一个**可复现、可解释、便于团队继续研究和优化的多因子投资策略框架**，而不是宣称已经找到在所有市场环境下都最优的投资模型。