# Extreme SEC Shares Validation — DEV

# Extreme SEC Shares Semantic Validation — DEV Acceptance Report

- **Evidence stage:** `DEV_CURRENT_UNIVERSE`
- **Scope:** source-semantic audit and simulation only; the existing canonical market-cap object is not modified.
- **Decision:** `FAIL` for market-cap semantic closure.
- **Rule status:** `NOT_READY_FOR_FORMAL_SIGNOFF` for the relative transient-high-share candidate below.

## Population and source conclusion

The current diagnostic threshold identifies **20 selected DEI source observations** across **13 tickers**, propagating to **274 available ticker-weeks**. The threshold is only a discovery device; it is not the proposed production rule.

The source sequence shows 10^3/10^6 transient high-scale episodes. The delivered Yahoo action data has no contemporaneous standard or non-standard action that explains an affected boundary. SEC Company Facts retains form/date/accession/frame fields but not original XBRL dimensional context; this limitation is recorded rather than inferred.

## Candidate rule: EXTREME_SHARES_REJECTION_RULE

Within one issuer's eligible DEI primary-share filing sequence, identify a contiguous high-share run that subsequently reverts to a lower-scale direct DEI run by at least 100×. If the delivered standard-action chain between the runs does not explain the ratio, mark every observation in the transient high run `EXTREME_SHARES_UNRESOLVED` and unavailable. A run with normal-scale observations on both sides is `CONFIRMED_SCALE_ANOMALY`; an early-source run with only the subsequent reversion is `UNRESOLVED_EXTREME_SHARES`. Neither result implies a corrected value, alternate-tag fallback, or rescaling.

This is a relative, issuer-sequence rule. It does not reject a stable large issuer merely for exceeding 1e12 shares, and it is portable to a future formal source because it relies on source observation provenance and disclosed action evidence rather than ticker hardcodes. A standard/reverse split whose delivered action factor explains the boundary is exempt.

## Classification

| Classification | Source observations | Treatment |
| --- | ---: | --- |
| CONFIRMED_SCALE_ANOMALY | 17 | `EXTREME_SHARES_UNRESOLVED` / unavailable in the simulation |
| UNRESOLVED_EXTREME_SHARES | 3 | `EXTREME_SHARES_UNRESOLVED` / unavailable in the simulation |

## Simulated availability impact

| Measure | Window | Before | After | Change (pp) | Affected available ticker-weeks |
| --- | --- | ---: | ---: | ---: | ---: |
| market_cap_input | 2011–2015 | 69.74% | 69.62% | -0.1142 | 149 |
| market_cap_input | 2016–2020 | 80.28% | 80.25% | -0.0261 | 34 |
| market_cap_input | 2021–2026.09 | 88.60% | 88.60% | 0.0000 | 0 |
| value_two_of_three_input | 2011–2015 | 63.61% | 63.50% | -0.1142 | 149 |
| value_two_of_three_input | 2016–2020 | 77.83% | 77.81% | -0.0261 | 34 |
| value_two_of_three_input | 2021–2026.09 | 86.81% | 86.81% | 0.0000 | 0 |

## Distribution and jumps (simulation only)

Estimated market-cap max changes from 1.55077e+17 to 2.70048e+13; p99 changes from 5.71113e+11 to 5.20941e+11. No clipping or winsorization occurred.
The largest weekly jump before simulation is VTRS at 1.13067e+07×; after simulation it is VTRS at 1.13067e+07×. The full top-20 before/after rankings are in the jump audit.

## Mandatory case: ORCL

ORCL source accession `0001193125-12-401697` filed 2012-09-24 reports DEI shares `4,819,056,000,000,000`; the preceding normal value is `4,882,506,000` (about 987,004.6× lower) and the next normal value is `4,734,297,000` (about 1,017,903.2× lower). The same accession's diagnostic US-GAAP fact is `4,831,000,000`. No delivered action explains the episode. Classification: `CONFIRMED_SCALE_ANOMALY`.

## Mandatory case: VTRS

VTRS changes from `100` in `0001193125-20-290294` (filed 2020-11-10) to `1207082624` in `0001792044-21-000009` (filed 2021-03-01), a 1.20708e+07× source change. Persistent source issuer-transition sequence; no delivered split action and no raw Company Facts context/transaction metadata proves an economic explanation. It is not a transient >1e12 scale-anomaly run and is not included in this narrow rejection simulation.

## Boundary and next action

The 20-record evidence supports the candidate relative rule's design, but it is **not ready for formal sign-off** in this round. The post-rejection audit still has a 1,130万× VTRS jump and other million-level source-share jumps. Under the explicit closure condition, no patch/rebuild of the canonical object is recommended now: doing so would remove the 20 audited records while leaving unresolved source-semantic failures. The VTRS persistence/issuer-transition case is outside the `>1e12` population and must not be silently swept into this patch.

Formal PIT limitations remain unchanged: current-S&P-500 survivorship bias, current GICS, periodic SEC shares, absent RDQ and historical delisting universe, and Yahoo retrospective price/action adjustments. This report contains no factor, ratio, IC, portfolio, FMOM, or strategy result.
