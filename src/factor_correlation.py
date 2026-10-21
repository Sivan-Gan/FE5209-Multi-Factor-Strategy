#!/usr/bin/env python3
"""SGX six-factor correlation and incremental-contribution analysis (2011–2015), no charts.

Compares strategies on the SAME complete-case weekly universe; no lookahead,
no use of future returns for ranking, and no return shifting. Gross price returns.
"""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd

FACTORS=['Quality','Momentum','LowRisk','Investment','Size','Value']
ORDER=['Momentum','Quality','LowRisk','Investment','Size','Value']
COLS=[f'{x}_score' for x in FACTORS]
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / 'data' / 'sg_six_factor_panel_2006_2026_final.csv.gz'
DEFAULT_OUTDIR = PROJECT_ROOT / 'results' / 'correlation'

def max_dd(a):
    growth=(1+np.asarray(a, dtype=float)).cumprod()
    return float(np.min(growth / np.maximum.accumulate(np.r_[1., growth])[1:] - 1))

def metrics(s):
    x=pd.to_numeric(s,errors='coerce').dropna().to_numpy(dtype=float)
    if not len(x): return dict(weeks=0)
    wealth=float(np.prod(1+x)); vol=float(np.std(x,ddof=1)) if len(x)>1 else float('nan')
    return {'weeks':len(x),'weekly_mean':float(x.mean()),'weekly_vol':vol,
        'annualized_compound_return':(wealth**(52/len(x))-1) if wealth>0 else float('nan'),
        'annualized_vol':vol*np.sqrt(52),'sharpe_proxy':float(x.mean()/vol*np.sqrt(52)) if vol>0 else float('nan'),
        'max_drawdown':max_dd(x),'cumulative_return':wealth-1,'positive_weeks':float(np.mean(x>0))}

