"""Build the separate formal CRSP cash-dividend canonical extension.

The base formal canonical SQLite database is immutable.  This module reads the
already-audited CRSP CIZ Distribution delivery and emits a distinct event-level
SQLite extension, plus small tracked QA artifacts.  It never changes return
accounting, the base canonical database, or a factor score.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import sqlite3
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from src.data import wrds_canonical_build as canonical
from src.data import wrds_gate0_final as gate0
from src.data import wrds_gate0_phase1 as phase1
from src.data import wrds_gate0_phase2b as phase2b


FORMAL_CUTOFF = "2025-12-31"
RAW = Path("wrds_raw/US_WRDS_2026-10-09")
DISTRIBUTIONS = RAW / "crsp/CRSP_Distribution_CIZ_2004-01-01_2025-12-31_959_SP500_PERMNOs.csv.gz"
BASE_STORE = Path("data/canonical/wrds_formal/us_wrds_formal_canonical.sqlite")
STORE = Path("data/canonical/wrds_formal/extensions/us_crsp_cash_dividend_events.sqlite")
AUDIT = Path("outputs/audit/wrds_formal")
MANIFEST = AUDIT / "canonical_dividend_extension_manifest.csv"
QA = AUDIT / "canonical_dividend_extension_qa.csv"
REPORT = AUDIT / "WRDS_CANONICAL_DIVIDEND_EXTENSION_REPORT.md"
DETERMINISM = AUDIT / "canonical_dividend_extension_determinism.csv"
EVIDENCE_STAGE = "FORMAL_WRDS_CANONICAL_CASH_DIVIDEND_EXTENSION"
SOURCE_GUIDE = "https://www.crsp.org/crsp_pdf/crsp-us-stock-indexes-databases-data-descriptions-guide-crspaccess/"

EVENT_COLUMNS = (
    "permno", "event_ex_date", "disseqnbr", "distype", "disordinaryflg",
    "disfreqtype", "dispaymenttype", "disdetailtype", "distaxtype",
    "disorigcurtype", "cash_dividend_amount", "disfacpr", "disfacshr",
    "disdeclaredt", "disrecorddt", "dispaydt", "dispermno", "dispermco",
    "disamountsourcetype", "phase2b_classification", "ordinary_cash_dividend_flag",
    "standard_split_flag", "split_share_multiplier", "nonstandard_share_basis_flag",
    "source_provenance", "evidence_stage",
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _clean(value: object) -> str:
    return str(value or "").strip()


def _number(value: object) -> float | None:
    try:
        parsed = float(_clean(value))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(_clean(value))
    except ValueError:
        return None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _raw_rows(path: Path) -> Iterator[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def _write_csv(root: Path, relative: Path, columns: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    phase1._write_csv(root / relative, tuple(columns), list(rows))


def standard_split_multiplier(row: dict[str, str]) -> float | None:
    """Return the CRSP-standard share multiplier for a supported split event.

    CRSP documents ``DisFacShr`` as the number of additional shares per old
    share for a stock split or stock dividend.  The resulting share multiplier
    is therefore ``1 + DisFacShr``.  This function deliberately accepts only
    Phase-2B split-classified FRS/STKSPL or FRS/STKDIV events with matching
    price/share factors; all other corporate actions stay outside the chain.
    """

    if phase2b._distribution_class(row) != "SPLIT_OR_SHARE_ADJUSTMENT":
        return None
    if _clean(row.get("distype")).upper() != "FRS":
        return None
    if _clean(row.get("disdetailtype")).upper() not in {"STKSPL", "STKDIV"}:
        return None
    share_factor, price_factor = _number(row.get("disfacshr")), _number(row.get("disfacpr"))
    if share_factor is None or price_factor is None or not math.isclose(share_factor, price_factor, rel_tol=0.0, abs_tol=1e-12):
        return None
    multiplier = 1.0 + share_factor
    return multiplier if math.isfinite(multiplier) and multiplier > 0 else None


def normalize_dividend_to_signal_basis(
    cash_dividend_amount: float,
    dividend_ex_date: date,
    signal_date: date,
    standard_split_events: Iterable[tuple[date, float]],
) -> float | None:
    """Express one cash dividend on the signal-date share basis.

    A standard split after an ex-date creates ``1 + DisFacShr`` new-basis
    shares for each old-basis share.  The per-old-share cash amount is divided
    by the product of those subsequent multipliers.  A same-date split is
    intentionally not ordered here: callers must mark it unavailable unless
    authoritative ordering evidence is added.
    """

    if not (math.isfinite(cash_dividend_amount) and dividend_ex_date <= signal_date):
        return None
    multiplier = 1.0
    for split_date, split_multiplier in standard_split_events:
        if dividend_ex_date < split_date <= signal_date:
            if not (math.isfinite(split_multiplier) and split_multiplier > 0):
                return None
            multiplier *= split_multiplier
    return cash_dividend_amount / multiplier if math.isfinite(multiplier) and multiplier > 0 else None


def trailing_window_bounds(signal_date: date) -> tuple[date, date]:
    return signal_date - timedelta(days=365), signal_date


def dividend_window_status(
    signal_date: date,
    coverage_start: date,
    coverage_end: date,
) -> str:
    start, _ = trailing_window_bounds(signal_date)
    if start < coverage_start:
        return "WINDOW_BEFORE_RAW_COVERAGE"
    if signal_date > coverage_end:
        return "WINDOW_AFTER_RAW_COVERAGE"
    return "WINDOW_COMPLETE"


def _event_row(raw: dict[str, str], permitted_permnos: set[str]) -> tuple[Any, ...] | None:
    permno, ex_date, sequence = _clean(raw.get("permno")), _clean(raw.get("disexdt")), _clean(raw.get("disseqnbr"))
    if permno not in permitted_permnos or not _date(ex_date) or not sequence:
        return None
    event_class = phase2b._distribution_class(raw)
    multiplier = standard_split_multiplier(raw)
    share_factor = _number(raw.get("disfacshr"))
    nonstandard_share_basis = int(
        event_class == "SPLIT_OR_SHARE_ADJUSTMENT"
        and multiplier is None
        and share_factor is not None
        and not math.isclose(share_factor, 0.0, abs_tol=1e-12)
    )
    return (
        permno, ex_date, sequence, _clean(raw.get("distype")), _clean(raw.get("disordinaryflg")),
        _clean(raw.get("disfreqtype")), _clean(raw.get("dispaymenttype")), _clean(raw.get("disdetailtype")),
        _clean(raw.get("distaxtype")), _clean(raw.get("disorigcurtype")), _number(raw.get("disdivamt")),
        _number(raw.get("disfacpr")), share_factor, _clean(raw.get("disdeclaredt")),
        _clean(raw.get("disrecorddt")), _clean(raw.get("dispaydt")), _clean(raw.get("dispermno")),
        _clean(raw.get("dispermco")), _clean(raw.get("disamountsourcetype")), event_class,
        int(event_class == "ORDINARY_DIVIDEND"), int(multiplier is not None), multiplier,
        nonstandard_share_basis, DISTRIBUTIONS.as_posix(), EVIDENCE_STAGE,
    )


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE crsp_distribution_events (
          permno TEXT NOT NULL, event_ex_date TEXT NOT NULL, disseqnbr TEXT NOT NULL,
          distype TEXT, disordinaryflg TEXT, disfreqtype TEXT, dispaymenttype TEXT,
          disdetailtype TEXT, distaxtype TEXT, disorigcurtype TEXT,
          cash_dividend_amount REAL, disfacpr REAL, disfacshr REAL,
          disdeclaredt TEXT, disrecorddt TEXT, dispaydt TEXT, dispermno TEXT,
          dispermco TEXT, disamountsourcetype TEXT, phase2b_classification TEXT NOT NULL,
          ordinary_cash_dividend_flag INTEGER NOT NULL, standard_split_flag INTEGER NOT NULL,
          split_share_multiplier REAL, nonstandard_share_basis_flag INTEGER NOT NULL,
          source_provenance TEXT NOT NULL, evidence_stage TEXT NOT NULL,
          PRIMARY KEY (permno, event_ex_date, disseqnbr)
        );
        CREATE INDEX events_permno_date ON crsp_distribution_events(permno, event_ex_date);
        """
    )


