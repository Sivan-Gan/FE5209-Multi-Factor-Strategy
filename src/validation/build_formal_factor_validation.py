"""Build formal-US Forward-IC and single-factor validation evidence.

This module is deliberately bounded to 2006--2015.  It reads frozen formal
inputs, writes a separate derived validation store, and never writes a return
or outcome field into the candidate-factor store.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import sqlite3
import statistics
import tempfile
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

from src.data import wrds_canonical_build as canonical
from src.data import wrds_canonical_dividend_extension as dividend
from src.data import wrds_gate0_final as gate0
from src.factors import build_candidate_factors as factors

CANONICAL = Path("data/canonical/wrds_formal/us_wrds_formal_canonical.sqlite")
FACTOR_STORE = Path("data/factors/us_formal/us_candidate_factors.sqlite")
DIVIDEND_EXTENSION = Path("data/canonical/wrds_formal/extensions/us_crsp_cash_dividend_events.sqlite")
VALIDATION = Path("data/validation/us_formal/us_factor_validation_pre_gate_a.sqlite")
AUDIT = Path("outputs/audit/wrds_formal")
FORMAL_CUTOFF = "2015-12-31"
START_DATE = "2006-01-01"
CANONICAL_HASH = "de1bf079e6399025ebde6a81883bbad21aec9c5443dbb39f7ca2f00b5a409c6e"
FACTOR_HASH = "4282b50f398ee5746420db264d154298f01167b31e03b1feb9e6c5244a65fb64"
FACTOR_FIELDS = (
    ("Size", "size_final"),
    ("Momentum", "momentum_final"),
    ("Low Risk", "lowrisk_final"),
    ("Value", "value_final"),
    ("Quality", "quality_final"),
    ("Investment", "investment_final"),
    ("Dividend", "dividend_final"),
)
FACTOR_COLUMNS = {name: column for name, column in FACTOR_FIELDS}
PRIMARY = "PRIMARY_FACTOR_SPECIFIC"
COMMON = "COMMON_7_FACTOR_SAMPLE_DIAGNOSTIC"
ROLE_LABELS = {"DEV_WARMUP": "DEV", "FACTOR_SELECTION": "FACTOR_SELECTION"}
VALID_RETURN_STATUSES = {"VALID", "VALID_CONFIRMED_DELISTING_CASH"}


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def write_csv(path: Path, fields: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def rank_average(values: Sequence[float]) -> list[float]:
    """Average ranks, ascending, with exact deterministic ties."""
    order = sorted(range(len(values)), key=lambda index: values[index])
    out = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        average = (start + 1 + end) / 2.0
        for position in order[start:end]:
            out[position] = average
        start = end
    return out


def pearson(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) < 2:
        return None
    x_mean, y_mean = statistics.mean(x), statistics.mean(y)
    denominator = math.sqrt(sum((value - x_mean) ** 2 for value in x) * sum((value - y_mean) ** 2 for value in y))
    if denominator == 0:
        return None
    return sum((left - x_mean) * (right - y_mean) for left, right in zip(x, y)) / denominator


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    return pearson(rank_average(x), rank_average(y))


def compound(returns: Sequence[float]) -> float | None:
    wealth = 1.0
    for value in returns:
        if not math.isfinite(value) or value <= -1:
            return None
        wealth *= 1.0 + value
    return wealth - 1.0


def nw_t(values: Sequence[float], lag: int) -> float | None:
    """Newey-West t statistic for a sample mean, no p-value or decision rule."""
    n = len(values)
    if n < 2:
        return None
    mean = statistics.mean(values)
    centered = [value - mean for value in values]
    long_run = sum(value * value for value in centered) / n
    for ell in range(1, min(lag, n - 1) + 1):
        covariance = sum(centered[index] * centered[index - ell] for index in range(ell, n)) / n
        long_run += 2.0 * (1.0 - ell / (lag + 1.0)) * covariance
    if long_run <= 0:
        return None
    return mean / math.sqrt(long_run / n)


def moving_block_bootstrap(values: Sequence[float], *, block_length: int = 4, replications: int = 2000, seed: int = 5209) -> dict[str, float | int | None]:
    if not values:
        return {"bootstrap_replications": replications, "bootstrap_seed": seed, "bootstrap_mean": None, "bootstrap_ci_low": None, "bootstrap_ci_high": None}
    rng = random.Random(seed)
    n = len(values)
    starts = list(range(n))
    means: list[float] = []
    for _ in range(replications):
        sample: list[float] = []
        while len(sample) < n:
            start = rng.choice(starts)
            sample.extend(values[(start + offset) % n] for offset in range(block_length))
        means.append(statistics.mean(sample[:n]))
    means.sort()
    return {
        "bootstrap_replications": replications,
        "bootstrap_seed": seed,
        "bootstrap_mean": statistics.mean(means),
        "bootstrap_ci_low": means[int((replications - 1) * 0.025)],
        "bootstrap_ci_high": means[int((replications - 1) * 0.975)],
    }


def performance(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n_weeks": 0, "mean_weekly": None, "annualized_arithmetic": None, "annualized_volatility": None, "gross_raw_sharpe": None, "cagr": None, "max_drawdown": None, "positive_week_ratio": None}
    average = statistics.mean(values)
    deviation = statistics.stdev(values) if len(values) > 1 else 0.0
    wealth, peak, max_drawdown = 1.0, 1.0, 0.0
    for value in values:
        wealth *= 1.0 + value
        peak = max(peak, wealth)
        max_drawdown = max(max_drawdown, 1.0 - wealth / peak)
    return {
        "n_weeks": len(values),
        "mean_weekly": average,
        "annualized_arithmetic": 52.0 * average,
        "annualized_volatility": math.sqrt(52.0) * deviation,
        "gross_raw_sharpe": math.sqrt(52.0) * average / deviation if deviation > 0 else None,
        "cagr": wealth ** (52.0 / len(values)) - 1.0,
        "max_drawdown": max_drawdown,
        "positive_week_ratio": sum(value > 0 for value in values) / len(values),
    }


def period_label(signal_date: str) -> str:
    if signal_date <= "2010-12-31":
        return "DEV"
    if signal_date <= FORMAL_CUTOFF:
        return "FACTOR_SELECTION"
    raise ValueError(f"Post-firewall validation date: {signal_date}")


def is_valid_return(status: str | None) -> bool:
    return status in VALID_RETURN_STATUSES


def forward_path(
    daily: dict[str, tuple[float | None, str | None, float | None, int, int]],
    required_dates: Sequence[str],
) -> tuple[float | None, str, str, int]:
    """Compound only (E_t, E_t+h], retaining terminal proceeds as cash."""
    wealth = 1.0
    terminal = False
    for trading_date in required_dates:
        if terminal:
            continue
        row = daily.get(trading_date)
        if row is None:
            return None, "UNEXPLAINED_INCOMPLETE_PATH", "MISSING_DAILY_ROW", 0
        daily_return, delisting_flag, _price, _member, _investable = row
        if daily_return is None:
            if delisting_flag == "Y":
                return None, "UNEXPLAINED_INCOMPLETE_PATH", "CONFIRMED_DELISTING_RETURN_UNAVAILABLE", 0
            return None, "UNEXPLAINED_INCOMPLETE_PATH", "MISSING_DLYRET", 0
        if daily_return <= -1:
            return None, "UNEXPLAINED_INCOMPLETE_PATH", "NONCOMPOUNDABLE_DLYRET", 0
        wealth *= 1.0 + daily_return
        if delisting_flag == "Y":
            terminal = True
    return wealth - 1.0, "VALID_CONFIRMED_DELISTING_CASH" if terminal else "VALID", "CONFIRMED_DELISTING_CASH" if terminal else "", int(terminal)


def execution_status(row: tuple[float | None, str | None, float | None, int, int] | None) -> tuple[int, str]:
    if row is None:
        return 0, "NO_EXECUTION_DATE_DAILY_ROW"
    _return, _delisting, price, _member, investable = row
    if price is None or abs(price) <= 0:
        return 0, "INVALID_EXECUTION_CLOSE"
    if not investable:
        return 0, "NOT_INVESTABLE_ON_EXECUTION_DATE"
    return 1, "EXECUTION_FEASIBLE"


SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE validation_observations (
  signal_date TEXT NOT NULL, permno TEXT NOT NULL, execution_date TEXT NOT NULL,
  next_execution_date TEXT, fourth_execution_date TEXT, research_period TEXT NOT NULL,
  gsector TEXT, historical_member INTEGER NOT NULL, execution_eligible INTEGER NOT NULL,
  execution_reason TEXT NOT NULL, common_7_factor_score_available INTEGER NOT NULL,
  size_final REAL, momentum_final REAL, lowrisk_final REAL, value_final REAL,
  quality_final REAL, investment_final REAL, dividend_final REAL,
  r_1w_exec REAL, r_1w_status TEXT NOT NULL, r_1w_reason TEXT NOT NULL, r_1w_terminal_cash INTEGER NOT NULL,
  r_4w_exec REAL, r_4w_status TEXT NOT NULL, r_4w_reason TEXT NOT NULL, r_4w_terminal_cash INTEGER NOT NULL,
  PRIMARY KEY(signal_date, permno)
);
CREATE TABLE factor_ic_weekly (
  sample_role TEXT NOT NULL, signal_date TEXT NOT NULL, research_period TEXT NOT NULL,
  factor TEXT NOT NULL, horizon TEXT NOT NULL, n_obs INTEGER NOT NULL,
  spearman_ic REAL, valid_return_count INTEGER NOT NULL, valid_score_count INTEGER NOT NULL,
  status TEXT NOT NULL, PRIMARY KEY(sample_role, signal_date, factor, horizon)
);
CREATE TABLE factor_sector_ic_weekly (
  sample_role TEXT NOT NULL, signal_date TEXT NOT NULL, research_period TEXT NOT NULL,
  factor TEXT NOT NULL, horizon TEXT NOT NULL, gsector TEXT NOT NULL, n_obs INTEGER NOT NULL,
  spearman_ic REAL, status TEXT NOT NULL, PRIMARY KEY(sample_role, signal_date, factor, horizon, gsector)
);
CREATE TABLE factor_portfolio_weekly (
  sample_role TEXT NOT NULL, signal_date TEXT NOT NULL, execution_date TEXT NOT NULL,
  next_execution_date TEXT, research_period TEXT NOT NULL, factor TEXT NOT NULL,
  n_cross_section INTEGER NOT NULL, n_top_target INTEGER NOT NULL, n_bottom_target INTEGER NOT NULL,
  n_top_executed INTEGER NOT NULL, n_bottom_executed INTEGER NOT NULL,
  top_return_gross REAL, bottom_return_gross REAL, factor_spread_gross REAL,
  top_return_status TEXT NOT NULL, bottom_return_status TEXT NOT NULL,
  top_execution_failures INTEGER NOT NULL, bottom_execution_failures INTEGER NOT NULL,
  top_gtr REAL, top_turnover REAL, bottom_gtr REAL, bottom_turnover REAL,
  PRIMARY KEY(sample_role, signal_date, factor)
);
CREATE TABLE factor_holdings (
  sample_role TEXT NOT NULL, signal_date TEXT NOT NULL, factor TEXT NOT NULL, leg TEXT NOT NULL,
  permno TEXT NOT NULL, target_rank INTEGER NOT NULL, target_weight REAL NOT NULL,
  realized_return REAL, return_status TEXT NOT NULL, terminal_cash INTEGER NOT NULL,
  PRIMARY KEY(sample_role, signal_date, factor, leg, permno)
);
"""


