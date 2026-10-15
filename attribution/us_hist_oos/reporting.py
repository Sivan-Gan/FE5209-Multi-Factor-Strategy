"""Portable Chinese report and static, exportable scientific plots."""
from __future__ import annotations

import base64
from html import escape
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
import numpy as np
import pandas as pd


COLORS = {'Dynamic':'#176b75', 'Static':'#d08a38', 'Negative':'#bd4854', 'Positive':'#267f75'}


def pct(value, digits=2):
    return f'{float(value)*100:.{digits}f}%'


def pp(value, digits=2):
    return f'{float(value)*100:+.{digits}f}'


def pretty(df, columns, percent=(), point=(), decimals=3):
    x = df[list(columns)].copy()
    for c in x:
        if c in percent:
            x[c] = x[c].map(lambda v: pct(v) if pd.notna(v) else '')
        elif c in point:
            x[c] = x[c].map(lambda v: pp(v) if pd.notna(v) else '')
        elif pd.api.types.is_float_dtype(x[c]):
            x[c] = x[c].map(lambda v: f'{v:.{decimals}f}' if pd.notna(v) else '')
        x[c] = x[c].fillna('').astype(str)
    return x.rename(columns=columns)


def markdown_table(df):
    rows = [list(df.columns)]+df.astype(str).values.tolist()
    rows = [[str(c).replace('|','/').replace('\n',' ') for c in r] for r in rows]
    return '\n'.join(['| '+' | '.join(rows[0])+' |', '| '+' | '.join(['---']*len(rows[0]))+' |']+
                     ['| '+' | '.join(r)+' |' for r in rows[1:]])


def stock_labels(df):
    return [str(r.ticker) if pd.notna(r.ticker) and str(r.ticker) else str(r.PERMNO) for r in df.itertuples()]


