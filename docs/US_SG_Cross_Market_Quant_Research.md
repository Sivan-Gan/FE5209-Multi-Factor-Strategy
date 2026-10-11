# US–Singapore Factor-Momentum-Tilted Multi-Factor Strategy Research Framework

> 本文档是 FE5209 项目当前版本的研究逻辑总纲。  
> 相较此前较抽象的“US–Singapore Cross-Market Quant Strategy”框架，当前策略已经进一步具体化为：
>
> **先在美国市场从候选股票因子中筛选最终有效因子集合，构建“静态多因子 + Factor Momentum Tilt”的周度纯多头策略；在美国完成策略开发、参数冻结和深度验证后，再将同一核心策略迁移至新加坡市场进行外部跨市场验证。随后分别形成 US 与 SG 的完整 Strategy Profile，比较同一策略在两个市场中的表现差异，解释差异机制，并只针对已经识别出的市场结构问题进行有限适配。**
>
> 当前的 Value、Quality、Momentum、Low Risk、Investment、Dividend、Size 为 **Candidate Factor Pool**，不是最终因子集合。最终到底保留几个因子，只能在美国开发期完成 Forward IC、单因子组合回测、稳定性与冗余性分析后确定。每个经济因子家族的度量方式与该家族是否进入最终策略是两个不同的问题：前者由 Gate A0 的 within-family specification selection 处理，后者由 Gate A 的跨家族 Final Factor Set selection 处理。
>
> 新加坡数据目前尚未完整获取，因此当前实施顺序为 **US-first Development → SG Out-of-Market External Validation**。这里的 “External” 指 **Out-of-Market**：SG 未参与 US Base 的核心开发；它并不自动等于时间意义上的 **Out-of-Time OOS**。文档中已经预设的部分 US/SG 参数差异属于 **Ex-Ante Market Feasibility Parameters**：这些差异是在看到 SG 策略结果之前、基于两个市场客观结构差异预设的规则，不属于后续的 Post-Diagnostic Adaptation。

---


# 0. 本手册如何使用：六人协作与交付规则

这份文件不仅是 Research Blueprint，也作为后续六人并行工作的 **Single Source of Truth**。任何成员在实现某一步时，应优先以本文件中已经冻结的定义、参数、时间规则和输出约定为准；若发现需要修改，必须先更新 Master Parameter Registry 或在组内确认后再执行，避免不同成员使用不同口径。

## 0.1 六人分工总览

以下先用 Member 1–6 作为占位符，组内确认后可直接替换为姓名。

| 成员 | Primary Workstream | 主要负责 | 跨市场延伸责任 |
|---|---|---|---|
| **Member 1** | Data & PIT Lead | US / SG Universe、PIT、价格/财务/行业/FX、Coverage、Data Ready Gate | 两个市场的数据管线、Return Closure、数据审计 |
| **Member 2** | Factor Engineering Lead | Candidate Factor 公式、字段映射、清洗、内部合成、Neutralization；按已冻结 Gate A0 registry 构造 within-family specifications | 同一 Factor Engine 应用于 US / SG；Factor / Sector Attribution |
| **Member 3** | Factor Research Lead | Forward IC、Single-Factor Backtest、稳定性、冗余性；Gate A0 evidence 与 Gate A Final Factor Set | 同一 Validation Engine 应用于 US / SG；Ablation / Robustness |
| **Member 4** | Strategy Engine Lead | Factor Return、FMOM、动态权重、Composite Score、选股、Buffer、执行逻辑 | 同一 Strategy Engine 应用于 US / SG；FMOM Attribution |
| **Member 5** | Backtest & Risk Lead | Backtest、Benchmark、绩效、Turnover、Cost、Regression、Liquidity、Capacity、Drawdown | 同一 Backtest / Risk Engine 应用于 US / SG；Stress / Capacity |
| **Member 6** | Cross-Market & Integration Lead | SG Transfer Specification、Strict vs Feasible 定义、Matched Comparison、Mechanism Diagnosis | Adaptation、Final Integration、Cross-Market Conclusions、Gate 协调 |

### 分工原则

- 每个模块只设 **1 名 Primary Owner**；Contributor 可以协助，但不能产生第二套正式口径。
- SG **不重新实现一套独立 pipeline**。M1–M5 的同一数据 / factor / strategy / backtest engine 必须能够以 `market` 参数运行 US 与 SG；M6 负责 SG specification 与跨市场识别，不负责重写整条 SG pipeline。
- 关键结果必须至少由 **1 名非负责人 Reviewer** 复核。
- Gate A0–F 的冻结决定由全组确认，不允许单个成员自行改变。
- 任何带有 `/` 或 `+` 的协作关系，都必须在任务表中拆成 **Primary Owner / Contributor / Reviewer**，避免共同负责导致无人最终负责。

---

## 0.2 六人协作的整体流水线

第一层是 **Reusable Research Engine**：

$$
\boxed{
M1\ Data
\rightarrow
M2\ Factor\ Engineering
\rightarrow
M3\ Factor\ Validation
\rightarrow
M4\ Strategy\ Engine
\rightarrow
M5\ Backtest\ \&\ Risk
}
$$

这条管线先在 US 开发并冻结，再以同一实现应用到 SG。

第二层是 **Cross-Market Identification**：

$$
\boxed{
M6\ Transfer\ Specification
\rightarrow
Strict\ Transfer
\rightarrow
Feasibility\ Adjusted
\rightarrow
Matched\ Comparison
\rightarrow
Mechanism\ Diagnosis
}
$$

最终解释与审计阶段：

$$
\boxed{
M1\ Closure
+
M2\ Attribution
+
M3\ Ablation/Inference
+
M4\ FMOM\ Attribution
+
M5\ Stress/Capacity
+
M6\ Integration
}
$$

这样既保证六个人都有独立核心责任，也保证 US / SG 不是由两套不同代码路径产生。

---

## 0.3 Step Ownership Matrix

| 模块 / Steps | Primary Owner | Contributor | Reviewer | 核心交付 |
|---|---|---|---|---|
| Step 01–02 US Data | M1 | M2 | M6 | US Data Contract、PIT Panel、Coverage Report |
| Step 03–11 Factor Engineering | M2 | M1 | M3 | Formula Dictionary、Candidate / Neutralized Scores |
| Step 12–15 Factor Research | M3 | M2 | M4 | Gate A0 within-family evidence、IC、Single-Factor Returns、Decision Cards、Final Factor Set Proposal |
| Step 16–22 Strategy Engine | M4 | M3 | M5 | Factor Returns、FMOM、Weights、Scores、Holdings、Trades |
| Step 23–29A US Backtest & Diagnostics | M5 | M2/M3/M4 | M6 | US Backtest、Regression、Attribution Inputs、Robustness、US Strategy Profile |
| Step 30 SG Data | M1 | M6 | M2 | SG Data Contract、Universe、Coverage、REIT Treatment Inputs |
| Step 31 SG Transfer Specification | M6 | M1/M4 | M3 | Strict / Feasible Transfer Spec、Pre-Registration |
| Step 32 SG Strategy Engine | M4 | M6 | M5 | SG Factor Returns、FMOM、Weights、Holdings |
| Step 33.1–33.3 SG Signal Diagnostics | M3 | M2 | M6 | SG IC / Single-Factor / FMOM Evidence |
| Step 33.4–33.8 SG Backtest & Risk | M5 | M2/M4 | M6 | SG NAV、Comparable Regression、Cost/Capacity/Drawdown |
| Step 33.9 SG Robustness | M3 | M5 | M6 | Strict / Feasible + Parameter Robustness |
| Step 34–39 Cross-Market Analysis | M6 | M3/M5 | M1 | Matched Comparison、Difference Inference、Mechanism Diagnosis |
| Step 40 / 40.1 / 41–42 Adaptation / Final Freeze | M6 | Relevant Owner | 全组 | Adaptation Register、Adaptation Validation、Frozen Config、Final Test |
| Step 43 FMOM Attribution | M4 | M3 | M5 | Timing Increment / Turnover Attribution |
| Step 44 Factor/Sector/Stock Attribution | M2 | M1/M4 | M6 | Holdings-Based Attribution |
| Step 45 Ablation & Statistical Recheck | M3 | M4 | M5 | Ablation、Bootstrap / Multiple-Testing Summary |
| Step 46–47 Cost / Capacity / Stress | M5 | M1 | M6 | Realism / Stress Package |
| Step 48 Final Closure | M1 | M4/M5 | M6 | Final Reconciliation Ledger |

**责任规则：** 每一行只有一个 Primary Owner。Contributor 可以修改自己负责的上游模块，但任何影响 Frozen Definition 的修改必须重新过 Gate。

---

## 0.4 每一步统一完成模板

后续任何 Step 被标记为“完成”之前，都必须能够回答五件事：

### Input

这一阶段读取哪些上游文件、哪些字段、哪个日期范围？

### Work

使用什么明确公式、参数和时间规则？

### Output

必须保存什么标准文件、表格或结果？

### Acceptance Criteria

哪些检查通过才算真正完成？不能只以“代码跑通”为完成标准。

### Dependency / Handoff

下一位成员需要什么结果，必须能直接读取，而不需要重新理解或重做上游逻辑。

**原则：** 后续如果某一步仍然存在“组员自己决定”的实现细节，应优先在本文件中补齐，而不是让不同成员各自假设。

---

## 0.5 Canonical Outputs：全组统一交接文件

为避免六个人产生不同版本的同类结果，统一保留以下逻辑文件名。内部可使用 Parquet，同时导出同名 CSV 用于组内检查和课程交付。

| 文件 | Primary Owner | Contributors | 主要内容 |
|---|---|---|---|
| `us_universe_weekly.csv` | M1 | — | 每周 US 可投资标记及剔除原因 |
| `sg_universe_weekly.csv` | M1 | M6 | 每周 SG 可投资标记及剔除原因 |
| `data_coverage_report.csv` | M1 | M2 | 每期每指标覆盖率、缺失率、样本数 |
| `factor_formula_dictionary.csv` | M2 | M1 | Factor、字段、公式、方向、lag、sector / REIT branch、neutralization |
| `factor_scores_weekly.csv` | M2 | — | Raw / Winsorized / Z-score / Neutralized Factor Scores |
| `factor_ic_timeseries.csv` | M3 | — | 每周 Forward IC |
| `factor_validation_summary.csv` | M3 | M2 | IC、ICIR、HAC、FDR、稳定性、Decision Card |
| `factor_returns_weekly.csv` | M3 | M4 | 每个 Factor 的 Top、Bottom、Long–Short Returns |
| `factor_weights_weekly.csv` | M4 | — | EW、FMOM、Blended Weights |
| `stock_scores_weekly.csv` | M4 | — | Composite Score、Rank、有效 Factor 数 |
| `target_holdings_weekly.csv` | M4 | — | 目标权重、进入/退出原因、constraint flags |
| `trades.csv` | M4 | M5 | Execution Date、Old/New Weight、Trade、Turnover、Cost |
| `portfolio_nav_daily.csv` | M5 | M1 | Gross / Net NAV、Benchmark、USD NAV |
| `us_strategy_profile.csv` | M5 | M2/M3/M4 | US 核心绩效与诊断指标 |
| `sg_strict_profile.csv` | M5 | M6 | SG Strict Transfer 核心 portability 指标 |
| `sg_strategy_profile.csv` | M5 | M6 | SG Feasibility-Adjusted Base 完整诊断指标 |
| `comparable_market_regression.csv` | M5 | M6 | US / SG 统一 USD Market-Only Regression |
| `us_factor_regression.csv` | M5 | M2 | US richer local factor diagnostics |
| `sg_factor_regression.csv` | M5 | M2/M6 | SG local / regional richer diagnostics（如数据支持） |
| `strategy_difference_inference.csv` | M3 | M5/M6 | Dynamic−Static、US−SG matched differences 的 HAC / block-bootstrap inference |
| `us_return_source_map.csv` | M2 | M4/M5 | 分层 attribution：FMOM Increment、Holdings Contribution、Cost |
| `sg_return_source_map.csv` | M2 | M4/M5/M6 | 分层 attribution：FMOM、Holdings、FX、Cost |
| `us_robustness_summary.csv` | M3 | M5 | US 预设扰动、2×2 Momentum/FMom decomposition |
| `sg_robustness_summary.csv` | M3 | M5/M6 | SG Strict / Feasible 与同口径 robustness |
| `market_diagnostic_gate.csv` | M6 | M5 | US / SG Mandatory Diagnostic Package 完成状态 |
| `cross_market_matched_comparison.csv` | M6 | M3/M5 | 同期 US–SG 对比 |
| `adaptation_register.csv` | M6 | Relevant Owner | Problem → Mechanism → Proposed Change → Validation Design |
| `adaptation_validation.csv` | M6 | M5 / Relevant Owner | Base vs Adapted、Primary Mechanism Metric、Secondary Metrics、Decision |
| `reconciliation_ledger.csv` | M1 | M4/M5 | Holdings → Gross → Costs / FX → Net 的逐期闭合 |
| `run_manifest.csv` | M1 | 全体 | Data / Config / Code / Environment / Sample 的可复现记录 |
| `oos_contamination_register.csv` | M6 | 全体 | 后期样本的 prior exposure、潜在设计影响与 claim classification |
| `acceptance_test_report.csv` | M1 | 各模块 Owner | Gate 自动化 invariant tests 与 PASS / FAIL 状态 |

这些文件构成 **handoff contract**。下游成员不得绕过标准输出重新从更早阶段自行构造另一套口径。

---

## 0.6 两人复核与 Gate Approval

### 日常模块复核

Primary Owner 完成后，Reviewer 至少检查：

- 输入日期范围；
- 参数读取是否来自统一配置；
- 是否存在 forward-looking 信息；
- 输出行数 / 日期 / security ID 是否合理；
- 关键恒等式是否成立；
- 与上一阶段 Handoff 是否一致。

### Gate 冻结

以下 Gate 必须全组确认：

- Gate 0：Data Ready；
- Gate A0：Within-Family Specification Freeze；
- Gate A：Final Factor Set；
- Gate B：FMOM Configuration；
- Gate C：US Base Strategy；
- Gate D：SG Transfer Specification；
- Gate E：Adaptation；
- Gate F：Final Untouched Test。

冻结后如需修改，必须记录：

1. 修改原因；
2. 修改日期；
3. 修改影响范围；
4. 是否导致原样本失去 OOS 身份。

---

## 0.7 Decision Rights：不同样本允许做什么

| Sample / Evidence Stage | 可以做什么 | 不允许做什么 |
|---|---|---|
| Warm-up | 累积历史、初始化窗口 | 用绩效选策略 |
| Factor Selection | 在 Gate A0 选择 within-family representative specification；在 Gate A 选择 factor family、方向（仅在既有 integrity rules 下）与既授权的 Neutralization | 选择最终 FMOM 配置；使用 2016–2025 outcome 选择 Gate A0 或 Gate A 结果 |
| Configuration Selection | 选 $J$、Risk Adjustment、$\lambda$、Buffer 等 | 回头根据 OOS 重选 Factor |
| Formal Historical OOS (`HIST_OOS_BASE`) | 评价冻结的 US Base、做预先规定的诊断 | 看完结果再修改 Base Strategy；与 2026 extension 混合统计 |
| Recent / Operational Extension (`RECENT_EXTENSION`) | 用冻结策略进行近期/运行延伸与 bridge diagnostics | 用结果重新选择 Factor、参数或将其并入 formal OOS headline statistics |
| Adaptation Validation | 验证已登记的针对性改进 | 无限制参数搜索 |
| Final Untouched Test | 最终验证与报告 | 任何策略修改 |

如果某个样本已经被用于提出或选择新的 Adaptation，它就不再是 Final Untouched Sample。

---

## 0.8 Reproducibility Manifest：每次正式运行必须可复现

任何进入 Gate、正式表格或最终报告的 run，都必须生成 `run_manifest.csv` 或等价记录，至少包含：

- `run_id`；
- `run_timestamp`；
- `market`；
- `sample_start / sample_end`；
- `data_version / vendor_snapshot`；
- `universe_version`；
- `factor_formula_version`；
- `config_version / config_hash`；
- `code_commit / code_hash`；
- `environment / package lock version`；
- `output_artifact_version`；
- `primary_owner`；
- `reviewer`；
- `gate_status`。

正式结果如果无法对应唯一的 `run_id`，不得进入 Final Report。

---

## 0.9 Mandatory Single-Market Diagnostic Package

US 与 SG 都必须分别完成同一套 **Mandatory Diagnostic Package**。这套分析不是“有时间再做”的附加项，而是每个市场被视为“研究完成”的最低要求。

每个市场至少必须覆盖：

1. **Performance Profile**：Gross / Net Return、Volatility、Sharpe、Sortino、Max DD、Calmar、Hit Rate、Benchmark-relative；
2. **Performance Stability**：Year-by-Year、Rolling 12/24/36m、Bull/Bear、High/Low Vol、Crisis/Recovery；
3. **Signal Evidence**：Forward IC、ICIR、Rolling IC、Signal Decay、Single-Factor Return；
4. **Static vs Dynamic Increment**：$\lambda=0$ vs $\lambda=0.5$，明确 Factor Momentum 的增量；
5. **Formal Strategy-Level Factor Regression**：Alpha、Market Beta、Style Betas、$R^2$、HAC / Newey-West inference；
6. **Return Source Map**：Static Base、FMOM Increment、Sector / Stock、FX（SG）、Cost；
7. **Sector / Stock Attribution**：收益由哪些行业与股票贡献，且必须基于真实 holdings；
8. **Holdings Structure**：Number of Holdings、Top-5 / Top-10、HHI、Market-Cap Distribution；
9. **Turnover**：Weekly / Annualized Turnover、Signal / Rebalance Turnover；
10. **Gross → Net**：Transaction Cost、Cost Drag、Break-Even Cost；
11. **Liquidity / Capacity**：ADV、Participation Rate、AUM Scenarios、Impact Sensitivity；
12. **Drawdown Anatomy**：最差时期的 Stock / Sector / Factor / FMOM / Beta / Liquidity / Signal deterioration；
13. **Robustness**：预先规定的合理参数扰动，不用于重新选参；
14. **Regime / Stress**：Bull/Bear、High/Low Vol、Crisis、Rate Regime、Market-Specific Stress；
15. **Return Closure**：Holdings → Gross → Cost/FX → Net 的逐期闭合；
16. **Comparable Market Regression**：US / SG 都必须运行统一的 USD Market-Only specification，作为可横向比较的 Alpha / Beta 口径；
17. **Strategy-Difference Inference**：Dynamic−Static 与正式 Matched US−SG 差异必须提供 HAC 或 block-bootstrap uncertainty；
18. **Momentum × FMOM 2×2 Decomposition**：With / Without Stock Momentum × Static / Dynamic Factor Weighting，检查股票 Momentum 与 Factor Momentum 是否重复。

只有全部完成，才能形成：

$$
\boxed{
US\ Strategy\ Profile
}
$$

或：

$$
\boxed{
SG\ Strategy\ Profile
}
$$

并通过对应的 Strategy Diagnostic Gate。

---

## 0.10 Automated Acceptance Test Suite：Gate 不只靠人工检查

除 Reviewer 人工复核外，所有正式 Gate Run 都必须输出一份自动化验收报告。测试阈值统一写入 config，不能散落在不同成员代码中。

至少包含以下 invariants：

### Data / PIT Tests

- canonical uniqueness key 为 `(date, market, security_id)`；US formal pipeline 中 `security_id = PERMNO`，SG 中为其 data contract 冻结的 security identifier；ticker 只作 descriptive / diagnostic field；
- US formal `(date, PERMNO)` 重复记录数 = 0；
- 不允许 `NaN / inf` 静默进入关键回报、权重与价格字段；
- `information_date <= signal_cutoff`；
- 若只有财报公布日期、没有具体公布时间戳，则保守规定：**从公布日后的下一个本地交易日开始可用**；
- Universe Entry / Exit 与当期 membership 可重建；
- Currency、price、market cap、ADV 单位检查通过。

### Factor Tests

- Z-score / neutralized score 的均值与标准差在 config 允许的 numerical tolerance 内；
- Factor Direction 与冻结定义一致；
- Neutralization 后与被控制变量的残余暴露满足诊断要求；
- Factor availability、有效样本数与覆盖率被完整记录。

### FMOM / Timing Tests

- `factor_return_t` 必须来自上一期 formation 的 factor portfolio；
- `FMOM_t` 不得读取 `factor_return_{t+1}`；
- baseline Forward IC 的 score 必须在 $S_t$ 已知，且被评价的 return 必须从 $E_t$ 开始；任何 $S_t\rightarrow S_{t+1}$ IC 必须单独标记为 non-executable predictive diagnostic；
- Dynamic / Static 两条策略除 Factor Weighting 外其他 inputs 完全一致；
- factor weights 每期和为 1。

### Portfolio Tests

- Target stock weight + residual cash = 1；
- Long-only baseline 不出现负股票权重；
- Sector Cap / Buffer / selection algorithm 与冻结配置一致；
- target count、constraint infeasibility、missing-factor flags 可追踪。

### Accounting Tests

- Holdings 可以重建 Gross Return；
- Trade Ledger 可以重建 Cost；
- Gross → Net 恒等式通过；
- SG Local / FX / Interaction → USD 恒等式通过；
- Final Reconciliation Residual 在冻结 numerical tolerance 内。

