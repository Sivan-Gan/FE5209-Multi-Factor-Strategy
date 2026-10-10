# Formal WRDS Canonical CRSP Cash-Dividend Extension

## Verdict

**CANONICAL DIVIDEND EXTENSION — PASS WITH WARNINGS**

## Scope and immutability

This is a separate event-level SQLite extension. It does not mutate the base canonical SQLite store, CRSP `DlyRet`, raw data, or return accounting.

## Raw schema and event grain

Raw fields used: `permno`, `disexdt`, `disseqnbr`, `distype`, `disordinaryflg`, distribution type/detail fields, `disdivamt`, `disfacpr`, and `disfacshr`. The retained natural key is `(PERMNO, DisExDt, DisSeqNbr)`.

## Frozen classification reconciliation

The retained formal-security scope has 43,436 ordinary cash-dividend events, 1,164 Phase-2B split/share-adjustment events, and 18 nonordinary events, exactly reconciling to the accepted Phase-2B counts.

## Split-basis contract

For an accepted standard `FRS/STKSPL` or `FRS/STKDIV` event, CRSP defines `DisFacShr` as additional shares per old share. Its share multiplier is therefore `1 + DisFacShr`. An ordinary cash dividend D on date e is expressed on a later signal-date s share basis as `D / product(1 + DisFacShr_q)` for accepted standard split events q with `e < q <= s`. This follows CRSP's factor definition; it is not calibrated from yields. The source is the CRSP Data Descriptions Guide: https://www.crsp.org/crsp_pdf/crsp-us-stock-indexes-databases-data-descriptions-guide-crspaccess/.

A nonstandard event that changes the share basis, or a same-ex-date share-action ordering ambiguity, makes the affected dividend signal unavailable rather than guessed. The raw event layer retains those flags for downstream attribution.

## Timing and coverage

Event dates range from 2004-01-02 through 2025-12-31; there are 0 post-cutoff / 2026 events. A future event is never assigned to an earlier signal. A zero dividend is permitted only when the complete `(signal_date-365 days, signal_date]` raw-coverage window exists.

## Integrity

Raw Distribution SHA256 before/after: `2cce09c967f1474733b6a0ff691099f6a7083c42cacb036dba13ca18fd046245` / `2cce09c967f1474733b6a0ff691099f6a7083c42cacb036dba13ca18fd046245`. Base canonical logical hash before/after: `de1bf079e6399025ebde6a81883bbad21aec9c5443dbb39f7ca2f00b5a409c6e` / `de1bf079e6399025ebde6a81883bbad21aec9c5443dbb39f7ca2f00b5a409c6e`. Extension logical hash: `f5e1edcf58e28a96cdc4e1cb24ec2d38bc4917a71058fbeccd5d7e1a9228d82d`.

## Warning

The extension is formal within the delivered 2004–2025 CRSP coverage. Events after the formal cutoff are deliberately absent, and nonstandard share-basis actions cause explicit factor-level unavailability rather than an inferred adjustment.
