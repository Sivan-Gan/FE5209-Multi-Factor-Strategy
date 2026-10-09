# Historical Market Cap Specification — DEV Acceptance Report

- **Evidence stage:** `DEV_CURRENT_UNIVERSE`
- **Decision:** PASS WITH WARNINGS
- **Scope:** source/share/price/split/multi-class specification and aggregate audit only. No historical market-cap panel, valuation ratio, Size, neutralization, IC, portfolio, or strategy result is created.

| Check | Status | Evidence / disposition |
| --- | --- | --- |
| Raw package integrity | PASS | 508/508 supplied payload hashes passed. |
| Shares PIT timing | PASS | Primary selected shares with `filed_date > signal_date`: 0. |
| Direct-share validity diagnostic | WARN | 824 selected primary security-signal observations have reported shares <= 1; no automatic exclusion rule is approved. |
| Exact signal-date price rule | PASS WITH WARNINGS | The audit never substitutes a future/next-day/forward-filled price. Missing or invalid exact-date prices remain unavailable; zero-volume/review handling requires sign-off. |
| Split-event tests | PASS WITH WARNINGS | Standard forward and reverse vendor split factors are tested; nonstandard vendor action factors are explicitly excluded from the recommended chain pending review. |
| Multi-class safety | WARN | Issuer shares are not allocated across the six securities of the three multi-class CIKs; they remain `MULTICLASS_UNRESOLVED`. |
| Source naming | PASS | Current method is an ex-post delivery-basis `estimated_market_cap` candidate, never an exact contemporaneous or Formal PIT market-cap claim. |
| Prohibited construction | PASS | No `us_weekly_market_cap_dev` table/panel or factor output is written. |

## DEV blockers

No blocker prevents a specification handoff. Construction remains blocked pending explicit sign-off of price/shares/split/multi-class/missing-data rules.

## DEV warnings

Yahoo delivery prices are retrospectively action-adjusted; reported shares are periodic and stale; direct-tag values require a signed validity/outlier policy; RDQ timestamps are absent; current-universe survivorship bias and non-PIT GICS remain; vendor action factors include nonstandard values; and multi-class issuer allocation is unavailable.

## Formal PIT limitations

CRSP historical shares/market cap, historical identifier/class history, historical constituents/GICS, delistings, and approved PERMNO/GVKEY links remain future Formal PIT inputs. They do not make this DEV specification fail.