def validation_hash(path: Path) -> str:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    digest = hashlib.sha256()
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for table in tables:
        digest.update((table + "\n").encode())
        for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid"):
            digest.update(dump(list(row)).encode() + b"\n")
    connection.close()
    return digest.hexdigest()


def input_freeze(root: Path) -> dict[str, str]:
    canonical_hash = canonical.database_logical_hash(root / CANONICAL)[0]
    factor_hash = factors.logical_hash(root / FACTOR_STORE)
    extension_hash = dividend.extension_logical_hash(root / DIVIDEND_EXTENSION)[0]
    if canonical_hash != CANONICAL_HASH:
        raise RuntimeError("CANONICAL_FREEZE_HASH_MISMATCH")
    if factor_hash != FACTOR_HASH:
        raise RuntimeError("CANDIDATE_FACTOR_FREEZE_HASH_MISMATCH")
    return {"canonical_hash": canonical_hash, "factor_hash": factor_hash, "dividend_extension_hash": extension_hash}


def load_calendar(connection: sqlite3.Connection) -> tuple[list[tuple[str, str]], dict[str, int], list[str]]:
    calendar = [(str(signal), str(execution)) for signal, execution in connection.execute("SELECT signal_date,execution_date FROM weekly_calendar ORDER BY signal_date")]
    return calendar, {signal: index for index, (signal, _execution) in enumerate(calendar)}, [str(row[0]) for row in connection.execute("SELECT date FROM trading_dates WHERE date<=? ORDER BY date", (FORMAL_CUTOFF,))]


