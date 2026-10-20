# 最终回测设定：FINAL_100K_DIV14_V1

2026-10-20按小组确认冻结。版本1.0.0。后续按此配置交付，不再根据收益表现调整资金或到账假设。

## 主场景

| 项目 | 最终设定 |
|---|---|
| 策略 | 原数据 Adaptive Top 4，TUNE_T4L3_IV_N20_EQ_NONE |
| 策略来源 | EricLu/sg-adaptive-top4，def5d53；原策略代码保持不变 |
| 数据 | 原SGX_comprehensive_2006_2026_handoff.zip |
| 初始资金 | SGD 100,000，2021评估期现金重置，之后自然复利 |
| 目标 | 冻结源策略目标，逐日递推实际股数、现金、应收与订单 |
| 分红 | 使用原包cash_dividend事件和原每股金额 |
| 支付日期 | 除权日加14个自然日，属于工作假设 |
| 非交易日支付 | 第一个不早于假定支付日的代理交易日才可用于交易 |
| 权益确认 | 按除权前实际持股确认应收；卖出后仍保留应收权益 |
| 股数/币种假设 | 保留源价格与分红的供应商股数单位，金额按SGD且不另扣税处理；未认证为真实历史口径 |
| 拆股事件 | 不再次应用源拆股表，避免与已有历史价格调整重复计算 |
| 整手/现金预留 | 100股；1% |
| 参与率/订单期限 | 同时受日量10%和滞后20日ADV的10%限制；5个交易日 |
| 佣金/市场费用 | 每边5bp、最低SGD5；市场费用4bp；费用税率0 |
| 固定滑点/冲击 | 10bp；规模冲击系数0，未进行实证校准 |
| 交收 | 卖出款T+2到账；买入预付；到账前股份出售受限 |
| 主结果标识 | SOURCE_DIVIDEND_14D_ESTIMATE_UNVERIFIED_PRICE_SHARE_BASIS |

## 运行与输出

```powershell
.\.venv\Scripts\python.exe run_top4.py --archive local_inputs/SGX_comprehensive_2006_2026_handoff.zip --output runs/final_100k_div14_v1
```

先看根目录 `FINAL_RUN_SUMMARY.json`。逐日详情在 `execution/legacy_top4_frozen_100k_div14_final/`，包括 `daily.csv`、`holdings.csv`、`orders.csv`、`fills.csv`、`events.csv`、`contributions.csv`、`dividends_ASSUMED_INPUT.csv` 与 `audit.json`。源分红表缺失或选定事件日期不在代理日历时停止，不自动退回不含分红版本。

仍输出原Top3/Top4的周频研究对照；其复权收益不再加现金分红。该对照与最终股数现金账户口径不同，汇报主场景请读取 `FINAL_RUN_SUMMARY.json`，不要混用。

最终入口检查资金、分红政策及执行设置是否与冻结设定一致；参数不匹配会停止。`--skip-execution`只用于源策略核对，不产生最终主场景结果。通用 `run_execution.py` 和容量诊断入口属于辅助工具，不改变本次主场景。

## 结果应怎样表述

本版是固定假设下、计入源分红的课程研究回测。14日支付是小组选择的假设，不是逐笔真实支付日；历史价格/成交股数单位、分红币种税项、PIT、股票池和真实市场冲击的限制继续保留。不要将核验标志改为true，不将本版称为已验证的实盘总回报。

公开交付包含代码、最终配置、接入说明及合成测试。源行情、真实绩效、分红明细、持仓、订单和私有数据指纹由组员在本地生成。
