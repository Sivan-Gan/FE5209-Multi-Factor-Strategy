# US weekly performance attribution, 2021–2025

This package attributes the existing Dynamic FMOM and Static Multi-Factor results from `member5-risk-analysis`, pinned to commit `7b48586e45b1c456478c8a977e96043bc3108dad`. It changes no upstream strategy configuration, holdings, trades, returns, or research decisions.

## Read the results

- [Chinese report](results/ATTRIBUTION_REPORT_ZH.md)
- [Self-contained HTML report](results/ATTRIBUTION_REPORT_ZH.html) — download to view in a browser; all eight charts are embedded.
- [English course-report draft](results/COURSE_ATTRIBUTION_SECTION_EN.md)
- [Accounting and source checks](results/qa_checks.csv)
- [Security contributors](results/stock_contributors_full_period.csv)
- [Sector contributors](results/sector_contributors_full_period.csv)
- [Field definitions](results/DATA_DICTIONARY.csv)

The report covers 259 completed weekly intervals. Net contributions include the cost-before-holding convention and are linked with pre-period net wealth. Cumulative contributions add to cumulative return, not CAGR. Signal-year tables, arithmetic return increments, and terminal wealth differences are explicitly distinguished.

Views include security/sector contributors; Dynamic-minus-Static holdings and cost effects; maximum drawdown contributors; frozen factor weights and holdings score exposures; the existing four-factor mimicking-portfolio diagnostic; and the M5 FF6 regression explanation. Regression and mimicking-portfolio explanations are not added to holdings P&L.

## Reproduce from this repository

Use Python 3.12+. From the repository root:

```bash
git lfs pull
python -m pip install -r attribution/us_hist_oos/requirements-attribution.txt
python -m unittest discover -s attribution/us_hist_oos -p test_attribution_engine.py -v
python attribution/us_hist_oos/run_attribution.py
```

To use already-downloaded LFS payloads from another local copy:

```bash
python attribution/us_hist_oos/run_attribution.py --data /path/to/that/repository
```

`source_contract.json` lists only the frozen upstream inputs used for validation, with LF-normalized text hashes and exact Git LFS payload hashes. The reader checks those specific files, rather than recursively scanning the repository. Source paths in published manifests are portable `repo:` / `data:` paths. Input immutability is verified after analysis.

The run regenerates two detailed security-week Parquets locally. Those larger observation-level intermediates are intentionally not committed, following the existing M5 handoff convention. The uploaded package includes the reports, eight PNG/SVG figure pairs, all CSV analysis tables, validation records and code; it does not duplicate the upstream Parquets, raw vendor data, environments, caches, or credentials. `output_manifest.csv` indexes the published results and excludes the two regenerated Parquets.

## Existing limitations retained

- Dynamic and Static each contain four unpriced held-security weeks under the original zero-return convention; all eight observations are disclosed.
- One inherited exit-cost observation has no verifiable same-signal-date historical sector and remains UNKNOWN.
- The report retains the M5 weekly NAV / 52-week convention. Final daily-versus-weekly reporting approval, team sign-off, and remaining upstream validation work are not closed by this attribution package.
- Source configuration and M4/M5 outputs are preserved. No OOS retuning or strategy construction is performed.
