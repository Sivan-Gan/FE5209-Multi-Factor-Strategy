#!/usr/bin/env python3
"""2011–2015 SGX six-factor validation: Top 20% EW excess, annual compound LS, HAC tests.

No charts, no factor rebuilding, no return shifts. This is a validation diagnostic;
price_eligible=True is the file's EXISTING flag, not an independently re-certified P020 filter.
"""
import argparse
from math import ceil, erfc, sqrt
from pathlib import Path
import numpy as np
import pandas as pd
FACTORS = ('Quality','Momentum','LowRisk','Investment','Size','Value')
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / 'data' / 'sg_six_factor_panel_2006_2026_final.csv.gz'
DEFAULT_OUTDIR = PROJECT_ROOT / 'results' / 'validation'

def hac_mean_test(x, lags=4):
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 3:
        return dict(n=n,mean=np.nan,se=np.nan,t=np.nan,p_two_sided=np.nan)
    mean = x.mean()
    d = x - mean
    gamma0 = np.dot(d,d)/n
    L = min(lags,n-1)
    long_run = gamma0
    for k in range(1,L+1):
        gamma = np.dot(d[k:],d[:-k])/n
        long_run += 2*(1-k/(L+1))*gamma
    se = sqrt(max(long_run,0)/n)
    t = mean/se if se>0 else np.nan
    # Standard-normal asymptotic two-sided p-value; no scipy required.
    p = erfc(abs(t)/sqrt(2)) if np.isfinite(t) else np.nan
    return dict(n=n,mean=mean,se=se,t=t,p_two_sided=p)

def compounded(x):
    s = pd.to_numeric(pd.Series(x), errors='coerce').dropna()
    return float(np.expm1(np.log1p(s).sum())) if len(s) and (s > -1).all() else np.nan

