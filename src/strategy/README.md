# M4 Strategy Engine — 实现口径说明（Steps 16–21 + Gate B）

维护人：Eric Lu（M4）。本文件说明 `src/strategy/` 的计算口径，并逐项标明：
**[SSOT]** = 按 `docs/US_SG_Cross_Market_Quant_Research.md` 的明确规定实现（附章节）；
**[M4 选择]** = SSOT 没写、由 M4 决定的实现细节，**需要全组确认**，确认后建议补进 SSOT；
**[偏离]** = 与 SSOT 最终口径不同之处，以及原因和后续处理。

SSOT 是唯一权威文档；本文件和 SSOT 冲突时以 SSOT 为准，并应修正本文件。

---

## 1. 流程与文件

| 步骤 | 文件 | 输入 | 输出（`outputs/strategy/`，不进 Git） |
|---|---|---|---|
| Step 16 因子多空收益 | `factor_returns.py` | 因子分数、股票周收益 | `factor_returns_weekly_2016_2020.csv` |
| Steps 17–19 FMOM 与因子权重 | `fmom_weights.py` | 种子 + Step 16 | `factor_weights_weekly_2016_2020.csv` |
| Step 20 综合分 | `composite_score.py` | 因子分数 + 权重 | `stock_scores_weekly_…csv` |
| Step 21 选股 | `portfolio.py` | 综合分 | `target_holdings_weekly_…csv` |
| 共享指标函数 | `metrics.py` | 持仓 + 收益 | — |
| Gate B 总控 | `run_gate_b.py` | 以上全部 + rf + 基准 | `gate_b/` 下的持仓、总表、周度和逐年收益 |

复现（项目根目录，需先 `git lfs pull`）：

```
python -m src.strategy.factor_returns --start 2016-01-01 --end 2020-12-31
python -m src.strategy.fmom_weights
python -m src.strategy.run_gate_b            # 默认单边成本 10bp，压力测试 20bp
```

测试：`python -m unittest tests.test_factor_returns tests.test_fmom_weights tests.test_composite_score tests.test_portfolio tests.test_metrics tests.test_run_gate_b`

所有脚本都有日期保护：读取或使用 2020-12-31 以后的数据会直接报错（SSOT 0.7 / 4.2，Gate B 只用 2016–2020）。

---

## 2. Step 16 — 因子多空收益

- [SSOT 4.3 C、Step 16] 前 30% 做多、后 30% 做空，数量 floor(0.3·N)，同分按 PERMNO 升序；两腿等权；只算可交易股票；E_t → E_{t+1} 持有；毛收益 = 多头均值 − 空头均值。与冻结种子（2011–2015）逐周一致，最大误差约 1e-17（`--check-seed`）。
- [与种子一致] 一条腿里只要有一只可交易股票的收益状态不是 `VALID` / `VALID_CONFIRMED_DELISTING_CASH`，该因子当周收益记为不可用。实例：2020-10-30 的 Quality、Investment。
- [M4 选择] 持有期结束日晚于 2020-12-31 的周（2020-12-24、2020-12-31）标为 `ENDPOINT_AFTER_WINDOW`，不计算。

## 3. Steps 17–19 — FMOM 与因子权重

- [SSOT 4.3 D] 只用已完成的周：E_{u+1} ≤ S_t。实际上最新能用的是两周前那期。
- [SSOT Step 17] 输入为毛多空收益；需要完整 J 周；缺失不补 0；任一因子缺失时，整个动态部分退回等权，原因记在 `dynamic_status`。
- [SSOT Steps 18–19] S = max(FMOM, 0)；w_FMOM = S/ΣS（全 ≤ 0 时为 1/K）；w = (1−λ)/K + λ·w_FMOM。网格 J ∈ {13, 26, 52}、λ ∈ {0, 0.5, 1}、风险调整开/关。
- [SSOT Step 17] 风险调整：FMOM / σ(最近 104 个完成周)，需要完整 104 周。
- [M4 选择] 窗口 = 最近 J 个**已完成的日历周**，不跳过缺失周去凑满 J 个有效周（按 SSOT 公式 LastJ(H_t) 的字面理解）。
- [M4 选择] σ 用样本标准差（ddof = 1）。
- [M4 选择] 种子止于 2015-12-31，2015 年 12 月下旬持有期跨进 2016 的几周，用 Step 16 代码补算（数据都在 2020 年以前），否则 2016 年初会连续 J 周退回等权。

## 4. Step 20 — 综合分

