# FE5209

This repository supports a US–Singapore cross-market quantitative research
project: develop and freeze a weekly, long-only US multi-factor strategy with a
factor-momentum tilt, then test its transfer to Singapore. Candidate factors
remain Value, Quality, Momentum, Low Risk, Investment, Dividend, and Size; they
are not a frozen final factor set.

## Authority and current status

- [US_SG_Cross_Market_Quant_Research.md](docs/US_SG_Cross_Market_Quant_Research.md)
  is the frozen research SSOT. It governs all research design, data contracts,
  Gates, factor definitions, strategy rules, and evidence claims.
- The active formal branch is `formal-wrds-rebuild`. Its formal US raw-data
  layer exists at `wrds_raw/US_WRDS_2026-10-09/`.
- The formal historical stack is CRSP + Compustat + CCM + Historical GICS +
  CRSP market/S&P 500 indexes + Fama–French/RF. Its market-data cutoff is
  `2025-12-31`.
- Raw-data placement is not Formal Gate 0 approval. No formal canonical data,
  factor result, portfolio, FMOM result, strategy backtest, or Gate A decision
  has been created from the WRDS layer.

## Formal timeline

| Stage | Period | Evidence / use |
| --- | --- | --- |
| Initialization / pre-sample buffer | 2004–2005 | Initialize long-lookback signals; not performance evaluation. |
| Development / warm-up | 2006–2010 | Validate pipeline and signals. |
| Factor selection | 2011–2015 | Gate A freezes the Final Factor Set. |
| Configuration selection | 2016–2020 | Gates B/C freeze FMOM and the US Base strategy. |
| Formal Historical OOS | 2021–2025.12 | `HIST_OOS_BASE`; primary formal US evidence. |
| Recent / operational extension | 2026.01–2026.09 | `RECENT_EXTENSION`; never combine with formal OOS headline statistics. |
| Live / forward test | 2026.10+ | `LIVE_OPS` after an actual strategy freeze. |

## Formal and DEV evidence boundaries

Formal historical research uses the WRDS stack after Formal Gate 0. Its
security key is PERMNO, its company key is GVKEY, and the formal mapping is
historical S&P 500 membership → CRSP PERMNO → time-valid CCM → Compustat GVKEY.
Historical GICS, CRSP `DlyCap`, and the formal CRSP total-return index series
are subject to the Gate 0 data contract.

`raw_package/US_source_data_2026-10-08/` remains immutable Yahoo + SEC
**DEV historical evidence** and a possible 2026 Recent Extension support
source. It is not the formal 2006–2025 research stack. The retired
Yahoo-price × SEC-shares historical-market-cap route has the formal status
`STOP_DEV_MARKET_CAP_REPAIR`; retained Yahoo price / SEC PIT infrastructure
may still support explicitly labelled DEV audits or the 2026 extension.

## Repository boundaries

| Location | Purpose |
| --- | --- |
| `raw_package/` | Immutable Yahoo + SEC DEV evidence; never write derived data here. |
| `wrds_raw/` | Formal WRDS raw-data layer; immutable source inputs for the formal rebuild. |
| `docs/dev_legacy/` | Archived DEV documentation. |
| `docs/formal_wrds/` | Reserved formal WRDS documentation. |
| `data/dev_legacy/` | Archived processed DEV datasets. |
| `data/interim/`, `data/canonical/` | Reserved formal WRDS intermediate and validated canonical datasets. |
| `outputs/audit/dev_legacy/`, `outputs/factor_research/dev_legacy/` | Archived DEV evidence. |
| `outputs/audit/wrds_formal/`, `outputs/factor_research/formal/` | Reserved Formal Gate 0 and formal factor-research outputs. |
| `config/`, `src/`, `tests/` | Shared frozen parameters, reusable implementation, and automated checks. |

`docs/PROJECT_UNDERSTANDING.md` is a project-state summary. It does not
override the SSOT.