def figures(a):
    output = a.out/'figures'
    output.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family':'DejaVu Sans', 'font.size':10,
                         'axes.spines.top':False, 'axes.spines.right':False,
                         'axes.labelcolor':'#263742', 'text.color':'#263742',
                         'axes.titlesize':13, 'figure.facecolor':'white', 'axes.facecolor':'white',
                         'savefig.facecolor':'white', 'svg.fonttype':'none'})
    result = []

    def save(fig, name, title, note):
        fig.tight_layout()
        fig.savefig(output/f'{name}.png', dpi=180, bbox_inches='tight')
        fig.savefig(output/f'{name}.svg', bbox_inches='tight')
        plt.close(fig)
        result.append(dict(file=f'figures/{name}.png', title=title, note=note))

    fig, ax = plt.subplots(figsize=(10.8,4.6))
    for strategy, p in a.path.groupby('strategy'):
        dates = pd.to_datetime([p.execution_date.iloc[0]]+p.period_end.tolist())
        ax.plot(dates, np.r_[1.,p.net_nav], label=strategy, color=COLORS[strategy], linewidth=2)
    b = a.benchmark.set_index('signal_date').reindex(a.dates)
    dates = pd.to_datetime([a.calendar.execution_date.iloc[0]]+a.calendar.next_execution_date.tolist())
    ax.plot(dates, np.r_[1.,np.cumprod(1+b.benchmark_total_return)], label='S&P 500 total return', color='#82919c', linewidth=1.4)
    ax.set(title='Existing weekly net wealth paths | 259 completed periods', ylabel='Wealth per unit of initial capital')
    ax.grid(axis='y', alpha=.18)
    ax.legend(frameon=False, ncol=3, loc='upper left')
    save(fig,'01_net_wealth','净值路径复核','初始资本归一化为 1；使用既有 259 周收益，未重建策略。')

    sec = a.tables['sector_contributors_full_period']
    wide = sec.pivot(index='sector_name',columns='strategy',values='net_linked').fillna(0)
    wide = wide.loc[~wide.index.isin(['Cash','Unknown'])].sort_values('Dynamic')
    fig, ax = plt.subplots(figsize=(10.8,5.6))
    y = np.arange(len(wide))
    ax.barh(y-.18,wide.Dynamic*100,height=.34,color=COLORS['Dynamic'],label='Dynamic')
    ax.barh(y+.18,wide.Static*100,height=.34,color=COLORS['Static'],label='Static')
    ax.set_yticks(y,wide.index)
    ax.axvline(0,color='#82919c',linewidth=.7)
    ax.set(title='Sector contributions to cumulative net wealth',xlabel='Percentage points of initial capital')
    ax.legend(frameon=False)
    ax.grid(axis='x',alpha=.15)
    save(fig,'02_sector_contributors','行业累计净贡献','各策略按自己的净值路径连接；加上现金／未知分类后分别加总至累计净收益，不是 CAGR 的分解。')

    stocks = a.tables['stock_contributors_full_period']
    d = stocks[(stocks.strategy == 'Dynamic') & stocks.PERMNO.ne('CASH')]
    selected = pd.concat([d.nsmallest(10,'net_linked'),d.nlargest(10,'net_linked')]).sort_values('net_linked')
    fig, ax = plt.subplots(figsize=(10.8,7.2))
    ax.barh(np.arange(len(selected)),selected.net_linked*100,
            color=np.where(selected.net_linked>=0,COLORS['Positive'],COLORS['Negative']))
    ax.set_yticks(np.arange(len(selected)),stock_labels(selected))
    ax.axvline(0,color='#82919c',linewidth=.7)
    ax.set(title='Dynamic | ten largest contributors and detractors',xlabel='Net wealth contribution | pp of initial capital')
    ax.grid(axis='x',alpha=.15)
    save(fig,'03_stock_contributors','动态策略个股贡献','名称为样本内最后一次观察到的有效历史名称；完整表保留 PERMNO 与名称日期。')

    paired = a.tables['dynamic_minus_static_by_signal_year']
    fig, ax = plt.subplots(figsize=(10.8,4.8))
    x = np.arange(len(paired))
    g = paired.gross_increment_arithmetic_sum*100
    c = paired.exact_cost_effect_arithmetic_sum*100
    ax.bar(x-.2,g,width=.25,label='Gross increment',color='#5598a1')
    ax.bar(x+.05,c,width=.25,label='Exact cost effect',color='#bd4854')
    ax.plot(x,paired.net_increment_arithmetic_sum*100,'o-',color='#263742',label='Net increment')
    ax.set_xticks(x,paired.signal_year.astype(str))
    ax.axhline(0,color='#82919c',linewidth=.7)
    ax.set(title='Dynamic minus Static | gross and cost bridge by signal year',ylabel='Sum of weekly increments | pp')
    ax.legend(frameon=False,ncol=3)
    ax.grid(axis='y',alpha=.15)
    save(fig,'04_timing_cost_bridge','FMOM 规则增量与成本','逐周差额的算术合计；按信号年份归组，不是两策略复合年收益的差。')

    dd = a.tables['drawdown_sector_contributors']
    dd = dd[(dd.strategy=='Dynamic') & dd.sector.ne('CASH')].sort_values('drawdown_net_linked')
    fig, ax = plt.subplots(figsize=(10.8,5.4))
    ax.barh(np.arange(len(dd)),dd.drawdown_net_linked*100,
            color=np.where(dd.drawdown_net_linked>=0,COLORS['Positive'],COLORS['Negative']))
    ax.set_yticks(np.arange(len(dd)),dd.sector_name)
    ax.axvline(0,color='#82919c',linewidth=.7)
    ax.set(title='Dynamic maximum drawdown | 2021-12-27 to 2023-10-30',xlabel='Net contribution | pp of peak wealth')
    ax.grid(axis='x',alpha=.15)
    save(fig,'05_drawdown_contributors','最大回撤的行业贡献','以动态策略回撤起点资本重新归一化，各项加总至 −21.17% 的峰谷回撤。')

    w = a.weights.pivot(index='signal_date',columns='family',values='weight')[['Quality','Momentum','Low Risk','Investment']]
    fig, ax = plt.subplots(figsize=(10.8,4.4))
    ax.stackplot(pd.to_datetime(w.index),w.T.to_numpy(),labels=w.columns,
                 colors=['#3a768b','#71aaa0','#d9b65e','#b37875'],alpha=.95)
    ax.yaxis.set_major_formatter(PercentFormatter(1))
    ax.set(ylim=(0,1),title='Frozen factor weights | 259 OOS signal weeks',ylabel='Composite-score weight')
    ax.legend(frameon=False,ncol=4,loc='upper center',bbox_to_anchor=(.5,-.12))
    save(fig,'06_factor_weights','冻结因子权重轨迹','这是选股评分的输入权重；不代表四个因子分别占有这些比例的策略损益。')

    timing = a.tables['factor_timing_family_summary'].sort_values('diagnostic_annual_mean_contribution')
    fig, axes = plt.subplots(1,2,figsize=(11.4,4.4))
    axes[0].barh(timing.family,timing.diagnostic_annual_mean_contribution*100,
                 color=np.where(timing.diagnostic_annual_mean_contribution>=0,COLORS['Positive'],COLORS['Negative']))
    axes[0].axvline(0,color='#82919c',linewidth=.7)
    axes[0].set(title='Factor-mimicking timing diagnostic',xlabel='Annualized arithmetic contribution | pp')
    ex = a.tables['holdings_factor_score_exposure_summary'].pivot(index='family',columns='strategy',values='mean_holdings_weighted_score')
    yy = np.arange(len(ex))
    axes[1].barh(yy-.17,ex.Dynamic,height=.32,label='Dynamic',color=COLORS['Dynamic'])
    axes[1].barh(yy+.17,ex.Static,height=.32,label='Static',color=COLORS['Static'])
    axes[1].set_yticks(yy,ex.index)
    axes[1].set(title='Realized holdings score exposure',xlabel='Mean holdings-weighted standardized score')
    axes[1].legend(frameon=False)
    save(fig,'07_factor_diagnostics','因子层诊断与组合暴露','左图仅用四因子均可用的 256 周，属于因子模拟组合诊断；右图是实际持仓暴露，两者均不可与股票贡献横向相加。')

    reg = a.tables['regression_mean_return_explanation']
    reg = reg[reg.model.eq('Dynamic FMOM')]
    fig, ax = plt.subplots(figsize=(10.8,4.7))
    vals = reg.set_index('component').annualized_arithmetic_mean_contribution.reindex(['RF','Mkt-RF','SMB','HML','RMW','CMA','Mom','Alpha','Residual'])
    ax.bar(vals.index,vals*100,color=np.where(vals>=0,COLORS['Positive'],COLORS['Negative']))
    ax.axhline(0,color='#82919c',linewidth=.7)
    ax.set(title='Dynamic | FF6 model explanation of mean net return',ylabel='Annualized arithmetic mean component | pp')
    ax.grid(axis='y',alpha=.15)
    save(fig,'08_regression_view','FF6 回归解释视图','完全复用 M5 的执行区间聚合与 HAC(4) 口径；这是事后模型解释，不是四个选股因子的实际损益分摊。')
    return result