- [SSOT Step 20] Score = Σ w_k F_k，只算 eligible 股票。
- [SSOT Step 20，政策待定] 缺因子政策做成参数：`--min-factors 4`（完整因子）或 `3`（可用因子权重重新归一化：Σ_{可用} w_k F_k / Σ_{可用} w_k）。每周保存缺失诊断。
- [M4 选择] 被标为不可用的分数一律视为缺失，即使数值存在。

## 5. Step 21 — 选股

- [SSOT 4.7] N_target = max(1, floor(0.20·N))；排序：分数降序、PERMNO 升序；等权、只做多。
- [SSOT 4.7 算法 2] 行业上限：每个行业最多 floor(0.30·N_target) 只，从高分往下逐只加入，满了就跳过。填不满时剩余留现金，标记 `constraint_infeasible`，不放大权重。
- [SSOT Step 21 / 4.7] Buffer（可选）：新买入要进前 20%；持有的股票跌出前 30% 才卖。
- [M4 选择] N 的分母 = 当周**有综合分的股票数**（约 500），不是全部 eligible 股票（约 505）；与 Step 16 一致。
- [M4 选择] 没有行业分类的股票归入 `UNKNOWN`，单独受 30% 上限约束（基准数据里有 81 行）。
- [M4 选择] Buffer 下，保留的股票优先占用行业名额；新股按排名补足，前 20% 不够时（因为行业上限）再往下取。实际运行中从未发生。
- [SSOT 附加] 股票层等权是 SSOT 参数表里的 BASELINE；Top 20% 和 30% 行业上限是 CANDIDATE，需在 Gate B 确认。

## 6. 成本与净收益（`metrics.py`）

- [SSOT 4.4] GTR_t = Σ_i |w_target − w_pretrade|；单边换手 = GTR / 2；TC_t = c_oneway · GTR_t。
- [SSOT 4.4] 净收益用精确 NAV 公式：1 + R_net = (1 − TC_t)(1 + R_hold)，不是 R_gross − TC 的近似。
- [SSOT 4.4] w_pretrade = 上周目标权重按本周实际收益漂移后的权重。
- [SSOT Step 46] 成本分析：Base Cost、Double-Cost Stress（2 倍）、Break-Even Cost、Cost Drag。
- [与 Gate A 一致] Break-even 单边成本：解 Π(1 − c·GTR_t)(1 + R_t) = 1。
- [M4 选择，需全组冻结] **基准单边成本 10bp**，压力测试 20bp。课程和 SSOT 都没给具体数值（SSOT 参数表 Transaction Cost = TBD）；冻结前所有净结果都是 **PROVISIONAL**（SSOT 4.4 Cost-Parameter Status）。
- [M4 选择] GTR 只算证券、不算现金，所以第一周从现金建仓 GTR = 1（买入 100% 一次），计入成本。
- [M4 选择] 持仓里当周收益不可用的股票（不可交易或状态不合格），当周收益按 0 计，报告为 `avg_unpriced_weight`（实际平均约 0.017%）。
- [SSOT 4.7] 现金收益为 0%。

## 6b. 基准（SSOT Step 23）

- [SSOT Step 23] 市场基准：CRSP S&P 500 市值加权全收益（`benchmark_weekly_execution_returns`），对应总表的 `net_bench_*`、`net_active_*`、`net_information_ratio`、`net_beta` 列。
- [SSOT Step 23] 等权全样本基准：总表最后一行 `BENCH_EQUAL_WEIGHT_UNIVERSE`，每周把所有 eligible 股票等权持有、不用任何因子分数，调仓频率、成本、执行规则与策略完全相同。用来区分"等权 / 股票池效应"和"因子选股效应"。
- [SSOT Step 23] 静态多因子（λ=0）与动态 FMOM（λ=0.5）之间，除因子权重外的所有设定都相同。

## 7. 指标定义（`metrics.py`）

除特别说明外均用净收益（扣基准成本）序列计算，频率为**周度**。

