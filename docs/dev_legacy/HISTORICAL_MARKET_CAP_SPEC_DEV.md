# Historical Market Cap Specification — DEV

## Status and scope

- **Evidence stage:** `DEV_CURRENT_UNIVERSE`
- **Specification-audit decision:** `PASS WITH WARNINGS`
- **Construction decision:** **not authorized**. This document defines auditable
  candidates and questions for human sign-off; it does not create a historical
  market-cap panel.
- **Name if later approved for the current inputs:** `estimated_market_cap`,
  with `market_cap_source=SEC_YAHOO_ESTIMATE_DEV`. It must not be called a
  contemporaneous vendor market-cap field or Formal PIT market cap.

The frozen manual requires price, market-cap, ADV, currency, timing, and PIT
controls to be fixed before dependent research. It identifies E/P, B/P, and FCF
Yield as Value inputs and market cap/log market cap as Size candidates, but it
does not freeze a price field, share tag, split convention, or issuer-versus-
security level. This DEV review fills none of those gaps silently.

Only aggregate audit evidence and this specification were created. In
particular, this phase creates no `us_weekly_market_cap_dev`, E/P, B/P, FCF
Yield, Size, financial ratio, factor, neutralization, IC, portfolio, FMOM,
backtest, or strategy result.

Machine-readable evidence is in:

- [SEC share-tag coverage](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/sec_share_tag_coverage_dev.csv)
- [SEC share staleness](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/sec_share_staleness_dev.csv)
- [Yahoo price/split-basis audit](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/yahoo_split_basis_audit_dev.csv)
- [split-event validation](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/market_cap_split_event_validation_dev.csv)
- [multi-class audit](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/multiclass_market_cap_audit_dev.csv)
- [candidate-method comparison](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/market_cap_candidate_method_comparison_dev.csv)
- [acceptance report](/Users/ganjunhui/Desktop/课堂/FE5209/FE5209/outputs/audit/historical_market_cap_spec_acceptance_report.md)

The raw-package manifest again verifies 508/508 supplied payloads. No file in
`raw_package/` was edited.

## 1. Economic level: issuer versus security

| Use | Recommended economic level | Current DEV consequence |
| --- | --- | --- |
| E/P, B/P, FCF Yield | **Issuer level.** The financial numerator is an SEC issuer fact, so its denominator must be total issuer equity value. | A one-security issuer can be represented by its sole listed security after all controls pass. A multi-class issuer cannot divide issuer earnings/equity/FCF by one class price times duplicated issuer shares. |
| Size and Size neutralization | **Issuer level, subject to sign-off.** This avoids representing one issuer twice merely because it has two listed classes. | The same one-security-issuer restriction is the only safe current DEV candidate. A class-level Size exposure needs class-specific shares and a frozen aggregation/portfolio rule. |

This is an economic-level recommendation, not a constructed field. It leaves
the frozen manual's downstream Size transformation and neutralization choices
unchanged.

The delivered master has 503 current securities, of which 500 are SEC-mapped
to 497 issuers. Three master tickers (`FDXF`, `HONA`, `Q`) are not SEC-mapped
and are unavailable for an SEC-share candidate. Six mapped securities belong to
three multi-class issuers: `NWS/NWSA`, `GOOG/GOOGL`, and `FOX/FOXA`. There is
no delivered share-class field or demonstrated SEC class-specific share
allocation. They are therefore `MULTICLASS_UNRESOLVED`, not implicitly split
or duplicated.

## 2. Price evidence and signal-date rule

The canonical daily table contains 2,392,900 rows. `close` and `adj_close`
differ on 1,998,160 rows; there are 28,556 positive-dividend rows and 331
non-zero vendor action-factor rows. The source documentation and download
script establish that `close` is the split-adjusted delivery field, while
`adj_close` additionally reflects dividends and other adjustments.

**Recommended price candidate:** the positive `close` on the exact weekly
signal date. `adj_close` is not a price-level market-cap input, because a
dividend/total-return adjustment must not alter the equity-price level used in
a market-cap denominator.