def build_report(a):
    figs = figures(a)
    tabs = a.tables
    perf = tabs['performance_reconciliation'].set_index('strategy')
    d, s = perf.loc['Dynamic'], perf.loc['Static']
    inc = tabs['timing_increment_summary'].iloc[0]
    stocks = tabs['stock_contributors_full_period']
    dynamic_stocks = stocks[(stocks.strategy=='Dynamic') & stocks.PERMNO.ne('CASH')]
    top = dynamic_stocks.nlargest(10,'net_linked')
    bottom = dynamic_stocks.nsmallest(10,'net_linked')
    sec = tabs['sector_contributors_full_period']
    sec_gap = tabs['dynamic_minus_static_sector_summary'].sort_values('terminal_net_wealth_gap')
    stock_gap = tabs['dynamic_minus_static_stock_summary']
    dd = tabs['max_drawdown_events']
    factor = tabs['factor_timing_predictive_summary'].iloc[0]
    unknown = a.stock[a.stock.sector.eq('UNKNOWN')]
    qa = tabs['qa_checks']
    max_resid = pd.to_numeric(qa.metric,errors='coerce').max()
    sections = []

    def add(title, text, tables=(), figure_ids=()):
        sections.append(dict(title=title,text=text,tables=list(tables),figures=list(figure_ids)))

    table_perf = pretty(perf.reset_index(), {'strategy':'策略','cumulative_net_return':'累计净收益',
                         'cagr_net':'CAGR（52周年化）','sharpe':'Sharpe','max_drawdown':'最大回撤','annual_turnover':'年换手'},
                        percent=['cumulative_net_return','cagr_net','max_drawdown'])
    add('1. 核心结论与结果边界',
        f'本报告基于 member5-risk-analysis 分支已提交的回测结果，分析 2021-01-08 至 2025-12-19 的 259 个信号周；'
        f'持有期在 2021-01-11 至 2025-12-29 之间。最后一个跨入 2026 年的信号周未纳入。策略配置、持仓和交易均沿用 M4/M5，'
        f'本工作只做解释、核对与会计分解。\n\n'
        f'动态策略累计净收益为 {pct(d.cumulative_net_return)}，静态策略为 {pct(s.cumulative_net_return)}；'
        f'每投入 1 单位起始资本，动态策略期末少 {abs(inc.terminal_net_wealth_gap):.4f} 单位。'
        f'对应的 CAGR 分别为 {pct(d.cagr_net)} 与 {pct(s.cagr_net)}，CAGR 差额为 {pp(d.cagr_net-s.cagr_net)} 个百分点。\n\n'
        f'逐周净收益差的年化算术平均为 {pp(inc.mean_annualized)} 个百分点，'
        f'由毛收益差 {pp(inc.gross_mean_annualized)} 与精确成本影响 {pp(inc.annualized_exact_cost_effect)} 个百分点闭合。'
        f'HAC(4) p 值为 {inc.hac_p:.4f}，95% 区间为 [{pp(inc.ci_low_ann)}, {pp(inc.ci_high_ann)}] 个百分点；'
        f'样本点估计为负，不能据此认定总体增量在 5% 水平上显著为负。\n\n'
        '正式解释限定在既有周度回测口径。M5 已披露的日频 NAV／周频 NAV 口径确认、最终团队签字和其他未关闭审计，仍属于上游待确认事项。',
        [table_perf],[0])

    add('2. 归因口径与单位',
        '每个信号日 S_t 对应下一交易日收盘 E_t 执行，个股持有期收益覆盖 (E_t, E_{t+1}]。'
        '股票用 PERMNO 关联，行业直接使用冻结持仓中的历史 GICS 并与同信号日股票池核对。现金收益为零。\n\n'
        '逐周毛贡献：c_gross(i,t) = w(i,t) × r(i,t)。成本以执行前 NAV 为基数，TC_t 等于交易账本各股票成本之和；'
        '净收益严格按 R_net = (1−TC_t)(1+R_gross)−1 复核。\n\n'
        '本报告逐股票净贡献采用“先扣成本再持有”的账本约定：'
        'c_net(i,t) = (1−TC_t)c_gross(i,t) − cost(i,t)。卖空仓位为零但发生卖出费用的证券也保留在表中。'
        '累计净贡献 = Σ_t NAV_net(t−1)c_net(i,t)，以起始资本为 1，所有股票与现金加总等于累计净收益。'
        '行业贡献是同一组股票贡献的聚合，两者不能重复相加。\n\n'
        '报告中的“累计贡献百分点”指起始资本的百分比贡献，不是 CAGR 分解。年度表按 M5 的信号年份分组并在每年起点重新归一化，'
        '不是严格日历年收益。静态／动态收益差、因子模拟组合诊断、FF6 回归解释是不同视图，不能横向相加。'
        '美股均使用 USD，不增加独立的外汇换算贡献。')

    stock_cols = {'PERMNO':'PERMNO','ticker':'历史代码','issuer':'历史公司名','holding_weeks':'持有周数',
                  'net_linked':'累计净贡献（起始资本百分点）','name_as_of':'名称日期'}
    add('3. 个股 contributors 与 detractors',
        f'动态策略最大的正贡献证券是 {top.iloc[0].ticker}（{pp(top.iloc[0].net_linked)} 个起始资本百分点），'
        f'最大的负贡献证券是 {bottom.iloc[0].ticker}（{pp(bottom.iloc[0].net_linked)} 个百分点）。'
        '以下排名包含持有损益、扣成本后的资金影响及该股票发生的交易费用，不按单只股票原始涨跌幅排名。'
        '名称只作展示，证券身份始终由 PERMNO 决定，名称日期列表示样本内最后有效观察日。完整表还包括静态策略及所有证券。',
        [pretty(top,stock_cols,point=['net_linked']),pretty(bottom,stock_cols,point=['net_linked'])],[2])

    dsec = sec[(sec.strategy=='Dynamic') & sec.sector.ne('CASH')].sort_values('net_linked',ascending=False)
    add('4. 行业贡献与相对静态策略差额',
        '先看动态策略本身的行业累计贡献，再看动态减静态的期末财富差额。后者把两套策略各自的净值路径连接后做差，'
        f'全行业加总为 {pp(inc.terminal_net_wealth_gap)} 个起始资本百分点。主要负差额来自 '
        f'{", ".join(sec_gap.head(3).sector_name)}；Information Technology 的差额为正。'
        '正差额表示动态相对静态在该行业留下更多期末财富，并不表示该行业自身的股票都上涨。\n\n'
        f'有 {len(unknown)} 个持仓／交易证券周缺少可核实的同信号日行业分类，保留 UNKNOWN 分类；'
        f'其累计直接成本贡献为 {pp(unknown.direct_cost_linked.sum(),4)} 个起始资本百分点。'
        '不使用当前行业或凭公司名补填。行业完整闭合包括 UNKNOWN 与现金。'
        '当前数据没有 S&P 500 成分股权重与行业收益接口，因此不把这些结果标为相对指数的 Brinson 配置／选择归因。',
        [pretty(dsec,{'sector_name':'动态策略行业','mean_weight_all_259_weeks':'全期平均权重','net_linked':'累计净贡献（百分点）'},
                percent=['mean_weight_all_259_weeks'],point=['net_linked']),
         pretty(sec_gap,{'sector_name':'行业','terminal_net_wealth_gap':'动态−静态期末财富差（百分点）'},point=['terminal_net_wealth_gap'])],[1])

    gapcols = {'ticker':'历史代码','issuer':'历史公司名','terminal_net_wealth_gap':'动态−静态期末财富差（百分点）'}
    cost_d, cost_s = d.terminal_gross_minus_net,s.terminal_gross_minus_net
    bridge = pd.DataFrame([
        {'item':'毛收益路径的期末财富差','value':inc.terminal_gross_wealth_gap},
        {'item':'动态额外成本的期末财富影响','value':-(cost_d-cost_s)},
        {'item':'净收益路径的期末财富差','value':inc.terminal_net_wealth_gap},
    ])
    yearly = tabs['dynamic_minus_static_by_signal_year']
    add('5. FMOM 规则增量：选股结果与成本',
        f'两套策略的平均持仓重合率为 {pct(inc.mean_overlap)}，Jaccard 为 {pct(inc.mean_jaccard)}。'
        '因此最终股票组合的增量应从共同持仓权重变化、动态独有持仓、静态独有持仓及卖出费用来解释，'
        '不能只用四个因子权重乘多空因子收益来代替。\n\n'
        f'2025 信号年是差额最差的一年：周度毛差额合计 {pp(yearly.iloc[-1].gross_increment_arithmetic_sum)} 个百分点，'
        f'成本影响 {pp(yearly.iloc[-1].exact_cost_effect_arithmetic_sum)} 个百分点，净差额合计 '
        f'{pp(yearly.iloc[-1].net_increment_arithmetic_sum)} 个百分点。'
        '这些是周度差额的算术合计，与两条年度复合收益之差不同。\n\n'
        f'整个样本的期末财富桥为：毛路径差 {pp(inc.terminal_gross_wealth_gap)}，额外成本影响 '
        f'{pp(-(cost_d-cost_s))}，合计净路径差 {pp(inc.terminal_net_wealth_gap)} 个起始资本百分点。'
        '该全期财富桥与前述年化平均差额使用不同单位，分别闭合。',
        [pretty(yearly,{'signal_year':'信号年份','n_weeks':'周数','dynamic_net_compounded':'动态复合净收益',
                        'static_net_compounded':'静态复合净收益','gross_increment_arithmetic_sum':'毛差额合计（百分点）',
                        'exact_cost_effect_arithmetic_sum':'成本影响（百分点）','net_increment_arithmetic_sum':'净差额合计（百分点）'},
                percent=['dynamic_net_compounded','static_net_compounded'],
                point=['gross_increment_arithmetic_sum','exact_cost_effect_arithmetic_sum','net_increment_arithmetic_sum']),
         pretty(bridge,{'item':'全期财富桥','value':'起始资本百分点'},point=['value']),
         pretty(stock_gap.nsmallest(10,'terminal_net_wealth_gap'),gapcols,point=['terminal_net_wealth_gap']),
         pretty(stock_gap.nlargest(10,'terminal_net_wealth_gap'),gapcols,point=['terminal_net_wealth_gap'])],[3])

    costs = pretty(perf.reset_index(),{'strategy':'策略','paid_cost_per_initial_capital':'累计支付费用／初始资本',
                    'terminal_gross_minus_net':'成本导致的期末财富差／初始资本','annual_turnover':'年换手'},
                   percent=['paid_cost_per_initial_capital','terminal_gross_minus_net'])
    add('6. 成本归因的精确闭合',
        '“累计支付费用”是每周执行前净值乘 TC_t 后相加；“期末财富损失”还包括这些资金本可获得的后续收益，'
        '两者经济含义不同。期末毛／净财富差采用逐期精确递推计算，不能简单用所有周的成本率之和代替。'
        '两套策略均沿用单边 10bp，成本差来自既有交易路径与换手。归因没有重新选择成本参数。', [costs])

    dds = tabs['drawdown_sector_contributors']
    ddstk = tabs['drawdown_stock_contributors']
    add('7. 最大回撤的贡献来源',
        '对每套策略各自的峰谷区间，以峰值资本重新归一化，逐周连接净贡献。'
        '只计峰值之后、谷值当期结束之前的持有期，不把到达峰值的前一期收益放进回撤。'
        '动态策略的最大回撤区间较静态策略长，因此两者各自回撤贡献表不用于直接比较同一市场窗口。',
        [pretty(dd,{'strategy':'策略','peak_date':'峰值日','trough_date':'谷值日','recovery_date':'恢复日',
                    'peak_to_trough_weeks':'峰谷周数','signed_drawdown_return':'峰谷收益'},percent=['signed_drawdown_return']),
         pretty(dds[dds.strategy=='Dynamic'].sort_values('drawdown_net_linked'),
                {'sector_name':'动态回撤行业','drawdown_net_linked':'峰值资本贡献（百分点）'},point=['drawdown_net_linked']),
         pretty(ddstk[ddstk.strategy=='Dynamic'].nsmallest(10,'drawdown_net_linked'),
                {'ticker':'历史代码','issuer':'历史公司名','drawdown_net_linked':'峰值资本贡献（百分点）'},point=['drawdown_net_linked'])],[4])

    add('8. 四因子权重、模拟组合择时与实际持仓暴露',
        f'主股票归因使用全部 259 周；四因子模拟组合择时诊断只用四个因子收益同时可用的 {int(factor.complete_weeks)} 周，'
        f'排除 {int(factor.excluded_weeks)} 周且不补收益。完全复用已提交的 M4 多空因子构造函数以及 M5 诊断口径。'
        f'诊断年化平均增量为 {pp(factor.diagnostic_annual_mean_increment)} 个百分点，'
        f'平均横截面 rank IC 为 {factor.mean_rank_ic:.4f}，最高信号因子的胜出命中率为 {pct(factor.winner_hit_rate)}。\n\n'
        'Momentum 与 Low Risk 的模拟组合择时贡献为负，Quality 略为正；这些结果提供机制线索，不能加到股票收益上。'
        '实际股票组合中，动态的平均 Quality 与 Low Risk 分数暴露均低于静态，而 Investment 暴露较高。'
        '因子输入权重变化经过综合评分、排名、buffer 与行业约束后，未必转化为同方向、同幅度的持仓风格暴露变化。',
        [pretty(tabs['factor_weights_summary'],{'family':'因子','mean_weight':'平均输入权重','overweight_fraction':'高于25%的周比例'},
                percent=['mean_weight','overweight_fraction']),
         pretty(tabs['factor_timing_family_summary'],{'family':'因子','complete_weeks':'完整周数',
                'diagnostic_annual_mean_contribution':'模拟组合择时年化贡献（百分点）'},point=['diagnostic_annual_mean_contribution']),
         pretty(tabs['holdings_factor_score_exposure_summary'],{'strategy':'策略','family':'因子','mean_holdings_weighted_score':'平均持仓分数暴露'})],[5,6])

    add('9. FF6 回归解释：独立于持仓损益视图',
        '复用 M5 的 FF5 + Momentum 模型、执行区间聚合和 HAC(4) 推断，并逐项复现既有 alpha、p 值与 R²。'
        '市场因子先将日度市场总收益与 RF 分别复合，再相减；其他多空因子沿用 M5 的日收益复合近似，未重设聚合口径。'
        '模型中的市场、SMB、HML、RMW、CMA、Mom 与四个选股评分因子不是同一对象。\n\n'
        '回归解释表中 RF + alpha + β×因子 + residual 每周加回净收益；其年化列是 52×周均解释项，'
        '不是可实现的各因子策略 CAGR，也不是因果 alpha。残差均值接近零来自 OLS 截距的代数性质。',
        [pretty(tabs['ff6_regression_summary'],{'model':'模型','alpha_annualized':'年化alpha','alpha_pvalue':'HAC p值','r_squared':'R²'},
                percent=['alpha_annualized'],decimals=4),
         pretty(tabs['regression_mean_return_explanation'][tabs['regression_mean_return_explanation'].model.eq('Dynamic FMOM')],
                {'component':'动态模型解释项','annualized_arithmetic_mean_contribution':'年化均值贡献（百分点）'},
                point=['annualized_arithmetic_mean_contribution'])],[7])

    unpriced = tabs['unpriced_holdings']
    add('10. 数据缺口、核验与复现',
        f'数值验收中所有关键项通过，闭合检查的最大绝对误差为 {max_resid:.3e}。'
        '核验覆盖输入 LFS 哈希、持仓／收益联结、下一收盘执行时点、历史行业、权重、交易漂移、成本、'
        '逐周毛净收益、全期财富、行业汇总、回撤、M5 绩效与 FF6 复现，以及输入前后哈希不变。\n\n'
        f'冻结回测在动态和静态中各有 4 个证券周使用不可用收益按 0 计的既有规则，合计 {len(unpriced)} 条。'
        '本报告保留这些行、原始不可用原因和权重；这是解释已交付回测的条件，不能视为这些证券当周真实收益已被验证为零。'
        '完整清单见下表和 unpriced_holdings.csv。\n\n'
        '结果仅覆盖既有 2021–2025 周度历史样本外路径。未改动四因子、FMOM、持仓、交易、成本参数或任何上游结果，'
        '也没有用这些结果重新选策略。source_contract.json、input_manifest.csv、run_manifest.json、qa_checks.csv、'
        'output_manifest.csv 和运行脚本共同构成复核链条。',
        [pretty(unpriced,{'strategy':'策略','signal_date':'信号日','PERMNO':'PERMNO','weight':'受影响权重',
                         'unavailable_reason':'上游不可用原因'},percent=['weight'])])

    md = ['# 美股策略业绩归因报告', '',
          '来源：member5-risk-analysis / 7b48586e45b1c456478c8a977e96043bc3108dad。研究对象：既有 Dynamic FMOM 与 Static Multi-Factor 周度回测。', '']
    html_parts = []
    for section in sections:
        md.extend(['## '+section['title'],'',section['text'],''])
        part = ['<section><h2>'+escape(section['title'])+'</h2>']
        part += ['<p>'+escape(p)+'</p>' for p in section['text'].split('\n\n')]
        for table in section['tables']:
            md.extend([markdown_table(table),''])
            part.append('<div class="tablewrap">'+table.to_html(index=False,escape=True,border=0)+'</div>')
        for i in section['figures']:
            f = figs[i]
            md.extend([f"![{f['title']}]({f['file']})",'',f['note'],''])
            encoded = base64.b64encode((a.out/f['file']).read_bytes()).decode()
            part.append('<figure><img alt="'+escape(f['title'])+'" src="data:image/png;base64,'+encoded+'"><figcaption>'+escape(f['note'])+'</figcaption></figure>')
        part.append('</section>')
        html_parts.append(''.join(part))
    (a.out/'ATTRIBUTION_REPORT_ZH.md').write_text('\n'.join(md),encoding='utf-8')
    css = """body{margin:0;background:#f2f5f5;color:#21333d;font:16px/1.8 'Microsoft YaHei','Segoe UI',sans-serif}main{max-width:1140px;margin:auto;padding:40px 30px}header{padding:28px 32px;background:#123e4c;color:white;border-radius:16px}header h1{margin:0 0 12px;font-size:30px}header p{margin:4px 0;color:#d6e6e9}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:20px 0}.card{background:white;border-radius:12px;padding:20px}.card strong{font-size:28px;display:block;color:#176b75}.card span{font-size:13px;color:#65777d}section{background:white;padding:26px 30px;margin:20px 0;border-radius:14px;box-shadow:0 2px 9px #183e4a08}h2{font-size:21px;color:#145568;margin-top:0}p{margin:14px 0}.tablewrap{overflow-x:auto;margin:22px 0}table{border-collapse:collapse;width:100%;font-size:13px;line-height:1.6}th,td{text-align:left;padding:9px 11px;border-bottom:1px solid #e2eaed;white-space:normal;min-width:65px}th{background:#edf5f5;color:#215663}tr:nth-child(even){background:#fafcfc}figure{margin:26px 0}figure img{width:100%;height:auto}figcaption{font-size:13px;color:#62747d;margin-top:6px}nav{margin:20px 0;font-size:14px}nav a{color:#176b75;margin-right:15px}footer{font-size:13px;color:#62747d;padding:15px}.badge{font-size:13px;background:#e3f3f0;color:#165e51;padding:4px 10px;border-radius:6px}@media(max-width:700px){main{padding:15px}.cards{grid-template-columns:1fr}section{padding:18px}header h1{font-size:24px}}@media print{body{background:white}main{max-width:none;padding:0}section{box-shadow:none;break-inside:avoid}header{color:#123e4c;background:white}header p{color:#123e4c}nav{display:none}}"""
    cards = [('Dynamic 累计净收益',pct(d.cumulative_net_return)),('Static 累计净收益',pct(s.cumulative_net_return)),
             ('年化平均净增量',pp(inc.mean_annualized)+' pp')]
    card_html = ''.join('<div class="card"><span>'+escape(label)+'</span><strong>'+escape(value)+'</strong></div>' for label,value in cards)
    html = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>美股策略业绩归因</title><style>'+css+'</style><body><main><header><h1>美股策略业绩归因</h1><p>259 个完整周 · 2021–2025 Historical OOS · 既有冻结结果</p><p>Member 5 branch 7b48586 · 股票／行业贡献、精确成本、FMOM 增量与回撤</p></header><div class="cards">'+card_html+'</div><nav><a href="ATTRIBUTION_REPORT_ZH.md">Markdown 报告</a><a href="qa_checks.csv">核验记录</a><a href="stock_contributors_full_period.csv">完整股票贡献表</a><a href="sector_contributors_full_period.csv">完整行业贡献表</a></nav>'+''.join(html_parts)+'<footer>数值验收不替代上游正式 Gate 签字。所有图片已内嵌，HTML 可单独保存与分享；PNG 和 SVG 原图同时保留。</footer></main></body></html>'
    (a.out/'ATTRIBUTION_REPORT_ZH.html').write_text(html,encoding='utf-8')

    english = [
        '# US Performance Attribution, 2021–2025', '',
        'This section attributes the existing weekly backtest delivered on the member5-risk-analysis branch '
        '(commit 7b48586e45b1c456478c8a977e96043bc3108dad). The analysis uses the frozen dynamic and static '
        'portfolios over the same 259 completed holding periods. Signals are observed at the weekly close and '
        'executed at the next trading-day close; returns cover the interval from that execution close, excluded, '
        'to the next execution close, included. No factors, strategy parameters, holdings or trades are changed.', '',
        f'The dynamic strategy generated a cumulative net return of {pct(d.cumulative_net_return)}, compared with '
        f'{pct(s.cumulative_net_return)} for the static strategy. Their weekly-frequency CAGRs were '
        f'{pct(d.cagr_net)} and {pct(s.cagr_net)}. The dynamic strategy therefore ended with '
        f'{abs(inc.terminal_net_wealth_gap):.4f} less wealth per unit of initial capital. These figures reproduce '
        'the existing M5 results.', '',
        '## Holdings contributions', '',
        'Weekly security gross contribution is its executed target weight multiplied by its holding-period total return. '
        'Sector contributions aggregate those security contributions using historical GICS. For net contributions, '
        'we apply the existing cost-before-holding convention: c_net(i,t) = (1−TC_t)c_gross(i,t)−cost(i,t). '
        'Securities sold out of the portfolio remain in the ledger for their exit costs. Cumulative contributions '
        'are linked using pre-period net wealth and add to cumulative net return. They are expressed as '
        'percentage points of initial capital, rather than components of CAGR.', '',
        'Dynamic strategy: largest positive contributors', '',
        markdown_table(pretty(top.head(5),{'ticker':'Historical ticker','issuer':'Issuer','net_linked':'Net contribution, pp'},point=['net_linked'])), '',
        'Dynamic strategy: largest negative contributors', '',
        markdown_table(pretty(bottom.head(5),{'ticker':'Historical ticker','issuer':'Issuer','net_linked':'Net contribution, pp'},point=['net_linked'])), '',
        'The largest sector shortfalls relative to Static were Consumer Discretionary, Industrials and Financials. '
        'Information Technology provided a positive relative contribution. These are holdings-based wealth differences '
        'between two strategy portfolios. Benchmark constituent and sector weights were not supplied, so no '
        'Brinson allocation/selection decomposition relative to the S&P 500 is claimed.', '',
        '## Factor-momentum increment and costs', '',
        f'The annualized arithmetic mean of the paired Dynamic-minus-Static weekly net return was '
        f'{pp(inc.mean_annualized)} percentage points, decomposing exactly into a gross-return increment of '
        f'{pp(inc.gross_mean_annualized)} points and an incremental transaction-cost effect of '
        f'{pp(inc.annualized_exact_cost_effect)} points. The frozen one-way cost was 10 basis points. '
        f'The HAC(4) p-value was {inc.hac_p:.4f}, with an annualized 95% interval of '
        f'[{pp(inc.ci_low_ann)}, {pp(inc.ci_high_ann)}] points. The negative point estimate is not statistically '
        'different from zero at the 5% level under that test. This arithmetic mean increment is a different '
        'statistic from the difference in CAGRs.', '',
        f'The exact terminal-wealth bridge was {pp(inc.terminal_gross_wealth_gap)} initial-capital points from '
        f'the gross wealth paths and {pp(-(cost_d-cost_s))} points from additional cost-related terminal wealth loss, '
        f'giving {pp(inc.terminal_net_wealth_gap)} points in total. The terminal cost effect includes the '
        'subsequent growth foregone on fees and therefore differs from the sum of fee cashflows.', '',
        '## Drawdown and factor diagnostics', '',
        f'The dynamic maximum drawdown was {pct(d.max_drawdown)}, from 27 December 2021 to 30 October 2023. '
        'Security and sector contributions over that exact peak-to-trough window are rebased to peak wealth '
        'and sum to the signed drawdown. The full-period and drawdown contributor rankings answer different questions.', '',
        f'The factor-mimicking timing diagnostic uses {int(factor.complete_weeks)} complete-case weeks and yields '
        f'an annualized arithmetic increment of {pp(factor.diagnostic_annual_mean_increment)} points. '
        'Momentum and Low Risk contribute negatively in this diagnostic, while Quality contributes slightly positively. '
        'This mimicking-portfolio diagnostic is separate from the final long-only holdings decomposition. '
        'Actual holdings-weighted Quality and Low Risk scores are lower in Dynamic than in Static, while '
        'Investment exposure is higher. These exposures provide a description of the transmission through stock '
        'ranking, buffer rules and sector constraints; they do not establish a unique causal attribution to each input factor.', '',
        'The FF6 regression explanation reproduces the M5 execution-aligned aggregation and HAC(4) inference. '
        'RF, alpha, beta-times-factor terms and the residual add back to each weekly net return. '
        'The model factors and their explanatory components must not be added to holdings contributions.', '',
        '## Accounting checks and limitations', '',
        f'All critical numerical checks pass, with maximum reported residual {max_resid:.3e}. '
        'Input payloads are verified against Git LFS hashes and remain unchanged. The existing strategy uses '
        'a zero return for four unpriced held-security weeks in each portfolio; the affected observations are '
        'preserved and disclosed, rather than treated as validated zero realized returns. One inherited exit-cost '
        'observation lacks a verifiable same-signal-date sector and is retained as UNKNOWN. '
        'Annual tables group by signal year. Final team approval of the weekly-versus-daily NAV reporting '
        'convention and the other open upstream validation items remains necessary before presenting all formal gates as closed.', '',
    ]
    (a.out/'COURSE_ATTRIBUTION_SECTION_EN.md').write_text('\n'.join(english),encoding='utf-8')

    dictionary = pd.DataFrame([
        ('stock_contributions_weekly.parquet','strategy × signal_date × PERMNO','gross_contribution','周度证券毛收益贡献 = weight × stock_return_used；小数收益'),
        ('stock_contributions_weekly.parquet','strategy × signal_date × PERMNO','net_contribution','(1−总TC)×毛贡献−该证券交易成本；小数收益'),
        ('stock_contributions_weekly.parquet','strategy × signal_date × PERMNO','net_linked','执行前净财富×净贡献；单位起始资本'),
        ('stock_contributions_weekly.parquet','strategy × signal_date × PERMNO','gross_linked','执行前毛财富×毛贡献；单位起始资本，属于毛账户'),
        ('stock_contributions_weekly.parquet','strategy × signal_date × PERMNO','direct_cost_linked','−执行前净财富×该证券费用；单位起始资本'),
        ('stock_contributions_weekly.parquet','strategy × signal_date × PERMNO','cost','该证券交易费用／当周执行前 NAV；非美元金额'),
        ('stock_contributions_weekly.parquet','strategy × signal_date × PERMNO','weight','冻结执行后目标权重，保留现金；未自行归一化'),
        ('stock_contributions_weekly.parquet','strategy × signal_date × PERMNO','sector_source','冻结持仓行业、同信号日历史行业、先前持仓退出成本行业或 UNKNOWN'),
        ('dynamic_minus_static_stock_summary.csv','PERMNO','terminal_net_wealth_gap','两套策略各自净财富连接贡献之差；单位起始资本'),
        ('dynamic_minus_static_by_signal_year.csv','signal_year','net_increment_arithmetic_sum','当年周度净收益差之和；非复合收益差'),
        ('factor_timing_contributions_weekly.csv','signal_date × family','factor_timing_increment_diagnostic','(冻结因子权重−0.25)×冻结因子模拟组合多空收益；诊断视图'),
        ('regression_mean_return_explanation.csv','model × component','annualized_arithmetic_mean_contribution','52×FF6 每周模型解释项均值；非持仓贡献'),
        ('drawdown_stock_contributors.csv','strategy × PERMNO','drawdown_net_linked','峰谷区间净财富贡献／峰值财富；可加回有符号回撤'),
    ],columns=['file','grain','field','definition_and_unit'])
    dictionary.to_csv(a.out/'DATA_DICTIONARY.csv',index=False,encoding='utf-8-sig')