每次 Gate Run 保存 `acceptance_test_report.csv` 并写入对应 `run_id`。任一 Critical Test 失败，该 Run 自动标记为 `REJECTED`，不得进入正式结果表。

---

## 0.11 Evidence Labels & Claim Boundaries：每个结果必须知道自己能证明什么

任何主要结果必须带有以下证据标签之一：

| Evidence Label | 含义 | 可以声称什么 |
|---|---|---|
| `DEV_CURRENT_UNIVERSE` | 旧 Yahoo + SEC、现今成分股 / current-GICS DEV 数据层 | DEV historical evidence；不能作 Formal PIT 或无偏历史 universe claim |
| `FORMAL_PIT_UNIVERSE` | CRSP + Compustat + CCM + Historical GICS 的正式历史数据层 | 仅在 Formal Gate 0 通过后，作为正式 US 历史研究的输入基础；该标签本身不自动声称 vintage-perfect accounting snapshot |
| `DEV` | Development / Factor Selection 证据 | 仅用于设计与筛选 |
| `CONFIG` | Configuration Selection | 仅用于参数冻结 |
| `HIST_OOS_BASE` | 使用 Formal WRDS stack 的 2021–2025.12 Frozen Base 历史样本外 | Base Strategy 的主正式历史 OOS 证据 |
| `RECENT_EXTENSION` | 2026.01–2026.09 冻结策略的 mixed-source 近期 / operational extension | 单独的近期 OOS / operational evidence；不是 Formal Historical OOS，也不并入其 headline statistics |
| `EXT_TRANSFER_STRICT` | SG Strict Transfer | 最严格的 **Out-of-Market** 跨市场外部迁移证据；除非样本时间也完全未被研究过程使用，否则不称 Out-of-Time OOS |
| `FEASIBILITY_TEST` | SG Strict vs Feasible | 预注册市场可实施性调整的效果 |
| `ADAPT_VALIDATION` | Adaptation Validation | 针对性改进的验证证据，不自动等于 Final OOS |
| `FINAL_OOS` | 真正未使用的 Final Test | 最强的冻结后确认性证据 |
| `LIVE_OPS` | 2026.10+ 且在真实策略 freeze 后开始的实时运行 | Operational evidence，不等于短期统计显著性 |

### US Universe Claim Boundary

正式 US 历史研究的目标输入是 CRSP 历史 S&P 500 membership、CRSP market data、time-valid CCM 与 Compustat Historical GICS，统一标记为 `FORMAL_PIT_UNIVERSE`。在 Formal Gate 0 未通过前，不得把已放置的 raw WRDS files 直接视为正式研究结果。

`FORMAL_PIT_UNIVERSE` 首先要求 point-in-time **information availability**：例如 quarterly `RDQ` 与 signal date 的对齐。它不自动证明历史 Compustat accounting values 是 vintage-perfect snapshot；若 WRDS source semantics 未证实 historical revisions / restatements 已按当时 vintage 固定，正式方法必须将其表述为 **publication-date-aligned historical fundamentals**，并披露 residual revision / restatement bias。

旧 Yahoo + SEC package 的“现今 S&P 500 成分股向历史回推”仍存在 survivorship / reconstitution bias，并且 current GICS 不是 historical PIT classification。因此该 package 只能标记为 `DEV_CURRENT_UNIVERSE` 或在 2026 bridge 中作为 `RECENT_EXTENSION` 的有限来源：

- DEV / recent-extension 历史收益不得表述为“无偏的真实可投资历史收益”；
- 任何仍使用 DEV universe 的 Cross-Market 结论必须表述为 **conditional on the research universes used**；
- 不得用后处理消除 survivorship bias；
- Formal historical claims 必须使用经 Gate 0 验证的正式 WRDS pipeline，而不是旧 current-universe package。

### OOS Contamination / Prior-Exposure Register

`HIST_OOS_BASE` 保留为 2021–2025.12 的预先规定 formal research split；它不自动表示该时期从未被任何研究成员看到。由于 formal WRDS rebuild 前的 DEV work 可能已暴露部分后期信息，在 Gate A0 / A / B / C 被视为 fully frozen 前，必须建立并审核 `oos_contamination_register.csv`。

每条记录至少包含：已查看的 evidence / result、已知时的查看日期、是否影响 Factor Selection、是否影响 Configuration、是否影响 Portfolio Rule，以及 contamination classification。应登记的 prior exposure 至少包括 2021–2025 factor performance、strategy performance、parameter comparisons、market-cap diagnostics 及其他可能影响设计的 evidence。

若 later-period information 被实质用于 strategy design，相关 evidence 不得宣称为 completely untouched OOS。不得因此追溯发明 untouched sample，也不得仅因 register 改变既定 sample split；最终报告必须如实披露 prior exposure 与相应 claim limitation。

### Final OOS 不足时的处理

如果 Adaptation 提出后没有足够新的 untouched history：

- 保留原 Base Strategy 的 `HIST_OOS_BASE` 结论；
- Adapted Strategy 只能标记为 `ADAPT_VALIDATION`；
- 不得人为从已经看过的数据中“切一段”重新命名为 Final OOS；
- 真正的 Final Confirmation 只能等待新的未来数据或新的从未使用过的历史区间。

### 两类诊断的权限区别

**Pre-Final Diagnostics** 用于理解 Base Strategy，并可在规定的 Validation Sample 上提出有机制依据的 Adaptation。

**Final Diagnostics** 只用于解释冻结后的 Final OOS 结果：

$$
\boxed{
Explain\ Only;\ No\ Strategy\ Changes
}
$$

---

# 1. 当前项目定位

## 1.1 核心策略

项目当前研究的策略不是简单静态多因子等权，而是：

$$
\boxed{
\text{Multi-Factor Stock Selection}
+
\text{Factor Momentum Timing}
}
$$

最终保留 $K$ 个股票因子以后，每只股票在每期获得因子分数：

$$
F_{k,i,t}
$$

随后利用各因子的历史多空收益计算 Factor Momentum：

$$
FMOM_{k,t}
=
\sum_{j=0}^{J-1}
R^{factor}_{k,t-j}
$$

当前基准：

$$
J=26\text{ weeks}
$$

只保留正的 Factor Momentum：

$$
S_{k,t}=\max(FMOM_{k,t},0)
$$

并转化为动态权重：

$$
w^{FMOM}_{k,t}
=
\frac{S_{k,t}}
{\sum_j S_{j,t}}
$$

若当期所有因子：

$$
FMOM_{k,t}\le0
$$

则动态部分退回因子等权。

最终因子权重：

$$
\boxed{
w_{k,t}
=
(1-\lambda)\frac1K
+
\lambda w^{FMOM}_{k,t}
}
$$

当前基准：

$$
\lambda=0.5
$$

配置选择阶段只比较：

$$
\lambda\in\{0,0.5,1\}
$$

因此三种核心对象是：

- $\lambda=0$：静态等权多因子；
- $\lambda=0.5$：静态多因子 + Factor Momentum Tilt，当前主策略；
- $\lambda=1$：纯 Factor Momentum Weighting。

项目最核心的增量比较是：

$$
\boxed{
\text{Dynamic Factor-Momentum Strategy}
\quad vs\quad
\text{Static Equal-Weight Multi-Factor Strategy}
}
$$

要回答的问题不是“哪个 Sharpe 更高”这么简单，而是：

> **Factor Momentum Timing 是否在完全相同的股票池、因子集合、选股规则、调仓频率与交易成本假设下，为静态多因子策略提供稳定、可解释且成本后仍然存在的增量价值？**

---

## 1.2 股票层最终打分

每期股票总分为：

$$
Score_{i,t}
=
\sum_{k=1}^{K}
w_{k,t}F_{k,i,t}
$$

当前基础交易逻辑：

- 每周调仓；
- 每个市场使用自身交易日历；
- 信号在该市场每周最后一个交易日收盘后生成；
- 只能使用当时已经公开的信息；
- 下一交易日收盘成交；
- 组合纯多头；
- 基础版本组合内等权；
- 因子与选股使用本币数据；
- 最终组合收益统一折算为美元汇报。

当前 SOP 中“一只股票至少有 4 个因子可用”是基于未来 $K=6$ 的暂定规则。由于最终 Factor Set 尚未冻结，该规则不能提前视为最终参数；Factor Set 确定后，应同步把 minimum-available-factor rule 写成与最终 $K$ 一致的固定规则。

---

## 1.3 项目的核心研究问题

项目最终需要依次回答：

1. Candidate Factor Pool 中哪些股票因子真正有效？
2. 哪些因子值得进入最终 Multi-Factor Strategy？
3. Factor Momentum Timing 是否比静态因子权重更有效？
4. 这一策略在美国市场为什么有效或无效？
5. 美国开发出的策略迁移到新加坡后能否继续有效？
6. 如果 US 与 SG 表现不同，差异究竟来自哪里？
7. 哪些差异来自 Signal，哪些来自 Portfolio Translation、Market Structure、Risk Exposure 与 Trading Friction？
8. 针对明确问题进行有限适配后，效果是否真正改善？
9. 最终策略在交易成本、流动性、容量与压力测试以后是否仍然成立？

---

# 2. 项目总体逻辑链

当前最准确的项目主线为：

$$
\boxed{
US\ Data\ \&\ Candidate\ Metrics
}
$$

$$
\downarrow
$$

$$
\boxed{
Incumbent\ Family\ Construction
}
$$

$$
\downarrow
$$

$$
\boxed{
Initial\ Family\ Validation
}
$$

$$
\downarrow
$$

$$
\boxed{
Within\text{-}Family\ Specification\ Validation
}
$$

$$
\downarrow
$$

$$
\boxed{
Gate\ A0\ Family\ Representative\ Freeze
}
$$

$$
\downarrow
$$

$$
\boxed{
Cross\text{-}Family\ Gate\ A\ Review
}
$$

$$
\downarrow
$$

$$
\boxed{
Final\ Factor\ Set
}
$$

$$
\downarrow
$$

$$
\boxed{
Factor\ Momentum\ Configuration
}
$$

$$
\downarrow
$$

$$
\boxed{
US\ Base\ Strategy
}
$$

$$
\downarrow
$$

$$
\boxed{
US\ Deep\ Validation
}
$$

$$
\downarrow
$$

$$
\boxed{
SG\ External\ Transfer\ Test
}
$$

$$
\downarrow
$$

$$
\boxed{
SG\ Deep\ Validation
}
$$

$$
\downarrow
$$

$$
\boxed{
US\ vs\ SG\ Comparison
}
$$

$$
\downarrow
$$

$$
\boxed{
Mechanism\ Diagnosis
}
$$

$$
\downarrow
$$

$$
\boxed{
Market\text{-}Specific\ Adaptation
}
$$

$$
\downarrow
$$

$$
\boxed{
Final\ Revalidation
}
$$

$$
\downarrow
$$

$$
\boxed{
Attribution
+
Cost
+
Capacity
+
Stress
+
Return\ Closure
}
$$

这意味着 Singapore 当前主要承担两项研究功能：

1. **External Cross-Market Validation**：检验美国开发出的核心策略是否具有跨市场可迁移性；
2. **Market-Structure Experiment**：研究同一策略进入结构显著不同的市场以后，哪些环节保持有效、哪些环节发生变化以及为什么。

---

# 3. 三类参数必须严格区分

## 3.1 Core Strategy Parameters

这些参数决定“策略是什么”，主要使用美国开发期确定：

- Final Factor Set；
- 各 Factor 的固定定义；
- Factor Direction；
- Neutralization Rule；
- Factor Momentum Formula；
- FMOM Lookback $J$；
- Risk-Adjusted FMOM 是否启用；
- $\lambda$；
- Weekly Rebalance；
- Stock Score Formula；
- Portfolio Weighting Philosophy；
- Execution Timing。

进入 Singapore Base Transfer 后，这些核心算法原则上不重新根据 SG 历史表现优化。

---

## 3.2 Ex-Ante Market Feasibility Parameters

允许 US 与 SG 不同，但必须在看到 SG 策略结果之前冻结，并且差异来源于客观市场结构。

当前预设包括：

### 有效截面样本

- US：某指标有效股票少于 50 时当期不计算；
- SG：门槛暂设 15。

### 单因子多空组合

- US：Top 30% − Bottom 30%；
- SG：Top 1/3 − Bottom 1/3，以避免每组股票过少。

### 最终选股比例

- US：Composite Score Top 20%；
- SG：Composite Score Top 30%。

### Industry

- US：11 个 GICS 一级行业；
- SG：允许根据实际股票数量预先合并 GICS 行业。

### Liquidity / Universe

两个市场可以使用不同的绝对门槛，但筛选逻辑必须具有相同经济含义。

这些规则的性质是：

$$
\boxed{
Pre\text{-}Specified\ Market\ Feasibility\ Adjustment
}
$$

而不是 Post-Hoc Optimization。

---

## 3.3 Post-Diagnostic Adaptation

只有 US / SG 策略结果已经形成、并且某一问题经过分析确认以后才允许提出，例如：

- SG turnover 明显过高；
- SG portfolio breadth 太低；
- SG sector concentration 太强；
- 某一因子在 SG 长期失效；
- 某一市场存在明显不希望承担的 risk exposure。

必须遵循：

$$
\boxed{
Observed\ Problem
\rightarrow
Diagnosis
\rightarrow
Targeted\ Adaptation
\rightarrow
Independent\ Validation
}
$$

而不是：

$$
Low\ Sharpe
\rightarrow
Parameter\ Search
\rightarrow
Higher\ Sharpe
$$

---

## 3.4 正式 Strategy Objects：避免 “Base” 一词含义混乱

本项目正式区分以下对象：

$$
S_{US}^{Base}
$$

美国开发并冻结的核心策略。

$$
S_{SG}^{Strict}
$$

**Strict Transfer**：尽可能原样复制 US Base 的核心与 portfolio parameters，用于最严格的 portability benchmark。包括相同 Final Factor Set、$J$、$\lambda$、调仓频率、Factor L/S 划分、selection fraction 等；只有机械上无法执行的项目才允许标记为 `Not Feasible`，不得悄悄替换。

$$
S_{SG}^{Feasible}
$$

**Feasibility-Adjusted Base**：使用在看到 SG 策略结果前已经预先冻结的 SG market-feasibility parameters，例如三分位、Top 30%、SG industry grouping、SG liquidity threshold。它是实际 SG 深度诊断的主要对象。

$$
S_{US}^{Adapt},\quad S_{SG}^{Adapt}
$$

只有在诊断机制后、通过 Adaptation Validation 才允许存在。

因此正式比较关系为：

$$
US^{Base}\ vs\ SG^{Strict}
\rightarrow
Pure\ Portability
$$

$$
SG^{Strict}\ vs\ SG^{Feasible}
\rightarrow
Ex\text{-}Ante\ Feasibility\ Adjustment\ Effect
$$

$$
Base/Feasible\ vs\ Adapted
\rightarrow
Post\text{-}Diagnostic\ Adaptation\ Effect
$$

---

# 4. 时间切分与 Research Gates

当前正式 US 时间切分为：

| 阶段 | 区间 | 主要用途 / 权限 |
|---|---|---|
| Initialization / Pre-sample Buffer | 2004–2005 | 初始化 252-day 与其他长 lookback 的 Momentum、Volatility、IVOL 及相关 rolling metrics；不作正式绩效评价 |
| Development / Warm-up | 2006–2010 | 管线与 signal-construction 验证、implementation debugging、基本经济 sanity check；不得使用后续证据反向优化 |
| Factor Selection | 2011–2015 | Candidate metrics / specifications、Forward IC、single-factor Top/Bottom portfolio、稳定性、经济逻辑、互补性；先由 Gate A0 冻结每个多规格经济家族的一项 representative specification，再由 Gate A 冻结 Final Factor Set |
| Configuration Selection | 2016–2020 | FMOM、$\lambda$、组合构造、调仓 / execution rules 等允许的配置选择；期末 Gate B 和 Gate C 冻结 |
| Formal Historical OOS | 2021–2025.12 | 使用 formal WRDS historical stack 的 Frozen US Base 主历史样本外，标签 `HIST_OOS_BASE` |
| Recent / Operational Extension | 2026.01–2026.09 | 使用冻结策略的独立近期 / operational extension，标签 `RECENT_EXTENSION`；不并入 formal OOS headline statistics |
| Live / Forward Test | 2026.10+ | 真实 freeze 后开始的前瞻运行，标签 `LIVE_OPS` |

基本面因子的实际起点不强行设为 2006。若某一期合格股票的基本面覆盖率不足 70%，该因子在该期视为不可用。

### 时间切分的冻结含义

Gate B / Gate C 的配置决策只能使用截至 2020 年的数据；对本 historical research design，核心策略按 **2020 sample cutoff** 视为冻结。这是研究设计的 freeze boundary，不是团队在现实日历 2020 年末已经完成项目的声明。2021–2025.12 是主要 formal US OOS，因此 CAGR、Sharpe、drawdown、alpha、benchmark-relative performance、turnover / cost 与正式 robustness conclusion 必须主要基于这个窗口；OOS contamination / prior-exposure register 仍决定该窗口能否诚实地称为 completely untouched。

2026.01–2026.09 不是 Formal Historical OOS。它可作为按上述历史 sample-cutoff freeze 处理的策略的 Recent / Operational Extension，但不得与 2021–2025.12 合并计算或展示 formal OOS headline CAGR、Sharpe、alpha 或其他主结论。观察 2026 extension 后，不允许重新选择 Factor、参数或重构策略。

2026.10+ 只有在实际 Strategy Freeze 后的第一个可执行 signal date 起，才可称为 `LIVE_OPS`；此前的回填或 shadow run 不能追溯成为 Live evidence。

但如果未来我们使用 Formal Historical OOS 中某段结果：

- 诊断 SG / US 差异；
- 设计 Market-Specific Adaptation；
- 选择新的参数；

那么被使用的时期不能继续被称为 **Final Untouched OOS**。

因此，当 SG 数据范围确认以后，需进一步确定：

- Adaptation / Validation Sample；
- Final Untouched Test Sample。

如果无法再保留足够长的独立历史 Final Test，则 Market-Specific Adaptation 必须明确标记为 **exploratory / post-hoc extension**，而不能与原始 Base Strategy 的 OOS 证据混为一谈。

---


## 4.1 Formal Gate 0 — WRDS Data Ready

在任何 formal canonical dataset construction 或 Step 03 Candidate Factor 计算开始之前，必须先通过 Formal Gate 0。仅有 `wrds_raw/US_WRDS_2026-10-09/` 的 raw files 不构成 Gate 0 pass。

### 必查项目

- Raw-file manifest、SHA256、archive / decompression integrity、schema 与 row/date coverage 完整；
- Formal historical cutoff 固定为 **2025-12-31**，不得以其他 market-data source 静默延长 formal sample；
- CRSP historical S&P 500 membership 的 `(date, PERMNO)` universe key、duplicate keys、entry / exit 与 959 historical-PERMNO coverage 可重建；
- Formal identifier flow 固定为 `Historical S&P 500 Membership → PERMNO → time-valid CCM → GVKEY`；ticker / CUSIP 只可作 audit / diagnostic identity；
- CCM baseline 为 `LINKTYPE ∈ {LC, LU, LS}`、`LINKPRIM ∈ {P, C, J}`，并严格满足 `LINKDT <= t <= LINKENDDT`，不允许 link forward-fill / backward-fill；`J` link 保留且标记 `secondary_issue = True`，`N` 不自动进入 baseline；
- Historical GICS 使用 `comp_na_daily_all.co_hgic` 的 `gvkey, indtype, gsector, ggroup, gind, gsubind, indfrom, indthru`，只接受 `indtype = GICS` 且 `INDFROM <= t <= INDTHRU` 的 assignment；
- Compustat PIT availability、quarterly / annual period semantics、fundamental coverage 与 duplicate / conflict resolution 通过抽样审计，并明确审查 historical revision / restatement semantics：下载值是否可能是 later-restated historical values，以及相应 claim limitation；
- CRSP `DlyCap` 的单位和 **security-level** 语义已验证；`ShrOut` 可用于 audit / related use，但不得复活 Yahoo × SEC market-cap route；
- issuer/company-level market-cap canonical design 已定义：Value 的 company-level accounting numerator 必须配对经济一致的 issuer-level denominator，且在 aggregation rule 冻结前验证所有 relevant equity share classes 的 coverage，不能假设仅汇总 959 个 S&P500-member PERMNOs 已充分；
- 同一 GVKEY / PERMCO 的 multi-share-class securities 已显式识别；issuer-level duplicate / concentration diagnostics 能覆盖 investable universe 与 portfolio；
- CRSP CIZ daily-return、corporate-action 与 delisting semantics 已验证，避免将 legacy return formula 重复套用而 double count delisting effects；
- CRSP index identities、`DlyTotRet` / price-return field use、currency、ADV 与单位固定；
- Fama–French / RF fields 的 decimal-return convention 已验证；
- Signal Date、Information Date、Execution Date 可追踪，PIT spot checks 通过；原始数据不可被下游覆盖。

### Gate 0 主要输出

- raw manifest / SHA256 / schema / coverage audit；
- `data_coverage_report.csv` 与 formal universe summary；
- PERMNO–GVKEY CCM exception table、Historical-GICS timing spot-check table；
- PIT spot-check table、Data Dictionary、Compustat vintage / restatement claim note、CRSP security-/issuer-level market-cap / return / index semantics note；
- Formal Data Ready Checklist 与 acceptance report。

