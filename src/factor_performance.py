#!/usr/bin/env python3
"""SGX six-factor performance analysis (2011–2015); table-only, no figures.

Run: python3 src/factor_performance.py [--input panel.csv.gz] [--outdir results/performance]
"""
import argparse
from pathlib import Path
import warnings

import numpy as np
import pandas as pd

FACTORS = ['Quality', 'Momentum', 'LowRisk', 'Investment', 'Size', 'Value']
PERIODS_PER_YEAR = 52
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / 'data' / 'sg_six_factor_panel_2006_2026_final.csv.gz'
DEFAULT_OUTDIR = PROJECT_ROOT / 'results' / 'performance'

def summarize_weekly(series):
    a = pd.to_numeric(series, errors='coerce').dropna()
    if a.empty:
        return {'weeks': 0, 'mean_weekly': np.nan, 'annualized_compound': np.nan,
                'annualized_vol': np.nan, 'sharpe_zero_rf': np.nan, 'max_drawdown': np.nan,
                'positive_week_rate': np.nan}
    sigma = a.std(ddof=1)
    # Long-short arithmetic P&L may be below -100%; the compounded metric is then undefined.
    compounded = (np.prod(1 + a.values) ** (PERIODS_PER_YEAR / len(a)) - 1) if (a > -1).all() else np.nan
    nav = (1 + a).cumprod()
    peak = np.maximum.accumulate(np.r_[1.0, nav.values])[1:]
    mdd = (nav.values / peak - 1).min() if (a > -1).all() else np.nan
    return {'weeks': len(a), 'mean_weekly': a.mean(), 'annualized_compound': compounded,
            'annualized_vol': sigma * np.sqrt(PERIODS_PER_YEAR),
            'sharpe_zero_rf': a.mean() / sigma * np.sqrt(PERIODS_PER_YEAR) if sigma > 0 else np.nan,
            'max_drawdown': mdd, 'positive_week_rate': (a > 0).mean()}

def prepare(input_path, start, end):
    cols = ['ticker', 'signal_date', 'execution_date', 'next_execution_date',
            'forward_return', 'price_eligible'] + [f'{f}_score' for f in FACTORS]
    # Reading only required columns substantially lowers memory use.
    df = pd.read_csv(input_path, usecols=lambda c: c in cols, low_memory=False)
    missing = set(cols) - set(df.columns)
    if missing:
        raise ValueError(f'Required columns missing: {sorted(missing)}')
    df['signal_date'] = pd.to_datetime(df['signal_date'], errors='coerce')
    df['execution_date'] = pd.to_datetime(df['execution_date'], errors='coerce')
    df['next_execution_date'] = pd.to_datetime(df['next_execution_date'], errors='coerce')
    df = df[df['signal_date'].between(pd.Timestamp(start), pd.Timestamp(end))].copy()
    if df.empty:
        raise ValueError('No rows in the requested time window.')
    for col in ['forward_return'] + [f'{f}_score' for f in FACTORS]:
        df[col] = pd.to_numeric(df[col], errors='coerce').replace([np.inf, -np.inf], np.nan)
    dup = df.duplicated(['ticker', 'signal_date'])
    if dup.any():
        raise ValueError(f'Duplicate ticker + signal_date keys: {dup.sum()}')
    bad_dates = (df['execution_date'].notna() & df['signal_date'].ge(df['execution_date'])) | (
        df['next_execution_date'].notna() & df['execution_date'].ge(df['next_execution_date']))
    if bad_dates.any():
        raise ValueError(f'Nonforward execution dates: {bad_dates.sum()}')
    eligible = df['price_eligible'].astype('string').str.lower().str.strip()
    df['_price_eligible_bool'] = eligible.map({'true': True, '1': True, 'false': False, '0': False})
    return df