def analyze(src,outdir, min_stocks=20, top_n=20):
    df=pd.read_csv(src,low_memory=False)
    required=['ticker','signal_date','forward_return','price_eligible']+COLS
    missing=[c for c in required if c not in df.columns]
    if missing:raise ValueError(f'Missing columns: {missing}')
    df['signal_date']=pd.to_datetime(df['signal_date'])
    df=df.loc[df['signal_date'].between('2011-01-01','2015-12-31')].copy()
    for c in COLS+['forward_return']: df[c]=pd.to_numeric(df[c], errors='coerce').replace([np.inf,-np.inf],np.nan)
    assert not df.duplicated(['signal_date','ticker']).any(),'Duplicate signal_date/ticker'
    frames=[]; correlations=[]; coverage=[]; weekly=[]; detail=[]
    for mode in ['ALL_PAIRED','PRICE_ELIGIBLE_TRUE']:
        base=df if mode=='ALL_PAIRED' else df.loc[df['price_eligible'].astype(str).str.lower().isin(['true','1','1.0'])]
        complete=base.dropna(subset=COLS+['forward_return']).copy()
        combos={'UNIVERSE_EW':[],**{f'ADD_{k}':ORDER[:k] for k in range(1,7)},
                **{f'WITHOUT_{f}':[g for g in FACTORS if g!=f] for f in FACTORS}}
        cov=pd.DataFrame({'signal_date':sorted(base['signal_date'].unique())})
        counts=complete.groupby('signal_date').size().rename('six_complete_n').reset_index()
        cov=cov.merge(counts,on='signal_date',how='left').fillna({'six_complete_n':0})
        cov['base_n']=cov['signal_date'].map(base.groupby('signal_date').size()).fillna(0).astype(int)
        cov['mode']=mode
        coverage.append(cov)
        byweek=list(complete.groupby('signal_date',sort=True))
        # Pairwise IC: broader available pairs, plus conditional fixed complete-case sample
        for x in range(len(FACTORS)):
            for y in range(x+1,len(FACTORS)):
                f,g=FACTORS[x],FACTORS[y]
                for universe, dat in [('paired',base),('six_complete',complete)]:
                    sub=dat.dropna(subset=[f+'_score',g+'_score'])
                    corr=sub.groupby('signal_date').apply(lambda t:t[f+'_score'].corr(t[g+'_score'],method='spearman') if len(t)>=10 else np.nan,include_groups=False)
                    correlations.append({'mode':mode,'universe':universe,'factor_a':f,'factor_b':g,'weeks':int(corr.notna().sum()),'mean_weekly_spearman':float(corr.mean()),'median_weekly_spearman':float(corr.median()),'abs_mean_weekly_spearman':float(corr.abs().mean())})
        for date,group in byweek:
            if len(group)<max(min_stocks,top_n): continue
            # Identical complete-case securities throughout this week's models.
            group=group.sort_values('ticker').copy()
            for f in FACTORS:
                # Percentile ranks ensure 6 scores comparable without distorting directions.
                group[f+'_rank']=group[f+'_score'].rank(method='average',pct=True)
            m={'mode':mode,'signal_date':date,'universe_n':len(group)}
            m['UNIVERSE_EW']=float(group['forward_return'].mean())
            for name,fs in combos.items():
                if name=='UNIVERSE_EW':continue
                group['composite']=group[[f+'_rank' for f in fs]].mean(axis=1)
                pick=group.sort_values(['composite','ticker'],ascending=[False,True],kind='stable').head(top_n)
                m[name]=float(pick['forward_return'].mean())
                detail.append({'mode':mode,'signal_date':date,'strategy':name,
                    'selected_n':len(pick),'universe_n':len(group),
                    'tickers':'|'.join(pick['ticker'].astype(str))})
            weekly.append(m)
    weeks=pd.DataFrame(weekly)
    result=[]
    for mode,part in weeks.groupby('mode'):
        baseline=part['UNIVERSE_EW']
        for strategy in [c for c in part.columns if c not in ['mode','signal_date','universe_n']]:
            mm=metrics(part[strategy]);excess=part[strategy]-baseline
            result.append({'mode':mode,'strategy':strategy,**mm,
                'mean_weekly_excess_vs_universe':float(excess.mean()),
                'excess_positive_week_share':float((excess>0).mean())})
    inc=[]
    for mode,part in weeks.groupby('mode'):
        for n in range(2,7):
            new='ADD_'+str(n);prior='ADD_'+str(n-1)
            delta=part[new]-part[prior]
            inc.append({'mode':mode,'comparison':f'{prior} -> {new}','added_factor':ORDER[n-1],
                'mean_weekly_increment':float(delta.mean()),'positive_weeks_share':float((delta>0).mean()),
                'annualized_tracking_error':float(delta.std(ddof=1)*np.sqrt(52)), 'weeks':int(len(delta))})
        for f in FACTORS:
            delta=part['ADD_6']-part['WITHOUT_'+f]
            inc.append({'mode':mode,'comparison':f'WITHOUT_{f} -> ADD_6','added_factor':f,
                'mean_weekly_increment':float(delta.mean()),'positive_weeks_share':float((delta>0).mean()),
                'annualized_tracking_error':float(delta.std(ddof=1)*np.sqrt(52)), 'weeks':int(len(delta))})
    output=Path(outdir);output.mkdir(parents=True,exist_ok=True)
    for fn,dat in [('factor_pairwise_weekly_spearman.csv',pd.DataFrame(correlations)),
        ('complete_case_coverage.csv',pd.concat(coverage,ignore_index=True)),
        ('composite_portfolio_weekly_returns.csv',weeks),('composite_portfolio_performance_summary.csv',pd.DataFrame(result)),
        ('factor_incremental_contributions.csv',pd.DataFrame(inc)),
        ('composite_portfolio_selection_audit.csv',pd.DataFrame(detail))]:
        dat.to_csv(output/fn,index=False)
    audit={'source':str(src),'period':'2011-01-01 to 2015-12-31','factors':FACTORS,
        'rank_direction':'higher original score preferred','factor_blend':'equal weighted within-week percentile ranks',
        'top_n':top_n,'sample_rule':'six factor scores and forward_return present; same stocks per week per filter',
        'income':'gross unhedged returns from source forward_return; no costs; benchmark cross-sectional EW',
        'warnings':['Price eligible uses existing flag, not independently verified as P020.',
                    'Fixed complete-case comparison ignores missing-score names: coverage reported separately.',
                    'Incremental contributions are diagnostic associations not causal effects.',
                    'Annualized quantities are descriptive, not strategy-level verified live metrics.'],
        'portfolio_week_counts':weeks.groupby('mode').size().to_dict()}
    (output/'methodology.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print('\n=== CORRELATION PORTFOLIOS ===')
    print(pd.DataFrame(result).query("strategy in ['ADD_1','ADD_2','ADD_3','ADD_4','ADD_5','ADD_6']")[['mode','strategy','weeks','weekly_mean','mean_weekly_excess_vs_universe','max_drawdown']].to_string(index=False))
    print('\n=== INCREMENTAL ADDITIONS ===')
    print(pd.DataFrame(inc).query("comparison.str.startswith('ADD_')",engine='python')[['mode','added_factor','mean_weekly_increment','positive_weeks_share']].to_string(index=False))
    print('Saved to:',output)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',default=DEFAULT_INPUT);parser.add_argument('--outdir',default=DEFAULT_OUTDIR)
    parser.add_argument('--top-n',type=int,default=20);parser.add_argument('--min-stocks',type=int,default=20)
    a=parser.parse_args();analyze(a.input,a.outdir,a.min_stocks,a.top_n)
