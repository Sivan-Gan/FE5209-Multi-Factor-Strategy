# 老数据Top 4：组员接入与运行指南

**最终版本：1.0.0 / FINAL_100K_DIV14_V1。主场景固定初始资金10万新币，加入源分红，假设除权后14个自然日支付。** 运行入口会检查冻结参数；完整设定见 [FINAL_SPEC_ZH.md](FINAL_SPEC_ZH.md)。

## 1. 下载正确仓库与分支

仓库：`Sivan-Gan/FE5209-Multi-Factor-Strategy`。框架分支：`Backtest-Framework-Top4`，目录 `delivery/sg/top4_backtest/`。

Git用户：

```powershell
git clone --branch Backtest-Framework-Top4 --single-branch https://github.com/Sivan-Gan/FE5209-Multi-Factor-Strategy.git
cd FE5209-Multi-Factor-Strategy/delivery/sg/top4_backtest
```

也可以在GitHub切换到该分支后下载ZIP。进入本框架目录再运行以下命令，避免找不到sgbt模块。

## 2. 环境与不依赖市场数据的演示

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe examples/run_demo.py
```

依赖是numpy、pandas、scipy、openpyxl。openpyxl用于读取原包中的STI工作簿。无需券商或LSEG账号即可读取已有文件。使用虚拟环境的完整Python路径，不必激活PowerShell脚本。

演示使用虚构证券和人工日历，仅测试程序接入；结果不代表策略业绩。再次运行请换目录：

```powershell
.\.venv\Scripts\python.exe examples/run_demo.py --output runs/demo_second
```

## 3. 唯一需要的数据包

原 `SGX_comprehensive_2006_2026_handoff.zip`，也可为相同内容的 `(1).zip`。建议放在本机 `local_inputs/`。

策略重建只读取四个锁定数据成员：

1. `factor_panels/sg_seven_factor_candidate_panel_2006_latest.csv.gz`
2. `data/prices_daily_2006.csv`
3. `data/stock_universe_six_sectors.csv`
4. `benchmark/STI_Total_Return_LSEG_export.xlsx`

不会运行ZIP里的Python脚本，不会整包解压，也不需要 `SGX_TEAM_HANDOFF.zip` 或PIT替换包。每次运行把实际归档和四个成员的哈希写入本地 `INPUT_DATA_HASHES.json`，不将数据指纹发到公开仓库。若组内已有经确认的私有数据锁，可以加 `--data-lock local_inputs/DATA_LOCK.json`；只有提供预期锁时，数据内容不匹配才会停止。不要将“记录实际哈希”误认为已核实数据供应商或PIT。

另外从同一ZIP读取 `data/corporate_actions_2006.csv`，审查事件清单及价格一致性，并将选定期间、入选股票的现金分红转换为假定14日支付的事件。审查结果为 `corporate_action_readiness.json`、`corporate_action_events_AUDIT.csv` 和 `price_adjustment_steps_AUDIT.csv`。事件成员哈希仅记在本地。缺少或重复事件成员会停止；不会静默退回价格账户，也不再次应用源拆股事件。

## 4. 一次运行最终主场景与源周频对照

```powershell
.\.venv\Scripts\python.exe run_top4.py --archive local_inputs/SGX_comprehensive_2006_2026_handoff.zip --output runs/final_100k_div14_v1
```

数据包在其他位置时，直接把archive改为其绝对路径，有空格时加引号。输出目录必须不存在或为空，保留旧运行以便核对。

只核对原周频策略、不产生最终主场景：

```powershell
.\.venv\Scripts\python.exe run_top4.py --archive local_inputs/SGX_comprehensive_2006_2026_handoff.zip --output runs/top4_weekly_only --skip-execution
```

该入口重建Top3/Top4周频对照，执行唯一最终场景 `legacy_top4_frozen_100k_div14_final`。不运行144组搜索、不按收益选择模型。`configs/legacy_top4.json`、`configs/execution_settings.json` 与 `final_preset.py` 冻结本版规则；资金、分红延迟或执行参数被改动时会停止。

## 5. 输出在哪里、先看什么

| 文件 | 用途 |
|---|---|
| FINAL_RUN_SUMMARY.json | **最终主结果**：日期、资金、分红政策、估算收益、费用、成交完成比例 |
| run_manifest.json | 数据与策略版本、是否使用新数据、运行完成状态 |
| source_performance_summary.csv | 原函数口径的各段10/20/30bp表现 |
| source_reconciliation.json | 独立重建与原函数的收益、换手核对 |
| INPUT_DATA_HASHES.json | 本地输入指纹，可作为后续运行的私有data-lock |
| benchmark_summary.json | 同评估期STI全收益指标 |
| top4/weights.csv | 年度入选因子与逆波动权重 |
| top4/targets.csv | 可交给执行入口的完整目标持仓快照 |
| top4/source_weekly_path.csv | 原函数周收益路径 |
| top4/evaluation_cash_reset_10bp.csv | 2021现金重置后的周频参照路径 |
| execution_diagnostic_summary.csv | 唯一最终股数现金场景的汇总，含明确估算口径 |
| execution/场景名/ | 每日现金、股数、订单、代理成交、失败原因、贡献与风险诊断 |
| execution/legacy_top4_frozen_100k_div14_final/dividends_ASSUMED_INPUT.csv | 源分红转换后的除权日及假定支付日 |

`top3/` 同样提供原周频对照。主场景文件夹是 `execution/legacy_top4_frozen_100k_div14_final/`。检查每日 `accounting_error` 接近0、现金非负，先读audit.json中的收益口径。`events.csv` 可核对分红应收和支付，样本末尚未支付的分红保留应收。`_private_inputs/` 与全部真实运行结果不上传。

## 6. 组员已有其他持仓文件，怎样接入

标准目标格式：`signal_date,execution_date,ticker,weight`。本次目标沿用原`.SI`股票代码。每个日期提供完整目标快照，未列出的旧股票会成为卖出目标；全部清仓可以保留一行已知证券且权重0。

另准备日行情 `date,ticker,close,volume`、完整日历 `date`、证券主表 `ticker,currency`（可加six_sector）。量单位为股，时间统一为新加坡本地日期。至少提供20个执行日前的完整预热交易日，日历在最终估值日后保留结算缓冲交易日。

```powershell
.\.venv\Scripts\python.exe run_execution.py --prices local_inputs/prices.csv --sessions local_inputs/sessions.csv --targets local_inputs/targets.csv --security-master local_inputs/master.csv --settings configs/execution_settings.json --end 2026-09-21 --allow-price-only --output runs/custom_targets
```

日期end应按自己的数据范围填写。多策略文件需要指定 `--strategy`。本入口是冻结目标回放，不会因未成交而自行重新选因子或改变下一周保留缓冲；若要按真实持仓重算目标，应使用 `simulate(..., selector=...)` 的回调接口，并明确初始化及策略定义。

## 7. 费用与执行口径

源周频表以每边10bp为基准，另报告20/30bp。股数账户使用 `configs/execution_settings.json`：整手、最低佣金、市场费用、固定滑点、参与率、结算和现金预留均为可见情景假设，示例不含GST。两种成本模型不同，不能只比较两个年化数值就认定全部差额来自交易成本。

源成交量为0当天不代理成交并顺延，但零量可能是数据问题，不等于已确认停牌。全日成交量与收盘价也不能证明收盘竞价可完成订单。

最终股数账户计入源分红并假设14个自然日支付，标为 `SOURCE_DIVIDEND_14D_ESTIMATE_UNVERIFIED_PRICE_SHARE_BASIS`。除权日按此前实际持股确认应收，支付日不在代理日历时于下一代理交易日变成可用现金。源金额按SGD且不另扣税处理，历史股数单位沿用供应商口径，不重复拆股。以上是估算假设，不是已核实的真实支付日或完整总回报；不能直接与STI全收益作已验证的超额收益比较。不要为绕过检查将核验标志改为true。

## 8. 与组员交流时应附什么

至少附策略代码、原数据包版本、成本、样本区间、是否重置2021现金、是否冻结目标以及异常说明。不要将旧仓库老数据Top4、新数据sg_pipeline和另一仓库示例K=4混为同一个结果。

原始数据、真实绩效、数据指纹、完整持仓、订单及环境只保留本地（runs/local_inputs/.venv均忽略）；组内结果通过已有授权渠道交接。公开分支只提供代码、接入说明、合成例子和程序测试，不包含真实数据派生结果。

## 9. 公司行动与固定规模容量的补充检查

已经运行出目标文件与交易日历后：

```powershell
.\.venv\Scripts\python.exe run_evidence_checks.py --archive local_inputs/SGX_comprehensive_2006_2026_handoff.zip --targets runs/final_100k_div14_v1/top4/targets.csv --sessions runs/final_100k_div14_v1/sessions_PROXY.csv --output runs/evidence_first
```

该入口不重新选因子、不调整原策略。每个信号日分别把理想化旧目标组合按价格漂移后归一到10万/50万/100万，检查新目标调仓、从零建仓和全目标组合出售；另减半成交量，报告1/5/10/20日预算。它是独立截面的容量上限筛查，不是持仓连续递推的固定NAV收益回测。现金、交收、价差、排队和市场冲击未进入上限计算；建仓与清仓的成交预算对称，不代表真实买卖影响相同。样本末端没有足够观察窗口时单独列为截尾，不当成已完成。

实际交易只能知道当时信息。这里使用后续日成交量作事后诊断，不将其用于决定过去的信号。未来成交量未知，所以通过上限筛查也不能保证成交。源价格及成交股数的历史单位未核实时，上限也只能作为暂定诊断。

### 公司行动怎样才能正式接入

- 股数账户要求当时不复权成交价格和实际成交股数单位。复权收益可以用于研究收益，不能据其价格直接认定当时100股整手金额。
- 分红需核对除权日、支付日、币种、税前/税后金额和每股单位；除权确认应收，支付后才成为可用现金。已含分红的复权收益不能再加一次现金分红。
- `kind=split` 的 `value` 是新旧股数比例。若是送股，且新增股份晚于除权日可出售，填写经核实的 `share_trade_date`；新增部分在该日期前受出售限制。空值沿用即时可交易的普通拆股假设，只能用于已核实适用的事件。
- 零碎股、配股、分拆、合并换股、延迟合股及退市回收仍需明确事件处理，不能将“缺少记录”设为零。引擎对未支持事件拒绝执行。
- 不能因为源事件与复权因子吻合，就把完整覆盖、历史股数单位或PIT核验标志改成true。