def extension_logical_hash(path: Path) -> tuple[str, int]:
    connection = sqlite3.connect(path)
    digest = hashlib.sha256()
    count = 0
    for row in connection.execute("SELECT * FROM crsp_distribution_events ORDER BY permno,event_ex_date,disseqnbr"):
        digest.update(_json(list(row)).encode("utf-8")); digest.update(b"\n"); count += 1
    connection.close()
    return digest.hexdigest(), count


def _base_permitted_permnos(base_store: Path) -> set[str]:
    connection = sqlite3.connect(base_store)
    values = {str(row[0]) for row in connection.execute("SELECT DISTINCT permno FROM security_daily")}
    connection.close()
    return values


def _base_hash(path: Path) -> str:
    return canonical.database_logical_hash(path)[0]


def _event_statistics(store: Path) -> dict[str, Any]:
    connection = sqlite3.connect(store)
    scalar = lambda sql: connection.execute(sql).fetchone()[0]
    result = {
        "row_count": scalar("SELECT COUNT(*) FROM crsp_distribution_events"),
        "unique_permno": scalar("SELECT COUNT(DISTINCT permno) FROM crsp_distribution_events"),
        "min_date": scalar("SELECT MIN(event_ex_date) FROM crsp_distribution_events"),
        "max_date": scalar("SELECT MAX(event_ex_date) FROM crsp_distribution_events"),
        "ordinary_count": scalar("SELECT COUNT(*) FROM crsp_distribution_events WHERE ordinary_cash_dividend_flag=1"),
        "split_count": scalar("SELECT COUNT(*) FROM crsp_distribution_events WHERE phase2b_classification='SPLIT_OR_SHARE_ADJUSTMENT'"),
        "nonordinary_count": scalar("SELECT COUNT(*) FROM crsp_distribution_events WHERE phase2b_classification='NONORDINARY_DISTRIBUTION'"),
        "standard_split_count": scalar("SELECT COUNT(*) FROM crsp_distribution_events WHERE standard_split_flag=1"),
        "nonstandard_share_basis_count": scalar("SELECT COUNT(*) FROM crsp_distribution_events WHERE nonstandard_share_basis_flag=1"),
        "duplicate_keys": scalar("SELECT COUNT(*) - COUNT(DISTINCT permno || '|' || event_ex_date || '|' || disseqnbr) FROM crsp_distribution_events"),
        "post_cutoff_count": scalar("SELECT COUNT(*) FROM crsp_distribution_events WHERE event_ex_date > '2025-12-31'"),
        "ordinary_nonordinary_overlap": scalar("SELECT COUNT(*) FROM crsp_distribution_events WHERE ordinary_cash_dividend_flag=1 AND phase2b_classification<>'ORDINARY_DIVIDEND'"),
    }
    connection.close()
    return result


