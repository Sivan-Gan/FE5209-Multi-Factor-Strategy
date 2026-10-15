# SGX LSEG fundamentals team handoff

## Scope

This package combines the existing daily SGX price dataset with 102 annual LSEG company-fundamentals source workbooks. The source workbooks are retained unchanged under `raw_lseg_annual/`.

## Key files

- `data/prices_daily_2006.csv`: daily OHLC, adjusted close, volume and derived trading-value fields from the earlier SGX price pipeline.
- `data/stock_universe_six_sectors.csv`: original universe and six-sector mapping.
- `data/corporate_actions_2006.csv`: price-side corporate-action reference data.
- `data/fundamentals_lseg_raw.csv`: one row per source workbook column. Raw duplicates are retained for audit.
- `data/fundamentals_lseg_panel_unverified_pit.csv`: one annual record per usable company/fiscal-period end, suitable for cleaning and factor research only after PIT dates are supplied.
- `reports/fundamental_coverage_lseg.csv`: per-company coverage and source currency.
- `reports/unresolved_lseg_exports.csv`: exports not admitted into the usable fundamental panel.

## Important limitations

1. Annual accounting values are in their original reporting currency and source scale (usually millions). Do not compare currency-denominated values across companies until conversion is explicitly performed.
2. `reported_date_source_field` is retained exactly as exported. It is **not** asserted to be an actual public-announcement timestamp. `available_date` is blank and `PIT_usable=False` for every record.
3. Four exports are marked unresolved/incomplete rather than silently substituted or deleted. Their price data remains available for price-only factors.
4. Companies with shorter history were listed later or have limited provider coverage. Their absent pre-listing years are missing, not zero.
5. Financial institutions, REITs, and non-SGD reporters retain source-specific accounting definitions. Use sector-aware factor definitions.

## Current counts

- Raw LSEG workbooks: 102
- Usable annual source workbooks: 98
- Unresolved/incomplete exports: 4
- Raw annual source observations: 1819
- Deduplicated usable annual panel observations: 1805