未通过 Gate 0，不进入 formal Factor Construction，也不得以 raw-file presence 宣称 `FORMAL_PIT_UNIVERSE` pipeline 已可用于 Gate A。

---

## 4.2 Master Parameter Registry

所有成员只能从同一参数表读取已经冻结或待测试的参数。下面是当前版本的主登记表。

| Parameter | 当前基准 | 状态 | 决定阶段 | US | SG |
|---|---|---|---|---|---|
| Rebalance Frequency | Weekly | **FIXED** | 已定 | Weekly | Weekly |
| Signal Time | Weekly Last Close | **FIXED** | 已定 | Same | Same |
| Execution | Next Trading Day Close | **FIXED** | 已定 | Same | Same |
| Reporting Currency | USD | **FIXED** | 已定 | USD | USD |
| FX Hedge | None | **FIXED** | 已定 | — | None |
| Winsorization | 1% / 99% | **CANDIDATE** | Factor Selection | Same | Same |
| Fundamental Coverage | 70% | **CANDIDATE** | Factor Selection | Same logic | Same logic |
| Min Cross-Section | — | **EX-ANTE MARKET** | Pre-Specified | 50 | 15 |
| Single-Factor L/S | — | **EX-ANTE MARKET** | Pre-Specified | Top/Bottom 30% | Top/Bottom 1/3 |
| FMOM Lookback $J$ | 26 weeks | **CANDIDATE** | Configuration | Same | Same |
| Risk-Adjusted FMOM | Off | **CANDIDATE** | Configuration | Same | Same |
| $\lambda$ | 0.5 | **CANDIDATE** | Configuration | Same | Same |
| Final Stock Selection | — | **EX-ANTE MARKET** | Pre-Specified | Top 20% | Top 30% |
| Portfolio Weight | Equal Weight | **BASELINE** | Configuration | Same | Same |
| Sector Cap | 30% | **CANDIDATE** | Configuration | Same principle | Same principle |
| Turnover Buffer | TBD | **TBD** | Configuration | TBD | TBD |
| Transaction Cost | TBD | **TBD** | Before OOS | US-specific | SG-specific |
| Within-Family Specification | Gate A0 candidate registry | **GATE A0 CANDIDATE** | Factor Selection / Gate A0 | Existing-constituent Round 1 only | Same methodology when authorized |
| Gate A0 Family Representative | TBD | **TBD** | Gate A0 | One at most per economic family | Base transfer uses frozen US representative |
| Final Factor Set | TBD | **TBD** | Gate A | Developed in US | Base transfer uses same set |
| US/SG Capital Allocation | 80/20 proposed | **DEPLOYMENT TBD** | Final Deployment | 80% | 20% |
| FMOM Input Return | Gross factor L/S return | **FIXED** | Gate B | Same | Same |
| FMOM Minimum History | Full $J$ valid weeks | **FIXED** | Gate B | Same | Same |
| Risk-Adjusted FMOM History | 104 valid weeks | **FIXED if enabled** | Gate B | Same | Same |
| Quantile Count Rounding | `floor(p × N_eligible)` | **BASELINE** | Gate B / D | Same rule | Same rule |
| Tie Break | Score desc, Security ID asc | **FIXED** | 已定 | Same | Same |
| Cost Convention | One-way all-in bps × gross traded notional | **FIXED FORMULA** | Before OOS | Value TBD | Value TBD |
| HAC Lag Rule | TBD | **TBD** | Before formal OOS inference | Same rule | Same rule |
| Bootstrap Block Rule | TBD | **TBD** | Before formal OOS inference | Same rule | Same rule |
| Formal Regime Definitions | TBD | **TBD** | Before regime inference | Pre-specified | Pre-specified |

### 状态定义

- **FIXED**：不得再作为优化变量；
- **BASELINE**：当前基础实现，若要改必须在指定阶段比较；
- **CANDIDATE**：只能在指定 Development Stage 决定；
- **EX-ANTE MARKET**：允许市场不同，但必须在 SG 结果出现前冻结；
- **TBD**：尚未决定，执行到相关 Gate 前必须解决；
- **POST-DIAGNOSTIC ONLY**：只能在发现具体问题后提出。

---

## 4.3 Weekly Timing Contract：以 Signal Cutoff 与 Execution Date 为唯一时间轴

为彻底消除 Factor Return、FMOM 与最终持仓之间的时点歧义，所有周度模块统一使用两个事件时间：

- $S_t$：第 $t$ 个 **Signal Cutoff**，即该市场该周最后一个交易日收盘；
- $E_t$：紧随 $S_t$ 之后的 **下一本地交易日收盘**，也是该期真正的执行价格。

因此：

$$
S_t < E_t < S_{t+1}
$$

### A. Stock / Factor Signal Formation

在 $S_t$ 收盘后，只使用此时已经公开的信息计算：

$$
F_{i,t}
$$

并同时生成：

- Single-Factor Long / Short target；
- Static Multi-Factor target；
- Dynamic Multi-Factor target。

这些 target **都不能假设在 $S_t$ 同一收盘成交**。

### B. Execution

所有 portfolio target 均在：

$$
E_t
$$

即 $S_t$ 后的下一本地交易日收盘执行。

### C. Executable Factor Return

由 $S_t$ 形成并在 $E_t$ 执行的 Factor Portfolio，其完整 executable factor return 定义为：

$$
R^{factor}_{k,t}
=
R^{Top}_{k,E_t\rightarrow E_{t+1}}
-
R^{Bottom}_{k,E_t\rightarrow E_{t+1}}
$$

也就是说，Factor Return 与最终 Strategy 使用相同的 **next-trading-day-close execution convention**。

### D. FMOM 的信息可用边界

在当前 Signal Cutoff $S_t$ 计算 $FMOM$ 时，只允许使用在 $S_t$ 之前已经完整结束的 executable factor-return periods：

$$
\mathcal H_t
=
\{u: E_{u+1}\le S_t\}
$$

因此：

$$
FMOM_{k,t}
=
\sum_{u\in LastJ(\mathcal H_t)}R^{factor}_{k,u}
$$

**当前仍在持有、尚未到 $E_{u+1}$ 的 factor return 不得提前进入 FMOM。**

这意味着最新可用 Factor Return 可能比当前 signal week 滞后一小段时间；这是为了保持真实可执行性而接受的保守 lag。

### E. Indexing Rule

后文若使用简写：

$$
FMOM_{k,t}=\sum_{j=0}^{J-1}R^{factor}_{k,t-j}
$$

其中右侧的 $t$ **指“截至当前 Signal Cutoff 已经完成的最新 factor-return period”**，不是当前尚未完成的持有期。

### 严禁

- 在 $FMOM_t$ 中使用 $E_{t+1}$ 尚未发生时的 Factor Return；
- 用 $S_t$ Close 既算 Signal 又假设同一 Close 成交；
- Single-Factor、Static、Dynamic 三条路径使用不同 execution convention；
- 为了让 FMOM 更“及时”而把未完成 factor return mark-to-market 到中途价格并与 executable return 混用。

---

## 4.4 Metric Contract：统一绩效口径

除非另有明确说明，最终策略指标使用 **USD、扣除成本后的 Daily NAV** 计算。

### Annual Return

$$
AnnualReturn
=
\left(\frac{V_T}{V_0}\right)^{252/N}-1
$$

### Annual Volatility

$$
AnnualVol
=
Std(r^{daily})\sqrt{252}
$$

### Sharpe Ratio

$$
Sharpe
=
\frac{Mean(r^{daily}-r_f^{daily})}
{Std(r^{daily}-r_f^{daily})}
\sqrt{252}
$$

Formal US excess-return、Sharpe 与 regression 的 risk-free baseline 使用已验证单位的 Fama–French `rf` series；其 released convention、frequency alignment 与任何跨市场使用限制必须在 Formal Gate 0 / data contract 中记录，不得再以未核实的通用 3-Month US Treasury 表述替代。

### Turnover 与 Cost：必须区分 Reporting Turnover 和真实交易名义金额

定义股票层 gross traded-notional ratio：

$$
GTR_t
=
\sum_i|w_{i,t}^{target}-w_{i,t}^{pretrade}|
$$

统一报告的 one-way turnover：

$$
Turnover_t
=
\frac12GTR_t
$$

若 $c^{oneway}_{i,t}$ 表示每 1 美元实际成交名义金额的单边 all-in cost：

$$
TC_t
=
\sum_i c^{oneway}_{i,t}|w_{i,t}^{target}-w_{i,t}^{pretrade}|
$$

若使用市场级常数成本 $c^{oneway}$：

$$
TC_t=c^{oneway}GTR_t
$$

等价地，若使用 round-trip cost $c^{roundtrip}=2c^{oneway}$：

$$
TC_t=c^{roundtrip}\times Turnover_t
$$

不得把“单边 bps”直接乘以 $0.5\sum|\Delta w|$ 而不说明成本定义。

### Net Return

Canonical backtest and reconciliation accounting use NAV order, not an additive approximation. For a rebalance executed at $E_t$, let $V_t^-$ be NAV immediately before transaction costs, and let the cost ratio $TC_t$ be measured relative to that pre-cost NAV:

$$
C_t=V_t^-\times TC_t
$$

$$
V_t^+=V_t^- - C_t
=
V_t^-(1-TC_t)
$$

If $R_t^{hold}$ is the post-trade gross holding-period return, then:

$$
V_{t+1}^-=V_t^+(1+R_t^{hold})
$$

and the exact net period return relative to $V_t^-$ is:

$$
1+R_t^{net}=(1-TC_t)(1+R_t^{hold})
$$

$$
R_t^{net}=(1-TC_t)(1+R_t^{hold})-1
$$

The shorthand $R_t^{net}\approx R_t^{gross}-TC_t$ is permitted only when explicitly labeled as a first-order approximation. The canonical NAV, trade ledger, attribution reconciliation, and return-closure tests must use the exact identity. For SG, first close this accounting in SGD, then translate the resulting local return to USD using the separate FX identity in Section 19; costs and FX must not be double counted.

Baseline transaction-cost parameters should explicitly comprise commission / exchange fee, spread / slippage proxy, and other observable explicit costs. Market impact remains a separate stress / capacity scenario until calibrated, without double counting baseline cost.

### Cost-Parameter Status

Gate A may evaluate turnover, break-even cost, and cost sensitivity, but must not use a net performance result based on an unfrozen transaction-cost assumption as a hard Keep / Drop criterion. An illustrative net result is allowed only under an explicitly pre-specified research-cost scenario and must be labeled provisional. A cost assumption becomes a formal decision criterion only if frozen before the relevant decision.

### Max Drawdown：统一正负号约定

先定义 Drawdown Series：

$$
DD_t
=
\frac{V_t}{\max_{s\le t}V_s}-1
\le 0
$$

图表若展示 drawdown path，使用 $DD_t$ 的负值形式。

正式表格统一报告 **非负损失幅度**：

$$
MDD
=
-\min_t(DD_t)
=
\max_t\left(1-\frac{V_t}{\max_{s\le t}V_s}\right)
\ge0
$$

因此例如“最大回撤 20%”在表格中记为 `20%`，而不是 `-20%`。Calmar 的分母直接使用该正值 $MDD$。

所有组员必须调用同一套 Metric Functions；不得在不同模块分别实现一套 Sharpe / Drawdown / Turnover 定义。

---


### Sortino Ratio

以与 Sharpe 相同的 reporting-currency excess return 为分子，downside deviation 以 0 excess return 为阈值：

$$
Sortino
=
\frac{\overline{r_t-r_{f,t}}}
{\sqrt{E[\min(r_t-r_{f,t},0)^2]}}
\times\sqrt{252}
$$

### Calmar Ratio

$$
Calmar
=
\frac{Annualized\ Return}{MDD}
$$

### Hit Rate：必须区分两个概念

- **Period Hit Rate**：日 / 周收益为正的期间占比；
- **Trade Win Rate**：一只股票从完整建仓到完整退出的一笔 round-trip trade 的净收益为正占比。

最终报告不得只写 `Hit Rate` 而不说明是哪一种。

### Benchmark-Relative Metrics

$$
ActiveReturn_t=R_{p,t}-R_{b,t}
$$

$$
TrackingError=\sigma(ActiveReturn_t)\times\sqrt{252}
$$

$$
InformationRatio
=\frac{\overline{ActiveReturn_t}}{\sigma(ActiveReturn_t)}\times\sqrt{252}
$$

### Frequency Rule

若最终 NAV 以 daily series 为权威，则 Annual Return、Volatility、Sharpe、Sortino、Drawdown、Benchmark-relative metrics 均由同一 daily NAV / return series 计算；周度 series 只用于 Factor / FMOM research，不允许把 daily 与 weekly annualization 混在同一结果表中。

---

## 4.5 Native Sample 与 Matched Calendar-Month Sample

US 和 SG 的可用历史很可能不同，因此 Cross-Market Comparison 必须同时保留两种样本。

### Native Sample

每个市场使用自身全部可靠历史。

用途：

> 完整理解该市场内策略自身的长期行为。

### Matched Calendar-Month Sample

正式跨市场主比较不直接取两地 daily trading dates 的交集。两地节假日、时区与收盘时点不同，因此主比较先从各自权威 Daily USD NAV 复合得到自然月收益：

$$
R^{US}_{m},\quad R^{SG}_{m}
$$

随后只使用共同月份：

$$
Months_{US}
\cap
Months_{SG}
$$

用途：

> 排除历史时期不同与 daily calendar mismatch 带来的影响，进行真正可比的 US–SG Cross-Market Test。

正式 portability、active-return difference、comparable regression 与 cross-market block-bootstrap 均优先使用 Matched Calendar-Month Sample。若做 matched-week robustness，必须先定义共同 week label，不能直接按本地交易日期强行对齐。

Native Sample 用于补充各市场长期画像。

Matched Calendar-Month Sample 至少比较：

- IC / ICIR；
- Factor Timing Contribution；
- Gross / Net Return；
- Sharpe；
- Drawdown；
- Turnover；
- Cost Drag；
- Exposure；
- Concentration。

---

## 4.6 Gate A0 Specification Review Card 与 Gate A Family Decision Card

Gate A0 前，每一个 eligible within-family specification 必须生成同样的 Specification Review Card，防止同一经济家族的不同 measurement specification 使用不同标准解释。Gate A 前，每一个已冻结的 family representative 必须生成同样的 Family Decision Card，防止不同经济家族使用不同标准解释。

| Dimension | 必须记录 |
|---|---|
| Economic Rationale | Pass / Concern + 原因 |
| Coverage | Mean / Min Coverage |
| Mean IC | 数值 |
| ICIR | 数值 |
| Positive IC Ratio | 数值 |
| HAC / Newey-West t | 数值 |
| Single-Factor Gross Return | 数值 |
| Top-Leg Excess vs Eligible-Universe EW | 数值 |
| Single-Factor Gross Sharpe | 数值 |
| Max Drawdown | 数值 |
| Turnover | 数值 |
| Break-Even Cost / Cost Sensitivity | 数值 / 情景 |
| Illustrative Net Result | 仅在 pre-specified research-cost scenario 下；未冻结成本前不得作为硬性 Decision criterion |
| Subperiod Stability | Stable / Mixed / Weak |
| Highest Correlation with Other Factor | 数值 + Factor Name |
| Gate A0 Decision | Retain incumbent / replace with challenger / documented exception；仅由全组决定 |
| Gate A Decision | Keep / Watch / Drop family |
| Decision Rationale | 一段明确说明 |

不提前设定机械的“IC > X 才保留”门槛。Gate A0 不设置 highest-Sharpe rule、weighted evidence score 或自动 winner；Gate A 不设置最高 Sharpe 的 family selection rule。两个阶段都必须在各自完全相同的证据框架下进行。

---

### Factor Decision Rule：避免 researcher discretion

Factor Selection 不设置单一机械阈值（例如 “IC > x 即保留”），但所有 Factor 必须按同一决策顺序：

1. Data / Formula Integrity 通过；
2. Economic Rationale 不自相矛盾；
3. Forward IC / ICIR / stability 有方向一致证据；
4. Single-Factor Portfolio 具有可交易性；
5. 与已保留 Factor 具有互补性；
6. Turnover、break-even cost 与预先规定的 cost sensitivity 不显示信息价值在合理成本情景下完全不可实施；若成本尚未在 Gate A 前冻结，illustrative net result 不是硬性 Keep / Drop criterion。

统计显著性不是唯一筛选标准，但因为存在多个 Candidate Factors、多个 horizon 与多个 robustness checks，Factor Selection 阶段至少报告：

- raw p-values；
- Benjamini–Hochberg FDR-adjusted q-values（或组内预先确定的等价 multiple-testing control）；
- tested hypotheses 数量。

Gate A0 representative choice 与最终 Gate-A `Keep / Watch / Drop` 都必须由全组确认，并在对应 Card 中保存：`Decision`、`Reason`、`Exception`、`Approver`。任何例外不能只写口头理由。

---

## 4.7 Portfolio Construction 的唯一执行顺序

正式代码必须只保留一个权威执行顺序。当前在 Turnover Buffer 最终确定前，流程先固定为：

$$
Universe
\rightarrow
FactorAvailability
\rightarrow
CompositeScore
\rightarrow
Rank
\rightarrow
CandidateSelection
\rightarrow
SectorConstraint
\rightarrow
WeightNormalization
\rightarrow
TargetHoldings
\rightarrow
Trades
$$

如果 Gate B 最终启用 Turnover Buffer，则统一改为：

$$
Rank
\rightarrow
RetainExistingHoldingsByExitRule
\rightarrow
FillNewNamesByEntryRule
\rightarrow
SectorConstraint
\rightarrow
Normalize
\rightarrow
Trades
$$

### Selection Count、Tie Break 与 Sector Cap

目标持股数统一按：

$$
N_t^{target}=\left\lfloor p\times N_t^{eligible}\right\rfloor
$$

并保证至少 1 只。Factor Long / Short quantile 的目标数量使用同一 rounding convention。

排名 tie-break 固定为：

1. Score 降序；
2. 若 Score 完全相同，则按固定 Security ID 升序。

不得依赖 dataframe 原始行顺序或 library 默认 tie behavior。

Sector Cap 最终必须明确采用其中一种算法，并写入配置：

1. 先选 Top-N 后对超限 Sector 逐名替换；或
2. 从高分到低分逐名加入，只要加入后不违反 Sector Cap。

如果遍历全部 eligible names 后仍无法填满 $N_t^{target}$：

- 不得静默违反 Sector Cap；
- 未配置部分保留为 Cash；
- 输出 `constraint_infeasible = 1`；
- 在 Strategy Profile 中报告发生频率和 Cash Drag。

### Residual Cash Convention

Baseline 中，因约束无法配置出去的残余现金使用本币 **0% cash return**：

$$
R^{cash,local}_t=0
$$

这是保守且最容易闭合的默认口径。若未来希望让 cash earn local risk-free rate，只能作为预先冻结的 robustness / deployment assumption，不能在 OOS 后临时切换。

因此本币 Gross Return 必须写成：

$$
R^{local,gross}_t
=
\sum_iw_{i,t}R^{local}_{i,t}
+
w^{cash}_tR^{cash,local}_t
$$

并满足：

$$
\sum_iw_{i,t}+w^{cash}_t=1
$$

SG sleeve 的 SGD cash 即使本币 return 为 0，折算到 USD 后仍会受到 USD/SGD translation 影响。

不能由不同成员各自实现不同版本。

---

## 4.8 Return Accounting：禁止 Dividend Double Count

Backtest 与 Live Position Accounting 可以采用不同表现形式，但同一套收益计算中只能使用一种完整口径。

### Backtest 推荐

如果使用 Total-Return Adjusted Price / Return：

> 不再额外把 Dividend Cashflow 加一次。

### Live Position NAV

如果使用 Raw Close：

$$
NAV
=
Shares\times RawClose
+
Cash
$$

则 Dividend 应作为真实 Cashflow 进入现金账户，并正确处理 Split / Corporate Action。

### 原则

$$
\boxed{
Adjusted\ Total\ Return
\quad OR\quad
Raw\ Price+Explicit\ Cashflows
}
$$

不能两者同时重复计入。

---

## 4.9 Statistical Inference Contract：关键差异不能只看点估计

### Dynamic vs Static

定义同日期差异：

$$
\Delta R_t^{FMOM}
=
R_t^{Dynamic}-R_t^{Static}
$$

必须报告：

- Mean $\Delta R$；
- HAC / Newey-West t-stat 或 moving-block / stationary bootstrap CI；
- $\Delta$ Sharpe 的 block-bootstrap confidence interval；
- $\Delta$ MaxDD / turnover 作为经济意义补充。

### US vs SG

正式 cross-market inference 只使用 **Matched Calendar-Month Sample**，所有下式中的 $t$ 在该小节表示共同自然月 $m$。首先比较原始策略差异：

$$
\Delta R_t^{Market}=R_{US,t}^{Base}-R_{SG,t}^{Strict}
$$

同时比较 benchmark-relative active return：

$$
A_t^{US}=R_t^{US}-R_t^{SP500}
$$

$$
A_t^{SG}=R_t^{SG}-R_t^{STI,USD}
$$

