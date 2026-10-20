import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import numpy as np
import pandas as pd
import legacy_adapter as A
from support import HERE, add_repo, check_source_lock
from run_top4 import validate_config


class LegacyRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (HERE/'runs').mkdir(exist_ok=True)
    def test_frozen_route_is_recommended_old_data_top4(self):
        c = json.loads((HERE/'configs/legacy_top4.json').read_text())
        validate_config(c)
        self.assertEqual(c['strategy'], 'TUNE_T4L3_IV_N20_EQ_NONE')
        c['data_route'] = 'SGX_TEAM_HANDOFF'
        with self.assertRaises(ValueError):
            validate_config(c)

    def test_source_lock_and_six_factor_direction(self):
        lock = check_source_lock()
        self.assertEqual(len(lock['canonical_lf_sha256']), 9)
        T, L, pf, mt = A.modules(Path('.'))
        self.assertNotIn('Dividend', L.COLS)
        self.assertEqual(L.COLS['Size'], 'SizeSmall_score')
        self.assertEqual(pf.SELECT_FRACTION, .2)
        self.assertEqual(pf.EXIT_FRACTION, .3)

    def test_future_factor_returns_cannot_change_old_top4_choice(self):
        T, L, pf, mt = A.modules(Path('.'))
        rng = np.random.default_rng(5209)
        dates = pd.date_range('2012-01-06', '2018-12-28', freq='W-FRI')
        rows = [dict(signal_date=d, next_execution_date=d+pd.Timedelta(days=10),
                     family=f, factor_return_gross=float(rng.normal(.001, .02)),
                     factor_return_available=1) for d in dates for f in T.POOL]
        h = pd.DataFrame(rows)
        a = T.decisions(h, ['2016-01-08'], 'T4L3')[2016]
        h.loc[h.next_execution_date > pd.Timestamp('2016-01-08'), 'factor_return_gross'] = 8.
        b = T.decisions(h, ['2016-01-08'], 'T4L3')[2016]
        self.assertEqual(a[0], b[0])
        for family in a[0]:
            self.assertAlmostEqual(a[1]['IV'][family], b[1]['IV'][family])

    def test_new_team_archive_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=HERE/'runs') as temp:
            root = Path(temp)
            archive = root/'SGX_TEAM_HANDOFF.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('SGX_TEAM_HANDOFF/data/sgx_daily_market.parquet', b'not-used')
            with self.assertRaisesRegex(ValueError, 'original comprehensive'):
                A.extract_inputs(archive, root/'out')

    def test_extracts_only_whitelisted_data_no_scripts_or_path_traversal(self):
        with tempfile.TemporaryDirectory(dir=HERE/'runs') as temp:
            root = Path(temp)
            payloads = {name: name.encode() for name in A.MEMBERS}
            (root/'DATA_LOCK.json').write_text(json.dumps({'member_sha256': {
                k: hashlib.sha256(v).hexdigest() for k, v in payloads.items()}}))
            archive = root/'old.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                for name, payload in payloads.items():
                    z.writestr(A.PREFIX+name, payload)
                z.writestr('../escape.txt', b'not extracted')
                z.writestr(A.PREFIX+'scripts/run.py', b'raise RuntimeError("never execute")')
            with patch.object(A, 'HERE', root):
                A.extract_inputs(archive, root/'out', root/'DATA_LOCK.json')
            self.assertFalse((root/'escape.txt').exists())
            self.assertFalse((root/'out/scripts').exists())
            self.assertEqual(len([p for p in (root/'out').rglob('*') if p.is_file()]), 4)

    def test_independent_weekly_matches_source_missing_price_rule_and_fee(self):
        add_repo()
        from src.strategy import metrics as mt
        h = pd.DataFrame([['2021-01-08', 'A', .5], ['2021-01-08', 'B', .5],
                          ['2021-01-15', 'B', 1.]], columns=['signal_date', 'PERMNO', 'weight'])
        r = pd.DataFrame([['2021-01-08', 'A', '2021-01-18', .1, 1],
                          ['2021-01-08', 'B', '2021-01-18', np.nan, 0],
                          ['2021-01-15', 'B', '2021-01-25', .02, 1]], columns=[
                              'signal_date', 'PERMNO', 'next_execution_date',
                              'execution_to_next_execution_total_return', 'usable'])
        calendar = r.groupby('signal_date')[['next_execution_date']].first()
        independent = A.independent_weekly(h, r, calendar, 10).set_index('signal_date')
        source = mt.weekly_path(h, r, '2021-01-31').set_index('signal_date')
        np.testing.assert_allclose(independent.gtr, source.gtr)
        np.testing.assert_allclose(independent.net_return, mt.net_returns(source, .001))
        self.assertEqual(independent.unpriced_weight.iloc[0], .5)
        self.assertAlmostEqual(independent.net_return.iloc[0], (1-.001)*1.05-1)


if __name__ == '__main__':
    unittest.main()
