# M5 — US Backtest & Risk Analysis 实现说明

**维护人：M5（Backtest & Risk）**  
**研究市场：US（美国市场）**  
**研究阶段：HIST_OOS_BASE（2021–2025）**  
**冻结策略：`J26_lam0.5_ra1_min4_buf1`**

本文件说明 `src/risk_analysis/` 中各模块的功能、数据来源、计算口径、运行方式及验收状态。

标记约定：

- **[SSOT]**：按照 `docs/US_SG_Cross_Market_Quant_Research.md` 的规定执行。
- **[M4 冻结]**：沿用 Member 4 已冻结的策略配置、持仓和交易规则。
- **[M5 实现]**：由 Member 5 实现的绩效评估、统计推断和风险分析方法。
- **[偏离]**：与原始 SSOT 要求不完全一致的部分，需团队确认。
- **[待验收]**：已生成部分结果，但尚未完成正式检查或团队签字。

SSOT 为最终权威文件。如本 README 与 SSOT 不一致，应记录差异并通过团队治理流程处理，不得直接修改冻结策略。

---

## 1. 模块范围与研究目标

本模块负责对美国市场冻结策略进行样本外回测绩效评估与风险分析，覆盖原始研究手册中的 Backtest、Benchmark、Statistical Inference、Regression、Risk、Cost、Liquidity、Capacity 和 Stress Testing 等要求。

本模块不负责：

- 重新选择最终因子集合；
- 修改 Gate A / Gate B 已冻结的参数；
- 根据 2021–2025 OOS 结果重新优化策略；
- 修改 Member 4 的冻结策略引擎；
- 新加坡市场回测及跨市场验证。

主要研究对象包括：

| 策略 | 定义 | 作用 |
|---|---|---|
| Dynamic FMOM | 使用动态因子权重构造股票组合 | 正式研究策略 |
| Static Multi-Factor | 四个因子等权，其余组合规则与 Dynamic 一致 | 衡量 FMOM 增量价值 |
| Universe Equal Weight | 所有 Eligible 股票等权配置 | 衡量股票池及多因子选股效果 |
| S&P 500 Total Return | 美国市场市值加权全收益基准 | 市场绩效比较 |

其中 Dynamic 和 Static 使用相同的股票池、选股比例、Buffer、Sector Cap 和交易成本规则，仅因子权重不同。

Universe EW 不使用因子评分，也不施加策略的 Sector Cap，因此它与 Static 的差异不能直接解释为纯粹的因子选股贡献。

---

## 2. 冻结策略与回测口径

### 2.1 冻结策略配置

[沿用 M4 冻结配置]

| 参数 | 冻结值 |
|---|---|
| Factor Set | Quality、Momentum、Low Risk、Investment |
| FMOM Lookback | J = 26 |
| Risk Adjustment | 开启，104 个有效完成周 |
| Lambda | 0.5 |
| Minimum Available Factors | 4 |
| Stock Selection | Composite Score Top 20% |
| Portfolio | Long-only、Equal Weight |
| Buffer | Entry Top 20%，Exit Below Top 30% |
| Sector Cap | 30% |
| Rebalance | Weekly |
| One-Way Transaction Cost | 10bp |
| Cost Stress | 20bp |

完整配置以 `config/strategy/us_base_strategy.json` 为准。

### 2.2 信号与交易时间

[SSOT / M4 冻结]

- `signal_date`：每周最后交易日收盘产生信号。
- `execution_date`：下一交易日收盘执行交易。
- `period_end`：下一次调仓对应的执行日。
- 每期收益使用执行日收盘至下一执行日收盘的股票总收益。

策略从 2016 年连续运行，因此 2021 年首周从 2020 年末持仓调整，而不是重新从现金建仓。

2021–2025 年包含 260 个信号周，但只有 259 个在 2025 年末之前完成的持有期。

**所有正式 OOS 绩效比较均使用相同的 259 个完成周期。**

### 2.3 交易成本与净收益

[SSOT 4.4 / M4 冻结]

交易前权重使用上一持有期收益漂移后的权重。

Gross Trading Ratio：

\[
GTR_t=\sum_i|w_{i,t}^{target}-w_{i,t}^{pretrade}|
\]

单边换手：

\[
Turnover_t=\frac{GTR_t}{2}
\]

交易成本：

\[
TC_t=c\times GTR_t
\]

净收益：

\[
R_t^{net}=(1-TC_t)(1+R_t^{gross})-1
\]

其中 \(c=0.001\) 对应单边 10bp。

现金收益为零。