$$
\Delta A_t=A_t^{US}-A_t^{SG}
$$

这样可以区分“两个市场本身回报不同”与“策略相对各自市场的增量能力不同”。

Block Bootstrap 必须 **jointly resample the same time blocks across all compared series**（US Base、SG Strict、Static、Dynamic、benchmarks），保留同期相关性与 serial dependence。

不能用简单独立样本 t-test 取代 paired / block-based comparison。

### Robustness / Multiple Testing

Robustness Grid 不用于重新选赢家。若同时报告大量参数 / factor hypothesis，应明确：

- Primary hypothesis；
- Secondary / robustness hypotheses；
- multiple-testing adjustment 或至少 family-wise test count；
- 不根据最小 p-value 改写主结论。

---

## 4.10 Attribution Taxonomy：不同解释层不能横向相加

项目统一使用五个彼此不同的 attribution view：

### View A — Strategy-Rule Increment

$$
R_t^{Dynamic}
=
R_t^{Static}
+
\underbrace{(R_t^{Dynamic}-R_t^{Static})}_{FMOM\ Increment}
$$

用于回答 Factor Momentum rule 的增量效果。

### View B — Holdings Contribution

$$
R_t^{Gross}
=
\sum_i w_{i,t}R_{i,t}
$$

Stock Contribution 可以聚合到 Sector Contribution。Sector / Stock 是同一个 Gross Return 的内部 decomposition，不能再与 Gross Return 横向相加。

### View C — Currency Translation（SG）

若 $R^{FX}$ 表示 1 SGD 对 USD 的收益：

$$
1+R_t^{USD}
=
(1+R_t^{SGD})(1+R_t^{FX})
$$

因此：

$$
R_t^{USD}
=
R_t^{SGD}
+
R_t^{FX}
+
R_t^{SGD}R_t^{FX}
$$

如果使用 additive attribution，必须保留 interaction term，不能把 Local + FX 当成严格恒等式。

### View D — Cost Accounting

Canonical cost attribution uses the NAV identity:

$$
1+R_t^{Net}=(1-TC_t)(1+R_t^{hold})
$$

The cost view may report the first-order cost drag separately for interpretation, but it must not replace the exact NAV / reconciliation identity.

### View E — Factor Regression

$$
R_t-R_{f,t}=\alpha+\beta'F_t+\epsilon_t
$$

Factor Regression 是 exposure explanation，不是 holdings contribution identity，也不能与 Sector / Stock contribution 相加。

最终报告任何“收益来源”图，都必须明确标注属于哪一个 View。

---

## 4.11 Live / Operational Test Governance

Live Test 只验证：

- 数据是否能按时更新；
- Signal 是否按冻结规则生成；
- 交易是否能按 execution rule 记录；
- Holdings / NAV / Cost 是否可实时闭合。

Live Test **不是短期统计显著性证据**。

更重要的是：Live period 不允许回溯定义。只有在策略实际完成 Gate C / 对应部署 Freeze 以后，第一个可执行 signal date 起的记录，才能称为 `Live / Untouched Operational Test`。

如果 2026-10-01 至实际冻结日前已经过去，则该段最多标记为：

- Backfilled / Shadow Run；
- Operational Reconstruction；

不得追溯称为 untouched live performance。

---

## 4.12 Capacity Claim Rule

在没有经过真实 market-impact calibration 前，所有 AUM / ADV / participation analysis 统一称为：

$$
\boxed{Capacity\ Scenario\ Analysis}
$$

只有在 impact function、spread / slippage、trade size calibration 都有可靠数据支持后，才使用 `Estimated Capacity`。不得把简单 ADV threshold 直接表述为真实 capacity ceiling。

---

## 4.13 Regime Definition Contract：正式 Regime Analysis 必须预先定义

为了避免看到策略收益后再人为划分“有利 / 不利时期”，正式 Regime Comparison 必须在对应结果分析前冻结 regime definition。

优先使用外生市场变量，例如：Benchmark Bull / Bear、Realized Market Volatility High / Low、Interest-Rate High / Low、pre-defined crisis windows 与 market-specific macro regimes。

正式 regime 不得直接根据本策略自身收益高低定义。

Drawdown Episode Analysis 可以事后选择最差时期做描述性诊断，但必须标记为：

> **Descriptive Post-Hoc Diagnostic，不属于 confirmatory regime inference。**

Regime threshold、lookback 与 data source 写入 Master Parameter Registry / config。

---

# 5. Part I — 美国数据与 PIT 研究基础

## Step 01 — US Universe 与 Data Contract

### Formal US Historical Universe

正式 US historical universe 使用 **CRSP historical S&P 500 membership**，以 PERMNO 为 security-level key。当前正式 2006–2025 research window 的提取 universe 包含 **959 historical PERMNOs**。这取代了旧 Yahoo + SEC package 中“现今 S&P 500 成分股向历史回推”的 DEV universe，后者不得用于 formal historical claims。

正式历史数据层来自 `wrds_raw/US_WRDS_2026-10-09/`：

$$
CRSP
+
Compustat
+
CCM
+
Historical\ GICS
+
CRSP\ Market/S\&P500\ Indexes
+
Fama\text{-}French/RF
$$

formal historical market-data cutoff 固定为 **2025-12-31**。不得以其他 market-data source 静默把 formal historical sample 延长至该日以后。

`raw_package/US_source_data_2026-10-08/` 的 Yahoo + SEC data 继续保留为 immutable **DEV historical evidence**，以及可能的 2026 Recent Extension source；它不是 formal 2006–2025 research stack。

### Formal Identifier Hierarchy 与 CCM Mapping

正式 mapping flow 固定为：

$$
Historical\ S\&P500\ Membership
\rightarrow
CRSP\ PERMNO
\rightarrow
Time\text{-}Valid\ CCM
\rightarrow
Compustat\ GVKEY
$$

- `PERMNO` 是 security-level key；`GVKEY` 是 company-level key；
- ticker / CUSIP 只可用于适当的 diagnostic 或 audit，不得作为 primary historical join key；
- baseline 接受 `LINKTYPE ∈ {LC, LU, LS}` 与 `LINKPRIM ∈ {P, C, J}`；
- `LINKPRIM=J` 的 genuine secondary / joiner share class 保留，并标记 `secondary_issue = True`；已识别实例包括 GOOG、NWS、UA、FOX、CMCSK 与 DISCK；
- `LINKPRIM=N` 不自动进入 baseline；
- 每一 mapping 必须严格满足：

$$
LINKDT\le t\le LINKENDDT
$$

不得跨 invalid date forward-fill 或 backward-fill CCM link。

已记录的 formal CCM audit 为：959 个 historical S&P 500 PERMNO 中 958 个存在 acceptable CCM link；2,527,140 个 PERMNO-date observations 中 2,527,111 个 matched、29 个 unmatched、0 个 simultaneous different-GVKEY ambiguity，coverage 为 99.998852%。29 个 unmatched 由 1 个真正 unlinked DVMT observation（PERMNO 16267）及 28 个单日 CCM / corporate-action boundary gaps 构成。异常必须分别标记为 `CCM_UNLINKED` 或 `CCM_BOUNDARY_GAP`。

缺少 CCM link 仅意味着该 security-date 没有可用 Compustat company fundamental；**不得因此删除有效的 CRSP market-return observation**。

### Historical GICS

正式 historical sector source 是 Compustat `comp_na_daily_all.co_hgic`，使用：

- `gvkey`；
- `indtype`；
- `gsector, ggroup, gind, gsubind`；
- `indfrom, indthru`。

供本正式资料层使用的全部观测 `indtype` 均为 `GICS`。在 signal date $t$ 只可使用满足：

$$
INDFROM\le t\le INDTHRU
$$

的 historical assignment。该分类用于 sector neutralization、Financials / non-financial / RE branching、sector cap、sector attribution 与 US–SG sector-structure diagnostics。不得以 current / header GICS backfill 历史。

### Formal Market Data、Market Cap、Index 与 RF Sources

CRSP `DlyCap` 是正式 historical market-cap 的 **security-level** raw source；`ShrOut` 仍可用于 audit / related use。旧的 Yahoo price × SEC shares route 已永久退休，正式决定为 `STOP_DEV_MARKET_CAP_REPAIR`，不得作为 formal market-cap baseline 重启。`DlyCap` 与 `ShrOut` 的确切 units / semantics 必须在 Formal Gate 0 和 WRDS data contract 中验证，之后才可进入 canonical construction。

正式 canonical design 必须区分以下经济对象：

- **Security-Level Market Cap**：单一 `PERMNO` 的 `DlyCap`；
- **Issuer / Company-Level Market Cap**：与公司 $g$ 对应的所有 relevant equity share classes 在 $t$ 的市值合计：

$$
IssuerMarketCap_{g,t}
=
\sum_{j\in RelevantEquityClasses(g,t)}DlyCap_{j,t}
$$

若 issuer 只有一个 relevant equity class，两者可以重合；但不得因此把这种偶然情形一般化。对 E/P、B/P、FCF Yield 等以 company / `GVKEY` accounting numerator 为分子的 Value ratio，必须使用经济一致的 issuer-level denominator，不得机械地除以其中一个 share class 的 `DlyCap`。Gate 0 必须先验证 all relevant equity share classes 的 coverage；不得预设只汇总 959 个 S&P 500-member PERMNOs 已经充分。

Size factor 与 size neutralization 的 market-cap economic level 必须在其正式定义中分别冻结并记录；portfolio holdings、ADV / liquidity 与交易成本仍以实际可交易的 security-level holding 为对象。多 share classes 不自动禁止进入 universe，但必须显式识别、以一致的 accounting treatment 处理，并在 investable universe 与 portfolio 报告 security-level 和 issuer-level duplicate / concentration diagnostics（包括同一 `GVKEY` / `PERMCO`）。

对 IVOL / US market regression，正式 proxy 为 `INDNO = 1000080` 的 CRSP NYSE / NYSEMKT / Nasdaq Value-Weighted Market Index，使用 `DlyTotRet`：

$$
R_{i,d}=\alpha_i+\beta_iR_{M,d}+\epsilon_{i,d},
\qquad
IVOL_i=Std(\epsilon_{i,d})
$$

对正式 US strategy benchmark，使用 `INDNO = 1000500` 的 CRSP Value-Weighted Index of the S&P 500 Universe，字段为 `DlyTotRet`。`INDNO = 1000502` S&P 500 Composite 仅保留为 price-return cross-check：提取数据有 `DlyPrcRet` 而无 `DlyTotRet`，因此不得作为主 total-return performance benchmark。

Fama–French / RF external series 中 `mktrf, smb, hml, rmw, cma, rf, umd` 已是 **decimal returns**，例如 `0.0134 = +1.34%`，不得再除以 100。`RF` 用于 excess return、Sharpe 与 regression；其他 fields 用于 external style / factor regression 与 diagnostic interpretation，而不是本项目 stock-level candidate-factor 的 raw construction input。月度对齐优先使用 `dateff`（月末最后交易日）；原始 FF data 即使延伸至 2026-08，也不得改变 2025-12-31 formal historical cutoff。

### 基础进入条件

当前规则：

- 至少 24 个月价格历史；
- 过去 60 个交易日中，有成交的天数不少于 80%；
- 已公告退市股票不再进入未来股票池；
- CRSP CIZ 的 return / delisting semantics 完成 Gate 0 验证后，才按冻结的 canonical-return rule 处理 delisting；
- 课程要求的必选资产按课程规则处理。

### 主要数据

至少需要：

- PERMNO、GVKEY，及仅供 diagnostics 的 ticker / CUSIP；
- Date；
- Market；
- Historical PIT GICS Sector；
- Currency；
- Adjusted Price / Total Return；
- CRSP DlyCap / ShrOut audit fields；
- Trading Volume / ADV；
- Accounting Variables；
- Quarterly RDQ 与 annual availability provenance；
- formal CRSP benchmark / market proxies；
- Fama–French Risk-Free Rate。

### 目的

建立完整可追踪的数据链：

$$
Data
\rightarrow
Signal
\rightarrow
Factor
\rightarrow
Holding
\rightarrow
Return
$$

### 2026 Recent / Operational Extension 与 CRSP–Yahoo Bridge

2026.01–2026.09 的 `RECENT_EXTENSION` 必须使用在 historical research design 中按 2020 sample cutoff 冻结的策略。旧 Yahoo daily market data 约覆盖至 2026-09-18，但其 current-S&P500 universe 以 2026 extraction date 为基础，可能含 survivorship / reconstitution bias。因此该 extension 不得声称为无偏 formal historical OOS，也不得并入 2021–2025.12 的 formal headline statistics。

在 CRSP annual data 不可用的 2026 bridge 中，优先继续使用可用的 Compustat fundamentals、Historical GICS 与 Fama–French / RF；Yahoo 仅作为 market-price / return bridge，SEC Company Facts 可在适当时作为 supplementary 2026 source，而不是 formal 2006–2025 baseline。

使用 Yahoo bridge 前，必须在 2025 overlap 对 matched securities 比较 CRSP returns 与 Yahoo adjusted returns，至少审计 correlation、mean / median absolute return difference、missing dates、split / corporate-action discrepancies、dividend-related differences、extreme mismatches 及 security-mapping coverage。可将 SPY 与 formal CRSP S&P 500 benchmark 作辅助 diagnostic，但二者不是相同产品。Bridge pass 只验证 bridge，不会把 2026 转化为 Formal Historical OOS。

---

## Step 02 — PIT Alignment 与数据清洗

### Point-in-Time 原则

$t+1$ 周的持仓只能使用：

> $t$ 周最后一个交易日收盘及以前已经公开的信息。

财务数据：

- 对适用的 Compustat quarterly fundamentals，`rdq` 是 primary public-information timing field；任何 `RDQ > signal_date` 的 observation 不得进入该 signal；
- 若只有公布日而没有具体发布时间戳，仍保守地从 **公布日后的下一个本地交易日** 起可用于 signal；
- `capxy` 与 `oancfy` 是 YTD-style quarterly cash-flow fields，构造 single-quarter flow 前必须按冻结规则正确 differencing；不得直接当作单季流量；
- 下载 schema 中没有 quarterly `gpq`，不得静默把 `Revenue - COGS` 作为 universal quarterly gross-profit baseline；任何后续 derived measure 必须另有明确经济定义与批准；
- annual `datadate` 不是 publication date，`datadate <= signal_date` 不足以构成 PIT availability。Formal Gate 0 / canonical PIT design 必须在 corresponding Q4 / earnings-release timing 或保守 lag rule 中定义可辩护的 annual availability rule；在此之前不得假装该 annual timing 已冻结；
- 财务数据只在新报告满足相应 availability rule 后更新。旧 DEV 资料中没有 RDQ 时的处理不得静默移植为 formal WRDS annual-PIT rule。

上述规则处理的是 **publication-date / information-availability alignment**，不自动等同于 historical-vintage / restatement integrity。当前 Compustat extraction 为 ordinary historical data；除非 Gate 0 证实 WRDS source semantics 能按当时 vintage 固定历史 accounting values，否则不得称其为 vintage-perfect PIT snapshot。Gate 0 必须审查 later-restated historical values、revision / restatement semantics 及相应 claim limitation；若不能建立 vintage-perfect property，正式方法应称为 **publication-date-aligned historical fundamentals**，并披露 residual revision / restatement bias。

### 收益与价格

- 正式 CRSP CIZ canonical return construction 必须在 Gate 0 先确认 return、corporate-action 与 delisting semantics；不得盲目套用 legacy：

$$
(1+RET)(1+DLRET)-1
$$

以免重复计入 delisting effects；
- 通过验证后，收益计算使用同一冻结的 Total Return 口径；
- 停牌或缺失日不通过简单前向填充制造虚假收益；
- Signal Date 与 Execution Date 分离。

### Winsorization

每期 × 每市场 × 每指标：

- 1% / 99%。

### Standardization

每期在本市场截面：

$$
Z_{i,t}
=
\frac{X_{i,t}-\bar X_t}{\sigma_t}
$$

US 与 SG 永远分开标准化。

### 数据版本

同时保存：

- Raw；
- Winsorized；
- Z-score；
- Neutralized。

### 目的

确保任何最终信号都可以追踪：

> 原始值 → 清洗值 → 标准化值 → 中性化值。

---

# 6. Part II — Candidate Factor Pool

当前不提前决定“最终六因子”。

正确状态为：

$$
\boxed{
Candidate\ Factor\ Pool
}
$$

---

## Step 03 — Value

组成：

- E/P；
- B/P；
- FCF Yield。

Financials / Real Estate：

- 使用 E/P；
- B/P；
- 不使用 FCF Yield。

方向：

$$
Higher\ Value\ Score
=
More\ Preferred
$$

---

## Step 04 — Quality

Non-Financials：

- ROE；
- GP/A；
- $-$Leverage。

Financials：

- ROE；
- ROA。

方向：

$$
Higher\ Profitability
+
Lower\ Leverage
=
Higher\ Score
$$

---

## Step 05 — Momentum

组成：

- MOM 12-1；
- MOM 6-1。

定义：

- MOM 12-1：过去 252 个交易日累计收益，跳过最近 21 个交易日；
- MOM 6-1：过去 126 个交易日累计收益，跳过最近 21 个交易日。

方向：

$$
Higher\ Momentum
=
Higher\ Score
$$

---

## Step 06 — Low Risk

组成：

- $-$Volatility；
- $-$IVOL。

Volatility：

- 过去 252 个交易日日收益标准差；
- 有效交易日不少于 200。

IVOL：

- 个股日收益对本市场指数日收益回归后的残差标准差。
- Formal US baseline 使用 CRSP `INDNO = 1000080` NYSE / NYSEMKT / Nasdaq Value-Weighted Market Index 的 `DlyTotRet`；其 exact return semantics 仍须先通过 Formal Gate 0。

方向：

$$
Lower\ Risk
=
Higher\ Score
$$

---

## Step 07 — Investment

定义：

$$
AssetGrowth
=
\frac{Latest\ Published\ TotalAssets}
{TotalAssets_{1year\ ago}}
-1
$$

使用：

$$
-AssetGrowth
$$

因此：

$$
Lower\ Asset\ Growth
=
Higher\ Score
$$

---

## Step 08 — Dividend Candidate

候选指标：

- 过去 12 个月 Dividend Yield。

当前状态：

$$
\boxed{
Candidate
}
$$

其最终方向和是否保留，不预先假定，等待：

- Forward IC；
- Single-Factor Portfolio；
- Stability；
- Cross-Factor Redundancy

验证。

---

## Step 09 — Size Candidate

候选指标：

- Market Cap / log Market Cap。

Formal US Size candidate 的 market-cap economic level 必须在 Gate 0 后随正式 Size definition 冻结：`DlyCap` 可作为 security-level raw source，而公司-level Size 需要已验证的 issuer aggregation。不得使用已退休的 Yahoo × SEC estimated market cap。

当前状态：

$$
\boxed{
Candidate
}
$$

必须区分：

> **Size as a Factor**

与：

> **Size as a Neutralization Control**

Size 作为控制变量出现，不代表它自动属于最终 Factor Set。

---

# 7. Part III — Factor Construction 与 Neutralization

## Step 10 — 因子内部合成

若某一 Factor 由多个指标组成，初始 incumbent construction 为：

$$
F^{raw}_{k,i,t}
=
Average(Z_{1,i,t},Z_{2,i,t},...)
$$

历史上 initial incumbent family-level validation 所使用的第一版一律为：

$$
\boxed{
Equal\ Weight\ Within\ Factor
}
$$

上述 equal-weight composites 仍是有效且保留的 **INCUMBENT BASELINE SPECIFICATIONS**；既有 validation 不被重写，也不得追溯声称 Gate A0 已经存在。

在 Gate A freeze 前，Gate A0 可在已冻结、有限且离散的 within-family candidate specification pool 中选择一个 representative specification。Gate A0 不允许连续内部权重优化、任意 component-weight grid、machine-learning combination 或以 Sharpe 最大化内部权重。Gate A0 的 pool 与 validation contract 见下文的 **Formal Gate A0 Within-Family Specification Governance Freeze — 2026-10-11**。

当前 availability 原则：

- 3 个指标组成的 Factor 至少 2 项可用；
- 2 个指标组成的 Factor 至少 1 项可用；
- 否则该股票该因子记为缺失。

Value 和 Quality 若按行业分支计算，分支合成后重新进行本市场 Z-score。

### 目的

减少最底层自由参数，避免过拟合。

---

## Step 11 — Size / Industry Neutralization

当前明确：

### Value

- Size Neutral；
- Industry Neutral。

### Quality

- Size Neutral；
- Industry Neutral。

### Investment

- Size Neutral；
- Industry Neutral。

统一截面回归：

$$
F^{raw}_{i,t}
=
\beta_t\log(MCap^{neutralization}_{i,t})
+
\sum_{s=1}^{S_t}\gamma_{s,t}D_{i,s,t}
+
\epsilon_{i,t}
$$

此 canonical regression **不含独立 intercept**，并纳入全部 mutually exclusive industry dummies；不得依赖 library default 的 dummy dropping / collinearity handling。它在 fitted-space 上等价于 intercept 加 $S-1$ 个 dummy，但避免由软件任意省略行业。

最终：

$$
F^{final}_{i,t}
=
Z(\epsilon_{i,t})
$$

