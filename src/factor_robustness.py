#!/usr/bin/env python3
"""Robustness audit of six original SGX factors, 2011-2015.
Reads tables produced by factor_performance.py; no figures, no re-selection.
Usage: python3 src/factor_robustness.py [--indir results/performance] [--outdir results/robustness]
"""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
FACTORS=['Quality','Momentum','LowRisk','Investment','Size','Value']
SAMPLES=['ALL_PAIRED','PRICE_ELIGIBLE_TRUE']
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INDIR = PROJECT_ROOT / 'results' / 'performance'
DEFAULT_OUTDIR = PROJECT_ROOT / 'results' / 'robustness'

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--indir',default=DEFAULT_INDIR)
    ap.add_argument('--outdir',default=DEFAULT_OUTDIR)
    a=ap.parse_args()
    inp,out=Path(a.indir),Path(a.outdir);out.mkdir(parents=True,exist_ok=True)
    evidence=[]; years=[]; quints=[]
    for s in SAMPLES:
        weekly=pd.read_csv(inp/f'{s}_factor_weekly_metrics.csv')
        annual=pd.read_csv(inp/f'{s}_annual_factor_performance.csv')
        qs=pd.read_csv(inp/f'{s}_factor_quintile_return_summary.csv')
        for f in FACTORS:
            w=weekly[weekly.factor.eq(f)].sort_values('signal_date')
            y=annual[annual.factor.eq(f)].sort_values('year')
            q=qs[qs.factor.eq(f)].set_index('quantile')['mean_weekly_return']
            if w.empty or y.empty or len(q)!=5: continue
            ic=w.rank_ic.dropna()
            # Newey-West/Bartlett approx SE for mean with 4 lags, not a substitute for full bootstrap.
            def hac_mean_t(x,lags=4):
                x=np.asarray(pd.to_numeric(x,errors='coerce').dropna(),float);n=len(x)
                if n<5:return np.nan
                z=x-x.mean(); vv=np.dot(z,z)/n
                for j in range(1,min(lags,n-1)+1):
                    vv+=2*(1-j/(lags+1))*np.dot(z[j:],z[:-j])/n
                return x.mean()/np.sqrt(vv/n) if vv>0 else np.nan
            # Directional signal is pre-specified high-score outperform low-score; no sign flipping.
            ev=dict(sample=s,factor=f,weeks=len(w),mean_rank_ic=ic.mean(),rank_ic_hac_t_4lags=hac_mean_t(ic),
                    ic_positive_weeks=int((ic>0).sum()),ic_positive_week_rate=(ic>0).mean(),
                    positive_ic_years=int((y.rank_ic_mean>0).sum()),positive_ls_years=int((y.long_short_mean_weekly>0).sum()),
                    positive_top20_excess_years=int((y.top20_excess_mean_weekly>0).sum()),
                    avg_top20_excess_weekly=w.top20_excess_ew.mean(),top20_excess_hac_t_4lags=hac_mean_t(w.top20_excess_ew),
                    avg_long_short_weekly=w.long_short_return.mean(),long_short_hac_t_4lags=hac_mean_t(w.long_short_return),
                    q1_mean=q.get(1),q2_mean=q.get(2),q3_mean=q.get(3),q4_mean=q.get(4),q5_mean=q.get(5),
                    q1_minus_q5_mean=q.get(1)-q.get(5),
                    monotonic_descending=bool(np.all(np.diff([q.get(j) for j in range(1,6)]) <= 0)))
            evidence.append(ev)
            for _, r in y.iterrows():
                years.append({'sample':s,'factor':f,'year':int(r.year),'mean_rank_ic':r.rank_ic_mean,
                              'mean_ls_weekly':r.long_short_mean_weekly,'mean_top20_excess_weekly':r.top20_excess_mean_weekly})
            for j in range(1,6):quints.append({'sample':s,'factor':f,'group':f'Q{j}','mean_weekly_return':q.get(j)})
    ev=pd.DataFrame(evidence);ev.to_csv(out/'factor_robustness_summary.csv',index=False)
    pd.DataFrame(years).to_csv(out/'annual_factor_robustness.csv',index=False)
    pd.DataFrame(quints).to_csv(out/'factor_quintile_robustness.csv',index=False)
    with (out/'robustness_methodology_notes.txt').open('w') as fh:
        fh.write('Original six factors only; no changes to scores or forward_return.\n')
        fh.write('All six factors retained regardless of results; no sign flipping, no cherry-picked years.\n')
        fh.write('Positive IC is a ranking signal, NOT proof of positive tradable alpha.\n')
        fh.write('Top20 excess compares each factor\'s eligible universe weekly EW mean, not STI.\n')
        fh.write('HAC t-values: descriptive Newey-West/Bartlett approximation with 4 weekly lags.\n')
        fh.write('Raw return currency and costs not necessarily consistent; use as research diagnostics only.\n')
        fh.write('price_eligible is original flag, NOT certified to equal P020.\n')
    print(ev[['sample','factor','positive_ic_years','positive_ls_years','positive_top20_excess_years','rank_ic_hac_t_4lags','q1_minus_q5_mean','monotonic_descending']].to_string(index=False,float_format=lambda z:f'{z:.4f}'))
    print('Output:',out.resolve())
if __name__=='__main__':main()