### 2.4 年化口径

[M5 实现 / 偏离]

当前 M5 分析统一使用周度净收益及 52 周年化。

主要指标：

\[
CAGR=(NAV_T/NAV_0)^{52/N}-1
\]

\[
AnnualVolatility=\sigma_{weekly}\sqrt{52}
\]

\[
Sharpe=\frac{\overline{R-R_f}}{\sigma(R-R_f)}\sqrt{52}
\]

最大回撤由周度 NAV 路径计算。

**偏离说明：** 原始 SSOT 要求最终使用日度净 NAV、252 年化。团队已讨论保留周度回测，但正式书面口径确认仍需完成。

不得在同一张绩效表中混用周度与日度年化指标。

---

## 3. 输入数据与文件结构

### 3.1 冻结策略输出

```text
outputs/strategy/frozen/hist_oos/
    run_manifest.csv
    acceptance_test_report.csv
    factor_weights.csv
    target_holdings.csv
    trade_ledger.csv
    weekly_path.csv
```

这些文件由 Member 4 的冻结策略引擎生成。

M5 仅将其作为只读输入，不修改原始冻结结果。

### 3.2 Static Benchmark

```text
outputs/strategy/benchmarks/hist_oos/
    static_target_holdings.csv
    static_trade_ledger.csv
    static_weekly_path.csv
```

Static 使用 λ=0，其余选股与交易规则保持一致。

### 3.3 上游数据接口

| 文件 | 用途 |
|---|---|
| `factor_scores_weekly.parquet` | 冻结四因子股票评分 |
| `security_weekly_execution_returns.parquet` | 股票周度执行期收益 |
| `benchmark_weekly_execution_returns.parquet` | S&P 500 全收益 |
| `risk_free_weekly.parquet` | 周度无风险收益 |
| `execution_liquidity_weekly.parquet` | ADV、历史市值、历史 GICS |
| `security_daily_market_hist_oos.parquet` | 日度市场数据及后续独立审计 |

上游数据位于 `handoff/final_factor/data/` 和 `handoff/us_downstream/data/`。

所有文件应遵守项目的数据许可和 Git LFS 管理规则。

---

## 4. 代码模块与功能

所有 M5 分析代码位于：

`src/risk_analysis/`

| 模块 | 功能 | 主要输出 |
|---|---|---|
| `benchmark_comparison.py` | Dynamic 与 S&P 500 绩效比较 | Benchmark-relative Metrics |
| `run_static_benchmark.py` | Static Multi-Factor 回测 | Static Holdings / Ledger / Path |
| `validate_static_benchmark.py` | Static 会计及持仓验收 | 22 项检查 |
| `significance.py` | HAC 与 Bootstrap 显著性检验 | Statistical Inference |
| `factor_regression.py` | 市场回归、Alpha、Beta | Market Regression |
| `ff_factor_regression.py` | FF6 回归与因子暴露 | FF6 Results |
| `drawdown_analysis.py` | NAV、年度收益、回撤事件 | Drawdown Analysis |
| `build_oos_factor_returns.py` | OOS 因子多空收益 | Factor Return Diagnostics |
| `factor_timing_diagnostics.py` | FMOM 预测能力及择时增量 | Rank IC / Timing Increment |
| `stock_selection_diagnostics.py` | 股票及行业收益贡献 | Stock Contribution |
| `universe_ew_benchmark.py` | Universe EW 基准 | Universe EW Path |
| `cost_analysis.py` | 成本敏感性及盈亏平衡成本 | Cost Sensitivity |
| `concentration.py` | 个股与行业集中度 | HHI / Sector Exposure |
| `liquidity_capacity.py` | 流动性与容量 | ADV Participation / Capacity |
| `market_cap_analysis.py` | 市值分布与行业暴露 | Market-Cap Distribution |
| `regime_analysis.py` | 市场状态分析 | Bull/Bear、High/Low Vol |
| `stress_analysis.py` | 历史压力测试 | Crash/Rally / Worst Weeks |
| `risk_map.py` | 综合风险指标汇总 | US Strategy Risk Map |

---

## 5. 运行顺序

在项目根目录运行。

### 5.1 环境准备

```bash
. .venv/bin/activate
```

环境应包含 pandas、numpy、pyarrow、matplotlib、scipy 和 statsmodels 等实际使用的依赖。


### 5.2 基准及绩效

```bash
python -m src.risk_analysis.benchmark_comparison
python -m src.risk_analysis.run_static_benchmark
python -m src.risk_analysis.validate_static_benchmark
python -m src.risk_analysis.universe_ew_benchmark
```