正式 US 的 market-cap control 必须从通过 Gate 0 的 CRSP `DlyCap` 及已冻结的 security- / issuer-level mapping 取得；industry dummy 必须来自 signal-date-valid Historical GICS。Value denominator、Size factor 和 size neutralization 不得静默假定为同一 market-cap economic level；各自定义必须记录。不得以旧 Yahoo/SEC market cap 或 current-GICS history backfill 替代。

### Momentum

- Size Neutral：Test；
- Industry Neutral：Test，初始倾向不做。

### Low Risk

- Size Neutral：Test；
- Industry Neutral：Test。

原因：

> Size / Industry Exposure 可能本身就是 Momentum 或 Low-Risk Anomaly 的组成部分，不能机械删除。

### Dividend

其 Neutralization Rule 在 Factor Selection 阶段确定。

### Size

不能进行 Size Neutralization；是否 Industry Neutral 另行验证。

### 中性化检查

- Final Factor 与 log Market Cap 相关接近 0；
- Industry Mean 接近 0；
- Industry Dummy Mapping 正确。

---

## Formal US Candidate-Factor Definition Freeze — 2026-10-11

> **Historical record.** At commit `cd0151bd1df80b1834ce4cfe04b7e967adc6cbde`, Candidate Factor Construction was correctly marked **BLOCKED** because the definitions below had not yet been frozen.  This section records the subsequent user-approved research-design freeze.  No IC, return, Sharpe, alpha, portfolio, or backtest evidence was consulted to make these decisions.

These rules apply to the Formal US candidate-factor construction only. They do not select a Gate A0 family representative or a Final Factor Set, do not determine whether a candidate survives Gate A, and do not alter the already frozen Momentum or Low Risk definitions.

### 1. Size

The Formal US Size candidate is issuer-level:

$$
SIZE_{raw}=-\log(IssuerMarketCap)
$$

`IssuerMarketCap` is the positive, explicitly available canonical CRSP issuer-level market capitalization in USD thousands.  Security-level `DlyCap`, a partial issuer denominator, and the retired Yahoo × SEC estimate are prohibited substitutions.  The score is winsorized at same-date 1/99, same-date z-scored, and is not size-neutralized.  Higher score means a smaller issuer.  Issuer market capitalization remains economically distinct from the derived Size score.

### 2. Fundamental source precedence and provenance

For every applicable metric, select information solely by PIT availability on or before the signal date and retain `source_frequency`, `source_datadate`, `source_availability_date`, and `fallback_used`.

- **Flows:** use the latest valid quarterly-derived TTM first; otherwise use the latest valid annual measure.  A logical flow metric never mixes annual and quarterly components.
- **Stocks:** use the latest valid quarterly point-in-time value first; otherwise use the latest valid annual point-in-time value.  A logical stock metric never uses future quarterly information.

The precedence is definitional, not a coverage or performance optimisation.

### 3. Value

Historical GICS Sector 40 (Financials) and 60 (Real Estate) use E/P and B/P only.  Other sectors use E/P, B/P, and FCF Yield.  Missing historical GICS makes a branch-dependent Value score unavailable; current-GICS backfill is prohibited.

- **E/P:** primary numerator is TTM NIQ from four consecutive PIT-valid quarterly observations; annual NI is the whole-metric fallback.  `EP_RAW = NI_USD_millions × 1000 / IssuerMarketCap_USD_thousands`.  Negative earnings are retained.
- **B/P:** primary numerator is latest PIT-valid CEQQ; annual CEQ is the whole-metric fallback.  `BP_RAW = CEQ_USD_millions × 1000 / IssuerMarketCap_USD_thousands`.  Negative common equity is retained unless an existing canonical rule makes it invalid.
- **FCF Yield:** primary numerator is quarterly-derived TTM OANCFY minus quarterly-derived TTM CAPXY, each requiring four valid consecutive canonical single-quarter flows; annual OANCF minus CAPX is the whole-metric fallback.  `FCFYIELD_RAW = FCF_USD_millions × 1000 / IssuerMarketCap_USD_thousands`.  Negative FCF is retained.

Each component is same-date 1/99 winsorized and z-scored.  Nonfinancial firms require at least two of three components; Financials/Real Estate require at least one of two.  Average only available component z-scores, then re-zscore the valid branch composite before neutralization.

### 4. Quality

The historical-GICS branch is mandatory: sectors 40/60 use ROE and ROA; all other sectors use ROE, GP/A, and negative Leverage.  A missing historical sector makes Quality unavailable.

- **ROE:** quarterly primary is TTM NIQ divided by the latest PIT-valid CEQQ at that TTM endpoint; annual fallback is NI / CEQ from the same accepted annual fiscal observation.  A zero denominator is unavailable; negative equity is not absolute-valued.
- **ROA:** quarterly primary is TTM NIQ divided by latest PIT-valid ATQ; annual fallback is annual NI / annual AT from the same accepted annual fiscal observation.  A zero denominator is unavailable.
- **GP/A:** Gross Profit is the exact approved canonical Sales/Revenue less COGS field construction.  Quarterly primary is TTM Gross Profit / latest PIT-valid ATQ; annual fallback is annual Gross Profit / annual AT.  Missing COGS is never filled with zero.
- **Leverage:** the nonfinancial metric is `(Short-Term Debt + Long-Term Debt) / Total Assets`, using the exact approved canonical mappings (`DLCQ + DLTTQ`) / `ATQ` quarterly and (`DLC + DLTT`) / `AT` annually when those are the approved field names.  Required missing debt components make Leverage unavailable.  Quality uses `-Leverage`.

No Quality metric mixes annual with quarterly numerator or denominator.  Components are same-date 1/99 winsorized and z-scored.  Nonfinancial firms require at least two of three components; Financials/Real Estate require at least one of two.  Average available components and re-zscore the valid branch composite before neutralization.

### 5. Investment

The Formal Investment candidate is negative asset growth.  Quarterly primary uses the latest PIT-valid ATQ and the same fiscal quarter in the immediately preceding fiscal year:

$$
AssetGrowth_Q=ATQ_{t}/ATQ_{same\ fiscal\ quarter,\ t-1}-1.
$$

If that exact same-fiscal-quarter pair is unavailable, annual fallback uses the latest PIT-valid AT and the immediately prior fiscal-year AT.  Nearest-calendar-date substitution and annual/quarterly mixing are prohibited.  `INVESTMENT_RAW = -AssetGrowth`, followed by same-date 1/99 winsorization, same-date z-score, and mandatory neutralization.

### 6. Mandatory neutralization

Only Value, Quality, and Investment are neutralized.  On each signal date, use valid observations only and fit:

$$
F^{pre}_{i,t}=\beta_t\log(IssuerMarketCap_{i,t})+\sum_s\gamma_{s,t}SectorDummy_{i,s,t}+\epsilon_{i,t}.
$$

The controls are canonical issuer-level `log(IssuerMarketCap)` and all mutually exclusive **historical GICS Sector** dummies valid on the signal date.  The regression has no separate intercept, drops no sector dummy, and cannot use security-level `DlyCap` or current GICS.  The final score is the same-date z-score of the residual.  Missing/nonpositive issuer market capitalization or missing historical sector makes the neutralized factor unavailable.  Momentum, Low Risk, Dividend, and Size have no baseline neutralization; Size must not be size-neutralized.

### 7. Dividend

The Dividend candidate is security-level trailing-12-month ordinary cash-dividend yield.  It uses only ordinary CRSP distribution events already accepted by Phase 2B; nonordinary distributions, return-of-capital events, stock distributions, split/share-adjustment events, and other non-cash claims are excluded.  The audited CRSP ex-date field is the economic event date, and an event is eligible only when `ex_date <= signal_date` and `ex_date > signal_date - 365 calendar days`.

Historical cash dividends must first be deterministically adjusted to the signal-date share basis using audited CRSP split semantics.  If Stage B cannot support that exact adjustment, Dividend remains blocked as `DIVIDEND_SPLIT_ADJUSTMENT_SEMANTICS_UNRESOLVED`; historical per-share amounts must not be summed across split events without that evidence.  With a valid normalized numerator:

$$
DIVYIELD_{raw}=TTM\ Ordinary\ Cash\ Dividend\ Per\ Share / SignalDate\ SecurityPrice.
$$

The security-level canonical signal-date price must be positive and finite.  A zero is permitted only when the full trailing event window is demonstrably observable and contains no eligible ordinary cash-dividend event; otherwise the metric is unavailable.  Dividend is same-date 1/99 winsorized and z-scored, with no baseline neutralization.  Dividend events are factor inputs only and never alter total-return accounting.

---

# 8. Part IV — Single-Factor Validation 与 Final Factor Set

## Formal Gate A0 Within-Family Specification Governance Freeze — 2026-10-11

> **Historical and adaptive-design disclosure.** The initial seven-family validation and the Gate-A human review package at commit `d19485a58ae5fc60520c167e13e11a458cf9c596` evaluated the incumbent, equal-weight family composites. That work remains valid under the framework then in force and is preserved as **INITIAL INCUMBENT FAMILY-LEVEL GATE-A EVIDENCE**. It is not retrospectively relabelled as within-family testing.
>
> After reviewing incumbent family-level 2011–2015 evidence, the group introduced Gate A0 because weak incumbent-composite evidence cannot distinguish a genuinely weak economic family from dilution by equal-weight aggregation of heterogeneous constituent signals. Gate A0 is therefore an **adaptive research refinement** within the designated 2011–2015 Factor Selection sample. It was not fully pre-specified before all factor-performance evidence. This disclosure does not open later periods: 2016–2020, 2021–2025, and 2026 outcome evidence remain inaccessible for Gate A0 selection.

### A0.1 Research sequence and distinct decisions

The frozen selection sequence is:

$$
Raw\ /\ Constituent\ Metrics
\rightarrow
Within\text{-}Family\ Candidate\ Specifications
\rightarrow
Gate\ A0
\rightarrow
One\ Frozen\ Representative\ per\ Economic\ Family
\rightarrow
Gate\ A
\rightarrow
Final\ Factor\ Set.
$$

Gate A0 asks **“How should this economic style be measured?”** Gate A asks **“Should this economic style enter the final multi-factor strategy?”** These questions must not be merged. Gate A0 is a sub-gate within the 2011–2015 Factor Selection stage and creates no new time sample.

### A0.2 Frozen Round-1 scope and family categories

The authoritative row-level contract is `outputs/audit/wrds_formal/gate_a0_within_family_specification_registry.csv`.

- **Multi-specification families:** Value, Quality, Momentum, and Low Risk enter formal Gate A0 comparison.
- **Single-specification families:** Investment, Dividend, and Size have `GATE_A0_STATUS = SINGLE_SPECIFICATION_NO_INTERNAL_SELECTION`. Their existing frozen specification proceeds as the family representative to later Gate A, subject to normal Gate-A review.
- **Component diagnostics:** domain-specific metrics may diagnose an incumbent family but are not eligible whole-family representatives and cannot enter cross-family Gate A separately in this round.

This is the **EXISTING-CONSTITUENT ROUND** only. No literature-motivated challenger is authorized in this round. In particular, do not add RMW / Operating Profitability, Cash Profitability / RMWc, Trend Momentum, Low Beta / BAB, Shareholder Yield / Net Payout Yield, EV-based Value, or an unlisted alternative. A future Round 2 requires a separate governance decision after Round-1 review.

### A0.3 Value specification pool

| Specification | Role | Eligible representative | Frozen definition |
|---|---|---:|---|
| `VALUE_CURRENT` | INCUMBENT | TRUE | Current branch-aware Value: Nonfinancial E/P, B/P, FCF Yield; Financials / Real Estate E/P, B/P; equal weight over valid standardized components under existing availability rules. |
| `VALUE_EP` | CHALLENGER | TRUE | Frozen E/P only. |
| `VALUE_BP` | CHALLENGER | TRUE | Frozen B/P only. |
| `VALUE_EP_BP` | CHALLENGER | TRUE | Equal-weight standardized E/P and B/P in every sector where both existing definitions are valid. |
| `VALUE_FCF_COMPONENT` | COMPONENT_DIAGNOSTIC_ONLY | FALSE | Frozen FCF Yield, only in its existing nonfinancial / non-Real-Estate natural domain. |

Every eligible Value representative inherits the exact existing PIT source precedence, issuer-level denominator, direction, same-date winsorization, z-scoring, availability semantics, historical-GICS Sector plus log issuer-market-cap neutralization, and final residual z-score. Financials / Real Estate are not redefined merely to make FCF Yield universal. `VALUE_FCF_COMPONENT` may diagnose whether FCF Yield strengthens or dilutes `VALUE_CURRENT`; it cannot become the whole-market representative automatically.

### A0.4 Quality specification pool

| Specification | Role | Eligible representative | Frozen definition |
|---|---|---:|---|
| `QUALITY_CURRENT` | INCUMBENT | TRUE | Current branch-aware Quality: Nonfinancial ROE, GP/A, -Leverage; Financials / Real Estate ROE, ROA. |
| `QUALITY_ROE` | CHALLENGER | TRUE | Frozen ROE only, the common cross-sector standalone Quality specification. |
| `QUALITY_PROFITABILITY` | CHALLENGER | TRUE | Nonfinancial equal-weight standardized ROE and GP/A; Financials / Real Estate equal-weight standardized ROE and ROA; re-zscore the valid branch composite. |
| `QUALITY_GPA_COMPONENT` | COMPONENT_DIAGNOSTIC_ONLY | FALSE | Frozen GP/A in the nonfinancial branch only. |
| `QUALITY_NEG_LEVERAGE_COMPONENT` | COMPONENT_DIAGNOSTIC_ONLY | FALSE | Frozen -Leverage in the nonfinancial branch only. |
| `QUALITY_ROA_COMPONENT` | COMPONENT_DIAGNOSTIC_ONLY | FALSE | Frozen ROA in the Financials / Real Estate branch only. |

No artificial universal GP/A or leverage signal may be created. Every eligible Quality representative inherits the exact existing branch, PIT, availability, direction, same-date winsorization and z-scoring rules; it then uses the same mandatory historical-GICS Sector plus log issuer-market-cap neutralization and final residual z-score as `QUALITY_CURRENT`.

### A0.5 Momentum specification pool

| Specification | Role | Eligible representative | Frozen definition |
|---|---|---:|---|
| `MOMENTUM_CURRENT` | INCUMBENT | TRUE | Existing equal-weight standardized MOM12-1 and MOM6-1 composite. |
| `MOMENTUM_12_1` | CHALLENGER | TRUE | Existing frozen MOM12-1 only. |
| `MOMENTUM_6_1` | CHALLENGER | TRUE | Existing frozen MOM6-1 only. |

All Momentum candidates retain existing direction, price-history and minimum-history rules, winsorization and z-scoring, with **no baseline size or sector neutralization**. Trend Momentum is a prohibited new literature challenger in Round 1.

### A0.6 Low Risk specification pool

| Specification | Role | Eligible representative | Frozen definition |
|---|---|---:|---|
| `LOWRISK_CURRENT` | INCUMBENT | TRUE | Existing equal-weight standardized -Volatility252 and -IVOL252 composite. |
| `LOWRISK_VOL` | CHALLENGER | TRUE | Existing frozen -Volatility252 only. |
| `LOWRISK_IVOL` | CHALLENGER | TRUE | Existing frozen -IVOL252 only. |

All Low Risk candidates retain existing 252-day definition, minimum-history, market-proxy, direction, winsorization and standardization rules, with **no baseline size or sector neutralization**. Low Beta / BAB is a prohibited new literature challenger in Round 1.

### A0.7 Single-specification families

`INVESTMENT_CURRENT = -Asset Growth`, `DIVIDEND_CURRENT = trailing-12-month ordinary cash-dividend yield`, and `SIZE_CURRENT = -log(IssuerMarketCap)` are frozen as single-specification families. Do not invent alternative Investment, Dividend, or Size definitions in this round, including shareholder yield, net payout yield, residual size, industry-relative size, or CMA alternatives.

### A0.8 Score-construction inheritance

Gate A0 does not recompute accounting concepts, alter timing, change issuer/security economic levels, alter winsorization, or flip direction. It uses the same source data and PIT rules as the corresponding incumbent family. Value and Quality eligible representatives retain mandatory historical-GICS Sector plus log issuer-market-cap neutralization. Momentum and Low Risk retain their incumbent baseline of no mandatory neutralization. Component diagnostics retain their natural sector domain.

### A0.9 Future validation and common-within-family comparison contract

When separately authorized, every eligible representative specification receives the identical 2011–2015-only validation framework: economic rationale, coverage, executable 1W and 4W forward IC, ICIR, positive IC ratio, HAC / Newey-West and 4W block-bootstrap inference, yearly and rolling IC stability, Top/Bottom 30% single-factor portfolio evidence, gross Sharpe, turnover, break-even cost, yearly portfolio stability, applicable sector breadth, common-within-family robustness, and same-family correlation / redundancy diagnostics. It must use the existing $S_t \rightarrow E_t \rightarrow E_{t+1}$ convention; no second validation methodology is permitted.

Each multi-specification family must have both (i) its natural factor-specific **PRIMARY** cross-section and (ii) the diagnostic **COMMON_WITHIN_FAMILY_SAMPLE**, where all eligible representatives of that family are simultaneously available. The common sample cannot silently replace the primary view.

### A0.10 Human decision, incumbent default, and one-representative rule

Gate A0 must assess integrity, economic interpretation, forward IC, stability, single-factor separation, portfolio stability, turnover / break-even economics, coverage, common-sample robustness, same-family redundancy, simplicity / interpretability, and multiple-testing evidence. There is no fixed mechanical IC or Sharpe threshold, weighted composite score, automated rank, or machine-selected winner.

Because Gate A0 was introduced after incumbent evidence was reviewed, the conservative default is: **retain the INCUMBENT specification unless challenger evidence provides a clear, economically coherent, documented reason to replace it.** A higher historical point estimate alone is insufficient; no arbitrary numerical “clear superiority” threshold is allowed.

At Gate A0 completion, each economic family has **at most one** formal representative entering Gate A. Component diagnostics never become separate cross-family factors in this round.

### A0.11 Multiple testing and performance firewall

Gate A0 must later record the number of tested eligible representatives, number of component diagnostics, number of IC horizons, raw p-values, Benjamini–Hochberg FDR-adjusted q-values, and family identifier. Within-family specification testing must be distinguished from later cross-family Gate A testing. The smallest p-value must not select a specification mechanically.

Gate A0 formal performance evidence is 2011–2015 only; 2006–2010 is DEV context only if required. Do not access 2016–2020, 2021–2025, or 2026 outcome evidence for Gate A0 factor selection. This section authorizes only the governance freeze; it does not authorize factor-score construction, IC, returns, portfolios, or representative selection.

---

## Formal Gate A0 Family-Representative Freeze — 2026-10-12

> **GATE A0 STATUS: FROZEN.** The approved human Gate A0 review was recorded after the within-family validation lineage at commit `f18e4c2c491c355bb7f05e9c61fdc0eac3414598`. It is a human decision, not an automated ranking or mechanical threshold outcome. Gate A remains TBD.

The single frozen representative for each economic family is:

| Family | Frozen representative | Gate A0 treatment |
|---|---|---|
| Value | `VALUE_CURRENT` | Retain incumbent |
| Quality | `QUALITY_CURRENT` | Retain incumbent |
| Momentum | `MOMENTUM_12_1` | Replace `MOMENTUM_CURRENT` |
| Low Risk | `LOWRISK_CURRENT` | Retain incumbent |
| Investment | `INVESTMENT_CURRENT` | Single-specification passthrough |
| Dividend | `DIVIDEND_CURRENT` | Single-specification passthrough |
| Size | `SIZE_CURRENT` | Single-specification passthrough |

Exactly one representative per family enters the later Gate A comparison. Component diagnostics, including `VALUE_FCF_COMPONENT` and `QUALITY_GPA_COMPONENT`, are not promoted to whole-market representatives. **ROUND 2 — NOT AUTHORIZED**: no RMW, RMWc, Trend Momentum, Low Beta / BAB, Shareholder Yield, Net Payout Yield, EV-based Value, or other new factor definition may enter the Formal Base Gate A process.

Gate A0 remains an adaptive refinement within the observed 2011–2015 Factor Selection evidence; that period is not labelled untouched for this design question. No 2016–2020, 2021–2025, or 2026 performance was accessed for this human decision. Gate A, Gate B, FMOM configuration, and the Final Factor Set remain unmade.

---

## Step 12 — Gate A0 Within-Family 与 Gate A Cross-Family Forward IC

只使用 Factor Selection Period。Gate A0 在另行授权后先按同一 executable validation engine 比较同一经济家族的已冻结 eligible representative specifications；Gate A 只比较每个家族经 Gate A0 冻结的一项 representative specification。

每周：

$$
IC_{k,t}
=
Spearman
\left(
F_{k,i,S_t},
R^{local}_{i,E_t\rightarrow E_{t+1}}
\right)
$$

Baseline Forward IC 的 factor score 必须在 Signal Cutoff $S_t$ 已知；被评价的本币 return 只从 next-trading-day execution close $E_t$ 开始。Future 4-Week IC 使用同一 executable 起点：

$$
Spearman
\left(
F_{k,i,S_t},
R^{local}_{i,E_t\rightarrow E_{t+4}}
\right)
$$