def _write_audits(root: Path, store: Path, raw_hash_before: str, raw_hash_after: str, base_hash_before: str, base_hash_after: str, permitted_permnos: set[str]) -> dict[str, Any]:
    stats = _event_statistics(store)
    logical_hash, hashed_rows = extension_logical_hash(store)
    raw_integrity = gate0.verify_raw_integrity(root)
    phase2b_counts: Counter[str] = Counter()
    for row in _raw_rows(root / DISTRIBUTIONS):
        phase2b_counts[phase2b._distribution_class(row)] += 1
    checks = {
        "RAW_IMMUTABILITY": raw_integrity["raw_hashes_match"] and raw_hash_before == raw_hash_after,
        "BASE_CANONICAL_IMMUTABILITY": base_hash_before == base_hash_after,
        "NATURAL_KEY": stats["duplicate_keys"] == 0,
        "ORDINARY_RECONCILIATION": stats["ordinary_count"] == phase2b_counts["ORDINARY_DIVIDEND"] == 43436,
        "SPLIT_RECONCILIATION": stats["split_count"] == phase2b_counts["SPLIT_OR_SHARE_ADJUSTMENT"] == 1164,
        "NONORDINARY_RECONCILIATION": stats["nonordinary_count"] == phase2b_counts["NONORDINARY_DISTRIBUTION"] == 18,
        "NO_2026_EVENTS": stats["post_cutoff_count"] == 0,
        "ORDINARY_FILTER": stats["ordinary_nonordinary_overlap"] == 0,
        "PERMNO_SCOPE": stats["unique_permno"] <= len(permitted_permnos),
        "LOGICAL_HASH": hashed_rows == stats["row_count"],
    }
    _write_csv(root, QA, ("check", "status", "value", "expected_or_rule", "notes"), [
        {"check": key, "status": "PASS" if passed else "FAIL", "value": _json(stats) if key == "NATURAL_KEY" else str(stats.get({"ORDINARY_RECONCILIATION": "ordinary_count", "SPLIT_RECONCILIATION": "split_count", "NONORDINARY_RECONCILIATION": "nonordinary_count", "NO_2026_EVENTS": "post_cutoff_count"}.get(key), "")), "expected_or_rule": "see formal Stage-B frozen contract", "notes": "No total-return field was modified."}
        for key, passed in checks.items()
    ])
    _write_csv(root, MANIFEST, ("path", "grain", "row_count", "unique_permno", "min_event_date", "max_event_date", "ordinary_dividend_count", "nonordinary_excluded_count", "split_adjustment_count", "standard_split_count", "nonstandard_share_basis_count", "logical_hash", "raw_source_hash", "base_canonical_logical_hash", "construction_script", "evidence_stage", "notes"), [{
        "path": STORE.as_posix(), "grain": "(PERMNO, DisExDt, DisSeqNbr)", "row_count": stats["row_count"], "unique_permno": stats["unique_permno"], "min_event_date": stats["min_date"], "max_event_date": stats["max_date"], "ordinary_dividend_count": stats["ordinary_count"], "nonordinary_excluded_count": stats["nonordinary_count"], "split_adjustment_count": stats["split_count"], "standard_split_count": stats["standard_split_count"], "nonstandard_share_basis_count": stats["nonstandard_share_basis_count"], "logical_hash": logical_hash, "raw_source_hash": raw_hash_after, "base_canonical_logical_hash": base_hash_after, "construction_script": "src/data/wrds_canonical_dividend_extension.py", "evidence_stage": EVIDENCE_STAGE, "notes": "Cash dividends remain security-level; nonordinary rows are retained only for auditable exclusion."
    }])
    lines = [
        "# Formal WRDS Canonical CRSP Cash-Dividend Extension", "",
        "## Verdict", "",
        "**CANONICAL DIVIDEND EXTENSION — PASS WITH WARNINGS**" if all(checks.values()) else "**CANONICAL DIVIDEND EXTENSION — FAIL**", "",
        "## Scope and immutability", "",
        "This is a separate event-level SQLite extension. It does not mutate the base canonical SQLite store, CRSP `DlyRet`, raw data, or return accounting.", "",
        "## Raw schema and event grain", "",
        "Raw fields used: `permno`, `disexdt`, `disseqnbr`, `distype`, `disordinaryflg`, distribution type/detail fields, `disdivamt`, `disfacpr`, and `disfacshr`. The retained natural key is `(PERMNO, DisExDt, DisSeqNbr)`.", "",
        "## Frozen classification reconciliation", "",
        f"The retained formal-security scope has {stats['ordinary_count']:,} ordinary cash-dividend events, {stats['split_count']:,} Phase-2B split/share-adjustment events, and {stats['nonordinary_count']:,} nonordinary events, exactly reconciling to the accepted Phase-2B counts.", "",
        "## Split-basis contract", "",
        "For an accepted standard `FRS/STKSPL` or `FRS/STKDIV` event, CRSP defines `DisFacShr` as additional shares per old share. Its share multiplier is therefore `1 + DisFacShr`. An ordinary cash dividend D on date e is expressed on a later signal-date s share basis as `D / product(1 + DisFacShr_q)` for accepted standard split events q with `e < q <= s`. This follows CRSP's factor definition; it is not calibrated from yields. The source is the CRSP Data Descriptions Guide: " + SOURCE_GUIDE + ".", "",
        "A nonstandard event that changes the share basis, or a same-ex-date share-action ordering ambiguity, makes the affected dividend signal unavailable rather than guessed. The raw event layer retains those flags for downstream attribution.", "",
        "## Timing and coverage", "",
        f"Event dates range from {stats['min_date']} through {stats['max_date']}; there are {stats['post_cutoff_count']} post-cutoff / 2026 events. A future event is never assigned to an earlier signal. A zero dividend is permitted only when the complete `(signal_date-365 days, signal_date]` raw-coverage window exists.", "",
        "## Integrity", "",
        f"Raw Distribution SHA256 before/after: `{raw_hash_before}` / `{raw_hash_after}`. Base canonical logical hash before/after: `{base_hash_before}` / `{base_hash_after}`. Extension logical hash: `{logical_hash}`.", "",
        "## Warning", "",
        "The extension is formal within the delivered 2004–2025 CRSP coverage. Events after the formal cutoff are deliberately absent, and nonstandard share-basis actions cause explicit factor-level unavailability rather than an inferred adjustment.", "",
    ]
    (root / REPORT).write_text("\n".join(lines), encoding="utf-8")
    return {"checks": checks, "stats": stats, "logical_hash": logical_hash, "raw_integrity": raw_integrity}