### 5.3 统计检验与回归

```bash
python -m src.risk_analysis.significance
python -m src.risk_analysis.factor_regression
python -m src.risk_analysis.ff_factor_regression
```

### 5.4 回撤与风险

```bash
python -m src.risk_analysis.drawdown_analysis
python -m src.risk_analysis.concentration
python -m src.risk_analysis.market_cap_analysis
```

### 5.5 成本、流动性与容量

```bash
python -m src.risk_analysis.cost_analysis
python -m src.risk_analysis.liquidity_capacity
```

### 5.6 Regime 与 Stress

```bash
python -m src.risk_analysis.regime_analysis
python -m src.risk_analysis.stress_analysis
```

### 5.7 补充机制诊断

```bash
python -m src.risk_analysis.build_oos_factor_returns
python -m src.risk_analysis.factor_timing_diagnostics
python -m src.risk_analysis.stock_selection_diagnostics
```

这些诊断不参与重新选择冻结策略。

### 5.8 综合 Risk Map

在所有上游分析完成后运行：

```bash
python -m src.risk_analysis.risk_map
```

输出：

```text
outputs/risk_analysis/us_hist_oos/
    us_strategy_risk_map.csv
    us_strategy_risk_summary.csv
    us_risk_map_validation.csv
    us_risk_dashboard.png
```

---

## 6. 主要分析方法

### 6.1 Benchmark Comparison

[M5 实现]

比较四条策略的 CAGR、Volatility、Sharpe、Sortino、MDD 和 Calmar。

同时计算相对于 S&P 500 的：

- Annualized Active Return；
- Tracking Error；
- Information Ratio；
- Market Beta。

所有比较均使用相同的 259 个已完成执行期。

### 6.2 Statistical Inference

[M5 实现]

使用 HAC（Newey-West）对 Dynamic − Static 的周度收益增量进行统计检验。

使用 Block Bootstrap 估计 Sharpe Difference 的置信区间，以部分保留收益序列的时间依赖结构。

主要检验对象是 Dynamic 权重是否相对于 Static 提供增量价值，而不是 Dynamic 是否绝对盈利。

### 6.3 Factor Regression

[M5 实现]

市场回归：

\[
R_{p,t}-R_{f,t}
=
\alpha+\beta(R_{m,t}-R_{f,t})+\epsilon_t
\]

FF6 回归使用：

- Mkt-RF
- SMB
- HML
- RMW
- CMA
- Mom

回归结果包括 Alpha、HAC t-stat、p-value、Factor Exposure 和 R²。

### 6.4 Drawdown Analysis

[M5 实现]

基于周度 NAV 计算：

- Maximum Drawdown；
- Peak Date；
- Trough Date；
- Recovery Date；
- Drawdown Duration；
- Rolling Risk Metrics。

区分 Peak-to-Recovery 和 Trough-to-Recovery，不混用两种恢复时间。

### 6.5 Transaction Cost Analysis

[M5 实现]

固定历史持仓和交易路径，测试单边成本：

\[
c\in\{0,10,20,30\}\text{bp}
\]

计算不同成本下的 CAGR、Volatility、MDD 和 NAV。

Break-Even Cost 定义为使整个 OOS 最终累计净值等于初始净值的单边成本。

该指标不代表实际可接受的最大交易成本。

### 6.6 Portfolio Concentration

[M5 实现]

个股 HHI：

\[
HHI_{stock}=\sum_iw_i^2
\]

Effective Number of Holdings：

\[
N_{eff}=\frac1{HHI_{stock}}
\]

行业 HHI：

\[
HHI_{sector}=\sum_sW_s^2
\]

同时计算 Top-5、Top-10、Max Sector Weight 和 Sector Cap Violations。

### 6.7 Market-Cap Analysis

[M5 实现]

使用历史 `issuer_market_cap` 衡量发行主体规模。

CRSP DlyCap 原始计量单位为千美元，因此：

\[
MarketCap_{USD}=IssuerCap_{raw}\times1000
\]

市值分组：

| 分组 | USD Market Cap |
|---|---|
| Small | < $2B |
| Mid | $2B–$10B |
| Large | $10B–$200B |
| Mega | ≥ $200B |

计算 Weighted Mean、Weighted Median、各组权重和数据覆盖率。

### 6.8 Liquidity & Capacity

[M5 实现]

Participation Rate：

\[
P_{i,t}=\frac{AUM|\Delta w_{i,t}|}{ADV20_{i,t}}
\]

测试 AUM：

