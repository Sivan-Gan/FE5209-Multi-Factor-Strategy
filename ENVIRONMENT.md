# FE5209 Environment

## Recommended runtime

Use Python **3.12** (the delivery environment was validated with Python 3.12.8 on macOS). Python 3.12+ is expected to work when the declared dependency bounds are respected.

## Setup

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

macOS and Linux use the same commands apart from the Python launcher available on the host. `.venv/` is local and ignored by Git.

## Dependency scope

Tracked runtime code directly imports `pyarrow` for Final Factor Handoff Parquet export/loading and `wrds` only for the separately governed supplemental-extraction client. Core construction and tests otherwise use the Python standard library, including `sqlite3` and `unittest`.

Validated local versions at delivery preparation:

| Component | Version / status |
| --- | --- |
| Python | 3.12.8 |
| pyarrow | 26.0.0 |
| wrds | 3.5.0 |
| pandas / numpy | available indirectly in the local environment; not direct tracked imports |
| scipy / statsmodels / duckdb / pytest | not required by tracked code and not installed in the delivery environment |

No credentials are stored by these setup steps. If a separately authorized WRDS operation needs authentication, the receiver must use their own institutionally managed access outside Git.

## Validation

```bash
python -m unittest tests.test_github_delivery -v
python scripts/smoke_test_handoff.py
python -m unittest discover -s tests -p 'test_*.py'
```

The first two commands run without vendor raw data and without the external full handoff Parquet files. The full suite may require the documented local research stores.