def rebuild_event_store(root: Path | None = None) -> dict[str, Any]:
    """Atomically rebuild only the ignored event store from frozen inputs.

    This deliberately has no audit-output side effects.  It allows the
    deterministic second build to run as a bounded operation after the first
    full construction has already checked raw and base-canonical immutability.
    """

    root = root or project_root()
    raw_path, base_store, store = root / DISTRIBUTIONS, root / BASE_STORE, root / STORE
    if not raw_path.is_file() or not base_store.is_file():
        raise RuntimeError("Missing immutable CRSP Distribution raw file or base canonical store.")
    permitted_permnos = _base_permitted_permnos(base_store)
    store.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".us_crsp_cash_dividend_events.", suffix=".sqlite.tmp", dir=store.parent)
    os.close(descriptor); temporary = Path(temporary_name)
    try:
        connection = sqlite3.connect(temporary)
        connection.execute("PRAGMA journal_mode=OFF"); connection.execute("PRAGMA synchronous=OFF")
        _create_schema(connection)
        batch: list[tuple[Any, ...]] = []
        for raw in _raw_rows(raw_path):
            parsed = _event_row(raw, permitted_permnos)
            if parsed is not None:
                batch.append(parsed)
            if len(batch) >= 5000:
                connection.executemany("INSERT INTO crsp_distribution_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch); batch.clear()
        if batch:
            connection.executemany("INSERT INTO crsp_distribution_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
        connection.commit(); connection.close()
        os.replace(temporary, store)
    finally:
        if temporary.exists():
            temporary.unlink()
    logical_hash, row_count = extension_logical_hash(store)
    return {"store": store, "logical_hash": logical_hash, "row_count": row_count, "permitted_permno_count": len(permitted_permnos)}


def build_extension(root: Path | None = None) -> dict[str, Any]:
    """Build the extension once and write its complete Stage-B audit evidence."""

    root = root or project_root()
    raw_path, base_store = root / DISTRIBUTIONS, root / BASE_STORE
    if not raw_path.is_file() or not base_store.is_file():
        raise RuntimeError("Missing immutable CRSP Distribution raw file or base canonical store.")
    raw_hash_before = phase1.sha256_file(raw_path)
    base_hash_before = _base_hash(base_store)
    rebuild = rebuild_event_store(root)
    permitted_permnos = _base_permitted_permnos(base_store)
    raw_hash_after = phase1.sha256_file(raw_path)
    base_hash_after = _base_hash(base_store)
    result = _write_audits(root, rebuild["store"], raw_hash_before, raw_hash_after, base_hash_before, base_hash_after, permitted_permnos)
    if not all(result["checks"].values()):
        raise RuntimeError(f"Dividend extension acceptance failed: {result['checks']}")
    return result | {"raw_hash_before": raw_hash_before, "raw_hash_after": raw_hash_after, "base_hash_before": base_hash_before, "base_hash_after": base_hash_after, "store": rebuild["store"]}


def record_determinism(expected_logical_hash: str, root: Path | None = None) -> dict[str, str]:
    """Record a comparison after a separately executed second event-store rebuild."""

    root = root or project_root()
    actual_logical_hash, _ = extension_logical_hash(root / STORE)
    status = "PASS" if expected_logical_hash == actual_logical_hash else "FAIL"
    _write_csv(root, DETERMINISM, ("run_1_logical_hash", "run_2_logical_hash", "status", "notes"), [{
        "run_1_logical_hash": expected_logical_hash,
        "run_2_logical_hash": actual_logical_hash,
        "status": status,
        "notes": "Two atomic rebuilds from the same immutable CRSP Distribution input and base canonical store.",
    }])
    if status != "PASS":
        raise RuntimeError("Dividend extension logical determinism failed.")
    return {"run_1_logical_hash": expected_logical_hash, "run_2_logical_hash": actual_logical_hash, "status": status}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build formal CRSP cash-dividend event extension.")
    parser.add_argument("--root", type=Path, default=project_root())
    parser.add_argument("--stage", choices=("build", "rebuild-only", "record-determinism"), default="build")
    parser.add_argument("--expected-logical-hash")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.stage == "build":
        result = build_extension(root)
    elif args.stage == "rebuild-only":
        result = rebuild_event_store(root)
    else:
        if not args.expected_logical_hash:
            parser.error("--stage record-determinism requires --expected-logical-hash")
        result = record_determinism(args.expected_logical_hash, root)
    print(_json(result))


if __name__ == "__main__":
    main()