- $10M
- $50M
- $100M

参与率限制：

- 1%
- 5%
- 10%

容量瓶颈：

\[
Capacity=\min_{i,t}\frac{p_{max}ADV20_{i,t}}{|\Delta w_{i,t}|}
\]

同时测试 ADV 下降 50% 的压力情景。

结果属于历史流动性约束下的容量情景，不是完整 Market Impact Model。

### 6.9 Market Regime

[M5 实现]

使用滞后 26 周市场收益及历史波动率，将市场划分为：

- Bull / Bear；
- High / Low Volatility。

状态划分不使用当前持有期的未来收益。

### 6.10 Historical Stress

[M5 实现]

按照实际市场持有期收益划分：

- Market Crash：最低 10%；
- Market Rally：最高 10%；
- Normal：其余 80%。

同时记录 Dynamic − Static 最差的若干持有期。

该分类属于 Ex-Post Diagnostic，不是预测信号。

---

## 7. 验收结果

### 7.1 已通过的检查

| 模块 | 结果 |
|---|---|
| Frozen OOS Acceptance | ACCEPTED，15 项 PASS |
| Static Benchmark Acceptance | ACCEPTED，22 项 PASS |
| Universe EW Acceptance | 7 项 PASS |
| 259 Completed Weeks Alignment | PASS |
| 10bp CAGR Reconciliation | PASS |
| Gross / Net Accounting Closure | PASS |
| Sector Cap Check | 0 Violations |
| Risk Map Implemented Checks | 7 项 PASS |

### 7.2 待完成或待团队确认

| 项目 | 状态 |
|---|---|
| Independent PIT / Execution Return Audit | PENDING |
| ADV Missingness & Bottleneck QA | PENDING |
| Market Impact Calibration | PENDING |
| High / Low Rate Regime | PENDING |
| Full Mandatory Robustness | PENDING |
| Daily NAV / Weekly NAV Deviation | TEAM APPROVAL REQUIRED |
| Gate B Full Configuration Grid | NOT FOUND LOCALLY |
| Final Gate B/C Sign-Off | REQUIRES CONFIRMATION |

Risk Map 中已执行的数值检查全部通过，但不代表 US Strategy Diagnostic Gate 已经正式关闭。

---

## 8. 已知局限与注意事项

### 8.1 OOS 不允许重新调参

2021–2025 为冻结后的正式 OOS。

所有根据该区间结果提出的新配置都必须标记为 Exploratory，不得再把同一段数据当作独立验证样本。

### 8.2 周度与日度口径

当前报告使用周度 NAV、52 年化。

原 SSOT 要求日度 NAV、252 年化。需要团队正式记录该偏离。

### 8.3 Capacity 的局限

历史容量估计依赖有效 ADV 数据，未完整覆盖全部交易，也没有建立非线性 Market Impact 模型。

不能将 Capacity Bottleneck 直接解释为实际可投资的最大 AUM。

### 8.4 Factor-Level 与 Stock-Level 不可直接相加

因子多空组合收益与最终 Long-Only 股票组合收益属于不同的投资对象。

Factor-Level Timing Increment 不能直接作为最终股票策略 Gross Return Difference 的精确归因组成项。

### 8.5 Gate B Robustness

Member 4 已在 2016–2020 年完成 28 组配置的研究，并记录在：

`docs/GATE_B_C_US_BASE_DECISION.md`

该证据属于配置期，不能代替正式 OOS Robustness。

完整配置网格 CSV 尚未在本地找到。

### 8.6 数据交付与 Git 管理

`outputs/strategy/` 和 `outputs/risk_analysis/` 的本地文件不一定已经提交到 GitHub。

交接前必须确认接收人能够取得必要的代码、CSV、PNG 和已授权 Parquet 数据。

不得上传原始 WRDS 数据、凭证或未经批准的大型数据文件。

---

## 9. 最终交接

正式研究结果与风险结论详见：

`docs/MEMBER5_US_BACKTEST_RISK_HANDOFF.md`

该文件包含：

- Frozen Strategy 与 Run Identity；
- 四策略 OOS Performance；
- Statistical Significance；
- Market / FF6 Regression；
- Drawdown；
- Cost Sensitivity；
- Liquidity & Capacity；
- Portfolio Concentration；
- Regime & Stress；
- Robustness Evidence；
- Validation Status；
- Receiving-Member Checklist。

**当前状态：** 美国市场主要周度回测与风险分析已完成，可以供团队使用。正式 Gate 关闭仍需处理待验收项目及团队确认事项。
