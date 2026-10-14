# WRDS 2026 DIVIDEND_CURRENT Final Semantic Closure

## Result

**CRSP ORDINARY FLAG SEMANTICS NOT REPRODUCIBLE FROM COMPUSTAT**.

This is the final read-only source-investigation result. It changes neither the frozen Dividend definition nor any data, canonical object, code, factor score, or historical output.

## Delivered raw controls and integrity

| Control | Raw file | SHA-256 | Rows | Compustat identity | Frozen CRSP control |
|---|---|---|---:|---|---|
| TDG 2024 / 2025 included | `compustat_security_daily_dividend_validation_4stocks_2024_2025_raw.csv.gz` | `8882a762360ac1ba84a8fa42668a91226e97cbc9cdee21a0dfb12a2b0cc361a3` | 2,008 | GVKEY 148349 / IID 01 / PERMNO 91152 | `Y/SD/SDIV`, ordinary included |
| Caremark excluded | `compustat_security_daily_caremark_special_followup_raw.csv.gz` | `043aca5556fc544ad6fe7657b1f21b29ae8aeb7db449fd7ecd11eeb3903ed53e` | 34 | GVKEY 031549 / IID 01 / PERMNO 81262 | `N/SD/SDIV`, excluded |
| Dell excluded | `compustat_security_daily_dell_special_followup_raw.csv.gz` | `946ebc875a3196f7d29525ade5157829c26aa680a11586e33c2353e9c2c04ab0` | 42 | GVKEY 014489 / IID 01 / PERMNO 11081 | `N/SD/SDIV`, excluded |

The frozen accepted CCM identities agree with every raw `GVKEY`/`IID`/`LPERMNO` control. Dell’s raw historical CUSIP is `24702R101`, while the accepted CCM-delivered CUSIP is `24703L202`; this difference is preserved in the comparison and no identifier is replaced.

## Event-date alignment

| Control | frozen CRSP ex-date | Compustat `DATADATE` carrying `DIV=DIVSP` | treatment |
|---|---|---|---|
| TDG 2024 | 2024-10-04 | 2024-10-04 | exact |
| TDG 2025 | 2025-09-02 | 2025-09-02 | exact |
| Caremark | 2007-03-22 | 2007-03-21 | one trading day before; retained explicitly, not aligned/fill-adjusted |
| Dell | 2013-10-30 | 2013-10-30 | exact |

Caremark's Compustat row date also equals the frozen CRSP record date (2007-03-21), but no field-definition inference or date substitution is made.

## Included versus excluded taxonomy

| Control | CRSP `disordinaryflg` / inclusion | `DIV` | `DIVD` | `DIVSP` | `DVI` | `AJEXDI` | `TRFD` | `CAPGN` / `CHEQV` |
|---|---|---:|---|---:|---|---|---|---|
| TDG 2024 | Y / included | 75.0000 | blank | 75.0000 | 0.00000000 | 1.00000000 | 2.54692351 | blank / blank |
| TDG 2025 | Y / included | 90.0000 | blank | 90.0000 | 0.00000000 | 1.00000000 | 2.72258956 | blank / blank |
| Caremark | N / excluded | 7.5000 | blank | 7.5000 | 0.40000000 | 1.00000000 | 1.12126957 | blank / blank |
| Dell | N / excluded | 0.0200 | blank | 0.0200 | blank | blank | 1.03531080 | blank / blank |

All four controls therefore have the same effective delivered dividend taxonomy: positive `DIV`, equal positive `DIVSP`, and missing `DIVD`. The full side-by-side record for every delivered field plausibly relevant to dividend classification, timing, adjustments, corporate actions, identities, status, and market context is in `dividend_special_final_included_excluded_comparison.csv`.

## Deterministic-rule test

No semantically defensible delivered Compustat field or field combination distinguishes the included TDG controls from both excluded controls:

- `DIV`, `DIVD`, and `DIVSP` fail directly: their effective taxonomy is identical across all four controls.
- `DVI`, dates, `AJEXDI`, and `TRFD` either lack a common excluded pattern or are rate/timing/adjustment/return fields, not documented ordinary-dividend classifiers.
- `CAPGN` and `CHEQV` are blank in all four rows.
- Security status, exchange, identity, and current-listing-related fields are prohibited as dividend classifiers and lack the required economic meaning.
- Caremark and Dell each have a same-date frozen CRSP nonstandard corporate-action record, but the delivered Compustat event fields do not expose a matching ordinary/nonordinary classifier. That fact cannot be converted into a heuristic.

No amount threshold, company identity, listing status, future information, `DIVD + DIVSP`, `DIV`, `DIVSP`, `TRFD`, or heuristic classifier is used.

## Final Dividend decision

| factor / evidence period | status | reason |
|---|---|---|
| `DIVIDEND_CURRENT`, 2006–2025 | **AVAILABLE** | Formal CRSP Distribution CIZ under the frozen definition |
| `DIVIDEND_CURRENT`, 2026 `RECENT_EXTENSION_MIXED_SOURCE` | **NOT AVAILABLE** | `CRSP_DISTRIBUTION_TAXONOMY_NOT_REPRODUCIBLE` |

The frozen factor definition is unchanged. No 2026 Dividend score may be constructed from `DIVD + DIVSP`, `DIV`, `DIVSP`, `TRFD`, or a heuristic classifier.

## Full 20-factor consequence

The final 2026 research-factor state is **18 / 20 available or ready with governed missingness**. The two unavailable diagnostic factors are:

1. `LOWRISK_IVOL` — `CRSP_1000080_2026_SOURCE_UNAVAILABLE`.
2. `DIVIDEND_CURRENT` — `CRSP_DISTRIBUTION_TAXONOMY_NOT_REPRODUCIBLE`.

Neither is a member of the current Final K=4 (`QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, `INVESTMENT_CURRENT`); neither blocks it.

## Scope confirmation

Only the three authorized closure artifacts were created. No raw source, canonical data, SSOT, factor code, factor output, Git history, or remote state was changed.