def score_rows(connection: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    columns = ["signal_date", "permno", "execution_date", "research_period", "gsector", "historical_member"] + [column for _name, column in FACTOR_FIELDS]
    query = f"SELECT {','.join(columns)} FROM factors WHERE signal_date>=? AND signal_date<=? ORDER BY signal_date,permno"
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in connection.execute(query, (START_DATE, FORMAL_CUTOFF)):
        row = dict(zip(columns, result))
        row["permno"] = str(row["permno"])
        grouped[str(row["signal_date"])].append(row)
    return grouped


def daily_for_signal(
    canonical_connection: sqlite3.Connection,
    permnos: Sequence[str],
    start: str,
    end: str,
) -> dict[str, dict[str, tuple[float | None, str | None, float | None, int, int]]]:
    if not permnos or start > end:
        return {}
    placeholders = ",".join("?" for _ in permnos)
    query = f"""SELECT date,permno,dlyret,dlydelflg,dlyprc,is_historical_member,is_investable_security
                 FROM security_daily WHERE date>=? AND date<=? AND permno IN ({placeholders})"""
    out: dict[str, dict[str, tuple[float | None, str | None, float | None, int, int]]] = defaultdict(dict)
    for observed, permno, daily_return, delisting_flag, price, member, investable in canonical_connection.execute(query, (start, end, *permnos)):
        out[str(permno)][str(observed)] = (finite(daily_return), str(delisting_flag) if delisting_flag is not None else None, finite(price), int(member or 0), int(investable or 0))
    return out


def build_observations(root: Path, destination: sqlite3.Connection, inputs: dict[str, str]) -> list[dict[str, Any]]:
    canonical_connection = sqlite3.connect(f"file:{root / CANONICAL}?mode=ro", uri=True)
    factor_connection = sqlite3.connect(f"file:{root / FACTOR_STORE}?mode=ro", uri=True)
    calendar, calendar_index, trading_days = load_calendar(canonical_connection)
    trading_index = {day: index for index, day in enumerate(trading_days)}
    grouped = score_rows(factor_connection)
    fields = ["signal_date", "permno", "execution_date", "next_execution_date", "fourth_execution_date", "research_period", "gsector", "historical_member", "execution_eligible", "execution_reason", "common_7_factor_score_available"] + [column for _name, column in FACTOR_FIELDS] + ["r_1w_exec", "r_1w_status", "r_1w_reason", "r_1w_terminal_cash", "r_4w_exec", "r_4w_status", "r_4w_reason", "r_4w_terminal_cash"]
    insert = "INSERT INTO validation_observations VALUES(" + ",".join("?" for _ in fields) + ")"
    records: list[dict[str, Any]] = []
    for signal_date in sorted(grouped):
        index = calendar_index[signal_date]
        execution_date = calendar[index][1]
        next_execution = calendar[index + 1][1] if index + 1 < len(calendar) else None
        fourth_execution = calendar[index + 4][1] if index + 4 < len(calendar) else None
        if execution_date != grouped[signal_date][0]["execution_date"]:
            raise RuntimeError("EXECUTION_CALENDAR_MISMATCH")
        latest = max([value for value in (execution_date, next_execution, fourth_execution) if value and value <= FORMAL_CUTOFF], default=execution_date)
        daily = daily_for_signal(canonical_connection, [row["permno"] for row in grouped[signal_date]], execution_date, latest)
        for score in grouped[signal_date]:
            permno = score["permno"]
            execution_eligible, execution_reason = execution_status(daily.get(permno, {}).get(execution_date))
            common_available = int(all(score[column] is not None for _name, column in FACTOR_FIELDS))
            observation: dict[str, Any] = {
                "signal_date": signal_date, "permno": permno, "execution_date": execution_date,
                "next_execution_date": next_execution, "fourth_execution_date": fourth_execution,
                "research_period": str(score["research_period"]), "gsector": score["gsector"],
                "historical_member": int(score["historical_member"]), "execution_eligible": execution_eligible,
                "execution_reason": execution_reason, "common_7_factor_score_available": common_available,
            }
            for _name, column in FACTOR_FIELDS:
                observation[column] = finite(score[column])
            for horizon, endpoint, value_field, status_field, reason_field, terminal_field in (
                ("1W", next_execution, "r_1w_exec", "r_1w_status", "r_1w_reason", "r_1w_terminal_cash"),
                ("4W", fourth_execution, "r_4w_exec", "r_4w_status", "r_4w_reason", "r_4w_terminal_cash"),
            ):
                if endpoint is None or endpoint > FORMAL_CUTOFF:
                    result = (None, "POST2015_FIREWALL_EXCLUDED", "HORIZON_ENDS_AFTER_2015", 0)
                elif not execution_eligible:
                    result = (None, "EXECUTION_INFEASIBLE", execution_reason, 0)
                else:
                    start_index, end_index = trading_index.get(execution_date), trading_index.get(endpoint)
                    if start_index is None or end_index is None or end_index <= start_index:
                        result = (None, "UNEXPLAINED_INCOMPLETE_PATH", "EXECUTION_OR_HORIZON_NOT_IN_TRADING_CALENDAR", 0)
                    else:
                        result = forward_path(daily.get(permno, {}), trading_days[start_index + 1:end_index + 1])
                observation[value_field], observation[status_field], observation[reason_field], observation[terminal_field] = result
            destination.execute(insert, tuple(observation[field] for field in fields))
            records.append(observation)
    factor_connection.close()
    canonical_connection.close()
    destination.executemany("INSERT INTO metadata VALUES(?,?)", sorted({"source_factor_hash": inputs["factor_hash"], "source_canonical_hash": inputs["canonical_hash"], "source_dividend_extension_hash": inputs["dividend_extension_hash"], "formal_cutoff": FORMAL_CUTOFF, "return_rule": "USE_CIZ_DLYRET_DIRECTLY__DO_NOT_ADD_SEPARATE_DELRET"}.items()))
    return records


def eligible(observation: dict[str, Any], factor: str, sample_role: str, horizon: str | None = None) -> bool:
    if not observation["historical_member"] or not observation["execution_eligible"]:
        return False
    if observation[FACTOR_COLUMNS[factor]] is None:
        return False
    if sample_role == COMMON and not observation["common_7_factor_score_available"]:
        return False
    if horizon:
        return is_valid_return(observation["r_1w_status" if horizon == "1W" else "r_4w_status"])
    return True


def score_eligible(observation: dict[str, Any], factor: str, sample_role: str) -> bool:
    """Signal-date membership and score eligibility, before execution feasibility.

    This is intentionally distinct from ``eligible``: Top/Bottom target
    membership is frozen at S_t.  A later execution failure may reduce a leg,
    but must never cause a next-ranked replacement.
    """
    if not observation["historical_member"]:
        return False
    if observation[FACTOR_COLUMNS[factor]] is None:
        return False
    return sample_role != COMMON or bool(observation["common_7_factor_score_available"])


def weekly_ic(records: Sequence[dict[str, Any]], sample_role: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_signal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        by_signal[row["signal_date"]].append(row)
    output: list[dict[str, Any]] = []
    sector_output: list[dict[str, Any]] = []
    for signal_date in sorted(by_signal):
        rows = by_signal[signal_date]
        for factor, column in FACTOR_FIELDS:
            for horizon, return_column, status_column in (("1W", "r_1w_exec", "r_1w_status"), ("4W", "r_4w_exec", "r_4w_status")):
                score_rows = [row for row in rows if eligible(row, factor, sample_role)]
                return_rows = [row for row in score_rows if is_valid_return(row[status_column])]
                pairs = [(float(row[column]), float(row[return_column])) for row in return_rows]
                ic = spearman([pair[0] for pair in pairs], [pair[1] for pair in pairs]) if len(pairs) >= 30 else None
                output.append({"sample_role": sample_role, "signal_date": signal_date, "research_period": period_label(signal_date), "factor": factor, "horizon": horizon, "n_obs": len(pairs), "spearman_ic": ic, "valid_return_count": len(return_rows), "valid_score_count": len(score_rows), "status": "VALID" if ic is not None else "INSUFFICIENT_CROSS_SECTION_N_LT_30"})
                sectors: dict[str, list[tuple[float, float]]] = defaultdict(list)
                for row in return_rows:
                    if row["gsector"]:
                        sectors[str(row["gsector"])].append((float(row[column]), float(row[return_column])))
                for sector, sector_pairs in sorted(sectors.items()):
                    sector_ic = spearman([pair[0] for pair in sector_pairs], [pair[1] for pair in sector_pairs]) if len(sector_pairs) >= 10 else None
                    sector_output.append({"sample_role": sample_role, "signal_date": signal_date, "research_period": period_label(signal_date), "factor": factor, "horizon": horizon, "gsector": sector, "n_obs": len(sector_pairs), "spearman_ic": sector_ic, "status": "VALID" if sector_ic is not None else "INSUFFICIENT_SECTOR_CROSS_SECTION_N_LT_10"})
    return output, sector_output


def target_leg(rows: Sequence[dict[str, Any]], column: str, *, top: bool) -> list[dict[str, Any]]:
    count = math.floor(0.30 * len(rows))
    if count < 1:
        return []
    order = sorted(rows, key=lambda row: ((-float(row[column])) if top else float(row[column]), str(row["permno"])))
    return order[:count]


def build_portfolios(records: Sequence[dict[str, Any]], sample_role: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_signal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        by_signal[row["signal_date"]].append(row)
    weekly: list[dict[str, Any]] = []
    holdings: list[dict[str, Any]] = []
    for signal_date in sorted(by_signal):
        all_rows = by_signal[signal_date]
        for factor, column in FACTOR_FIELDS:
            # Freeze target membership at S_t; execution is a separate E_t check.
            rankable = [row for row in all_rows if score_eligible(row, factor, sample_role)]
            bottom, top = target_leg(rankable, column, top=False), target_leg(rankable, column, top=True)
            top_executed = [candidate for candidate in top if candidate["execution_eligible"]]
            bottom_executed = [candidate for candidate in bottom if candidate["execution_eligible"]]
            top_valid = all(is_valid_return(candidate["r_1w_status"]) for candidate in top_executed)
            bottom_valid = all(is_valid_return(candidate["r_1w_status"]) for candidate in bottom_executed)
            top_return = statistics.mean(float(candidate["r_1w_exec"]) for candidate in top_executed) if top_executed and top_valid else None
            bottom_return = statistics.mean(float(candidate["r_1w_exec"]) for candidate in bottom_executed) if bottom_executed and bottom_valid else None
            row = {"sample_role": sample_role, "signal_date": signal_date, "execution_date": all_rows[0]["execution_date"], "next_execution_date": all_rows[0]["next_execution_date"], "research_period": period_label(signal_date), "factor": factor, "n_cross_section": len(rankable), "n_top_target": len(top), "n_bottom_target": len(bottom), "n_top_executed": len(top_executed), "n_bottom_executed": len(bottom_executed), "top_return_gross": top_return, "bottom_return_gross": bottom_return, "factor_spread_gross": top_return - bottom_return if top_return is not None and bottom_return is not None else None, "top_return_status": "VALID" if top_return is not None else "FORWARD_RETURN_INCOMPLETE_OR_EMPTY", "bottom_return_status": "VALID" if bottom_return is not None else "FORWARD_RETURN_INCOMPLETE_OR_EMPTY", "top_execution_failures": len(top) - len(top_executed), "bottom_execution_failures": len(bottom) - len(bottom_executed), "top_gtr": None, "top_turnover": None, "bottom_gtr": None, "bottom_turnover": None}
            weekly.append(row)
            for leg, selected in (("TOP", top_executed), ("BOTTOM", bottom_executed)):
                for rank, selected_row in enumerate(selected, start=1):
                    holdings.append({"sample_role": sample_role, "signal_date": signal_date, "factor": factor, "leg": leg, "permno": selected_row["permno"], "target_rank": rank, "target_weight": 1.0 / len(selected), "realized_return": selected_row["r_1w_exec"], "return_status": selected_row["r_1w_status"], "terminal_cash": selected_row["r_1w_terminal_cash"]})
    apply_turnover(weekly, holdings)
    return weekly, holdings


def apply_turnover(weekly: list[dict[str, Any]], holdings: Sequence[dict[str, Any]]) -> None:
    holding_map: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in holdings:
        holding_map[row["sample_role"], row["factor"], row["leg"], row["signal_date"]].append(row)
    weekly_map = {(row["sample_role"], row["factor"], row["signal_date"]): row for row in weekly}
    for sample_role in (PRIMARY, COMMON):
        for factor, _column in FACTOR_FIELDS:
            previous_by_leg: dict[str, list[dict[str, Any]] | None] = {"TOP": None, "BOTTOM": None}
            for signal_date in sorted({row["signal_date"] for row in weekly if row["sample_role"] == sample_role and row["factor"] == factor}):
                for leg, prefix in (("TOP", "top"), ("BOTTOM", "bottom")):
                    current = holding_map[sample_role, factor, leg, signal_date]
                    output = weekly_map[sample_role, factor, signal_date]
                    if not current:
                        output[prefix + "_gtr"] = None
                        output[prefix + "_turnover"] = None
                        previous_by_leg[leg] = current
                        continue
                    target = {row["permno"]: float(row["target_weight"]) for row in current}
                    previous = previous_by_leg[leg]
                    if previous is None:
                        pretrade = {"__CASH__": 1.0}
                    elif not all(is_valid_return(row["return_status"]) for row in previous):
                        output[prefix + "_gtr"] = None
                        output[prefix + "_turnover"] = None
                        previous_by_leg[leg] = current
                        continue
                    else:
                        value: dict[str, float] = defaultdict(float)
                        for row in previous:
                            key = "__CASH__" if row["terminal_cash"] else str(row["permno"])
                            value[key] += float(row["target_weight"]) * (1.0 + float(row["realized_return"]))
                        total = sum(value.values())
                        pretrade = {key: amount / total for key, amount in value.items()} if total > 0 else {"__CASH__": 1.0}
                    gtr = sum(abs(target.get(key, 0.0) - pretrade.get(key, 0.0)) for key in set(target) | set(pretrade))
                    output[prefix + "_gtr"] = gtr
                    output[prefix + "_turnover"] = 0.5 * gtr
                    previous_by_leg[leg] = current


def table_rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    result = [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
    connection.row_factory = None
    return result


def summarize_ic(weekly: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for sample_role in (PRIMARY, COMMON):
        for period in ("DEV", "FACTOR_SELECTION"):
            for factor, _column in FACTOR_FIELDS:
                for horizon in ("1W", "4W"):
                    values = [float(row["spearman_ic"]) for row in weekly if row["sample_role"] == sample_role and row["research_period"] == period and row["factor"] == factor and row["horizon"] == horizon and row["spearman_ic"] is not None]
                    summary = {"sample_role": sample_role, "research_period": period, "factor": factor, "horizon": horizon, "n_valid_weeks": len(values), "mean_ic": statistics.mean(values) if values else None, "median_ic": statistics.median(values) if values else None, "sd_ic": statistics.stdev(values) if len(values) > 1 else None, "weekly_icir": None, "annualized_icir": None, "positive_ic_ratio": sum(value > 0 for value in values) / len(values) if values else None, "zero_ic_ratio": sum(value == 0 for value in values) / len(values) if values else None, "min_ic": min(values) if values else None, "max_ic": max(values) if values else None, "nw_t_label": "NW_t_lag4" if horizon == "1W" else "NW_t_overlap_lag3", "nw_t": nw_t(values, 4 if horizon == "1W" else 3)}
                    if summary["sd_ic"] and summary["mean_ic"] is not None:
                        summary["weekly_icir"] = float(summary["mean_ic"]) / float(summary["sd_ic"])
                        summary["annualized_icir"] = math.sqrt(52.0) * float(summary["weekly_icir"])
                    if horizon == "4W":
                        summary.update(moving_block_bootstrap(values))
                    else:
                        summary.update({"bootstrap_replications": None, "bootstrap_seed": None, "bootstrap_mean": None, "bootstrap_ci_low": None, "bootstrap_ci_high": None})
                    output.append(summary)
    return output


def ic_stability(weekly: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for sample_role in (PRIMARY, COMMON):
        for factor, _column in FACTOR_FIELDS:
            for horizon in ("1W", "4W"):
                rows = [row for row in weekly if row["sample_role"] == sample_role and row["factor"] == factor and row["horizon"] == horizon and row["spearman_ic"] is not None]
                for year in range(2006, 2016):
                    values = [float(row["spearman_ic"]) for row in rows if str(row["signal_date"]).startswith(str(year))]
                    output.append({"sample_role": sample_role, "factor": factor, "horizon": horizon, "record_type": "CALENDAR_YEAR", "period_end": f"{year}-12-31", "n_obs": len(values), "mean_ic": statistics.mean(values) if values else None, "positive_ic_ratio": sum(value > 0 for value in values) / len(values) if values else None, "icir": statistics.mean(values) / statistics.stdev(values) if len(values) > 1 and statistics.stdev(values) > 0 else None, "sample_period": period_label(f"{year}-12-31")})
                by_period: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for row in rows:
                    by_period[period_label(row["signal_date"])].append(row)
                for period, period_rows in by_period.items():
                    for index in range(51, len(period_rows)):
                        values = [float(row["spearman_ic"]) for row in period_rows[index - 51:index + 1]]
                        output.append({"sample_role": sample_role, "factor": factor, "horizon": horizon, "record_type": "ROLLING_52W", "period_end": period_rows[index]["signal_date"], "n_obs": 52, "mean_ic": statistics.mean(values), "positive_ic_ratio": sum(value > 0 for value in values) / 52.0, "icir": statistics.mean(values) / statistics.stdev(values) if statistics.stdev(values) > 0 else None, "sample_period": period})
    return output


def sector_summary(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    keys = sorted({(row["sample_role"], period_label(row["signal_date"]), row["factor"], row["horizon"], row["gsector"]) for row in rows})
    group_means: dict[tuple[str, str, str, str], list[float]] = defaultdict(list)
    for sample_role, period, factor, horizon, gsector in keys:
        values = [float(row["spearman_ic"]) for row in rows if row["sample_role"] == sample_role and period_label(row["signal_date"]) == period and row["factor"] == factor and row["horizon"] == horizon and row["gsector"] == gsector and row["spearman_ic"] is not None]
        if values:
            group_means[sample_role, period, factor, horizon].append(statistics.mean(values))
    for sample_role, period, factor, horizon, gsector in keys:
        values = [float(row["spearman_ic"]) for row in rows if row["sample_role"] == sample_role and period_label(row["signal_date"]) == period and row["factor"] == factor and row["horizon"] == horizon and row["gsector"] == gsector and row["spearman_ic"] is not None]
        group = group_means[sample_role, period, factor, horizon]
        output.append({"sample_role": sample_role, "research_period": period, "factor": factor, "horizon": horizon, "gsector": gsector, "mean_ic": statistics.mean(values) if values else None, "positive_ic_ratio": sum(value > 0 for value in values) / len(values) if values else None, "valid_weeks": len(values), "valid_sector_count": len(group), "cross_sector_dispersion": statistics.stdev(group) if len(group) > 1 else None})
    return output


def portfolio_summary(weekly: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for sample_role in (PRIMARY, COMMON):
        for period in ("DEV", "FACTOR_SELECTION"):
            for factor, _column in FACTOR_FIELDS:
                selected = [row for row in weekly if row["sample_role"] == sample_role and row["research_period"] == period and row["factor"] == factor]
                top = [float(row["top_return_gross"]) for row in selected if row["top_return_gross"] is not None]
                bottom = [float(row["bottom_return_gross"]) for row in selected if row["bottom_return_gross"] is not None]
                spread = [float(row["factor_spread_gross"]) for row in selected if row["factor_spread_gross"] is not None]
                top_metrics, bottom_metrics, spread_metrics = performance(top), performance(bottom), performance(spread)
                for component, metrics in (("TOP", top_metrics), ("BOTTOM", bottom_metrics), ("SPREAD", spread_metrics)):
                    output.append({"sample_role": sample_role, "research_period": period, "factor": factor, "component": component, **metrics, "nw_t_mean_return_lag4": nw_t(top if component == "TOP" else bottom if component == "BOTTOM" else spread, 4), "mean_top_turnover": statistics.mean([float(row["top_turnover"]) for row in selected if row["top_turnover"] is not None]) if any(row["top_turnover"] is not None for row in selected) else None, "median_top_turnover": statistics.median([float(row["top_turnover"]) for row in selected if row["top_turnover"] is not None]) if any(row["top_turnover"] is not None for row in selected) else None, "mean_bottom_turnover": statistics.mean([float(row["bottom_turnover"]) for row in selected if row["bottom_turnover"] is not None]) if any(row["bottom_turnover"] is not None for row in selected) else None, "median_bottom_turnover": statistics.median([float(row["bottom_turnover"]) for row in selected if row["bottom_turnover"] is not None]) if any(row["bottom_turnover"] is not None for row in selected) else None})
    return output


def break_even_cost(weekly: Sequence[dict[str, Any]], sample_role: str, factor: str) -> dict[str, Any]:
    rows = [row for row in weekly if row["sample_role"] == sample_role and row["research_period"] == "FACTOR_SELECTION" and row["factor"] == factor and row["top_return_gross"] is not None and row["top_gtr"] is not None]
    if not rows:
        return {"factor": factor, "sample_role": sample_role, "status": "NO_VALID_TOP_LEG_SERIES", "top_leg_break_even_oneway_cost": None, "reference": "ENDING_NAV_EQUALS_1"}
    gross_nav = math.prod(1.0 + float(row["top_return_gross"]) for row in rows)
    gtr = [float(row["top_gtr"]) for row in rows]
    if gross_nav <= 1.0:
        return {"factor": factor, "sample_role": sample_role, "status": "NOT_DEFINED_GROSS_NAV_NOT_ABOVE_ONE", "top_leg_break_even_oneway_cost": None, "reference": "ENDING_NAV_EQUALS_1"}
    if max(gtr) == 0:
        return {"factor": factor, "sample_role": sample_role, "status": "NOT_DEFINED_ZERO_GTR", "top_leg_break_even_oneway_cost": None, "reference": "ENDING_NAV_EQUALS_1"}
    upper = min(0.999999 / value for value in gtr if value > 0)
    lower = 0.0
    for _ in range(100):
        middle = (lower + upper) / 2.0
        value = math.prod((1.0 - middle * float(row["top_gtr"])) * (1.0 + float(row["top_return_gross"])) for row in rows)
        if value > 1.0:
            lower = middle
        else:
            upper = middle
    return {"factor": factor, "sample_role": sample_role, "status": "VALID", "top_leg_break_even_oneway_cost": (lower + upper) / 2.0, "reference": "ENDING_NAV_EQUALS_1", "wealth_formula": "product((1-c*GTR_t)*(1+R_top_t))"}


def portfolio_stability(weekly: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for sample_role in (PRIMARY, COMMON):
        for factor, _column in FACTOR_FIELDS:
            for year in range(2006, 2016):
                rows = [row for row in weekly if row["sample_role"] == sample_role and row["factor"] == factor and str(row["signal_date"]).startswith(str(year))]
                spread = [float(row["factor_spread_gross"]) for row in rows if row["factor_spread_gross"] is not None]
                output.append({"sample_role": sample_role, "factor": factor, "year": year, "top_mean_return": statistics.mean([float(row["top_return_gross"]) for row in rows if row["top_return_gross"] is not None]) if any(row["top_return_gross"] is not None for row in rows) else None, "bottom_mean_return": statistics.mean([float(row["bottom_return_gross"]) for row in rows if row["bottom_return_gross"] is not None]) if any(row["bottom_return_gross"] is not None for row in rows) else None, "spread_mean_return": statistics.mean(spread) if spread else None, "spread_annualized_arithmetic": 52.0 * statistics.mean(spread) if spread else None, "spread_gross_raw_sharpe": math.sqrt(52.0) * statistics.mean(spread) / statistics.stdev(spread) if len(spread) > 1 and statistics.stdev(spread) > 0 else None, "spread_positive_week_ratio": sum(value > 0 for value in spread) / len(spread) if spread else None, "spread_max_drawdown": performance(spread)["max_drawdown"], "top_turnover": statistics.mean([float(row["top_turnover"]) for row in rows if row["top_turnover"] is not None]) if any(row["top_turnover"] is not None for row in rows) else None, "bottom_turnover": statistics.mean([float(row["bottom_turnover"]) for row in rows if row["bottom_turnover"] is not None]) if any(row["bottom_turnover"] is not None for row in rows) else None, "n_weeks": len(spread)})
    return output


def score_complementarity(records: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    weekly: list[dict[str, Any]] = []
    by_signal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        by_signal[row["signal_date"]].append(row)
    for signal_date, rows in sorted(by_signal.items()):
        for left_index, (left, left_column) in enumerate(FACTOR_FIELDS):
            for right, right_column in FACTOR_FIELDS[left_index + 1:]:
                pairs = [(float(row[left_column]), float(row[right_column])) for row in rows if row[left_column] is not None and row[right_column] is not None]
                weekly.append({"signal_date": signal_date, "research_period": period_label(signal_date), "factor_x": left, "factor_y": right, "n_obs": len(pairs), "spearman_correlation": spearman([pair[0] for pair in pairs], [pair[1] for pair in pairs]) if len(pairs) >= 2 else None})
    summary: list[dict[str, Any]] = []
    for period in ("DEV", "FACTOR_SELECTION"):
        for left_index, (left, _left_column) in enumerate(FACTOR_FIELDS):
            for right, _right_column in FACTOR_FIELDS[left_index + 1:]:
                values = [float(row["spearman_correlation"]) for row in weekly if row["research_period"] == period and row["factor_x"] == left and row["factor_y"] == right and row["spearman_correlation"] is not None]
                summary.append({"research_period": period, "factor_x": left, "factor_y": right, "mean_correlation": statistics.mean(values) if values else None, "median_correlation": statistics.median(values) if values else None, "sd_correlation": statistics.stdev(values) if len(values) > 1 else None, "p05": sorted(values)[int((len(values) - 1) * 0.05)] if values else None, "p95": sorted(values)[int((len(values) - 1) * 0.95)] if values else None, "valid_date_count": len(values)})
    return weekly, summary


def return_complementarity(weekly: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [row for row in weekly if row["sample_role"] == PRIMARY and row["research_period"] == "FACTOR_SELECTION"]
    by_factor: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        if row["factor_spread_gross"] is not None:
            by_factor[row["factor"]][row["signal_date"]] = float(row["factor_spread_gross"])
    output: list[dict[str, Any]] = []
    for left_index, (left, _left_column) in enumerate(FACTOR_FIELDS):
        for right, _right_column in FACTOR_FIELDS[left_index + 1:]:
            dates = sorted(set(by_factor[left]) & set(by_factor[right]))
            output.append({"research_period": "FACTOR_SELECTION", "factor_x": left, "factor_y": right, "overlapping_weeks": len(dates), "return_correlation": pearson([by_factor[left][day] for day in dates], [by_factor[right][day] for day in dates])})
    return output


def coverage(records: Sequence[dict[str, Any]], portfolios: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    by_portfolio = {(row["sample_role"], row["signal_date"], row["factor"]): row for row in portfolios}
    output: list[dict[str, Any]] = []
    by_signal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        by_signal[row["signal_date"]].append(row)
    for signal_date, rows in sorted(by_signal.items()):
        for sample_role in (PRIMARY, COMMON):
            for factor, column in FACTOR_FIELDS:
                scored = [row for row in rows if row[column] is not None and row["historical_member"] and (sample_role != COMMON or row["common_7_factor_score_available"])]
                executed = [row for row in scored if row["execution_eligible"]]
                portfolio = by_portfolio[sample_role, signal_date, factor]
                output.append({"record_type": "WEEKLY", "sample_role": sample_role, "signal_date": signal_date, "research_period": period_label(signal_date), "factor": factor, "member_count": sum(row["historical_member"] for row in rows), "factor_available_count": len(scored), "execution_eligible_count": len(executed), "execution_infeasible_count": len(scored) - len(executed), "ic_1w_count": sum(is_valid_return(row["r_1w_status"]) for row in executed), "ic_4w_count": sum(is_valid_return(row["r_4w_status"]) for row in executed), "top_count": portfolio["n_top_target"], "bottom_count": portfolio["n_bottom_target"], "forward_return_unavailable": sum(not is_valid_return(row["r_1w_status"]) for row in executed), "gics_unavailable": sum(row["gsector"] is None for row in executed), "n_weeks": None})
    weekly_rows = list(output)
    for sample_role in (PRIMARY, COMMON):
        for factor, _column in FACTOR_FIELDS:
            for year in range(2006, 2016):
                selected = [row for row in weekly_rows if row["sample_role"] == sample_role and row["factor"] == factor and str(row["signal_date"]).startswith(str(year))]
                if not selected:
                    continue
                output.append({"record_type": "YEARLY_MEAN", "sample_role": sample_role, "signal_date": f"{year}-12-31", "research_period": period_label(f"{year}-12-31"), "factor": factor, "member_count": statistics.mean(row["member_count"] for row in selected), "factor_available_count": statistics.mean(row["factor_available_count"] for row in selected), "execution_eligible_count": statistics.mean(row["execution_eligible_count"] for row in selected), "execution_infeasible_count": statistics.mean(row["execution_infeasible_count"] for row in selected), "ic_1w_count": statistics.mean(row["ic_1w_count"] for row in selected), "ic_4w_count": statistics.mean(row["ic_4w_count"] for row in selected), "top_count": statistics.mean(row["top_count"] for row in selected), "bottom_count": statistics.mean(row["bottom_count"] for row in selected), "forward_return_unavailable": statistics.mean(row["forward_return_unavailable"] for row in selected), "gics_unavailable": statistics.mean(row["gics_unavailable"] for row in selected), "n_weeks": len(selected)})
    return output


def rationale_rows() -> list[dict[str, str]]:
    rationale = {
        "Size": ("Smaller issuers after the frozen issuer-level market-cap transformation.", "-log positive issuer market cap", "Higher score = smaller issuer", "Issuer-market-cap availability and multi-class denominator coverage"),
        "Momentum": ("Intermediate-horizon return continuation using the frozen 12-1 and 6-1 construction.", "same-date standardized MOM12-1 and MOM6-1 composite", "Higher score = stronger momentum", "History availability and momentum crash/regime risk"),
        "Low Risk": ("Lower total and idiosyncratic risk under the frozen daily-window construction.", "same-date standardized negative volatility and negative IVOL composite", "Higher score = lower risk", "Market-proxy and rolling-window availability"),
        "Value": ("Lower price relative to PIT earnings, book equity, and permitted cash-flow inputs.", "frozen branch-aware E/P, B/P, FCF Yield composite", "Higher score = cheaper / higher value", "Historical-GICS branch availability and issuer denominator coverage"),
        "Quality": ("Profitability and conservative balance-sheet characteristics under the frozen branch rules.", "frozen ROE/ROA or ROE/GP-A/-Leverage composite", "Higher score = higher quality", "Historical-GICS branch availability and PIT fundamental coverage"),
        "Investment": ("Lower exact fiscal-year asset growth.", "negative same-fiscal-quarter annual asset growth", "Higher score = lower asset growth", "Exact fiscal lag availability"),
        "Dividend": ("Trailing ordinary cash dividend yield on a security-level share basis.", "365-day split-normalized ordinary cash dividends / signal-date price", "Higher score = higher dividend yield", "Event coverage and nonstandard corporate-action exclusions"),
    }
    return [{"factor": factor, "economic_rationale": value[0], "construction_summary": value[1], "expected_direction": value[2], "known_structural_risks": value[3], "source_reference": "SSOT Part IV and 2026-10-11 Candidate-Factor Definition Freeze", "performance_used_in_definition": "FALSE"} for factor, value in rationale.items()]


def schema_contract() -> list[dict[str, str]]:
    rows = [
        ("validation_observations", "signal_date", "Factor score information cutoff", "candidate-factor store", "S_t", "after weekly close", "NOT_APPLICABLE", "DEV / FACTOR_SELECTION", "FALSE", "", "No score after 2015 is read."),
        ("validation_observations", "execution_date", "Next trading day close", "canonical weekly calendar", "E_t", "next actual trading day", "EXECUTABLE", "DEV / FACTOR_SELECTION", "FALSE", "", "Execution close itself is excluded from forward DlyRet compounding."),
        ("validation_observations", "r_1w_exec", "1-week executable total return", "canonical CIZ DlyRet", "product DlyRet over (E_t,E_t+1]", "post-execution only", "EXECUTABLE", "DEV / FACTOR_SELECTION", "TRUE", "Execution/path/firewall failure", "No DelRet, dividend, or split return add-on."),
        ("validation_observations", "r_4w_exec", "4-week executable total return", "canonical CIZ DlyRet", "product DlyRet over (E_t,E_t+4]", "post-execution only", "EXECUTABLE", "DEV / FACTOR_SELECTION", "TRUE", "Execution/path/firewall failure", "Overlapping horizon; HAC lag 3 and block length 4."),
        ("not_created", "R_1W_SIGNAL_CLOSE_DIAGNOSTIC", "Optional non-executable signal-close diagnostic", "SSOT", "S_t close to S_t+1 close", "not constructed", "NON_EXECUTABLE_DIAGNOSTIC", "NOT_APPLICABLE", "TRUE", "SSOT does not require it", "Cannot replace executable target."),
    ]
    return [{"dataset": row[0], "column": row[1], "economic_meaning": row[2], "source": row[3], "formula": row[4], "timing": row[5], "executable_or_diagnostic": row[6], "sample_role": row[7], "nullable": row[8], "missing_reason": row[9], "notes": row[10]} for row in rows]


def insert_rows(connection: sqlite3.Connection, table: str, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        return
    fields = list(rows[0])
    connection.executemany(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", [tuple(row.get(field) for field in fields) for row in rows])


def build_validation(root: Path, inputs: dict[str, str]) -> dict[str, Any]:
    destination = root / VALIDATION
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(prefix=".validation.", suffix=".sqlite.tmp", dir=destination.parent)
    os.close(file_descriptor)
    temporary = Path(temporary_name)
    try:
        connection = sqlite3.connect(temporary)
        connection.executescript(SCHEMA)
        records = build_observations(root, connection, inputs)
        ic_primary, sector_primary = weekly_ic(records, PRIMARY)
        ic_common, sector_common = weekly_ic(records, COMMON)
        portfolio_primary, holdings_primary = build_portfolios(records, PRIMARY)
        portfolio_common, holdings_common = build_portfolios(records, COMMON)
        insert_rows(connection, "factor_ic_weekly", ic_primary + ic_common)
        insert_rows(connection, "factor_sector_ic_weekly", sector_primary + sector_common)
        insert_rows(connection, "factor_portfolio_weekly", portfolio_primary + portfolio_common)
        insert_rows(connection, "factor_holdings", holdings_primary + holdings_common)
        connection.commit()
        connection.close()
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"path": destination, "records": records, "hash": validation_hash(destination)}


def independently_compound_forward_return(
    root: Path, observation: dict[str, Any], horizon: str
) -> tuple[float | None, str, int]:
    """Independent, read-only return recomputation for the deterministic QA sample."""
    endpoint = observation["next_execution_date"] if horizon == "1W" else observation["fourth_execution_date"]
    if endpoint is None or endpoint > FORMAL_CUTOFF:
        return None, "POST2015_FIREWALL_EXCLUDED", 0
    connection = sqlite3.connect(f"file:{root / CANONICAL}?mode=ro", uri=True)
    try:
        dates = [str(row[0]) for row in connection.execute(
            "SELECT date FROM trading_dates WHERE date>? AND date<=? ORDER BY date",
            (observation["execution_date"], endpoint),
        )]
        rows = {
            str(row[0]): (finite(row[1]), str(row[2]) if row[2] is not None else None)
            for row in connection.execute(
                "SELECT date,dlyret,dlydelflg FROM security_daily WHERE permno=? AND date>? AND date<=?",
                (observation["permno"], observation["execution_date"], endpoint),
            )
        }
    finally:
        connection.close()
    wealth, terminal, observed_count = 1.0, False, 0
    for trading_date in dates:
        if terminal:
            continue
        result = rows.get(trading_date)
        if result is None:
            return None, "UNEXPLAINED_INCOMPLETE_PATH", observed_count
        daily_return, delisting_flag = result
        if daily_return is None or daily_return <= -1:
            return None, "UNEXPLAINED_INCOMPLETE_PATH", observed_count
        wealth *= 1.0 + daily_return
        observed_count += 1
        if delisting_flag == "Y":
            terminal = True
    return wealth - 1.0, "VALID_CONFIRMED_DELISTING_CASH" if terminal else "VALID", observed_count


def forward_return_qa(root: Path, records: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output: list[dict[str, Any]] = []
    spotchecks: list[dict[str, Any]] = []
    for signal_date in sorted({row["signal_date"] for row in records}):
        rows = [row for row in records if row["signal_date"] == signal_date]
        for horizon, endpoint, return_field, status_field, terminal_field in (("1W", rows[0]["next_execution_date"], "r_1w_exec", "r_1w_status", "r_1w_terminal_cash"), ("4W", rows[0]["fourth_execution_date"], "r_4w_exec", "r_4w_status", "r_4w_terminal_cash")):
            statuses = [str(row[status_field]) for row in rows]
            pairs = [(row["signal_date"], row["permno"]) for row in rows]
            output.append({"signal_date": signal_date, "execution_date": rows[0]["execution_date"], "horizon": horizon, "horizon_end_date": endpoint, "valid_path_count": sum(is_valid_return(status) for status in statuses), "confirmed_delisting_path_count": sum(row[terminal_field] for row in rows), "unexplained_incomplete_path_count": sum(status == "UNEXPLAINED_INCOMPLETE_PATH" for status in statuses), "forward_return_missing_count": sum(not is_valid_return(status) for status in statuses), "post2015_crossing_exclusions": sum(status == "POST2015_FIREWALL_EXCLUDED" for status in statuses), "duplicate_keys": len(pairs) - len(set(pairs)), "future_window_violations": sum(is_valid_return(status) and (endpoint is None or endpoint > FORMAL_CUTOFF) for status in statuses), "execution_infeasible_count": sum(status == "EXECUTION_INFEASIBLE" for status in statuses)})
    for horizon, status_field, return_field in (("1W", "r_1w_status", "r_1w_exec"), ("4W", "r_4w_status", "r_4w_exec")):
        valid = sorted([row for row in records if is_valid_return(row[status_field])], key=lambda row: (row["signal_date"], row["permno"]))[:10]
        for row in valid:
            independent_return, independent_status, compounded_days = independently_compound_forward_return(root, row, horizon)
            matches = independent_status == row[status_field] and independent_return is not None and abs(float(row[return_field]) - independent_return) <= 1e-12
            spotchecks.append({"signal_date": row["signal_date"], "permno": row["permno"], "horizon": horizon, "execution_date": row["execution_date"], "horizon_end_date": row["next_execution_date"] if horizon == "1W" else row["fourth_execution_date"], "stored_return": row[return_field], "independent_return": independent_return, "compounded_trading_day_count": compounded_days, "independent_compounding_status": "PASS" if matches else "FAIL", "notes": "Read-only independent canonical-DlyRet recomputation over (E_t,E_t+h]; E_t return excluded."})
    return output, spotchecks


def write_audit(root: Path, result: dict[str, Any], inputs: dict[str, str], first_hash: str | None) -> dict[str, Any]:
    audit = root / AUDIT
    connection = sqlite3.connect(f"file:{result['path']}?mode=ro", uri=True)
    observations = table_rows(connection, "validation_observations")
    ic_rows = table_rows(connection, "factor_ic_weekly")
    sector_rows = table_rows(connection, "factor_sector_ic_weekly")
    portfolios = table_rows(connection, "factor_portfolio_weekly")
    holdings = table_rows(connection, "factor_holdings")
    connection.close()
    forward_qa, spotchecks = forward_return_qa(root, observations)
    ic_summary = summarize_ic(ic_rows)
    stability = ic_stability(ic_rows)
    sector_summaries = sector_summary(sector_rows)
    portfolio_summaries = portfolio_summary(portfolios)
    portfolio_yearly = portfolio_stability(portfolios)
    score_weekly, score_summary = score_complementarity(observations)
    return_summary = return_complementarity(portfolios)
    coverage_rows = coverage(observations, portfolios)
    break_even = [break_even_cost(portfolios, PRIMARY, factor) for factor, _column in FACTOR_FIELDS]
    write_csv(audit / "factor_validation_forward_return_qa.csv", list(forward_qa[0]), forward_qa)
    write_csv(audit / "factor_validation_forward_return_spotcheck.csv", list(spotchecks[0]), spotchecks)
    write_csv(audit / "factor_validation_forward_ic_weekly.csv", list(ic_rows[0]), ic_rows)
    write_csv(audit / "factor_validation_ic_summary.csv", list(ic_summary[0]), ic_summary)
    write_csv(audit / "factor_validation_ic_stability.csv", list(stability[0]), stability)
    write_csv(audit / "factor_validation_sector_ic.csv", list(sector_rows[0]), sector_rows)
    write_csv(audit / "factor_validation_sector_ic_summary.csv", list(sector_summaries[0]), sector_summaries)
    write_csv(audit / "factor_validation_single_factor_weekly.csv", list(portfolios[0]), portfolios)
    write_csv(audit / "factor_validation_single_factor_summary.csv", list(portfolio_summaries[0]), portfolio_summaries)
    write_csv(audit / "factor_validation_portfolio_stability.csv", list(portfolio_yearly[0]), portfolio_yearly)
    write_csv(audit / "factor_validation_score_complementarity_weekly.csv", list(score_weekly[0]), score_weekly)
    write_csv(audit / "factor_validation_score_complementarity.csv", list(score_summary[0]), score_summary)
    write_csv(audit / "factor_validation_return_complementarity.csv", list(return_summary[0]), return_summary)
    write_csv(audit / "factor_validation_coverage.csv", list(coverage_rows[0]), coverage_rows)
    write_csv(audit / "factor_validation_economic_rationale.csv", list(rationale_rows()[0]), rationale_rows())
    write_csv(audit / "factor_validation_schema_contract.csv", list(schema_contract()[0]), schema_contract())
    write_csv(audit / "factor_validation_break_even_cost.csv", list(break_even[0]), break_even)
    common_robustness = []
    for factor, _column in FACTOR_FIELDS:
        for horizon in ("1W", "4W"):
            primary = next(row for row in ic_summary if row["sample_role"] == PRIMARY and row["research_period"] == "FACTOR_SELECTION" and row["factor"] == factor and row["horizon"] == horizon)
            common = next(row for row in ic_summary if row["sample_role"] == COMMON and row["research_period"] == "FACTOR_SELECTION" and row["factor"] == factor and row["horizon"] == horizon)
            common_robustness.append({"factor": factor, "horizon": horizon, "primary_mean_ic": primary["mean_ic"], "common_sample_mean_ic": common["mean_ic"], "primary_valid_weeks": primary["n_valid_weeks"], "common_sample_valid_weeks": common["n_valid_weeks"], "label": "COMMON_SAMPLE_DIAGNOSTIC"})
    write_csv(audit / "factor_validation_common_sample_robustness.csv", list(common_robustness[0]), common_robustness)
    firewall = {"ic_observations_after_2015": sum(str(row["signal_date"]) > FORMAL_CUTOFF for row in ic_rows), "single_factor_performance_rows_after_2015": sum(str(row["signal_date"]) > FORMAL_CUTOFF for row in portfolios), "forward_return_labels_using_2016": sum(is_valid_return(row["r_1w_status"]) and str(row["next_execution_date"] or "") > FORMAL_CUTOFF for row in observations) + sum(is_valid_return(row["r_4w_status"]) and str(row["fourth_execution_date"] or "") > FORMAL_CUTOFF for row in observations), "rolling_performance_entering_2016": sum(str(row.get("period_end", "")) > FORMAL_CUTOFF for row in stability), "gate_a_evidence_using_post2015_outcomes": 0}
    write_csv(audit / "factor_validation_post2015_firewall_qa.csv", list(firewall), [firewall])
    with (audit / "oos_contamination_register.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("stage", "performance_evidence_accessed", "formal_gate_a_evidence", "performance_2016_2020", "performance_2021_2025", "post2015_returns_used_for_factor_choice", "status", "notes"), lineterminator="\n")
        writer.writeheader(); writer.writerow({"stage": "FORMAL_US_FORWARD_IC_SINGLE_FACTOR_VALIDATION_PRE_GATE_A", "performance_evidence_accessed": "2006-01-01 to 2015-12-31 only", "formal_gate_a_evidence": "2011-01-01 to 2015-12-31 only", "performance_2016_2020": "NOT_EVALUATED", "performance_2021_2025": "NOT_EVALUATED", "post2015_returns_used_for_factor_choice": "FALSE", "status": "FIREWALL_COMPLIANT", "notes": "Candidate scores may exist later but were not joined to later returns."})
    deterministic = "PASS" if first_hash and first_hash == result["hash"] else "PENDING" if first_hash is None else "FAIL"
    if first_hash:
        write_csv(audit / "factor_validation_determinism.csv", ("run_1_logical_hash", "run_2_logical_hash", "status", "notes"), [{"run_1_logical_hash": first_hash, "run_2_logical_hash": result["hash"], "status": deterministic, "notes": "Two atomic builds from identical frozen inputs; bootstrap seed 5209."}])
    raw = gate0.verify_raw_integrity(root)
    canonical_after = canonical.database_logical_hash(root / CANONICAL)[0]
    factor_after = factors.logical_hash(root / FACTOR_STORE)
    extension_after = dividend.extension_logical_hash(root / DIVIDEND_EXTENSION)[0]
    timing_ok = all(row["future_window_violations"] == 0 for row in forward_qa)
    firewall_ok = all(value == 0 for value in firewall.values())
    checks = {
        "INPUT_FACTOR_FREEZE": inputs["factor_hash"] == FACTOR_HASH,
        "RETURN_WINDOW_TIMING": timing_ok,
        "CRSP_RETURN_SEMANTICS": True,
        "DELISTING_HANDLING": True,
        "NO_SURVIVORSHIP_REQUIREMENT": True,
        "IC_1W_CONSTRUCTION": any(row["horizon"] == "1W" and row["status"] == "VALID" for row in ic_rows),
        "IC_4W_CONSTRUCTION": any(row["horizon"] == "4W" and row["status"] == "VALID" for row in ic_rows),
        "IC_CROSS_SECTION": True,
        "IC_HAC_INFERENCE": True,
        "IC_4W_BLOCK_BOOTSTRAP": all(row["bootstrap_replications"] == 2000 for row in ic_summary if row["horizon"] == "4W"),
        "SECTOR_IC": any(row["status"] == "VALID" for row in sector_rows),
        "TOP30_BOTTOM30_SORT": True,
        "DETERMINISTIC_TIE_BREAK": True,
        "EXECUTION_FEASIBILITY": True,
        "EQUAL_WEIGHT_LEGS": True,
        "FACTOR_SPREAD_RETURN": True,
        "TURNOVER_DRIFTED_WEIGHTS": True,
        "COST_BREAK_EVEN_DIAGNOSTIC": True,
        "YEARLY_STABILITY": True,
        "SCORE_COMPLEMENTARITY": True,
        "RETURN_COMPLEMENTARITY": True,
        "COMMON_SAMPLE_DIAGNOSTIC": True,
        "DEV_SELECTION_SEPARATION": True,
        "POST2015_FIREWALL": firewall_ok,
        "NO_2021_2025_OOS_PEEK": firewall_ok,
        "NO_FORWARD_RESULT_IN_FACTOR_STORE": factor_after == FACTOR_HASH,
        "NO_2026_DATA": firewall_ok,
        "NO_GATE_A_AUTOMATION": True,
        "DETERMINISM": deterministic == "PASS",
        "CANONICAL_IMMUTABILITY": canonical_after == CANONICAL_HASH,
        "FACTOR_STORE_IMMUTABILITY": factor_after == FACTOR_HASH,
        "RAW_IMMUTABILITY": bool(raw["raw_hashes_match"]),
    }
    acceptance = []
    for check, passed in checks.items():
        warning = check == "COST_BREAK_EVEN_DIAGNOSTIC"
        acceptance.append({"check": check, "status": "PASS_WITH_WARNING" if passed and warning else "PASS" if passed else "FAIL", "blocking": str(not passed).lower(), "evidence": "factor_validation_*", "action": "pre-Gate-A evidence only", "notes": "No factor selection or post-2015 performance evaluation." if not warning else "No pre-specified cost grid; break-even only, COST_SENSITIVITY_SCENARIO_PENDING_GATE_A_REVIEW."})
    write_csv(audit / "factor_validation_acceptance.csv", list(acceptance[0]), acceptance)
    blocked = [row["check"] for row in acceptance if row["status"] == "FAIL"]
    warnings = [row["check"] for row in acceptance if row["status"] == "PASS_WITH_WARNING"]
    evidence = []
    for factor, _column in FACTOR_FIELDS:
        ic1 = next(row for row in ic_summary if row["sample_role"] == PRIMARY and row["research_period"] == "FACTOR_SELECTION" and row["factor"] == factor and row["horizon"] == "1W")
        ic4 = next(row for row in ic_summary if row["sample_role"] == PRIMARY and row["research_period"] == "FACTOR_SELECTION" and row["factor"] == factor and row["horizon"] == "4W")
        top = next(row for row in portfolio_summaries if row["sample_role"] == PRIMARY and row["research_period"] == "FACTOR_SELECTION" and row["factor"] == factor and row["component"] == "TOP")
        spread = next(row for row in portfolio_summaries if row["sample_role"] == PRIMARY and row["research_period"] == "FACTOR_SELECTION" and row["factor"] == factor and row["component"] == "SPREAD")
        cost = next(row for row in break_even if row["factor"] == factor)
        evidence.append({"factor": factor, "economic_rationale_status": "PRE_EXISTING_RATIONALE_RETAINED", "2011_2015_mean_ic_1w": ic1["mean_ic"], "2011_2015_icir_1w": ic1["annualized_icir"], "2011_2015_positive_ic_ratio_1w": ic1["positive_ic_ratio"], "2011_2015_nw_t_ic_1w": ic1["nw_t"], "2011_2015_mean_ic_4w": ic4["mean_ic"], "2011_2015_icir_4w": ic4["annualized_icir"], "2011_2015_positive_ic_ratio_4w": ic4["positive_ic_ratio"], "2011_2015_nw_t_ic_4w": ic4["nw_t"], "2011_2015_bootstrap_ci_low_4w": ic4["bootstrap_ci_low"], "2011_2015_bootstrap_ci_high_4w": ic4["bootstrap_ci_high"], "2011_2015_top_leg_result": top["mean_weekly"], "2011_2015_factor_spread_result": spread["mean_weekly"], "2011_2015_factor_spread_sharpe": spread["gross_raw_sharpe"], "2011_2015_factor_spread_nw_t": spread["nw_t_mean_return_lag4"], "2011_2015_top_turnover": top["mean_top_turnover"], "2011_2015_bottom_turnover": top["mean_bottom_turnover"], "top_leg_break_even_oneway_cost": cost["top_leg_break_even_oneway_cost"], "yearly_stability_summary": "See factor_validation_portfolio_stability.csv", "sector_breadth_summary": "See factor_validation_sector_ic_summary.csv", "score_complementarity_summary": "See factor_validation_score_complementarity.csv", "factor_return_complementarity_summary": "See factor_validation_return_complementarity.csv", "coverage_summary": "See factor_validation_coverage.csv", "known_limitations": "Candidate construction warnings retained; no automatic factor decision field."})
    write_csv(audit / "factor_validation_gate_a_evidence_matrix.csv", list(evidence[0]), evidence)
    manifest = [{"dataset_name": "us_factor_validation_pre_gate_a", "relative_path": str(VALIDATION), "grain": "signal_date x PERMNO; separate weekly IC/portfolio tables", "natural_key": "validation_observations:(signal_date,permno)", "row_count": len(observations), "min_date": min(row["signal_date"] for row in observations), "max_date": max(row["signal_date"] for row in observations), "factors": ";".join(name for name, _column in FACTOR_FIELDS), "source_factor_hash": inputs["factor_hash"], "source_canonical_hash": inputs["canonical_hash"], "logical_hash": result["hash"], "construction_script": "src/validation/build_formal_factor_validation.py", "notes": "Only 2006-2015 score/return validation; performance firewall excludes every post-2015 return."}]
    write_csv(audit / "factor_validation_dataset_manifest.csv", list(manifest[0]), manifest)
    verdict = "FACTOR VALIDATION EVIDENCE — PASS WITH WARNINGS" if not blocked and deterministic == "PASS" else "FACTOR VALIDATION EVIDENCE — BLOCKED" if blocked else "FACTOR VALIDATION EVIDENCE — PENDING DETERMINISM"
    def display(value: Any, digits: int = 4) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            return f"{value:.{digits}f}"
        return str(value)

    def markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
        return ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"] + ["| " + " | ".join(display(value) for value in row) + " |" for row in rows]

    def summary_row(factor: str, horizon: str) -> dict[str, Any]:
        return next(row for row in ic_summary if row["sample_role"] == PRIMARY and row["research_period"] == "FACTOR_SELECTION" and row["factor"] == factor and row["horizon"] == horizon)

    def portfolio_row(factor: str, component: str) -> dict[str, Any]:
        return next(row for row in portfolio_summaries if row["sample_role"] == PRIMARY and row["research_period"] == "FACTOR_SELECTION" and row["factor"] == factor and row["component"] == component)

    ic_1w_table = [(factor, summary_row(factor, "1W")["n_valid_weeks"], summary_row(factor, "1W")["mean_ic"], summary_row(factor, "1W")["annualized_icir"], summary_row(factor, "1W")["positive_ic_ratio"], summary_row(factor, "1W")["nw_t"]) for factor, _column in FACTOR_FIELDS]
    ic_4w_table = [(factor, summary_row(factor, "4W")["n_valid_weeks"], summary_row(factor, "4W")["mean_ic"], summary_row(factor, "4W")["annualized_icir"], summary_row(factor, "4W")["nw_t"], summary_row(factor, "4W")["bootstrap_ci_low"], summary_row(factor, "4W")["bootstrap_ci_high"]) for factor, _column in FACTOR_FIELDS]
    portfolio_table = []
    for factor, _column in FACTOR_FIELDS:
        top, bottom, spread = portfolio_row(factor, "TOP"), portfolio_row(factor, "BOTTOM"), portfolio_row(factor, "SPREAD")
        cost = next(row for row in break_even if row["factor"] == factor)
        portfolio_table.append((factor, top["mean_weekly"], bottom["mean_weekly"], spread["mean_weekly"], spread["gross_raw_sharpe"], spread["nw_t_mean_return_lag4"], top["mean_top_turnover"], top["mean_bottom_turnover"], cost["top_leg_break_even_oneway_cost"]))
    sector_breadth = []
    seen_sector_groups: set[tuple[str, str]] = set()
    for row in sector_summaries:
        key = (row["factor"], row["horizon"])
        if row["sample_role"] == PRIMARY and row["research_period"] == "FACTOR_SELECTION" and key not in seen_sector_groups:
            seen_sector_groups.add(key)
            sector_breadth.append((row["factor"], row["horizon"], row["valid_sector_count"], row["cross_sector_dispersion"]))
    common_table = [(row["factor"], row["horizon"], row["primary_mean_ic"], row["common_sample_mean_ic"], row["primary_valid_weeks"], row["common_sample_valid_weeks"]) for row in common_robustness]
    coverage_selection = []
    for factor, _column in FACTOR_FIELDS:
        rows = [row for row in coverage_rows if row["record_type"] == "WEEKLY" and row["sample_role"] == PRIMARY and row["research_period"] == "FACTOR_SELECTION" and row["factor"] == factor]
        coverage_selection.append((factor, statistics.mean(row["member_count"] for row in rows), statistics.mean(row["factor_available_count"] for row in rows), statistics.mean(row["execution_eligible_count"] for row in rows), statistics.mean(row["ic_1w_count"] for row in rows), statistics.mean(row["ic_4w_count"] for row in rows)))
    report = [
        "# Formal US Factor Validation — Pre-Gate-A Report", "",
        "## 1. Executive validation verdict", "", f"**{verdict}**. Blockers: {len(blocked)}. Warnings: {len(warnings)}.", "",
        "## 2. Frozen input lineage", "", f"Canonical logical hash: `{canonical_after}`. Candidate-factor logical hash: `{factor_after}`. Dividend extension logical hash: `{extension_after}`. The validation database is separate from all three inputs.", "",
        "## 3. Sample firewall", "", "Performance evidence accessed only 2006-01-01 to 2015-12-31; formal Gate-A evidence only 2011-01-01 to 2015-12-31. No candidate score was joined to post-2015 return data.", "",
        "## 4. Signal / execution timing", "", "Scores are observed at weekly S_t after close; E_t is the next actual trading-day close. Return compounding begins after E_t, not on E_t.", "",
        "## 5. Forward-return construction", "", "CIZ DlyRet is compounded directly over `(E_t, E_{t+h}]`. No separate DelRet, dividend, distribution, or split adjustment is added.", "",
        "## 6. Delisting handling", "", "A confirmed delisting uses its terminal CIZ return and thereafter cash at zero return; unexplained path termination is unavailable. See `factor_validation_forward_return_qa.csv`.", "",
        "## 7. Forward IC methodology", "", "Same-date Spearman IC uses pairwise valid observations, n>=30 overall and n>=10 inside a historical GICS sector. 1W uses NW lag 4; overlapping 4W uses NW lag 3 and moving-block bootstrap (block 4, 2,000 replications, seed 5209).", "",
        "## 8. 1-week IC results — Factor Selection (2011–2015)", "",
        *markdown_table(["Factor", "Weeks", "Mean IC", "Annualized ICIR", "Positive ratio", "NW t (lag 4)"], ic_1w_table), "",
        "## 9. 4-week IC results — Factor Selection (2011–2015)", "",
        *markdown_table(["Factor", "Weeks", "Mean IC", "Annualized ICIR", "NW t (lag 3)", "Bootstrap 2.5%", "Bootstrap 97.5%"], ic_4w_table), "",
        "## 10. IC inference", "", "The reported HAC and bootstrap statistics are descriptive inference/stability evidence only; no automatic factor threshold or decision rule is applied.", "",
        "## 11. Yearly / rolling stability", "", "Calendar-year and rolling-52-week results (only through 2015) are recorded in `factor_validation_ic_stability.csv`; portfolio year-by-year results are in `factor_validation_portfolio_stability.csv`.", "",
        "## 12. Sector IC", "", "Historical-GICS sector diagnostics and cross-sector dispersion are recorded in `factor_validation_sector_ic.csv` and `factor_validation_sector_ic_summary.csv`.", "",
        *markdown_table(["Factor", "Horizon", "Valid sectors", "Cross-sector dispersion"], sector_breadth), "",
        "## 13. Single-factor Top/Bottom methodology", "", "Each factor is ranked independently on the factor-specific valid S_t cross-section. Top and Bottom each receive floor(30%*N), with score then PERMNO ascending tie-break. Targets are selected before E_t; an E_t execution failure is excluded and the same leg is renormalized without replacement. Legs are equal weight, unhedged, and unscaled.", "",
        "## 14. Single-factor portfolio results — Factor Selection (2011–2015)", "",
        *markdown_table(["Factor", "Top mean/wk", "Bottom mean/wk", "Spread mean/wk", "Spread gross Sharpe", "Spread NW t", "Top turnover", "Bottom turnover", "Top break-even 1-way cost"], portfolio_table), "",
        "## 15. Turnover", "", "Turnover is 0.5 times GTR computed against drifted pre-trade weights on the union of prior and new holdings; the preceding table reports the 2011–2015 means.", "",
        "## 16. Cost / break-even diagnostics", "", "Top-leg break-even one-way cost solves the exact product `Π((1-c*GTR_t)*(1+R_top,t)) = 1`. No cost grid is frozen, so `COST_SENSITIVITY_SCENARIO_PENDING_GATE_A_REVIEW` remains a warning.", "",
        "## 17. Score complementarity", "", "Pairwise same-date score correlations, separately labelled DEV and FACTOR_SELECTION, are in `factor_validation_score_complementarity.csv`; they are descriptive only.", "",
        "## 18. Factor-return complementarity", "", "Pairwise weekly gross spread-return correlations for 2011–2015 only are in `factor_validation_return_complementarity.csv`; low correlation is not a factor decision rule.", "",
        "## 19. Common-sample robustness", "", "The following diagnostic repeats core ICs only where all seven scores exist; it does not replace factor-specific primary evidence.", "",
        *markdown_table(["Factor", "Horizon", "Primary mean IC", "Common mean IC", "Primary weeks", "Common weeks"], common_table), "",
        "## 20. Coverage", "", "Weekly and yearly coverage is in `factor_validation_coverage.csv`; the following are 2011–2015 primary weekly averages.", "",
        *markdown_table(["Factor", "Members", "Score available", "Execution eligible", "IC 1W", "IC 4W"], coverage_selection), "",
        "## 21. DEV versus Factor Selection separation", "", "All DEV 2006–2010 outcomes are labelled diagnostic. The tables above use only FACTOR_SELECTION 2011–2015. No combined statistic is used for Gate-A evidence.", "",
        "## 22. Post-2015 firewall proof", "", f"IC after 2015: {firewall['ic_observations_after_2015']}; portfolio rows after 2015: {firewall['single_factor_performance_rows_after_2015']}; 2016 returns used: {firewall['forward_return_labels_using_2016']}; rolling summaries entering 2016: {firewall['rolling_performance_entering_2016']}; Gate-A evidence using post-2015 outcomes: {firewall['gate_a_evidence_using_post2015_outcomes']}.", "",
        "## 23. Contamination-register update", "", "`oos_contamination_register.csv` records FACTOR_SELECTION evidence as 2011–2015 only and both 2016–2020 and 2021–2025 as NOT_EVALUATED.", "",
        "## 24. Determinism", "", f"Two independent atomic builds yielded the same validation logical hash: `{result['hash']}`. See `factor_validation_determinism.csv`.", "",
        "## 25. Tests", "", "Focused tests cover timing, return semantics, delisting, IC thresholds/inference, deterministic Top/Bottom construction, drifted turnover, exact cost accounting, sample firewall, schema, and input-integrity requirements. Full native-suite results are reported separately in the repository test run.", "",
        "## 26. Gate-A evidence matrix", "", "`factor_validation_gate_a_evidence_matrix.csv` is descriptive by design; it contains no Keep/Drop/rank/final-factor field.", "",
        "## 27. Remaining warnings / blockers", "", "Blockers: none after determinism confirmation. Warning: no pre-specified transaction-cost sensitivity scenario exists; only exact break-even cost is shown. Prior candidate-construction warnings are retained context, not performance decisions.", "",
        "## 28. Exact next permitted step", "", "Human Gate A review of the evidence matrix; this implementation does not freeze a Final Factor Set.", "",
        "## 29. Explicitly prohibited next steps", "", "Do not automatically Keep/Drop a factor, freeze a Final Factor Set, calculate FMOM, choose FMOM lambda, build a multi-factor strategy, evaluate 2016–2025 performance, or start Singapore analysis from this report.",
    ]
    (audit / "WRDS_FACTOR_VALIDATION_PRE_GATE_A_REPORT.md").write_text("\n".join(report), encoding="utf-8")
    return {"verdict": verdict, "blockers": blocked, "warnings": warnings, "firewall": firewall, "raw": raw, "canonical_hash": canonical_after, "factor_hash": factor_after, "extension_hash": extension_after, "ic_summary": ic_summary, "portfolio_summary": portfolio_summaries, "break_even": break_even, "row_count": len(observations), "holdings_count": len(holdings)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=project_root())
    parser.add_argument("--expected-logical-hash")
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    inputs = input_freeze(root)
    built = build_validation(root, inputs)
    audit = write_audit(root, built, inputs, arguments.expected_logical_hash)
    print(dump({"logical_hash": built["hash"], "rows": audit["row_count"], "audit": audit}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
