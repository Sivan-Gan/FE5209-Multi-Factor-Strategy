"""Self-contained synthetic onboarding example. No real market data or alpha."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT/'runs/demo_first_run')
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error('Use a new output folder, e.g. --output runs/demo_second_run')
    inputs = out/'synthetic_inputs'
    inputs.mkdir(parents=True, exist_ok=True)
    dates = pd.bdate_range('2024-01-02', periods=65)
    bars = pd.DataFrame([dict(date=d, ticker=t, close=price, volume=100000.)
                         for d in dates for t, price in [('DEMO_A', 10.), ('DEMO_B', 20.)]])
    # A zero-volume day demonstrates deferred execution, not an exchange halt.
    bars.loc[bars.date.eq(dates[26]) & bars.ticker.eq('DEMO_A'), 'volume'] = 0.
    bars.to_csv(inputs/'prices.csv', index=False)
    pd.DataFrame({'date': dates}).to_csv(inputs/'sessions.csv', index=False)
    pd.DataFrame([dict(signal_date=dates[s], execution_date=dates[s+1], ticker=t, weight=w)
                  for s, weights in [(25, {'DEMO_A': .5, 'DEMO_B': .5}),
                                     (40, {'DEMO_A': .3, 'DEMO_B': .7}),
                                     (50, {'DEMO_A': 0.})]
                  for t, w in weights.items()]).to_csv(inputs/'targets.csv', index=False)
    pd.DataFrame([dict(ticker=t, currency='SGD', six_sector='synthetic')
                  for t in ['DEMO_A', 'DEMO_B']]).to_csv(inputs/'security_master.csv', index=False)
    command = [sys.executable, str(ROOT/'run_execution.py'), '--prices', str(inputs/'prices.csv'),
               '--sessions', str(inputs/'sessions.csv'), '--targets', str(inputs/'targets.csv'),
               '--security-master', str(inputs/'security_master.csv'),
               '--settings', str(ROOT/'configs/execution_settings.json'),
               '--end', str(dates[60].date()), '--allow-price-only', '--output', str(out/'results')]
    subprocess.run(command, check=True)
    daily = pd.read_csv(out/'results/daily.csv')
    attempts = pd.read_csv(out/'results/attempts.csv')
    assert daily.cash.ge(-1e-8).all()
    assert daily.accounting_error.abs().max() < 1e-6
    assert 'SOURCE_ZERO_VOLUME' in attempts.reason.tolist()
    (out/'DEMO_ONLY.json').write_text(json.dumps(dict(synthetic=True, market_calendar=False,
        performance_not_strategy_evidence=True, output='results/', passed=True), indent=2), encoding='utf-8')
    print('Synthetic demo passed. See results/daily.csv, orders.csv, fills.csv and attempts.csv.')


if __name__ == '__main__':
    main()
