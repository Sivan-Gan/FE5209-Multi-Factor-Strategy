
"""Point-in-time adaptive factor risk weighting.

Reproduces historical EW, IV and MV methodology.

- Last 52 common completed factor-return weeks
- IV: inverse volatility with constrained projection
- MV: 10% diagonal covariance shrinkage
- SLSQP with 5%-80% factor weight bounds
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from scipy.optimize import minimize


DEFAULT_FAMILIES = [
    "Quality",
    "Momentum",
    "Low Risk",
    "Investment",
    "Value",
    "Dividend",
    "Size",
]


@dataclass(frozen=True)
class WeightingConfig:
    window: int = 52
    min_weight: float = 0.05
    max_weight: float = 0.80
    shrinkage: float = 0.10


def historical_covariance(
    history: pd.DataFrame,
    cutoff: str | pd.Timestamp,
    families: list[str],
    config: WeightingConfig,
) -> tuple[np.ndarray, pd.DataFrame]:
    """Estimate covariance using completed common factor returns."""

    cutoff = pd.Timestamp(cutoff)

    required = {
        "signal_date",
        "next_execution_date",
        "family",
        "factor_return_gross",
        "factor_return_available",
    }

    missing = required - set(history.columns)

    if missing:
        raise ValueError(
            f"Missing columns: {missing}"
        )

    data = history.copy()

    data["signal_date"] = pd.to_datetime(
        data["signal_date"]
    )

    data["next_execution_date"] = pd.to_datetime(
        data["next_execution_date"]
    )

    data["factor_return_gross"] = pd.to_numeric(
        data["factor_return_gross"],
        errors="coerce",
    )

    past = data[
        data["next_execution_date"].le(cutoff)
        & data["factor_return_available"].eq(1)
    ].copy()

    wide = past.pivot(
        index="signal_date",
        columns="family",
        values="factor_return_gross",
    )

    wide = wide.reindex(columns=families)

    # Preserve original methodology:
    # common complete observations across all families.
    wide = wide.dropna().tail(config.window)

    if len(wide) != config.window:
        raise ValueError(
            f"Insufficient common history at {cutoff}"
        )

    cov = wide.cov().to_numpy(dtype=float)

    if not np.isfinite(cov).all():
        raise ValueError("Invalid covariance matrix")

    return cov, wide


def optimize_weights(
    target: np.ndarray,
    config: WeightingConfig,
    cov: np.ndarray | None = None,
) -> np.ndarray:
    """Original constrained SLSQP optimizer."""

    target = np.asarray(target, dtype=float)
    n = len(target)

    if not n * config.min_weight <= 1 <= n * config.max_weight:
        raise ValueError("Infeasible weight bounds")

    if cov is None:
        objective = lambda w: float(
            np.sum((w - target) ** 2)
        )
    else:
        scale = max(float(np.trace(cov)), 1e-12)

        objective = lambda w: float(
            w @ cov @ w / scale
        )

    result = minimize(
        objective,
        x0=np.ones(n) / n,
        method="SLSQP",
        bounds=[
            (config.min_weight, config.max_weight)
        ] * n,
        constraints=[{
            "type": "eq",
            "fun": lambda w: np.sum(w) - 1.0,
        }],
        options={
            "ftol": 1e-12,
            "maxiter": 1000,
        },
    )

    if not result.success:
        raise RuntimeError(result.message)

    w = result.x

    if (
        not np.isclose(w.sum(), 1.0, atol=1e-9)
        or (w < config.min_weight - 1e-9).any()
        or (w > config.max_weight + 1e-9).any()
    ):
        raise ValueError("Invalid optimized weights")

    return w


def calculate_methods(
    cov: np.ndarray,
    config: WeightingConfig,
) -> dict[str, np.ndarray]:
    """Calculate EW, IV and MV weights for arbitrary K."""

    cov = np.asarray(cov, dtype=float)

    if cov.ndim != 2 or cov.shape[0] != cov.shape[1]:
        raise ValueError("Covariance must be square")

    n = cov.shape[0]

    if not n * config.min_weight <= 1 <= n * config.max_weight:
        raise ValueError("Infeasible weight bounds")

    ew = np.ones(n) / n

    vol = np.sqrt(np.diag(cov))

    if (
        not np.isfinite(vol).all()
        or (vol <= 0).any()
    ):
        raise ValueError("Invalid factor volatilities")

    iv_raw = 1.0 / vol
    iv_raw = iv_raw / iv_raw.sum()

    iv = optimize_weights(
        iv_raw,
        config,
    )

    shrunk = (
        (1 - config.shrinkage) * cov
        + config.shrinkage
        * np.diag(np.diag(cov))
    )

    mv = optimize_weights(
        ew,
        config,
        cov=shrunk,
    )

    return {
        "EW": ew,
        "IV": iv,
        "MV": mv,
    }


def calculate_selected_weights(
    history: pd.DataFrame,
    cutoff: str | pd.Timestamp,
    selected_factors: list[str],
    config: WeightingConfig = WeightingConfig(),
    covariance_families: list[str] | None = None,
) -> tuple[dict[str, dict[str, float]], pd.DataFrame]:
    """Calculate weights for selected factors.

    By default, covariance uses the full seven-factor
    common-history universe, then extracts selected factors.
    """

    if covariance_families is None:
        covariance_families = DEFAULT_FAMILIES

    if not set(selected_factors).issubset(covariance_families):
        raise ValueError(
            "Selected factors missing from covariance universe"
        )

    cov_all, wide = historical_covariance(
        history=history,
        cutoff=cutoff,
        families=covariance_families,
        config=config,
    )

    indices = [
        covariance_families.index(f)
        for f in selected_factors
    ]

    cov = cov_all[np.ix_(indices, indices)]

    methods = calculate_methods(
        cov,
        config,
    )

    weights = {
        method: {
            family: float(value)
            for family, value in zip(
                selected_factors, values
            )
        }
        for method, values in methods.items()
    }

    return weights, wide