| 指标 | 列名 | 定义 | 口径来源 |
|---|---|---|---|
| 年化收益 | `net_ann_return` | (V_T/V_0)^(52/N) − 1 | SSOT 4.4（日度用 252，见第 8 节） |
| 年化波动 | `net_ann_vol` | std(r)·√52 | SSOT 4.4（同上） |
| Sharpe | `net_sharpe` | mean(r − rf)/std(r − rf)·√52，rf 为 Fama-French | SSOT 4.4 |
| Sortino | `net_sortino` | mean(r − rf)/√E[min(r − rf, 0)²]·√52 | SSOT 4.4 |
| 最大回撤 | `net_max_drawdown` | max(1 − V_t/max V_s)，报**正数** | SSOT 4.4 |
| Calmar | `net_calmar` | 年化收益 / 最大回撤 | SSOT 4.4 |
| 周度胜率 | `net_period_hit_rate` | 收益 > 0 的周占比（Period Hit Rate） | SSOT 4.4 |
| 交易胜率 | `trade_win_rate` | 完整一买一卖交易的净收益 > 0 的占比（Trade Win Rate） | SSOT 4.4 |
| 跟踪误差 / IR | `net_tracking_error` / `net_information_ratio` | 超额 = r − 基准；std·√52；mean/std·√52 | SSOT 4.4 |
| 超额年化收益 | `net_active_ann_return` | mean(超额)·52（算术） | [M4 选择] |
| Beta | `net_beta` | cov(r, 基准)/var(基准) | [M4 选择]；正式回归见 SSOT Step 25 |
| 回撤次数 | `net_drawdown_episodes` | NAV 从高点跌下来又重新创新高，算一次；期末未恢复也算 | [M4 选择]，对应课程 "Drawdown Patterns" |
| 最长回撤 | `net_longest_drawdown_weeks` | 连续处于回撤中的最长周数 | [M4 选择] |
| 年换手 | `ann_turnover` | 平均单边周换手 × 52 | SSOT 4.4 / 4.10 第 9 条 |
| 成本拖累 | `cost_drag_ann` | 毛年化收益 − 净年化收益 | SSOT 4.10 第 10 条 |
| 完整交易数 | `n_round_trips` | 一只股票进入组合到离开组合 = 1 笔 | [M4 选择]，对应课程 "Number of Trades" |
| 买卖次数 | `n_trades_buy_plus_sell` | 每次买入 + 每次卖出 | [M4 选择] |
| 平均持有期 | `avg_holding_weeks` | 每笔交易连续持有的周数的平均 | [M4 选择]，对应课程 "Average Holding Period" |
| 每笔利润 | `avg_profit_per_trade` | (1 − c)²·Π(1 + r) − 1 的平均，单位为该仓位的百分比 | [M4 选择]，对应课程 "Profit per Trade" |

交易指标的 [M4 选择] 细节：
- 股票还在组合里、只是权重调整，不算新交易；
- 期末仍持有的交易，在期末截断计算；
- 每笔交易扣一次买入成本、一次卖出成本，持有期间再平衡的成本不分摊到单笔交易。

## 8. 与 SSOT 最终口径的偏离

- **[偏离] 周度对比日度**：SSOT 4.4 规定最终指标用**扣成本后的日度 NAV**，按 252 年化；周度序列只用于因子 / FMOM 研究，且不能在同一张表里混用日度和周度年化。目前 `run_gate_b.py` 的全部指标都是**周度**（按 52 年化），整张表口径一致，用于 Gate B 内部比较配置。**报告里的正式数字需由 M5 用日度 NAV、同一套公式复核**。
- **[偏离] 统计检验尚未实现**：SSOT 4.9 要求动态对比静态的差异要报 Newey-West t 值或 block-bootstrap 置信区间。目前只有点估计，后续在 Step 26 / Step 43 补上。

## 9. 待全组确认的清单

1. 基准交易成本数值（建议 US 10bp、压力 20bp；SG 另定）。
2. Top 20%、30% 行业上限是否作为基准；N 的分母；`UNKNOWN` 行业的处理。
3. 缺因子政策（min4 / min3）；是否用 Buffer。
4. FMOM 窗口遇到缺失周的处理（目前：不跳过，整周退回等权）。
5. 交易类指标的定义（第 7 节）。
6. 周度指标仅用于 Gate B 比较，最终由 M5 用日度 NAV 复核。

## 10. 冻结后的正式运行（Gate C）

- 冻结配置只放在 `config/strategy/us_base_strategy.json`；`run_frozen.py` 只读它，不做任何选择。
- `python -m src.strategy.run_frozen --period gate_b`：2016–2020 的 Gate C 验收运行，输出持仓、交易记录、权重、周度路径、`acceptance_test_report.csv`（SSOT 0.10）和 `run_manifest.csv`（SSOT 0.8）。
- `python -m src.strategy.run_frozen --period hist_oos`：2021–2025。`governance.py` 会检查 json 的 `status`、`freeze_date`、`approvers`、`transaction_cost.status`，任何一项没填好就拒绝运行。该模式不打印任何绩效数字。
- [M4 选择] 策略从 2016-01-01 连续运行，样本外第一周从 2020 年末的持仓调仓，而不是从现金建仓。
- 决策记录与签字表：`docs/GATE_B_C_US_BASE_DECISION.md`。