def evaluate(df, min_cross_section, group_count, top_n, low_frac, high_frac):
    weekly, coverage, quantiles = [], [], []
    for factor in FACTORS:
        sc = factor + '_score'
        valid = df[['ticker', 'signal_date', 'forward_return', sc]].dropna().copy()
        cov = {'factor': factor, 'total_rows': len(df), 'score_nonnull': int(df[sc].notna().sum()),
               'return_nonnull': int(df.forward_return.notna().sum()),
               'paired_rows': len(valid), 'paired_weeks': int(valid.signal_date.nunique()),
               'unique_tickers': int(valid.ticker.nunique())}
        coverage.append(cov)
        for date, g in valid.groupby('signal_date', sort=True):
            g = g.sort_values(['ticker']).copy()
            n = len(g)
            if n < min_cross_section or g[sc].nunique() < 2:
                continue
            g = g.sort_values([sc, 'ticker'], ascending=[False, True]).copy()
            k_long = max(1, int(np.ceil(n * high_frac)))
            k_short = max(1, int(np.ceil(n * low_frac)))
            # Fixed top / bottom portions with deterministic tie-break by ticker.
            avg_top = g.head(k_long).forward_return.mean()
            avg_bottom = g.tail(k_short).forward_return.mean()
            # A single rank tie-break ensures exactly q groups, even if scores tie.
            g['quantile'] = np.floor(np.arange(n) * group_count / n).astype(int) + 1
            # g is sorted descending, so quantile 1 = highest scores.
            qs = g.groupby('quantile').forward_return.agg(['mean', 'size'])
            for q, entry in qs.iterrows():
                quantiles.append({'factor': factor, 'signal_date': date, 'quantile': int(q),
                                  'return': float(entry['mean']), 'n': int(entry['size'])})
            # Spearman rank correlation: high score -> high forward return expected.
            ic = g[sc].corr(g['forward_return'], method='spearman') if g['forward_return'].nunique() > 1 else np.nan
            weekly.append({'factor': factor, 'signal_date': date, 'year': date.year,
                           'n': n, 'rank_ic': ic,
                           'top20_return': g.head(min(top_n, n)).forward_return.mean(),
                           'top20_n': min(top_n, n),
                           'universe_ew_return': g.forward_return.mean(),
                           'top20_excess_ew': g.head(min(top_n, n)).forward_return.mean() - g.forward_return.mean(),
                           'top_pct_return': avg_top, 'bottom_pct_return': avg_bottom,
                           'long_short_return': avg_top - avg_bottom,
                           'q1_minus_q5': qs.loc[1, 'mean'] - qs.loc[group_count, 'mean'] if len(qs) == group_count else np.nan})
    return pd.DataFrame(weekly), pd.DataFrame(coverage), pd.DataFrame(quantiles)

def aggregate(weekly, coverage):
    out = []
    for f in FACTORS:
        a = weekly[weekly.factor.eq(f)].sort_values('signal_date')
        ic = a.rank_ic.dropna()
        row = coverage[coverage.factor.eq(f)].iloc[0].to_dict()
        row.update({'evaluated_weeks': len(a), 'mean_rank_ic': ic.mean(),
                    'rank_ic_std': ic.std(ddof=1),
                    'rank_ic_ir': ic.mean() / ic.std(ddof=1) if len(ic) > 1 and ic.std(ddof=1) > 0 else np.nan,
                    'rank_ic_t_naive': ic.mean() / (ic.std(ddof=1)/np.sqrt(len(ic))) if len(ic) > 1 and ic.std(ddof=1) > 0 else np.nan,
                    'ic_positive_week_rate': (ic > 0).mean() if len(ic) else np.nan,
                    'mean_stock_count': a.n.mean(),
                    'top20_mean_weekly': a.top20_return.mean(),
                    'top20_excess_mean_weekly': a.top20_excess_ew.mean(),
                    'long_short_mean_weekly': a.long_short_return.mean(),
                    'q1_minus_q5_mean_weekly': a.q1_minus_q5.mean()})
        for key, col in [('top20', 'top20_return'), ('long_short', 'long_short_return'), ('top20_excess', 'top20_excess_ew')]:
            for k,v in summarize_weekly(a[col]).items():
                row[f'{key}_{k}'] = v
        out.append(row)
    return pd.DataFrame(out)