若另行报告 $S_t\rightarrow S_{t+1}$ signal-close-to-signal-close predictive IC，必须明确标记为 **non-executable predictive diagnostic**，与上述 baseline executable IC 分开，不得混合。

由于 Future 4-Week Return 存在重叠，正式 inference 必须使用能够处理 serial correlation 的 HAC / block-bootstrap；若按周构造 4-week overlapping return，HAC 至少覆盖由重叠产生的前三个周滞后，最终 maxlag rule 在正式 OOS inference 前统一冻结。

每个 Candidate Factor 输出：

- Mean IC；
- Standard Deviation；
- ICIR；
- Positive IC Ratio；
- Rolling IC；
- Sector IC；
- HAC / Newey-West t-stat。

### Direction Rule

如果理论方向和 IC 符号冲突：

1. 检查数据；
2. 检查公式；
3. 检查 timing；
4. 检查经济定义；
5. 不机械翻转方向追求更好结果。

确认后固定 Direction。

---

## Step 13 — Single-Factor Portfolio Backtest

每周按单因子 Final Score 排序，并严格使用 4.3 的统一 execution timing。

US Factor Portfolio：

- Top 30%：Long；
- Bottom 30%：Short；
- 组内 Equal Weight；
- $S_t$ 形成 target，$E_t$ 执行，持有到 $E_{t+1}$。

$$
R^{factor}_{k,t}
=
R^{Top}_{k,E_t\rightarrow E_{t+1}}
-
R^{Bottom}_{k,E_t\rightarrow E_{t+1}}
$$

该组合用于：

1. 验证单因子是否具有组合层面的经济价值；
2. 为后续 FMOM 提供 **executable gross Factor Return Series**。

它**不是最终实际持仓**，因此 Base 研究不把 long–short portfolio 的“净 Sharpe”当作筛选条件，避免在没有 short borrow / financing contract 的情况下制造伪精确 after-cost 结果。

同时记录：

- Long Leg Gross Return；
- Long Leg 相对当期 Eligible-Universe Equal-Weight 的 Gross Excess Return；
- Illustrative Long-Leg Net Excess Return / Net Excess Sharpe：仅在 pre-specified research-cost scenario 下（如使用）；transaction-cost freeze 前均为 provisional；
- Short Leg Gross Return；
- Long–Short Gross Return；
- Gross Volatility / Gross Sharpe；
- Max Drawdown；
- Turnover；
- Subperiod Stability。

Gate A 的 required evidence 仍是 gross evidence、turnover、break-even cost 与 cost sensitivity。未在 Gate A 前冻结相关成本假设时，上述 illustrative Long-Leg net result 不得作为 hard Keep / Drop criterion。

若未来要报告 Long–Short Net Sharpe，必须先单独冻结 short borrow cost、financing 与 short-side execution cost；在此之前不进入 Gate A 的正式 Decision Card。

---

## Step 14 — Redundancy & Complementarity

分析 Candidate Factors 之间：

- Score Correlation；
- IC Correlation；
- Factor Return Correlation；
- Holdings Overlap；
- Subperiod Co-Movement。

若某一对因子长期高度相关，例如：

$$
Corr>0.6
$$

则重点检查是否只需要保留一个。

### 目的

不是找“单因子 Sharpe 最高的若干个”，而是寻找：

$$
\boxed{
Effective
+
Stable
+
Complementary
}
$$

的 Factor Set。

---

## Step 15 — Gate A Cross-Family Final Factor Set Selection

Gate A0 先决定每个 multi-specification economic family 的 measurement specification。随后 Gate A 对最多七项 family representatives 同时从四层判断：

### A. Economic Rationale

经济含义是否合理？

### B. Forward Predictive Evidence

- IC；
- ICIR；
- Direction Stability；
- HAC significance。

### C. Tradable Single-Factor Portfolio

- Return；
- Sharpe；
- Drawdown；
- Turnover；
- Stability。

### D. Complementarity

是否提供已有因子没有的信息？

最终得到：

$$
\boxed{
\mathcal F^{Final}_{US}
}
$$

以及：

$$
K=
|\mathcal F^{Final}_{US}|
$$

到这一步才知道最终到底是 5、6、7 或其他数量的 Factor Strategy。

### Gate A — Final Factor Set Freeze

冻结：

- Final Factor Set；
- 每个已由 Gate A0 冻结的 family-representative Formula；
- Factor Direction；
- Neutralization Rule。

后续不得因为 US OOS 或 SG 结果不好而回头重选 Base Factors。

---

# 9. Part V — Factor Momentum Configuration

## Step 16 — Factor Return History

从 Warm-up Period 开始连续保存每个最终因子的周度 Long–Short Return：

$$
R^{factor}_{k,t}
$$

使进入 Configuration Selection Period 时已有足够历史。

---

## Step 17 — FMOM Construction

基准：

$$
FMOM_{k,t}
=
\sum_{j=0}^{25}R^{factor}_{k,t-j}
$$

Baseline FMOM 使用 **Gross Long–Short Factor Return** 作为 timing signal。因为该 factor-mimicking portfolio 不是最终真实交易组合，不在 baseline FMOM signal 中扣除假设性的 long-short implementation cost；cost-adjusted factor momentum 只能作为预先登记的 robustness。

只使用 4.3 定义下截至当前 Signal Cutoff 已经 **完整结束** 的 executable Factor Returns；不能使用尚未到下一 execution date 的在途 factor return，也不能使用任何未来收益。

### FMOM Availability Rule

- 普通 FMOM 必须拥有完整 $J$ 个有效周度 factor returns；
- Risk-Adjusted FMOM 若启用，必须拥有完整 104 个有效周度 factor returns；
- 不允许把缺失 factor return 自动填成 0；
- 如果任一 Final Factor 在当期缺少所需历史，**整个 Dynamic component 当期退回 Equal Weight**；
- fallback 原因必须保存在 `factor_weights_weekly.csv`。

候选 Risk-Adjusted FMOM：

$$
FMOM^{RA}_{k,t}
=
\frac{FMOM_{k,t}}
{\sigma(R^{factor}_{k,t-103:t})}
$$

是否启用只在 Configuration Selection 阶段决定。

---

## Step 18 — Dynamic Factor Weight

$$
S_{k,t}
=
\max(FMOM_{k,t},0)
$$

$$
w^{FMOM}_{k,t}
=
\frac{S_{k,t}}
{\sum_jS_{j,t}}
$$

若所有 $S_{k,t}=0$：

$$
w^{FMOM}_{k,t}
=
\frac1K
$$

不因为负 Factor Momentum 对某 Factor 做空。

---

## Step 19 — Static + Dynamic Blend

$$
w_{k,t}
=
(1-\lambda)\frac1K
+
\lambda w^{FMOM}_{k,t}
$$

只比较：

$$
\lambda=0,\ 0.5,\ 1
$$

并比较：

- FMOM lookback $J=13/26/52$ weeks；
- Risk Adjustment On / Off。

不做密集参数搜索。

### 核心问题

不是：

> 哪个参数历史 Sharpe 最高？

而是：

> Factor Momentum Tilt 是否在合理参数邻域内持续提供价值？

### Gate B — Factor Timing Configuration Freeze

冻结：

- $J$；
- Risk Adjustment；
- $\lambda$。

---

# 10. Part VI — US Base Strategy Construction

## Step 20 — Composite Stock Score

每周：

$$
Score_{i,t}
=
\sum_{k=1}^{K}
w_{k,t}F_{k,i,t}
$$

### Missing Factor

最终 Factor Set 确定以后，需要正式冻结：

- 股票最低需要多少个 Factor；
- 缺失 Factor Weight 如何重新归一化。

当前“至少 4 个 Factor”只作为 $K=6$ 时的初步方案。

若允许部分 Factor 缺失并对剩余 Factor 重新归一化，每期必须额外保存：

- `effective_factor_count` 分布；
- incomplete-factor stocks 占 eligible universe / holdings 的比例；
- 因缺失而重新分配的总 Factor Weight；
- incomplete-factor holdings 对组合收益的贡献。

这样可以检查 Missingness 是否事实上改变了某些股票的策略经济含义。

同时做 robustness：

- Complete-Factor Sample；
- Minimum-Available-Factor Sample。

### 目的

判断 Missingness 是否系统性影响最终组合。

---

## Step 21 — US Stock Selection

当前基准：

- Composite Score Top 20%；
- Long-Only；
- Equal Weight；
- Single Sector Weight ≤ 30%。

### Turnover Buffer

候选：

- New Buy：进入 Top 20%；
- Existing Holding：跌出 Top 30% 才卖出。

是否采用在 Configuration Selection 阶段冻结。

---

## Step 22 — Weekly Execution

每周顺序：

1. 周最后一个交易日收盘；
2. 更新当时可获得的 Universe；
3. 更新 PIT Fundamental Data；
4. 清洗并计算 Factor Scores；
5. 更新已实现的 Factor Return History；
6. 计算 FMOM；
7. 计算 Factor Weights；
8. 计算 Stock Composite Score；
9. 生成 Target Holdings；
10. 下一交易日收盘成交；
11. 记录 Turnover 与 Cost；
12. 持有至下一次调仓。

核心：

$$
\boxed{
Signal\ Close_t
<
Execution\ Close_{next}
}
$$

回测和实盘使用同一成交假设。

---

# 11. Part VII — US Strategy Evaluation

## Step 23 — Core Benchmarks

至少保留：

### Market Benchmark

- Formal US baseline 使用 CRSP `INDNO = 1000500` Value-Weighted Index of the S&P 500 Universe 的 `DlyTotRet`，经 Gate 0 验证后作为 S&P 500 total-return benchmark；
- `INDNO = 1000502` 仅为 `DlyPrcRet` price-return cross-check，不替代主 total-return benchmark。

### Eligible-Universe Equal Weight

对当期 eligible US universe 等权持有，不使用任何 Factor Score，用于区分：

$$
Universe\ Effect
\quad vs\quad
Factor\ Selection\ Effect
$$

### Static Multi-Factor

$$
\lambda=0
$$

### Dynamic Factor-Momentum Strategy

$$
\lambda=0.5
$$

除 Factor Weighting 外：

- Universe；
- Factor Set；
- Stock Selection；
- Rebalance；
- Costs；
- Execution；

必须完全相同。

因此：

$$
\boxed{
Dynamic
-
Static
}
$$

是 Factor Timing Incremental Value 的最核心对照。

---

## Step 24 — US Performance Profile

分析：

- Gross Return；
- Net Return；
- Annualized Return；
- Volatility；
- Sharpe；
- Sortino；
- Max Drawdown；
- Calmar；
- Hit Rate；
- Rolling Return；
- Rolling Sharpe；
- Benchmark-relative Performance。

### 目的

回答：

> Base Strategy 在美国市场是否真正有效？

---

## Step 25 — US Performance Stability

分析：

- Year-by-Year Return；
- Rolling 12m / 24m / 36m；
- Bull / Bear；
- High / Low Volatility；
- Crisis；
- Recovery；
- Major Drawdowns。

### 目的

判断策略是否只依赖少数年份。

---

## Step 26 — US Factor-Timing Attribution

核心比较：

$$
\lambda=0
\quad vs\quad
\lambda=0.5
$$

进一步分析：

- 哪些时期动态权重增加收益；
- 哪些时期动态权重损失收益；
- 哪些 Factors 被频繁 overweight；
- Factor Momentum 是否只是重复股票 Momentum；
- Dynamic Weighting 是否增加 turnover；
- Gross Timing Gain 是否能覆盖额外交易成本。

### 目的

真正回答：

$$
\boxed{
Why\ does\ Factor\ Momentum\ help?
}
$$

---

## Step 27 — US Strategy-Level Factor Regression / Risk / Holdings

这一部分必须把“策略赚了多少钱”与“这些收益能被什么系统性暴露解释”分开。

### 27.1 Formal Strategy-Level Factor Regression

基础形式：

$$
R^{US}_{p,t}-R_{f,t}
=
\alpha_{US}
+
\beta_{MKT}MKT_t
+
\beta'F_t
+
\epsilon_t
$$

其中 $F_t$ 为最终可获得且在报告前冻结的 style-factor set。优先考虑可解释 Size、Value、Profitability/Quality、Investment、Momentum 等暴露的标准因子；若最终使用的外部 factor data 不完整，必须明确区分“外部 benchmark factor”与“项目内部构造的 factor-mimicking return”。

至少输出：

- Annualized Alpha；
- Alpha HAC / Newey-West t-stat；
- Alpha p-value / confidence interval；
- Market Beta；
- Style Betas；
- $R^2$ / Adjusted $R^2$；
- Residual Volatility。

**统计原则：** 周度策略收益可能存在 serial correlation，因此正式 inference 不使用简单 IID standard error，优先使用 HAC / Newey-West；若做 Dynamic vs Static 的差异检验，可进一步使用 paired block bootstrap / time-series-consistent inference。

**Primary Comparable Regression：** 除 richer factor regression 外，US 必须额外运行与 SG 完全同口径的 USD Market-Only model：

$$
R^{US,USD}_{p,t}-RF^{USD}_t
=
\alpha^{Comp}_{US}
+
\beta^{Comp}_{US}(R^{SP500,TR}_t-RF^{USD}_t)
+
\epsilon_t
$$

它与 SG 对应的 STI-USD model 才是 Cross-Market Table 中可直接比较的 Alpha / Beta。

**解释原则：**

$$
Raw\ Return
\neq
Factor\text{-}Adjusted\ Alpha
$$

Factor Exposure 用于解释，不自动等同于 causal return source。

### 27.2 Holdings / Sector / Concentration

分析：

- Market Beta；
- Size / Value / Quality / Momentum / Low-Risk 等 style exposure；
- Sector Weights；
- Sector Contribution；
- Holdings Count；
- Top-5 / Top-10；
- HHI；
- Market-Cap Distribution。

### 27.3 US Return Source Map：使用分层 Attribution Tree

US 必须形成多视角收益解释，但不同视角不能横向相加。

#### A. Strategy-Rule Increment

$$
R^{FMOM\ Increment}_t
=
R^{Dynamic}_t-R^{Static}_t
$$

用于衡量 Factor Momentum rule 的增量。

#### B. Holdings Contribution

$$
R_t^{Gross}
=
\sum_iw_{i,t}R_{i,t}
$$

由 Stock Contribution 聚合到 Sector Contribution。

#### C. Cost Accounting

US cost accounting uses the exact NAV identity in Section 4.4:

$$
1+R_t^{Net}=(1-TC_t)(1+R_t^{hold})
$$

The cost view may show a clearly labeled first-order cost drag for interpretation, but that shorthand is not the reconciliation identity.

#### D. Factor Regression

用于解释 exposure / alpha，不进入 holdings contribution identity。

必须明确区分：Regression Alpha、Factor Exposure、Stock / Sector Contribution、Dynamic-minus-Static Timing Increment。

### 目的

判断收益究竟来自：

1. 原始股票因子 selection；
2. Factor Momentum Timing；
3. Systematic style exposure；
4. Sector / Stock concentration；
5. 以及成本前后的差异。

---

## Step 28 — US Turnover / Cost / Liquidity / Capacity

Reporting Turnover：

$$
Turnover_t=0.5\sum_i|w^{target}_{i,t}-w^{pretrade}_{i,t}|
$$

实际 Cost Ledger 按 one-way cost × gross traded notional：

$$
TC_t=\sum_i c^{oneway}_{i,t}|w^{target}_{i,t}-w^{pretrade}_{i,t}|
$$

Net Return uses the Section 4.4 exact NAV identity, with $TC_t$ measured against pre-cost NAV:

$$
1+R_t^{net}=(1-TC_t)(1+R_t^{hold})
$$

分析：

- Weekly Turnover；
- Annualized Turnover；
- Base Cost；
- Double-Cost Stress；
- Break-Even Cost；
- ADV；
- Participation Rate；
- Capacity by AUM。

### 目的

判断 Weekly Dynamic Strategy 在现实条件下是否仍有投资价值。

---

## Step 29 — US Drawdown / Failure Analysis

对最大回撤时期分析：

- Stock Contribution；
- Sector Contribution；
- Factor Exposure；
- FMOM Weight；
- Market Beta；
- Liquidity；
- Signal Deterioration。

要求至少选择：

- 最大单次 Drawdown；
- 一次 Market-wide Crisis；
- 一次策略自身明显失效、但市场未必大跌的时期（若存在）。

每个 episode 输出：

$$
What\ happened
\rightarrow
Which\ exposure\ changed
\rightarrow
Which\ holdings\ contributed
\rightarrow
Whether\ signal/FMOM\ deteriorated
$$

---

## Step 29A — US Robustness & Sensitivity

US 必须执行预先规定的稳健性测试。其目的不是重新寻找历史最优参数，而是检验 Base Result 是否依赖单一设定。

至少包含：

- FMOM lookback：$J=13/26/52$；
- $\lambda=0/0.5/1$；
- Weekly vs Monthly Rebalance；
- With vs Without Turnover Buffer（若 Base 使用 Buffer）；
- 1/99 vs 2.5/97.5 Winsorization；
- Base Cost vs 2× Cost；
- Complete-Factor Sample vs Minimum-Available-Factor Rule；
- 对关键 Factor 做 Leave-One-Factor-Out 检查，但正式 Ablation 仍放在 Final Deep Analysis；
- **Momentum × FMOM 2×2**：With / Without Stock Momentum × $\lambda=0$ / $\lambda=0.5$，用于识别股票 Momentum 与 Factor Momentum 是否重复。

所有结果都要报告，不能从 robustness grid 中重新挑“赢家”替换已冻结 Base Strategy。

另外，Dynamic vs Static 必须输出 `strategy_difference_inference.csv`：对 $\Delta R_t^{FMOM}$ 报告 HAC / block-bootstrap CI，并对 $\Delta Sharpe$ 报告 block-bootstrap confidence interval。

### US Strategy Diagnostic Gate — Mandatory

只有以下项目全部完成，US 才被视为“单市场研究完成”：

- [ ] Performance Profile；
- [ ] Stability；
- [ ] Forward Signal Evidence；
- [ ] Static vs FMOM Increment；
- [ ] Formal Factor Regression；
- [ ] Return Source Map；
- [ ] Sector Attribution；
- [ ] Stock Attribution；
- [ ] Holdings / Concentration；
- [ ] Turnover；
- [ ] Gross → Net；
- [ ] Cost Sensitivity；
- [ ] Liquidity / Capacity；
- [ ] Drawdown Anatomy；
- [ ] Robustness；
- [ ] Return Closure。

全部通过以后才形成：

$$
\boxed{
US\ Strategy\ Profile
}
$$

并允许进入正式 Cross-Market Comparison。

---

# 12. Part VIII — Singapore External Validation

SG 数据到位以后，不重新从零开发一套最佳策略。

第一目标是测试：

> **美国开发出来的核心策略能否迁移到 Singapore？**

---

## Step 30 — SG Universe & Data Contract

计划股票池：

- SGX Ordinary Shares；
- REITs；
- Market Cap Filter；
- Turnover / Liquidity Filter；
- Listing History Filter；
- 目标有效股票数约 60+。

具体门槛：

> 必须在看到 SG Strategy Result 以前冻结。

### REIT Treatment — Gate D 前必须冻结

由于 SG Universe 明确包含 REITs，必须在看到 SG strategy result 前建立 `REIT Factor Treatment Table`。每个 Candidate / Final Factor 至少记录：

| Factor | Ordinary Company | REIT | 处理原则 |
|---|---|---|---|
| Value | 按冻结公式 | 检查 FCF Yield 可比性 | 不可比指标设 NA，不强行填补 |
| Quality | ROE / GP/A / Leverage 等 | 使用经济含义可比的盈利 / 负债指标 | 分支公式必须预注册 |
| Momentum | 同一价格定义 | 同一价格定义 | 一致 |
| Low Risk | 同一价格定义 | 同一价格定义 | 一致 |
| Investment | Asset Growth | 检查资产扩张含义是否可比 | 若不可比则明确 availability rule |
| Dividend | Dividend Yield | Distribution Yield 需明确是否视为可比 | 必须在 Gate D 前固定 |

不能因为 REIT 回测表现差而事后更换其 Factor Definition。

### Neutralization Sample Adequacy

除了 `Minimum Cross-Section = 15`，每次截面 neutralization 还必须满足：

- observations 明显大于回归参数数量；
- 每个 industry group 至少达到预先规定的最小样本；
- 若不满足，则使用预先冻结的 industry merge / skip rule，而不是当期临时决定。

建议 Gate D 前根据实际 SG universe 统计后，固定 `min_obs_per_regression` 与 `min_names_per_industry_group`。

### Industry

SG 可以根据股票数量预先合并 GICS。

初步结构：

- Financials；
- Real Estate；
- Industrials / Energy / Materials / Utilities；
- Consumer / Health Care / Technology / Communication Services。

正式 backtest 前：

1. 拉出实际股票名单；
2. 统计每组股票数量；
3. 固定 mapping；
4. 全流程不再变化。

---

## Step 31 — SG Transfer Specification：Strict 与 Feasible 必须同时定义

### 31.1 SG Strict Transfer

$$
\boxed{S_{SG}^{Strict}}
$$

用于最严格的 Cross-Market Portability Test。它是 **Out-of-Market External Validation**：SG 没有参与 US Base 的核心开发；但如果 SG 回测使用的是历史上已经发生的年份，它并不自动成为时间意义上的 Out-of-Time OOS。

