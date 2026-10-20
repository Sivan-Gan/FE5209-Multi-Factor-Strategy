# 老数据版新加坡 Adaptive Top 4 回测框架

## 最终定稿：10万新币＋源分红假定14天到账

版本 **1.0.0 / FINAL_100K_DIV14_V1**。2026-10-20按小组决定冻结：初始资金SGD100,000，计入原包分红，假设除权后14个自然日支付；到首个不早于支付日的代理交易日才可交易。其余成本和执行规则保持原设定。完整口径见 [最终设定](FINAL_SPEC_ZH.md)。

## 先确认：这次采用哪条策略路线

**采用：** `src/sandbox/sg_tune_lab.py` 的 `TUNE_T4L3_IV_N20_EQ_NONE`。它在 `run_sg_all_strategies.py` 中标记为推荐Top 4，使用原有 `SGX_comprehensive_2006_2026_handoff.zip`。

**不需要新数据包。** 本入口不运行 `src/sg_pipeline`，不使用 `SGX_TEAM_HANDOFF.zip`，也不引入PIT替换包。原压缩包名字虽含七因子面板，策略仍只使用原六个因子，排除Dividend。

| 内容 | 入口 |
|---|---|
| 最终资金、分红和执行设定 | [最终冻结说明](FINAL_SPEC_ZH.md) |
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
.\.venv\Scripts\python.exe run_top4.py --archive local_inputs/SGX_comprehensive_2006_2026_handoff.zip --output runs/final_100k_div14_v1
```

这会重建原Top3/Top4周频对照、核对收益及换手，并运行唯一最终股数现金主场景：Top4、10万新币、分红假定14日到账。先看 `FINAL_RUN_SUMMARY.json`；明细位于 `execution/legacy_top4_frozen_100k_div14_final/`。不执行144组寻优，不执行ZIP脚本。

最终主场景标为 `SOURCE_DIVIDEND_14D_ESTIMATE_UNVERIFIED_PRICE_SHARE_BASIS`，是冻结假设下的含分红估算。实际支付日、历史价格/股数单位及PIT等限制仍保留。原周频对照沿用复权收益，不再加分红，不与最终现金账户混用。

公司行动审查与 `run_evidence_checks.py` 容量筛查继续作为辅助工具。最终主场景按已确认工作假设使用源现金分红；不再次应用源拆股表。通用引擎仍支持有核实资料时提供送股可卖出日期。

本目录新增于老仓库 `Backtest-Framework-Top4` 分支，基于 `EricLu/sg-adaptive-top4` 的 `def5d53`。原策略代码未修改。公开内容仅为代码、说明、合成演示与程序测试；真实数据的绩效汇总、数据指纹、持仓和订单保留在本地。