def annual(weekly):
    rows=[]
    for (factor, year), g in weekly.groupby(['factor','year'],sort=True):
        ic=g.rank_ic.dropna()
        rows.append({'factor':factor,'year':int(year),'weeks':len(g),'mean_n':g.n.mean(),
                     'rank_ic_mean':ic.mean(),'rank_ic_ir':ic.mean()/ic.std(ddof=1) if len(ic)>1 and ic.std(ddof=1)>0 else np.nan,
                     'ic_positive_week_rate':(ic>0).mean(),
                     'top20_compound_period_return':(1+g.top20_return).prod()-1,
                     'top20_excess_mean_weekly':g.top20_excess_ew.mean(),
                     'long_short_compound_period_return':(1+g.long_short_return).prod()-1 if (g.long_short_return > -1).all() else np.nan,
                     'long_short_mean_weekly':g.long_short_return.mean()})
    return pd.DataFrame(rows)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input',default=DEFAULT_INPUT,help='Original SGX six-factor panel CSV or CSV.GZ')
    ap.add_argument('--outdir',default=DEFAULT_OUTDIR)
    ap.add_argument('--start',default='2011-01-01')
    ap.add_argument('--end',default='2015-12-31')
    ap.add_argument('--min-stocks',type=int,default=20)
    ap.add_argument('--top-n',type=int,default=20)
    ap.add_argument('--groups',type=int,default=5)
    ap.add_argument('--tail-fraction',type=float,default=0.30)
    args=ap.parse_args()
    if args.groups < 2 or args.min_stocks < 2 or not 0 < args.tail_fraction < .5:
        ap.error('groups >= 2, min-stocks >= 2, 0 < tail-fraction < 0.5 are required')
    out=Path(args.outdir);out.mkdir(parents=True,exist_ok=True)
    df=prepare(args.input,args.start,args.end)
    overview=[]
    for label,sub in [('ALL_PAIRED',df),('PRICE_ELIGIBLE_TRUE',df[df['_price_eligible_bool'].eq(True)])]:
        weekly,cov,q=evaluate(sub,args.min_stocks,args.groups,args.top_n,args.tail_fraction,args.tail_fraction)
        if weekly.empty:
            warnings.warn(f'{label}: no evaluable weeks; writing coverage only')
            cov.to_csv(out/f'{label}_coverage.csv',index=False)
            continue
        agg=aggregate(weekly,cov); y=annual(weekly)
        weekly.to_csv(out/f'{label}_factor_weekly_metrics.csv',index=False)
        cov.to_csv(out/f'{label}_factor_coverage.csv',index=False)
        agg.to_csv(out/f'{label}_factor_performance_summary.csv',index=False)
        y.to_csv(out/f'{label}_annual_factor_performance.csv',index=False)
        q.to_csv(out/f'{label}_factor_quintile_weekly_returns.csv',index=False)
        quint=q.groupby(['factor','quantile'],as_index=False).agg(mean_weekly_return=('return','mean'),weeks=('return','count'),mean_group_n=('n','mean'))
        quint.to_csv(out/f'{label}_factor_quintile_return_summary.csv',index=False)
        overview.append((label,agg[['factor','paired_rows','evaluated_weeks','mean_rank_ic','rank_ic_ir','long_short_mean_weekly','top20_mean_weekly']]))
    print(f'Input: {args.input}\nWindow: {args.start} through {args.end}; rows={len(df)}, weeks={df.signal_date.nunique()}')
    print('No shift applied to forward_return. Quantile 1=highest score. Returns as decimals, not percent.')
    for label, t in overview:
        print('\n'+label+'\n'+t.to_string(index=False,float_format=lambda x:f'{x:.6f}'))
    print('\nOutput:',out.resolve())
    print('Notes: unadjusted for transaction costs and risk-free rate; price eligibility uses existing original column.')
    print('IC t-stat is naive (not HAC). Individual forward_return currency consistency must be considered.')

if __name__=='__main__':
    main()
