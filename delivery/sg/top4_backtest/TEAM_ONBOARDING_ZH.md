# 老数据Top 4：组员接入与运行指南

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

程序只读取四个锁定数据成员：

1. `factor_panels/sg_seven_factor_candidate_panel_2006_latest.csv.gz`
2. `data/prices_daily_2006.csv`
3. `data/stock_universe_six_sectors.csv`
4. `benchmark/STI_Total_Return_LSEG_export.xlsx`

不会运行ZIP里的Python脚本，不会整包解压，也不需要 `SGX_TEAM_HANDOFF.zip` 或PIT替换包。每次运行把实际归档和四个成员的哈希写入本地 `INPUT_DATA_HASHES.json`，不将数据指纹发到公开仓库。若组内已有经确认的私有数据锁，可以加 `--data-lock local_inputs/DATA_LOCK.json`；只有提供预期锁时，数据内容不匹配才会停止。不要将“记录实际哈希”误认为已核实数据供应商或PIT。

## 4. 一次运行原Top 3对照与正式老数据Top 4

```powershell
.\.venv\Scripts\python.exe run_top4.py --archive local_inputs/SGX_comprehensive_2006_2026_handoff.zip --output runs/top4_first_run
```

数据包在其他位置时，直接把archive改为其绝对路径，有空格时加引号。输出目录必须不存在或为空，保留旧运行以便核对。

只重建周频结果、不跑执行诊断：

```powershell
.\.venv\Scripts\python.exe run_top4.py --archive local_inputs/SGX_comprehensive_2006_2026_handoff.zip --output runs/top4_weekly_only --skip-execution
```

该入口执行Top3/Top4两个固定策略，不运行144组搜索、不自动选择最高收益模型。配置 `configs/legacy_top4.json` 锁定原规则；可以调整execution_scenarios中的初始资金，不能仅改一个标签就把其他策略冒充该版本。

## 5. 输出在哪里、先看什么

| 文件 | 用途 |
|---|---|
| run_manifest.json | 数据与策略版本、是否使用新数据、运行完成状态 |
| source_performance_summary.csv | 原函数口径的各段10/20/30bp表现 |
| source_reconciliation.json | 独立重建与原函数的收益、换手核对 |
| INPUT_DATA_HASHES.json | 本地输入指纹，可作为后续运行的私有data-lock |
| benchmark_summary.json | 同评估期STI全收益指标 |
| top4/weights.csv | 年度入选因子与逆波动权重 |
| top4/targets.csv | 可交给执行入口的完整目标持仓快照 |
| top4/source_weekly_path.csv | 原函数周收益路径 |
| top4/evaluation_cash_reset_10bp.csv | 2021现金重置后的周频参照路径 |
| execution_diagnostic_summary.csv | 三个股数现金场景的汇总 |
| execution/场景名/ | 每日现金、股数、订单、代理成交、失败原因、贡献与风险诊断 |

`top3/` 同样提供对照。`_private_inputs/` 是那四个受控提取的数据成员，不应上传。日账户检查 `accounting_error` 接近0、现金非负，并先读audit.json中的收益口径。

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

当前股数账户没有已核实完整分红拆股与价格口径，标为 `PRICE_ONLY_UNVERIFIED_ACTIONS_NOT_TOTAL_RETURN`。它不能与STI全收益直接比较。若以后补齐公司行动、历史规则和PIT证据，可使用templates和run_execution的严格入口；不要仅为绕过检查将核验标志改为true。

## 8. 与组员交流时应附什么

至少附策略代码、原数据包版本、成本、样本区间、是否重置2021现金、是否冻结目标以及异常说明。不要将旧仓库老数据Top4、新数据sg_pipeline和另一仓库示例K=4混为同一个结果。

原始数据、真实绩效、数据指纹、完整持仓、订单及环境只保留本地（runs/local_inputs/.venv均忽略）；组内结果通过已有授权渠道交接。公开分支只提供代码、接入说明、合成例子和程序测试，不包含真实数据派生结果。