def evaluate(df, min_stocks, hac_lags):
    weeks=[]; annual=[]; summary=[]
    for factor in FACTORS:
        score=f'{factor}_score'
        sub=df.loc[df[score].notna() & df.forward_return.notna(),
                   ['ticker','signal_date','forward_return',score]].copy()
        for date,g in sub.groupby('signal_date',sort=True):
            n=len(g)
            if n<min_stocks or g[score].nunique()<2 or g.forward_return.nunique()<2:
                continue
            g=g.sort_values([score,'ticker'],ascending=[False,True],kind='mergesort')
            k20=ceil(.20*n); k30=ceil(.30*n)
            top20pct=g.head(k20).forward_return.mean()
            bottom30=g.tail(k30).forward_return.mean()
            top30=g.head(k30).forward_return.mean()
            universe=g.forward_return.mean()
            ic=g[score].corr(g.forward_return,method='spearman')
            weeks.append(dict(factor=factor,signal_date=date,year=date.year,n=n,
                              top20pct_n=k20,rank_ic=ic,universe_ew_return=universe,
                              top20pct_return=top20pct,top20pct_excess_ew=top20pct-universe,
                              top30pct_return=top30,bottom30pct_return=bottom30,
                              long_short_30pct_return=top30-bottom30))
    weekly=pd.DataFrame(weeks)
    if weekly.empty: raise ValueError('No evaluable signal weeks')
    for (factor,year),g in weekly.groupby(['factor','year'],sort=True):
        g=g.sort_values('signal_date')
        annual.append(dict(factor=factor,year=year,weeks=len(g),
                           mean_ic=g.rank_ic.mean(),ic_positive_weeks=int(g.rank_ic.gt(0).sum()),
                           top20pct_compound_return=compounded(g.top20pct_return),
                           universe_ew_compound_return=compounded(g.universe_ew_return),
                           top20pct_excess_weekly_mean=g.top20pct_excess_ew.mean(),
                           top20pct_excess_compound_difference=(compounded(g.top20pct_return)-compounded(g.universe_ew_return)),
                           long_short_30pct_weekly_mean=g.long_short_30pct_return.mean(),
                           long_short_30pct_compound_return=compounded(g.long_short_30pct_return)))
    a=pd.DataFrame(annual)
    for f,g in weekly.groupby('factor',sort=False):
        years=a[a.factor.eq(f)]
        row={'factor':f,'weeks':len(g),'paired_stock_weeks':int(g.n.sum()),
             'mean_stocks_per_week':g.n.mean(),'ic_positive_years':int(years.mean_ic.gt(0).sum()),
             'ls_positive_compound_years':int(years.long_short_30pct_compound_return.gt(0).sum()),
             'top20pct_excess_positive_years':int(years.top20pct_excess_weekly_mean.gt(0).sum()),
             'mean_top20pct_weekly_return':g.top20pct_return.mean(),
             'mean_top20pct_excess_weekly':g.top20pct_excess_ew.mean(),
             'mean_ls_30pct_weekly_return':g.long_short_30pct_return.mean(),
             'rank_ic_ir':g.rank_ic.mean()/g.rank_ic.std(ddof=1) if g.rank_ic.std(ddof=1)>0 else np.nan}
        for col,label in [('rank_ic','ic'),('top20pct_excess_ew','top20pct_excess'),('long_short_30pct_return','ls_30pct')]:
            result=hac_mean_test(g[col],hac_lags)
            for key,val in result.items(): row[f'{label}_hac_{key}']=val
        row.update(screen_weeks_ge_150=len(g)>=150,
                   screen_mean_ic_positive=g.rank_ic.mean()>0,
                   screen_mean_ls_weekly_positive=g.long_short_30pct_return.mean()>0,
                   screen_ls_compound_positive_years_ge_3=row['ls_positive_compound_years']>=3)
        row['all_four_screens_pass']=all([row['screen_weeks_ge_150'],row['screen_mean_ic_positive'],
                                         row['screen_mean_ls_weekly_positive'],row['screen_ls_compound_positive_years_ge_3']])
        summary.append(row)
    return weekly, a, pd.DataFrame(summary)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',default=DEFAULT_INPUT)
    p.add_argument('--outdir',default=DEFAULT_OUTDIR)
    p.add_argument('--start',default='2011-01-01')
    p.add_argument('--end',default='2015-12-31')
    p.add_argument('--min-stocks',type=int,default=20)
    p.add_argument('--hac-lags',type=int,default=4)
    args=p.parse_args()
    if args.min_stocks<3 or args.hac_lags<0:p.error('min-stocks >= 3; hac-lags >= 0')
    needed=['ticker','signal_date','forward_return','price_eligible']+[f'{f}_score' for f in FACTORS]
    df=pd.read_csv(args.input,usecols=lambda c:c in needed,low_memory=False)
    if set(needed)-set(df):raise ValueError('Missing fields: '+str(sorted(set(needed)-set(df))))
    df['signal_date']=pd.to_datetime(df.signal_date,errors='coerce')
    df=df[df.signal_date.between(pd.Timestamp(args.start),pd.Timestamp(args.end))].copy()
    if df.duplicated(['ticker','signal_date']).any():raise ValueError('Duplicate ticker/signal_date')
    for c in ['forward_return']+[f'{f}_score' for f in FACTORS]:
        df[c]=pd.to_numeric(df[c],errors='coerce').replace([np.inf,-np.inf],np.nan)
    e=df.price_eligible.astype('string').str.lower().str.strip()
    eligible=e.isin(['true','1'])
    out=Path(args.outdir);out.mkdir(parents=True,exist_ok=True)
    for name,subset in [('ALL_PAIRED',df),('PRICE_ELIGIBLE_TRUE',df[eligible])]:
        weekly,annual,summary=evaluate(subset,args.min_stocks,args.hac_lags)
        weekly.to_csv(out/f'{name}_factor_validation_weekly.csv',index=False)
        annual.to_csv(out/f'{name}_annual_factor_validation.csv',index=False)
        summary.to_csv(out/f'{name}_factor_validation_summary.csv',index=False)
        print('\n'+name+'\n'+summary[['factor','weeks','ic_hac_mean','ic_hac_t','ic_hac_p_two_sided',
                  'mean_top20pct_excess_weekly','ls_positive_compound_years','all_four_screens_pass']].to_string(index=False,float_format=lambda v:f'{v:.6f}'))
    print('\nNo shift; same factor-specific universe in top and universe benchmark; gross equal weighted.')
    print('Note: PRICE_ELIGIBLE_TRUE is ORIGINAL panel flag; not certified P020.')
    print('HAC p-values are asymptotic two-sided; descriptive, not multiple-testing adjusted.')
    print('Top20pct excess is gross difference from contemporaneous equal-weight factor-available universe.')
    print('Year compound LS is not a financed or transaction-cost-adjusted trading return.')
    print('Output:',out.resolve())
if __name__=='__main__':main()