它尽可能复制 US Frozen Base：

- 相同 Final Factor Set；
- 相同 Factor Definitions / Directions；
- 相同 Neutralization Rule；
- 相同 FMOM Formula、$J$、$\lambda$；
- 相同 Weekly Rebalance / Execution Timing；
- 相同 Factor L/S quantile；
- 相同 Final Selection Fraction；
- 相同 Portfolio Weighting / Buffer / Sector Constraint（机械可执行时）。

如果某条 US rule 在 SG 机械上不可执行，必须显式记录为 `Not Feasible`，不得静默替换。Strict Transfer 不以“实用性最好”为目标，而是提供最干净的 external transfer benchmark。

### 31.2 SG Feasibility-Adjusted Base

$$
\boxed{S_{SG}^{Feasible}}
$$

在看到任何 SG strategy performance 以前，允许使用已预注册的 SG market-feasibility parameters，例如：

- Minimum Cross-Section = 15；
- Factor Long–Short 使用 tercile；
- Final Selection Top 30%；
- SG-specific Industry Mapping；
- SG-specific Liquidity Threshold；
- REIT-specific predefined availability / formula branches。

它是 SG 完整 Mandatory Diagnostic Package 的主要 practical object。

### 31.3 两个对象的研究用途

$$
US^{Base}\ vs\ SG^{Strict}
\Rightarrow
Pure\ Portability
$$

$$
SG^{Strict}\ vs\ SG^{Feasible}
\Rightarrow
Effect\ of\ Ex\text{-}Ante\ Market\ Feasibility\ Adjustment
$$

Strict 与 Feasible 的 specification 必须同时在 Gate D 冻结。

---

## Step 32 — SG Factor Momentum

Baseline 推荐：

$$
\boxed{
Same\ Algorithm
+
Local\ Factor\ Returns
}
$$

即：

$$
FMOM^{US}_{k,t}
=
f(R^{factor,US}_{k,t-j})
$$

$$
FMOM^{SG}_{k,t}
=
f(R^{factor,SG}_{k,t-j})
$$

函数 $f(\cdot)$ 完全一致。

因此“策略相同”指：

> **规则相同，而不是每周产生相同的 Factor Weight。**

把 US Factor Weight 直接复制给 SG 不作为 baseline，可单独作为：

$$
\boxed{
Cross\text{-}Market\ Factor\ Timing\ Transmission
}
$$

的附加实验。

---

## Step 33 — SG Deep Validation

SG 不允许只做“Transfer 后看 Sharpe”。必须使用与 US 尽量对称的 Mandatory Diagnostic Package。

### 33.1 Performance Profile

除 STI Total Return 外，SG 必须同步建立 **Eligible-Universe Equal-Weight** benchmark：对当期 SG eligible universe 等权持有，不使用任何 Factor Score。这样与 US 对称地区分：

$$
Universe\ Effect
\quad vs\quad
Factor\ Selection\ Effect
$$

统一分析：

- Gross / Net Return；
- Volatility；
- Sharpe；
- Sortino；
- Max Drawdown（表格按正损失幅度报告）；
- Calmar；
- Hit Rate；
- STI-relative Performance；
- Eligible-Universe-EW-relative Performance。

### 33.2 Performance Stability

- Yearly；
- Rolling 12m / 24m / 36m；
- Bull / Bear；
- High / Low Volatility；
- Crisis；
- Recovery。

### 33.3 Signal & Factor-Timing Evidence

- Forward IC；
- ICIR；
- Rolling IC；
- Signal Decay；
- Single-Factor Return；
- Static vs Dynamic；
- Local FMOM Contribution。

### 33.4 Formal Strategy-Level Factor Regression：Comparable 与 Rich Model 分离

#### Primary Comparable Model — 两市场必须完全同口径

为了让 US 与 SG Alpha / Beta 可以横向比较，Primary Comparable Regression 统一使用 **USD reporting currency + USD risk-free rate + 本市场 USD total-return benchmark**：

US：

$$
R^{US,USD}_{p,t}-RF^{USD}_t
=
\alpha^{Comp}_{US}
+
\beta^{Comp}_{US}
(R^{SP500,TR}_t-RF^{USD}_t)
+
\epsilon_t
$$

SG：

$$
R^{SG,USD}_{p,t}-RF^{USD}_t
=
\alpha^{Comp}_{SG}
+
\beta^{Comp}_{SG}
(R^{STI,USD,TR}_t-RF^{USD}_t)
+
\epsilon_t
$$

Primary Comparable Regression 固定使用 **Monthly Return**：由权威 Daily USD NAV 在自然月内复合得到月收益，US / SG 使用共同月份、相同频率和同一 HAC-lag rule。Weekly regression 如需做，只作为 robustness。

Cross-Market Table 中的 `Factor/Market-Adjusted Alpha` 只允许引用这一 comparable specification。

需要注意：US beta 是相对 S&P 500 Total Return，SG beta 是相对 STI USD Total Return。二者用于比较“各自本地市场依赖程度”，但并不是对同一个 underlying factor 的完全相同暴露，因此不得把 $\beta_{US}-\beta_{SG}$ 解释为对同一因子的严格差异。

#### Secondary Rich Diagnostics — 各市场内部解释

US 可进一步使用 FF-style external factors；SG 若有可靠 local / regional style factors，可进一步加入 Size / Value / Quality / Investment / Momentum。

若 SG 使用本币回归，则 return、benchmark 与 risk-free 必须全部使用一致 SGD 口径；如果无法获得可信 SGD risk-free rate，则使用明确标记的 raw-return market model，而不能把 SGD return 与 USD risk-free 混合。

项目内部构造的 factor-mimicking portfolios 只能标记为 `Internal Diagnostic Exposure`，不能与 external benchmark alpha 混为一谈。

至少输出：

- Alpha；
- HAC / Newey-West t-stat；
- p-value / confidence interval；
- Market Beta；
- Available Style Betas；
- $R^2$ / Adjusted $R^2$。

---

### 33.5 Portfolio / Holdings / Sector

- Holdings Count；
- Top-5 / Top-10；
- HHI；
- Sector Concentration；
- Sector Contribution；
- Market-Cap Distribution；
- REIT / Financial Exposure。

### 33.6 SG Return Source Map：使用分层 Attribution Tree

SG 不使用一条 `Static + FMOM + Sector/Stock + FX - Cost` 的横向加法，因为这些对象属于不同解释维度。

必须分别形成：

1. **Strategy-Rule Increment**：

$$
R^{FMOM\ Increment}_t
=
R^{Dynamic}_t-R^{Static}_t
$$

2. **Holdings Contribution**：

$$
R_t^{SGD,Gross}
=
\sum_i w_{i,t}R_{i,t}^{SGD}
$$

并从 Stock 聚合到 Sector；

3. **Currency Translation**：

$$
R_t^{USD}
=
R_t^{SGD}+R_t^{FX}+R_t^{SGD}R_t^{FX}
$$

4. **Cost Accounting**：

$$
1+R_t^{Net}=(1-TC_t)(1+R_t^{hold})
$$

For SG, this NAV closure is first completed in SGD; only then is the local net return translated to USD using the FX identity. A first-order cost drag, if shown, is interpretive only.

5. **Factor Regression**：单独作为 exposure explanation，不进入 holdings contribution identity。

每张图 / 表必须明确属于哪一个 attribution view。

---

### 33.7 Reality

- Weekly / Annualized Turnover；
- Transaction Cost；
- Break-Even Cost；
- ADV；
- Participation Rate；
- Liquidity Concentration；
- Capacity by AUM；
- Impact Sensitivity。

### 33.8 Drawdown / Failure Analysis

对主要亏损 episode 分析：

- Stock Contribution；
- Sector Contribution；
- Factor Exposure；
- FMOM Weight；
- Market Beta；
- Liquidity；
- Signal Deterioration；
- FX Contribution。

### 33.9 SG Robustness & Sensitivity

原则上执行与 US 同一套 robustness，以保证结果可比：

- $J=13/26/52$；
- $\lambda=0/0.5/1$；
- Weekly vs Monthly；
- With / Without Buffer；
- 1/99 vs 2.5/97.5 Winsorization；
- Base Cost vs 2× Cost；
- Complete-Factor vs Minimum-Available-Factor Rule；
- Strict Transfer vs Pre-Specified Feasibility Adjustment（正式 secondary comparison，而非仅 robustness）；
- **Momentum × FMOM 2×2**：With / Without Stock Momentum × $\lambda=0$ / $\lambda=0.5$。

Robustness 用于检验稳定性，不允许重新选择 SG-specific 历史赢家。

### SG Strategy Diagnostic Gate — Mandatory

只有以下项目全部完成，SG 才被视为“单市场研究完成”：

- [ ] Performance Profile；
- [ ] Stability；
- [ ] Forward Signal Evidence；
- [ ] Static vs FMOM Increment；
- [ ] Formal Factor Regression / Market Regression；
- [ ] Return Source Map；
- [ ] Sector Attribution；
- [ ] Stock Attribution；
- [ ] Holdings / Concentration；
- [ ] Turnover；
- [ ] Gross → Net；
- [ ] Cost Sensitivity；
- [ ] Liquidity / Capacity；
- [ ] Drawdown Anatomy；
- [ ] Robustness；
- [ ] Return Closure。

全部通过以后才形成：

$$
\boxed{
SG\ Strategy\ Profile
}
$$

只有 US 与 SG 两个 Diagnostic Gate 都通过，才进入正式 Cross-Market Comparison。

---

# 13. Part IX — Cross-Market Comparison

## Step 34 — Cross-Market Comparison：三层识别结构

### 34.1 Primary Portability Test

正式主问题：

$$
\boxed{US^{Base}\ vs\ SG^{Strict}}
$$

只使用 **Matched Calendar-Month Sample**，回答：

> 美国开发出的策略在尽量不改规则时，能否直接迁移？

### 34.2 Feasibility Adjustment Test

$$
\boxed{SG^{Strict}\ vs\ SG^{Feasible}}
$$

回答：

> 在不看 SG 历史赢家、只基于市场结构预先规定的可实施性调整，是否改善 breadth / implementability / net performance？

### 34.3 Feasibility Bridge：如果 SG Feasible 同时改了多项规则

若 SG Feasible 相对 Strict 同时改变 Factor L/S breadth、Final selection fraction、Industry grouping、Liquidity rule 或 REIT treatment，则不能只报告 `Strict → Feasible` 的最终总差异。

应额外保存一个预先规定的 bridge：

$$
Strict\rightarrow Change\ 1\rightarrow Change\ 2\rightarrow\cdots\rightarrow Feasible
$$

或做 one-at-a-time toggles。目的不是从中选最优组合，而是识别哪一项 Ex-Ante Feasibility Adjustment 主要改变 breadth、turnover、concentration 或 net performance。

Bridge 顺序必须在看到结果前冻结；若存在 path dependence，必须明确标记，不能把单一路径贡献解释成唯一因果贡献。

### 34.4 Native Market Profiles

US Base 与 SG Feasible 各自使用 Native Sample 做完整 Single-Market Deep Validation，用于理解每个市场自身；Native Sample 结果不替代 matched portability test。

### 34.5 核心比较表

| Dimension | US Base | SG Strict | SG Feasible | 解释 |
|---|---:|---:|---:|---|
| Mean IC | | | | Signal portability |
| ICIR | | | | Signal stability |
| FMOM Increment | | | | Timing portability |
| Comparable Market-Adjusted Alpha | | | | 仅统一 USD market-only model |
| Comparable Market Beta | | | | 统一口径 |
| Gross Return | | | | |
| Net Return | | | | |
| Active Return vs Local Benchmark | | | | Benchmark-adjusted portability |
| Information Ratio | | | | |
| Sharpe | | | | |
| Max Drawdown | | | | |
| Turnover | | | | |
| Cost Drag | | | | |
| Number of Holdings | | | | Breadth |
| HHI | | | | Concentration |
| Sector Concentration | | | | |
| Liquidity | | | | |
| Capacity Scenario | | | | 未校准 impact 前不称真实 capacity |
| Worst Regime | | | | |
| Robustness Verdict | | | | |
| Return Closure Residual | | | | Accounting integrity |

### 34.6 Strategy-Difference Statistical Inference

Primary matched comparisons 必须报告 uncertainty，而不是只比较点估计。

Dynamic vs Static：

$$
\Delta R_t^{FMOM}=R_t^{Dynamic}-R_t^{Static}
$$

US vs SG Strict：

$$
\Delta R_t^{Market}=R_{US,t}^{Base}-R_{SG,t}^{Strict}
$$

至少报告：

- Mean difference；
- HAC / Newey-West 或 block-bootstrap CI；
- Sharpe difference 的 block-bootstrap CI；
- paired matched-month result。

不允许使用简单独立样本 t-test 取代时间序列一致的比较。

最终回答：

$$
\boxed{What\ is\ portable?}
$$

以及：

$$
\boxed{What\ requires\ market\ feasibility\ adjustment?}
$$

以及：

$$
\boxed{What\ is\ genuinely\ market\ specific?}
$$

---

# 14. Part X — 为什么两个市场效果不同

## Step 35 — Signal Difference

比较：

- IC；
- ICIR；
- Signal Decay；
- Factor Return；
- FMOM Persistence。

问题：

> 同一 Candidate / Final Factor 在 SG 是否仍具有与 US 类似的 Forward Information？

---

## Step 36 — Portfolio Translation Difference

如果：

$$
IC_{US}\approx IC_{SG}
$$

但：

$$
Performance_{US}\neq Performance_{SG}
$$

分析：

- Breadth；
- Holdings Count；
- Selection Fraction；
- Concentration；
- Sector Structure；
- Weight Dispersion。

问题：

> 为什么相似的 Signal 在两个市场转化成不同的 Portfolio Outcome？

---

## Step 37 — Market Structure Difference

比较：

- Universe Size；
- Market-Cap Distribution；
- Sector Composition；
- Financial / REIT Weight；
- Cross-Sectional Return Dispersion；
- Average Stock Correlation；
- Volatility。

问题：

> 市场结构是否限制了 Signal 的表达空间与分散化能力？

---

## Step 38 — Implementation Difference

比较：

- Weekly Turnover；
- Liquidity；
- ADV；
- Spread Proxy；
- Transaction Cost；
- Capacity。

问题：

> Gross Performance Difference 与 Net Performance Difference 是否来自不同机制？

---

## Step 39 — Risk / Currency / Regime Difference

比较：

- Market Beta；
- Style Exposure；
- Sector Exposure；
- USD/SGD Contribution；
- Bull / Bear；
- High / Low Vol；
- Crisis；
- Rate Regimes。

最终形成：

$$
\boxed{
Performance\ Difference
\rightarrow
Mechanism\ Difference
}
$$

---

# 15. Part XI — Market-Specific Adaptation

## Step 40 — Adaptation Hypothesis Register

每个 adaptation 必须明确：

| 项目 | 内容 |
|---|---|
| Observed Problem | 具体问题是什么 |
| Diagnostic Evidence | 哪些数据支持 |
| Mechanism | 为什么会发生 |
| Proposed Change | 修改什么 |
| Expected Effect | 理论上应改善什么 |
| Primary Metric | 首先验证什么 |
| Secondary Metrics | 其次验证什么 |
| Failure Criterion | 什么结果说明无效 |

例如：

### SG Turnover 过高

可以测试：

- Weekly → Biweekly / Monthly；
- Turnover Penalty；
- Signal Smoothing。

首先验证：

$$
Turnover\downarrow
$$

其次：

$$
CostDrag\downarrow
$$

最后才看：

$$
NetSharpe\uparrow
$$

### 原则

Adaptation 不是必需环节。

如果 Base Strategy 在某市场已经稳定且可交易，不需要为了“增加创新”强行修改。

---

## Step 40.1 — Adaptation Validation：冻结前的强制验证环节

任何已经登记的 Adaptation 都必须先在 **独立于其提出过程的 Validation Sample** 上验证，才能进入 Gate E。

### A. 比较对象

对相关市场同时运行：

$$
Base
\quad vs\quad
Adapted
$$

且除被登记的 Adaptation 以外，其他规则保持不变。

### B. 验证顺序

Adaptation 必须先证明它解决了被诊断的问题，而不是先看 Sharpe：

$$
Primary\ Mechanism\ Metric
\rightarrow
Secondary\ Implementation\ Metrics
\rightarrow
Net\ Performance
$$

例如 turnover adaptation：

1. 首先要求 Turnover 按预期下降；
2. 再检查 Cost Drag 是否下降；
3. 最后才评价 Net Return / Net Sharpe 是否改善。

### C. 必须输出

`adaptation_validation.csv` 至少包含：

- Adaptation ID；
- Market；
- Validation Sample；
- Base Metric；
- Adapted Metric；
- Primary Metric Change；
- Secondary Metric Change；
- Net Performance Change；
- Failure Criterion；
- Pass / Fail；
- Evidence Label = `ADAPT_VALIDATION`。

### D. 决策规则

- 若 Primary Mechanism Metric 没有按预期改善：Adaptation 失败；
- 若机制改善但成本 / 风险显著恶化：必须记录 trade-off，不能只凭 Sharpe 通过；
- 若只提高 Sharpe、但没有解决原始问题：不得声称 mechanism-based adaptation 成功；
- 只有通过 Step 40.1 的 Adaptation 才能进入 Gate E / Strategy Freeze。

### E. 样本纪律

被用于提出 Adaptation 的时期不能同时充当该 Adaptation 的 independent validation。若无法保留独立 Validation Sample，则该 Adaptation 只能标记为 exploratory，不能进入 Frozen Adapted Strategy。

---

# 16. Part XII — Final Revalidation

## Step 41 — Strategy Freeze

最终冻结：

- Final Factor Set；
- Factor Formula；
- Factor Direction；
- Neutralization；
- FMOM $J$；
- Risk Adjustment；
- $\lambda$；
- Rebalance Frequency；
- Selection Rule；
- Buffer Rule；
- Sector Cap；
- Market Feasibility Parameters；
- Transaction Costs；
- Benchmark；
- FX Convention；
- Adaptation Rule。

之后：

$$
\boxed{
No\ More\ Parameter\ Changes
}
$$

---

## Step 42 — Final Untouched Test

最终可能比较：

- US Base；
- SG Strict Transfer（作为 portability benchmark）；
- SG Feasibility-Adjusted Base；
- US Adapted（若存在）；
- SG Adapted（若存在）。

Final Test 打开后：

> 新想法只能列入 Future Research，不能再进入当前策略。

如果项目时间范围内没有足够新的 untouched sample，则不得强行制造 `Final OOS`：

- Base Strategy 保留其原有 Historical OOS 证据；
- Adapted Strategy 只报告 Adaptation Validation；
- Final OOS 结论明确写为 `Not Yet Available`；
- 后续真实新数据到来后再执行 Gate F。

---

# 17. Part XIII — Final Deep Analysis

## 17.0 Final Diagnostic Rule

Final OOS 打开以后，需要再次对 US / SG 最终策略运行同一套核心 Diagnostic Package，但此时分析权限发生变化：

- 可以重新计算 Performance / Regression / Attribution / Cost / Capacity / Stress / Closure；
- 可以解释为什么 Final OOS 与此前不同；
- **不允许根据 Final Diagnostics 修改任何策略规则。**

因此必须明确区分：

$$
Pre\text{-}Final\ Diagnostics
\rightarrow
Can\ motivate\ registered\ adaptation
$$

与：

$$
Final\ Diagnostics
\rightarrow
Explain\ only
$$

---

## Step 43 — Factor-Momentum Incremental Attribution

回答：

$$
\boxed{
Why\ does\ FMOM\ help\ or\ fail?
}
$$

分析：

- Static vs Dynamic Return；
- Dynamic Weight Changes；
- Factor Overweight / Underweight；
- Timing Gain；
- Timing Loss；
- Incremental Turnover；
- Net Timing Contribution。

---

## Step 44 — Factor / Sector / Stock Attribution

分析：

- Factor Exposure；
- Sector Contribution；
- Stock Contribution；
- Market Beta；
- Currency Contribution；
- Drawdown Contribution。

注意：

> Regression / Exposure 是解释证据，不自动等同于 causal alpha。

---

## Step 45 — Ablation Study

依次移除：

- 某单一 Factor；
- Factor Momentum Tilt；
- Neutralization；
- Turnover Buffer；
- Market-Specific Adaptation。

观察：

$$
\Delta Return,\quad
\Delta Vol,\quad
\Delta Sharpe,\quad
\Delta Drawdown,\quad
\Delta Turnover
$$

区分：

- Return Enhancer；
- Risk Reducer；
- Cost Reducer；
- Stability Enhancer。

如果 Ablation 在 Final OOS 打开后执行，它只能作为 **explanatory counterfactual diagnostic**，不能把 ablated / modified variant 追溯性称为新的独立 OOS candidate strategy。

---

## Step 46 — Transaction Cost & Capacity

逐期：

$$
1+R^{net}_t
=
(1-TC_t)(1+R_t^{hold})
$$

其中 $TC_t$ 相对于 pre-cost NAV 计量；若报告 $R_t^{gross}-TC_t$，只能作为明确标注为 interpretive / first-order 的近似。分别使用 US / SG 成本假设。

分析：

