# 2026 Recent Extension Canonical Market Layer

The 2026 market layer is a separate `RECENT_EXTENSION_MIXED_SOURCE` artifact.
It is not part of the Formal 2006–2025 CRSP canonical history and must not be
described as such.

Its membership input is the audited 2026 point-in-time S&P 500 reconstruction.
Market observations come from immutable WRDS Compustat Security Daily extracts
and only the audited transition supplements. Ordinary daily returns use
`(PRCCD / AJEXDI * TRFD)_t / (PRCCD / AJEXDI * TRFD)_(t-1) - 1`; `TRFD` is
used as delivered and is never divided by 100. Market capitalization is
`ABS(PRCCD) * CSHOC`.

Only the already authorized 70 structural-null identities and Ciena
(`064356/01/171779309`) may use the constant-TRFD adjusted-price-ratio return
recovery. This recovery does not populate the source `TRFD` field. The nine
remaining structural-null identities and the three new-CTVA startup dates are
explicit return no-fills. DAY (3) and HOLX (2) are explicit market-cap no-fills.

The CTVA/VYLR temporal mapping uses the audited legacy CTVA economic series
through 2026-09-30, VYLR from 2026-10-01, and separate new CTVA (`077301/01`)
from 2026-10-01. No return is constructed across an identity boundary into new
CTVA. The legacy-CTVA-to-VYLR return sequence is allowed only under the audited
same-source continuity record; the EQR/VMRK transition uses the audited
2026-08-18 continuity mapping only.

The separate benchmark is Compustat S&P 500 Comp-Ltd (`GVKEYX 000003`, `I0003`)
with return `PRCCDDIV_t / PRCCDDIV_(t-1) - 1`. The layer is an input artifact
only; it does not authorize factor construction, portfolio construction, or
strategy evaluation.
