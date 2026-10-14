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

The project uses the following direct runtime dependencies:

| Component | Purpose |
|---|---|
| pyarrow | Parquet data loading and export |
| wrds | Authorized WRDS data extraction |
| pandas | Data processing, portfolio returns and analytics |
| numpy | Numerical calculations |
| scipy | Statistical tests and numerical methods |
| statsmodels | OLS regression and HAC/Newey-West inference |
| matplotlib | Performance and risk visualizations |

Python standard-library modules such as `sqlite3` and `unittest` do not require separate installation.

Install dependencies using:

```bash
python -m pip install -r requirements.txt
```

The recommended Python version is 3.12 or later.

The dependency versions in `requirements.txt` specify compatible ranges rather than an exact environment lock.

For reproducibility, record the actual installed versions when preparing a formal research delivery.

## Validation

```bash
python -m unittest tests.test_github_delivery -v
python scripts/smoke_test_handoff.py
python -m unittest discover -s tests -p 'test_*.py'
```

The first two commands run without vendor raw data and without the external full handoff Parquet files. The full suite may require the documented local research stores.