The audit has 495,410 positive exact signal-date closes across the 500
SEC-mapped securities and 1,081 signal dates. It has 45,090 missing exact-date
bars, 756 zero-volume exact-date bars, and 25,023 bars on a review security
(these diagnostics can overlap). The rules below are proposed, not applied to
a panel:

1. Never use the next trading date, a forward fill, or a prior-date substitute.
   A missing/non-positive exact-date close is unavailable.
2. Preserve zero-volume and `review` status. Whether either is unavailable or
   a flagged usable observation requires sign-off; the existing quality flag is
   not an automatic exclusion rule.
3. Preserve the close/adj-close basis and action provenance with every future
   estimate. Do not transfer the return-factor use of `adj_close` to market
   capitalization.

“Exact signal-date” means an actual row at the canonical weekly cutoff. It
does not make the delivered historical close a contemporaneous point-in-time
vendor snapshot: the delivered Yahoo series can be retrospectively adjusted.

## 3. SEC outstanding-share candidates and PIT timing

| Candidate | Actual raw support | Semantics and proposed role |
| --- | ---: | --- |
| `dei:EntityCommonStockSharesOutstanding`, unit `shares` | 469 issuers; 28,051 observations; all instant | Broadest direct candidate. SEC describes a cover-page count of outstanding shares or other ownership interests, and its description explicitly raises multi-class/instrument context. **Recommended primary candidate**, conditional on validity, class, form, and conflict controls. |
| `us-gaap:CommonStockSharesOutstanding`, unit `shares` | 393 issuers; 37,258 observations; all instant | Direct common-stock-outstanding concept but narrower. **Conditional fallback candidate only**; no automatic tag fallback is approved. |
| Weighted-average shares, issued/authorized/treasury/option/repurchase/transaction tags | Present in the raw source | Not equivalent to point-in-time common shares outstanding. They are not candidates for this specification. |

The primary raw forms include 10-K, 10-Q, 10-K/A, 10-Q/A, 20-F, 20-F/A,
8-K, and transition forms. The audit candidate keeps only 10-K/10-Q/20-F
families (including amendments only under their own filed date); it does not
silently enable 8-K. That form boundary needs sign-off.

For the 500 mapped securities × 1,081 weekly signals, the primary latest-
eligible selector has:

| Primary selection status | Security-signal cells |
| --- | ---: |
| `AVAILABLE` | 374,546 |
| `NO_ELIGIBLE_FILING` | 165,627 |
| `AMBIGUOUS` same latest filing date | 29 |
| `OPEN_IMPLEMENTATION_ISSUE` non-unique provenance | 298 |

It enforces `filed_date <= signal_date`: the audit finds **zero** selected
primary observations filed after the signal. An amendment is eligible only
from its own filing date. Same-latest-filing conflicts and non-unique
provenance remain missing; neither value, input order, nor a later filing is a
tie-break.

### Staleness and raw-value diagnostic

For available primary selections, the age from share as-of date is 57 days at
the median and 133 days at p95 (mean 152.1; maximum 6,197). Age from filing
date is 45 days at the median and 113 days at p95 (mean 140.5; maximum 6,161).
There is **no approved staleness cutoff**. These figures are descriptive, not
an authority to forward-fill an unavailable fact or to declare stale facts
valid indefinitely.

The audit also detects 824 selected primary security-signal cells with a raw
reported value no greater than one. For example, CSX has a selected
`2011-04-01` primary fact with value `1` from filing
`0000277948-11-000017`. This is a source-validity warning, not a license to
repair a value. Before construction, the team must sign a deterministic
validity/outlier policy and its provenance/audit treatment; it must not simply
take an older fact or an alternate tag.

## 4. Yahoo split basis and event evidence

The audit classifies 227 of the 331 non-zero vendor action factors as common
forward/reverse split ratios and 104 as nonstandard vendor-action factors. Ten
standard factors are reverse split ratios. The event file retains the prior,
event, and next close and nearest pre/post reported share facts without
producing market-cap values.

