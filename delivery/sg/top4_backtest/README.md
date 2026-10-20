# 老数据版新加坡 Adaptive Top 4 回测框架

## 先确认：这次采用哪条策略路线

**采用：** `src/sandbox/sg_tune_lab.py` 的 `TUNE_T4L3_IV_N20_EQ_NONE`。它在 `run_sg_all_strategies.py` 中标记为推荐Top 4，使用原有 `SGX_comprehensive_2006_2026_handoff.zip`。

**不需要新数据包。** 本入口不运行 `src/sg_pipeline`，不使用 `SGX_TEAM_HANDOFF.zip`，也不引入PIT替换包。原压缩包名字虽含七因子面板，策略仍只使用原六个因子，排除Dividend。

| 内容 | 入口 |
|---|---|
| 老数据Top 4定位与规则差异 | [策略路线核查](STRATEGY_ROUTE_ZH.md) |
| 安装、输入、运行、结果解读 | [组员接入指南](TEAM_ONBOARDING_ZH.md) |
| 程序验证及公开范围 | [验证说明](VALIDATION_ZH.md) |
| 已审查的公开源码版本 | `SOURCE_LOCK.json` |

## 快速运行

以下在 `delivery/sg/top4_backtest/` 目录执行，使用Python 3.12：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe examples/run_demo.py
```

将原数据包放到本机 `local_inputs/` 后：

```powershell
.\.venv\Scripts\python.exe run_top4.py --archive local_inputs/SGX_comprehensive_2006_2026_handoff.zip --output runs/top4_first_run
```

这会单独重建原Top 3和推荐Top 4、核对收益及换手，并运行三个逐日股数现金诊断场景。不会执行144组寻优，也不会运行压缩包里的脚本。结果只写入新的本地输出目录。

回测完成不等于能够真实成交。原周频回测沿用复权收益与10/20/30bp成本；日频股数账户目前是价格执行诊断，尚未核实完整分红、拆股、价格口径与PIT。

0.3版补充公司行动与价格口径自动核查，以及 `run_evidence_checks.py` 的固定净资产容量上限筛查。事件表存在不等于完整可入账；原包的 `close` 不能直接认定为不复权成交价。未核实到账日、分红币种/股数单位前，不自动将源事件计入净值。新增送股可卖出日期字段，支持除权后先计值、到账前限制出售新增股份。

本目录新增于老仓库 `Backtest-Framework-Top4` 分支，基于 `EricLu/sg-adaptive-top4` 的 `def5d53`。原策略代码未修改。公开内容仅为代码、说明、合成演示与程序测试；真实数据的绩效汇总、数据指纹、持仓和订单保留在本地。