- Base Cost；
- Double-Cost Stress；
- Break-Even Cost；
- AUM Scenarios；
- ADV；
- Participation Rate；
- Market Impact；
- Capacity Bottleneck。

---

## Step 47 — Regime & Stress Test

分析：

- Bull / Bear；
- High / Low Volatility；
- Crisis；
- High / Low Rate；
- US-Specific Stress；
- SG-Specific Stress。

形成：

$$
\boxed{
Strategy\ Risk\ Map
}
$$

而不是重新寻找最佳参数。

---

## Step 48 — Holdings & Return Closure

US 本币 / 报告货币均为 USD：

$$
R^{US,gross}_t
=
\sum_{i\in US}
w^{US}_{i,t}R^{US}_{i,t}
+
w^{US,cash}_tR^{US,cash}_t
$$

SG 先在 SGD 本币口径构造 gross holdings return：

$$
R^{SGD,gross}_{SG,t}
=
\sum_{i\in SG}
w^{SG}_{i,t}R^{SGD}_{i,t}
+
w^{SG,cash}_tR^{SGD,cash}_t
$$

Baseline residual cash：

$$
R^{US,cash}_t=R^{SGD,cash}_t=0
$$

并且每期：

$$
\sum_iw_{i,t}+w^{cash}_t=1
$$

随后以本币 pre-cost NAV 为基数应用 transaction cost，并在 SGD 完成 net NAV closure：

$$
1+R^{SGD,net}_{SG,t}
=
(1-TC_{SG,t})(1+R^{SGD,gross}_{SG,t})
$$

Gross translation 可保留用于 gross attribution / diagnostics：

$$
1+R^{USD,gross}_{SG,t}
=
(1+R^{SGD,gross}_{SG,t})
(1+R^{FX}_t)
$$

本币净 NAV closure 完成后，才用于最终 portfolio-level reconciliation 的 net translation 为：

$$
1+R^{USD,net}_{SG,t}
=
(1+R^{SGD,net}_{SG,t})
(1+R^{FX}_t)
$$

不得 double count FX 或 transaction cost。

最终要求：

- Holdings 可以精确重构 Gross Return；
- Transaction Cost 可以重构 SGD Net Return；
- final portfolio-level reconciliation 使用 SGD net → USD net translation identity；
- FX Contribution 可以单独解释；
- Attribution 使用的 holdings 与真实策略 holdings 一致；
- Reconciliation Residual 接近 0。

---

# 18. Final Deployment Portfolio

US / SG 的单市场研究与跨市场验证完成以后，如果课程要求最终运行一个两地组合，再进入 Deployment Layer。

当前预设：

$$
w^{US,*}=80\%
$$

$$
w^{SG,*}=20\%
$$

并设置：

$$
75\%
\le
w^{US}_{pretrade}
\le
85\%
$$

的 No-Trade Band。

若仍在 band 内：

- 不进行跨市场资金再平衡；
- 只执行市场内部股票调仓。

若突破 band：

- 恢复到 80% / 20%。

如果跨市场再平衡实际需要 USD↔SGD 现金转换，Deployment Net P&L 必须额外记录 **FX execution cost / spread**。这与持有 SG 资产期间的被动 FX translation return 是两个不同对象，不得混淆。

需要明确：

> **80/20 Combined Portfolio 是最终 Deployment Portfolio，不是判断策略是否分别在 US 与 SG 有效的主要研究对象。**

因此：

$$
US\ Base
$$

与：

$$
SG\ Base
$$

必须首先独立评价。

---

# 19. Currency Treatment

## Factor / Stock Selection

全部使用本币：

- US：USD；
- SG：SGD。

## Reporting Currency

统一：

$$
USD
$$

## SG USD Return

$$
R^{USD}_{SG,t}
=
(1+R^{SGD}_{SG,t})
\left(
\frac{S_t}{S_{t-1}}
\right)
-1
$$

其中：

$$
S_t
=
\text{USD value of 1 SGD}
$$

## FX Hedge

当前 baseline：

$$
\boxed{
No\ FX\ Forward\ Hedge
}
$$

但必须分别报告：

- SG Local Return；
- FX Return；
- Local × FX Interaction；
- SG USD Return。

并满足精确关系：

$$
R^{USD}=R^{SGD}+R^{FX}+R^{SGD}R^{FX}
$$

Primary Cross-Market Regression 使用 USD reporting currency 与 USD risk-free rate；SG local-currency richer diagnostics 必须保持 return / benchmark / risk-free 的币种一致。

---

# 20. 当前正式 Research Gates

## Formal Gate 0 — WRDS Data Ready

在 formal canonical construction 前完成 4.1 所列 WRDS integrity、PERMNO/GVKEY/CCM、Historical GICS、Compustat PIT、CRSP DlyCap / CIZ / index、Fama–French unit 与 cutoff 验证。raw files 已放置不等于 Gate 0 通过。

## Gate A0 — Within-Family Specification Freeze

Gate A0 在 2011–2015 Factor Selection sample 内进行，并冻结：

- 每个 multi-specification family 的一项 representative specification；
- 对应 formula、direction、branch rule 与 neutralization rule。

Investment、Dividend 与 Size 作为 single-specification families 直接通过 Gate A0，不进行内部 selection。Gate A0 不决定哪些经济家族进入最终 multi-factor strategy，也不改变 2011–2015 以外样本的访问权限。

## Gate A — Final Factor Set Freeze

在 Gate A0 representative freeze 后，Gate A 比较七个 economic-family representatives，并冻结：

- Final Factor Set；
- Definitions；
- Directions；
- Neutralization Rules。

---

## Gate B — Factor Momentum Configuration Freeze

在 US Configuration Selection 完成后冻结：

- $J$；
- Risk Adjustment；
- $\lambda$；
- Selection / Buffer Rules；
- Portfolio Construction。

---

## Gate C — US Base Strategy Freeze

形成：

$$
\boxed{
S^{Base}_{US}
}
$$

之后以 2021–2025.12 的 `HIST_OOS_BASE` 进入 formal US Historical OOS 与 Deep Validation；2026.01–2026.09 只能作为独立 `RECENT_EXTENSION`。

---

## Gate D — SG Transfer Specification Freeze

在正式查看任何 SG strategy performance 之前，同时冻结：

- SG Universe；
- Historical Coverage Rule；
- REIT Factor Treatment Table；
- Industry Mapping；
- Liquidity Filter；
- Minimum Cross-Section；
- `min_obs_per_regression`；
- `min_names_per_industry_group`；
- **SG Strict Transfer specification**；
- **SG Feasibility-Adjusted specification**；
- SG Reporting / FX conventions。

Gate D 之后，任何基于 SG 历史结果提出的变化都只能进入 Adaptation Register，不能回写 Strict / Feasible Base。

---

## Gate E — Adaptation Freeze

只有在诊断出明确问题时：

- 登记 Adaptation Hypothesis；
- 在独立 Validation Sample 中测试；
- 冻结最终版本。

---

## Gate F — Final Untouched Test

Final Test 第一次打开后：

$$
\boxed{
No\ More\ Strategy\ Changes
}
$$

---

# 21. 当前真正待确定的事项

以下内容仍然不能默认补齐。

## Factor Level

- Gate A0 within-family representative specification（仅 multi-specification families；在 Gate A0 前仍为 TBD）；
- Final Factor Set；
- Dividend 是否保留及最终方向；
- Size 是否进入 Final Set；
- Dividend / Size Neutralization；
- 原始财务字段映射；
- Profit 使用 TTM 还是 Single Quarter。

## Formal Data-Contract Level

- Compustat historical revision / restatement / vintage semantics：ordinary historical extract 是否包含 later-restated historical values，以及相应 PIT claim boundary；
- CRSP `DlyCap` / `ShrOut` exact unit and **security-level** semantic documentation；
- issuer-level vs security-level market-cap construction：Value denominator 的 relevant equity share-class coverage、multi-share-class accounting consistency，以及 Size / neutralization 的 economic-level rule；
- 同一 `GVKEY` / `PERMCO` 的 issuer-level holdings duplicate / concentration audit；
- CRSP CIZ return、corporate-action 与 delisting treatment，确保不 double count；
- Compustat annual PIT availability rule：`datadate` 不可直接作为 publication date，必须在 Q4 / earnings timing 或 conservative lag approach 中形成可辩护规则；
- quarterly flow differencing、field precedence 与 conflict handling；
- 2025 CRSP–Yahoo bridge validation 的 acceptance criteria，作为任何 2026 market-price / return bridge 的前置条件。

## Portfolio Level

- 最终 Minimum Available Factor Rule；
- Turnover Buffer 是否启用；
- US 单边 all-in transaction-cost calibration；
- SG 单边 all-in transaction-cost calibration；
- Sector-constraint 最终实现算法（两种候选中选一）；
- Drawdown Count Threshold；
- 若未来要报告 Long–Short Net Sharpe：Short Borrow / Financing / Short-Side Execution Cost。

## Singapore Level

- SG Universe Threshold；
- Historical Data Coverage；
- Final Industry Mapping；
- Liquidity Parameters；
- Historical / Live Price Source；
- Financial Data Source；
- FX Source。

## Validation Level

- SG 数据可靠起点；
- Adaptation / Validation Period；
- Final Untouched Test Period；
- 若无法保留新 Final Test，Adaptation 是否只能定位为 exploratory；
- HAC maxlag rule；
- Block-bootstrap block-length rule；
- 正式 Regime thresholds / data source。

## Deployment Level

- Final US / SG Capital Allocation；
- 80/20 是否最终采用；
- No-Trade Band 是否保持 75%–85%；
- Live Initial Capital；
- Live Daily Data Source；
- 实际 Strategy Freeze Date 与第一个真正 Live Signal Date（不得回溯认定）。

---

# 22. 项目最终必须回答的七个问题

## Q1 — 哪些股票因子真正有效？

从 Candidate Factor Pool 中，先通过 Gate A0 冻结每个经济家族的一项 measurement specification；随后 Gate A 通过：

$$
Economic\ Rationale
+
Forward\ IC
+
Single\text{-}Factor\ Backtest
+
Stability
+
Complementarity
$$

确定 Final Factor Set。

---

## Q2 — Factor Momentum 是否真正提供增量？

比较：

$$
\lambda=0
$$

与：

$$
\lambda=0.5
$$

回答：

> Factor Timing 是提高收益、降低风险，还是只增加 turnover？

---

## Q3 — 美国 Base Strategy 是否真正有效？

不仅看 Sharpe，还要看：

- Stability；
- Exposure；
- Drawdown；
- Turnover；
- Cost；
- Liquidity；
- Capacity。

---

## Q4 — US-Developed Strategy 能否迁移到 Singapore？

回答：

$$
\boxed{
Cross\text{-}Market\ Portability
}
$$

---

## Q5 — 为什么 US 与 SG 表现不同？

区分：

- Signal；
- Portfolio Translation；
- Market Structure；
- Risk Exposure；
- Implementation Friction；
- Currency；
- Regime。

---

## Q6 — Market-Specific Adaptation 是否真正有效？

必须遵循：

$$
Problem
\rightarrow
Mechanism
\rightarrow
Targeted\ Fix
\rightarrow
Validation
$$

---

## Q7 — 策略能否经受现实交易条件？

最终检验：

- Weekly Turnover；
- Transaction Costs；
- Liquidity；
- Capacity；
- FX；
- Stress；
- Return Closure。

---


# 23. 六人 Workstream 完成标准与最终交接

## 23.1 Member 1 — Data & PIT Lead

完成标准：

- US Data Contract 明确；
- SG 数据到位后完成同结构 Data Contract；
- Universe Entry / Exit 可追踪；
- Duplicate = 0；
- PIT Spot Check 通过；
- Coverage Report 完整；
- Currency / Price / Return / Corporate Action 定义固定；
- Final Reconciliation 可由 Holdings 重建；
- 每次正式 run 都有完整 `run_manifest.csv`；
- 每个 Gate Run 都有 `acceptance_test_report.csv`，Critical Test 必须全部通过。

不得自行决定：

- Factor 是否保留；
- FMOM 参数；
- 策略参数。

---

## 23.2 Member 2 — Factor Engineering Lead

完成标准：

- 每个 Candidate Factor 有字段级公式；
- Sector Branch 清楚；
- Direction 清楚；
- Winsorization / Z-score / Neutralization 顺序固定；
- Neutralization Check 通过；
- Raw → Final Score 可追踪；
- Gate A0 时只按 registry 中已冻结的 existing-constituent specifications 构造 score，不创造新概念或连续内部权重；
- 输出统一 `factor_scores_weekly.csv`；
- 后期协助 US / SG Return Source Map、Factor / Sector Attribution，确保 contribution 对真实 holdings 闭合。

不得自行根据回测结果改变 Factor 定义。

---

## 23.3 Member 3 — Factor Research Lead

完成标准：

- 所有 Candidate Factors 使用完全相同的 Forward IC 规则；
- Single-Factor Backtest 严格使用 $S_t \rightarrow E_t \rightarrow E_{t+1}$ execution timing，无错位；
- Long / Short / Long–Short 均保存；
- HAC t-stat、Stability、Redundancy 完成；
- 每个 Factor 有 Decision Card；
- Gate A0 对同一经济家族的 eligible specifications 使用同一 validation engine，并保留 PRIMARY 与 COMMON_WITHIN_FAMILY_SAMPLE 两种视图；
- Gate A0 准备 family-representative evidence package，但不得自动选择 winner；
- Gate A 给出 Final Factor Set Proposal 和保留/删除理由；
- 后期负责 US / SG 预先规定 Robustness 与 Ablation，禁止把 robustness grid 当作新的参数优化器。

不得只按最高 Sharpe 选择 Final Factors。

---

## 23.4 Member 4 — Strategy Engine Lead

完成标准：

- Factor Return 与 FMOM 严格遵守 4.3 的 completed-period rule，并通过人工样例与 automated timing test；
- $w^{FMOM}$ 与 Blended Weight 每期和为 1；
- All-Negative FMOM fallback 正确；
- Composite Score 可由 Factor Scores × Weights 重建；
- Target Holdings 可复算；
- Buffer / Sector Cap 使用唯一算法；
- Next-Day Close Execution 正确；
- Static 与 Dynamic Strategy 除 Factor Weight 外完全一致。

---

## 23.5 Member 5 — Backtest & Risk Lead

完成标准：

- Gross / Net NAV 可复算；
- Benchmark 使用统一 Total Return 口径；
- Metric Contract 全部统一；
- Weekly / Annual Turnover 可复算；
- Cost Drag、Break-Even Cost 完成；
- Drawdown、Exposure、Liquidity、Capacity 完成；
- Formal Strategy-Level Factor Regression 使用 HAC / Newey-West inference；
- US Return Source Map 完成；
- US Mandatory Robustness 完成；
- US Strategy Diagnostic Gate 全部通过；
- US Strategy Profile 形成；
- Final Stress Test 使用冻结策略，不反向调参。

---

## 23.6 Member 6 — Cross-Market & Integration Lead

完成标准：

- 在看 SG performance 前冻结 Gate D；
- 明确定义 $S_{SG}^{Strict}$ 与 $S_{SG}^{Feasible}$；
- 不重写 M1–M5 的 SG pipeline，只协调同一 engine 的 SG 参数化运行；
- Primary Portability 使用 US Base vs SG Strict 的 Matched Sample；
- Secondary Comparison 使用 SG Strict vs SG Feasible；
- Comparable Regression 只引用统一 USD Market-Only specification；
- Native 与 Matched Sample 均完成但用途不混淆；
- Cross-Market Difference 被分解为 Signal / Portfolio / Structure / Implementation / Risk / Currency / Regime；
- Strategy-difference inference 已完成；
- Adaptation 必须先进入 Register，再完成 Step 40.1 独立 Adaptation Validation；
- Final Integration 不修改 Final Test 后的策略。

不得：

- 因 SG Sharpe 不理想而回头修改 Gate D 的 Strict / Feasible specification；
- 自己重写一套与 US 不同的数据、factor 或 backtest engine；
- 直接横向比较不同 regression specification 的 Alpha。

---

## 23.7 每周组会建议优先看五张表

为了避免六个人汇报过度分散，每次组会优先更新：

1. **Master Parameter Registry**：本周有没有参数从 TBD → Candidate / Fixed；
2. **Gate Checklist**：当前距离下一个 Gate 还缺什么；
3. **Issue Log**：数据、公式、结果中有哪些 unresolved issues；
4. **Handoff Status**：谁已经交付、谁正在等待上游；
5. **Run Manifest / Reproducibility Status**：本周正式结果是否都有唯一 run_id 与版本记录。

详细图表和分析放在各 Workstream 输出中，不需要每次组会全部重新讲一遍。

---

# 24. 最终项目管理原则

六人协作时，本项目最重要的不是把 48 个 Step 平均分成六份，而是保证：

$$
\boxed{
One\ Definition
+
One\ Owner
+
One\ Output
+
One\ Reviewer
}
$$

每一步都应做到：

> **上游结果可以被下游直接读取，核心参数只能在规定阶段改变，任何人都不能为了让自己负责部分的结果更好而单独改变全局定义。**

最终这份文件应同时承担三种角色：

1. **Research Blueprint**：为什么这样研究；
2. **Strategy Specification**：策略到底怎么算；
3. **Team Execution Manual**：六个人分别做什么、交付什么、何时算完成。

同时增加第四个约束：

4. **Diagnostic Standard**：任何一个市场都不能只凭收益指标宣称研究完成，必须通过 Mandatory Single-Market Diagnostic Package 与对应 Strategy Diagnostic Gate。

---

# 25. Framework Completeness Audit：冻结前最终检查

以此前参考项目总结出的八层验证思想为基准，再加入本项目额外的 cross-market identification 与 team governance，当前框架覆盖如下：

| Validation Layer | 当前框架对应内容 | 状态 |
|---|---|---|
| Data Integrity | Gate 0、PIT、Coverage、Universe、REIT / Currency / Corporate Action、Run Manifest | **Covered** |
| Signal Integrity | Forward IC、Direction Rule、Neutralization、Decision Card、FDR | **Covered** |
| Portfolio Construction | 唯一执行顺序、FMOM、Buffer、Sector Cap、Strict / Feasible object | **Covered** |
| Return / Accounting | Gross→Net、Dividend rule、FX interaction、Return Closure | **Covered** |
| Research Design | Development / Config / OOS / Adaptation / Final Gates、Decision Rights | **Covered** |
| Statistical Inference | HAC、Block Bootstrap、Difference Test、Multiple Testing | **Covered** |
| Implementation Realism | Cost、Turnover、Liquidity、Capacity Scenario、Live Governance | **Covered** |
| Interpretation / Attribution | Increment / Holdings / FX / Cost / Regression 分层 taxonomy | **Covered** |
| Cross-Market Identification | US Base vs SG Strict、SG Strict vs SG Feasible、Matched Sample | **Covered** |
| Reproducibility / Governance | Single Owner、Reviewer、Canonical Outputs、Run Manifest、Gate Approval | **Covered** |
| Automated Acceptance / Invariants | Gate 自动测试、Timing / Weight / Accounting / FX Closure | **Covered** |
| Evidence / Claim Governance | DEV / OOS / Transfer / Adaptation / Final / Live 标签、Survivorship Claim Boundary | **Covered** |
| Execution-Timing Consistency | Signal Cutoff、Next-Close Execution、Executable Factor Return、Completed-Period FMOM | **Covered** |
| Adaptation Validation | Register → Independent Validation → Gate E → Freeze | **Covered** |

### 仍然存在但不属于“框架缺口”的 TBD

以下项目必须等待真实数据或组内决定后填入，因此不能在框架阶段假装已经解决：

- 最终 Factor Set；
- SG 数据覆盖起点；
- SG REIT 最终字段可用性；
- 实际 transaction-cost calibration；
- SG liquidity / industry thresholds；
- 是否有可靠 SG style-factor data；
- Adaptation 是否真的需要；
- Final Untouched Sample 能否保留足够长度；
- 实际 Live Freeze Date；
- 最终 80/20 deployment 是否采用。

这些是 **implementation-dependent inputs**，不是研究设计缺陷。

---

# 26. 项目一句话定位


> **本项目首先在美国市场从 Value、Quality、Momentum、Low Risk、Investment、Dividend 与 Size 等候选特征中，通过经济逻辑、Forward IC、单因子组合表现、稳定性和互补性筛选最终 Factor Set，并在此基础上构建“静态多因子 + Factor Momentum Tilt”的周度纯多头策略。核心配置只在美国开发样本内确定并冻结，随后先以 SG Strict Transfer 检验最严格的跨市场可迁移性，再以预注册的 SG Feasibility-Adjusted Base 检验客观市场结构调整的作用。US 与 SG 分别接受完整的收益、稳定性、Factor Timing、风险暴露、行业与持仓结构、交易成本、流动性、容量与回撤分析，再通过 Matched-Sample 跨市场比较解释表现差异，并只针对已经识别出的市场结构问题进行有限适配。若能够保留真正未使用的 Final OOS，则所有冻结策略在该样本中重新验证；若项目时间范围内无法提供新的 untouched sample，则 Base 保留其 Historical OOS 证据、Adapted Strategy 只报告 `ADAPT_VALIDATION`，并将 Final OOS 明确标记为 `Not Yet Available`。无论是否存在 Final OOS，都完成 Factor Timing、Factor / Sector / Stock、成本、容量、压力测试与逐期收益闭合分析。**