Representative standard events demonstrate the delivered price basis:

| Event | Vendor factor | Delivered close event/prior ratio | Finding |
| --- | ---: | ---: | --- |
| AAPL, 2014-06-09 | 7 | 1.0160 | `close` does not show a 1/7 price jump. Applying a signal-date 7× share multiplier would create a mechanical ~7× market-cap discontinuity. |
| AAPL, 2020-08-31 | 4 | 1.0339 | Same conclusion for the 4:1 event. |
| AMZN, 2022-06-06 | 20 | 1.0199 | Same conclusion for the 20:1 event. |
| C, 2011-05-09 | 0.1 | 0.9770 | Reverse-split case: `close` does not show the tenfold raw-price transformation. |

Therefore, **rejected method:** multiply shares dynamically by the split factor
when the signal date crosses a split. That creates a mechanical discontinuity
against a delivery close series already normalized for the action.

**Candidate DEV alignment method:** for a reported share fact, multiply its
units once by the cumulative sequence of *audited standard* split factors
strictly after the share as-of date through the delivery price basis, then
multiply by the exact-date `close`. The factor is tied to the share fact's
provenance, not recomputed at each signal date. Multiple standard factors
therefore chain multiplicatively; a reverse factor is handled in its supplied
direction. This is a delivery-basis reconciliation only, not proof that the
Yahoo history was known in real time.

The nearest pre/post SEC facts permit 133 standard-event share-pair reviews
(six reverse events). Their reported ratios are not uniformly clean: some
facts are anomalous or otherwise not comparable, including the CSX diagnostic
above. Consequently, event review supports the *direction* of the standard
delivery-basis chain but does not validate every raw share observation. All
104 nonstandard factors are excluded from the candidate chain until their
economic event semantics are individually approved.

## 5. Candidate-method comparison

Coverage below is a source-availability diagnostic before a signed share-
validity policy, not acceptance into a research panel. It uses the 500
SEC-mapped securities and preserves missing exact signal dates.

| Method | Mean weekly coverage | Split basis | Multi-class treatment | Status |
| --- | ---: | --- | --- | --- |
| `SEC_REPORTED_SHARES_X_CLOSE_UNALIGNED` | 69.11% | Fails: reported share units are not aligned to the delivery price basis. | Fails: issuer shares cannot be assigned to one listed class. | Not implementable baseline. |
| `SEC_YAHOO_STANDARD_SPLIT_CHAIN_ESTIMATE` | 63.59% | Candidate only for the audited standard delivery-basis chain; excludes a chain containing nonstandard action factors. | Candidate only for one-security issuers; excludes unresolved multi-class CIKs. | **Recommended baseline subject to sign-off.** |
| `CRSP_FORMAL` | Not available | Future vendor-defined formal convention. | Requires verified class/PERMNO identity. | Not implementable from this package. |

The split-chain candidate has 467 one-security issuers with at least one
eligible primary-share selection. Its average coverage is lower because it
does not hide nonstandard-action or multi-class uncertainty. Its appropriate
output name remains `estimated_market_cap`.

## 6. Replaceable source abstraction

No panel is defined here. If construction is later signed, its contract should
expose source and basis attributes at minimum:

| Contract attribute | DEV candidate | Future formal replacement |
| --- | --- | --- |
| `market_cap_source` | `SEC_YAHOO_ESTIMATE_DEV` | `CRSP_FORMAL` (or a separately approved equivalent) |
| Economic level | Issuer value for issuer accounting numerators; one-security issuers only | Verified issuer/security/PERMNO level with approved aggregation rule |
| Price convention | Exact signal-date Yahoo `close`, delivery-basis action normalized | Approved CRSP/PERMNO price and market-equity convention |
| Shares convention | Latest eligible primary DEI shares plus approved standard delivery-basis split chain | CRSP historical shares/market equity with documented convention |
| Identity | Current master ticker/CIK only | Historical security/identifier/class links and delisting coverage |
| Universe/GICS | Current S&P 500/current GICS; DEV only | Historical constituents and historical GICS; `FORMAL_PIT_UNIVERSE` |

