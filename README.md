# FE5209

This repository is the clean source-data foundation for the US–Singapore
cross-market quantitative research project. It now contains reproducible DEV
canonical US market inputs, price-factor preprocessing inputs, and a PIT SEC
fundamental-input/provenance layer; it contains no ratio, fundamental-factor,
portfolio, IC, FMOM, strategy, or backtest results.

The project has two evidence layers: current development uses
`DEV_CURRENT_UNIVERSE` (current S&P 500/current GICS plus the supplied SEC and
Yahoo data); a future rerun using historical constituents and historical GICS
is `FORMAL_PIT_UNIVERSE`. DEV results are preliminary and cannot close the
formal Gate A factor-set freeze. SEC Company Facts and `filed_date` support the
conservative availability rule `filed_date <= signal_date`; unavailable RDQ is
a timing limitation, not a DEV coding blocker.

## Authoritative materials

- `docs/US_SG_Cross_Market_Quant_Research.md` is the frozen research framework
  and methodological single source of truth.
- `raw_package/US_source_data_2026-10-08/` is immutable raw evidence. It is a
  source-layer handoff, not a factor-ready or point-in-time (PIT) research
  panel.
- `docs/PROJECT_UNDERSTANDING.md` records the current evidence-supported
  project state, boundaries, and pending inputs.
- `docs/DATA_CONTRACT_DEV.md` defines the immutable source-layer contract.
- `docs/PRICE_FACTOR_ENGINEERING_DEV.md` defines the DEV canonical market and
  price-factor-input contract.
- `docs/SEC_FUNDAMENTAL_INPUT_SPEC_DEV.md` records the DEV SEC raw-fact
  selection proposal, recorded DEV input controls, and remaining canonical-input
  decisions.
- `docs/CANONICAL_PIT_FUNDAMENTALS_DEV.md` defines the constructed signed-tag
  SEC fact layer and weekly PIT availability/provenance contract.
- `docs/HISTORICAL_MARKET_CAP_SPEC_DEV.md` records the DEV-only SEC-share,
  Yahoo-price/split, and multi-class specification audit. It authorizes no
  historical market-cap construction.
- `docs/LEVERAGE_DEFINITION_TARGETED_AUDIT_DEV.md` records the approved DEV
  governance decisions and the evidence-backed, non-construction recommendation
  for the ordinary non-Financial/non-RE Quality Leverage input.

## Repository boundaries

| Location | Purpose |
| --- | --- |
| `raw_package/` | Immutable supplied evidence. Never edit, overwrite, or write derived data here. |
| `data/interim/` | Reproducible intermediate datasets. |
| `data/canonical/` | Accepted downstream research inputs only. |
| `data/external/` | Reserved for future external sources, including potential CRSP, Compustat, historical constituent, and historical GICS data. |
| `config/` | Frozen research parameters once they are formally determined; currently empty. |
| `src/` | Reusable calculation logic only. |
| `tests/` | Reproducible automated checks. |
| `outputs/` | Generated audit, research, strategy, cross-market, and report artifacts only. |

`src/data/source_audit.py` provides read-only Gate 0 auditing. Canonical data
in `data/canonical/`, reproducible staging data in `data/interim/`, and the
three approved raw/preprocessed price metrics under
`outputs/factor_research/price_factors/` are all labelled
`DEV_CURRENT_UNIVERSE`. No fundamental factor, IVOL, neutralized factor, IC,
portfolio, FMOM, strategy, or backtest result has been generated.

SEC Company Facts have a constructed canonical source-input and weekly PIT
availability/provenance layer under the signed exact-tag rules. It contains no
financial ratio, yield, market capitalization, or fundamental-factor value.
Exact Gross Profit, primary FCF inputs with a negative-CapEx exception,
conditional US-GAAP 20-F treatment, and ambiguity/open-provenance missingness
are documented in `docs/CANONICAL_PIT_FUNDAMENTALS_DEV.md`.

The historical Yahoo-price × SEC-shares estimated-market-cap repair route is
retired from the active codebase: its formal decision is
`STOP_DEV_MARKET_CAP_REPAIR`. `raw_package/` remains immutable DEV audit
evidence, and Yahoo/SEC inputs remain available for DEV audits and a possible
2026 recent market-data extension. The formal data pipeline will instead move
to WRDS. The legacy local market-cap object and its audit trail are retained
only as historical DEV evidence; no active pipeline may consume them or
describe them as Formal PIT market cap.