The DEV candidate remains affected by current-S&P-500 survivorship bias,
non-PIT GICS, periodic share reporting, unavailable RDQ, Yahoo retrospective
adjustment, unresolved actions, and missing historical member/delisting
coverage. It cannot close formal Gate A.

## 7. Recommended sign-off set

| Decision for human sign-off | Recommendation | Reason |
| --- | --- | --- |
| Economic level for Value | `RECOMMEND_SIGNOFF`: issuer level; one-security issuer candidate only | Aligns issuer SEC numerators to issuer equity value and prevents class duplication. |
| Economic level for Size/neutralization | `RECOMMEND_SIGNOFF`: issuer level for current DEV candidate | Avoids duplicating multi-class issuers; class-level exposure needs separate source data/rule. |
| Primary tag/form family | `RECOMMEND_SIGNOFF`: DEI `EntityCommonStockSharesOutstanding`, unit `shares`, 10-K/10-Q/conditional 20-F families; amendment effective on own filed date | Broadest direct instant candidate; retains conservative PIT timing. |
| Tag fallback | `RECOMMEND_SIGNOFF`: no automatic fallback to US-GAAP outstanding shares | Direct tags may differ in reporting context; coverage is not semantic equivalence. |
| PIT/conflict rule | `RECOMMEND_SIGNOFF`: `filed_date <= signal_date`; conflicts/provenance uncertainty are unavailable | Zero future-filing selections; preserves prior conservative SEC governance. |
| Price field/timing | `RECOMMEND_SIGNOFF`: exact-date positive `close`; never `adj_close`, next-day, or fill | `adj_close` has dividend/other adjustments; strict timing is auditable. |
| Standard split handling | `RECOMMEND_SIGNOFF`: fact-provenance delivery-basis chain; no dynamic signal-date multiplier | Real standard events reject the dynamic method mechanically. |
| Naming | `RECOMMEND_SIGNOFF`: `estimated_market_cap` and `SEC_YAHOO_ESTIMATE_DEV` | Makes the ex-post delivery basis and DEV limitations explicit. |

## 8. Decisions still required before construction

1. **Share-value validity rule:** how to treat values such as the 824 selected
   cells at or below one, and how the decision is audited. No automatic repair,
   fallback, or stale-value substitution is approved.
2. **Nonstandard Yahoo action factors:** whether a documented action-specific
   reconciliation can ever include them. Until then, they remain unavailable
   for the split-chain candidate.
3. **Multi-class issuers:** whether to obtain class-specific share/market-cap
   data, build a separately approved issuer aggregation, or exclude them from
   market-cap-dependent DEV work. The current package cannot settle this.
4. **Zero-volume/review/invalid-price treatment:** decide whether such an
   exact-date price is unavailable or usable with an explicit flag. Missing or
   non-positive exact-date prices are already proposed unavailable.
5. **Staleness cutoff:** decide whether the descriptive ages warrant a maximum
   allowed age. None is imposed by this audit.
6. **20-F and amendment form policy for shares:** confirm the conditional form
   treatment and scope; do not import an accounting-tag mapping by assumption.
7. **Formal source choice:** approve CRSP/Compustat or equivalent fields,
   identity links, historical universe/GICS, delisting treatment, and its own
   market-equity convention before `FORMAL_PIT_UNIVERSE` construction.

## Entry condition for construction

The specification audit passes with warnings, but **it is not ready to build a
canonical market-cap object**. Construction can be separately authorized only
after the seven decisions above are explicitly resolved and acceptance criteria
are added for share validity, action-chain provenance, multi-class disposition,
price quality, staleness, and raw-manifest integrity. Any resulting current
source object remains DEV-only and must be rerun with historical
constituents/GICS and an approved formal source before it can support a formal
Gate A decision.
