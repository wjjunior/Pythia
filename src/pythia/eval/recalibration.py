"""Recalibration maps (§4.9): a monotone p_model → p_calibrated fit from a resolved track record.

Pure: no clock, no randomness, no I/O. Time enters as the `asof` parameter and per-sample
`resolved_at`, so a fit can only ever see resolutions strictly before `asof` (no lookahead). Maps
are monotone by construction (parametric slopes constrained ≥ 0; isotonic by pooling).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np
from scipy.optimize import minimize

from pythia.common import RecalibrationMethod
from pythia.eval.functions import brier_score
from pythia.eval.models import RecalibrationReport

# §4.9: isotonic needs a large set (~1000+) to beat a parametric fit; below this stay parametric.
ISOTONIC_MIN_N = 1000
_EPS = 1e-6
_L2 = 1e-3

Method = Literal["identity", "platt", "beta", "isotonic"]
Params = dict[str, float | list[float]]
Sample = tuple[float, int, datetime]


def as_method(value: str) -> Method:
    match value:
        case "identity" | "platt" | "beta" | "isotonic":
            return value
        case _:
            raise ValueError(f"unknown recalibration method: {value!r}")


@dataclass(frozen=True, slots=True)
class CalibrationMap:
    method: Method
    params: Params

    def apply(self, p: float) -> float:
        if self.method == "identity":
            return p
        pc = min(max(p, _EPS), 1.0 - _EPS)
        if self.method == "platt":
            z = _f(self.params["a"]) * _logit(pc) + _f(self.params["b"])
        elif self.method == "beta":
            z = (
                _f(self.params["a"]) * np.log(pc)
                - _f(self.params["b"]) * np.log(1.0 - pc)
                + _f(self.params["c"])
            )
        else:  # isotonic
            knots_x = self.params["x"]
            knots_y = self.params["y"]
            assert isinstance(knots_x, list) and isinstance(knots_y, list)
            return float(np.clip(np.interp(pc, knots_x, knots_y), 0.0, 1.0))
        return float(1.0 / (1.0 + np.exp(-z)))


def _f(value: float | list[float]) -> float:
    assert isinstance(value, float | int)
    return float(value)


def _logit(p: float) -> float:
    return float(np.log(p / (1.0 - p)))


def _fit_logistic(
    design: np.ndarray, outcomes: np.ndarray, bounds: list[tuple[float | None, float | None]]
) -> np.ndarray | None:
    def objective(theta: np.ndarray) -> float:
        z = design @ theta
        loss = np.mean(np.logaddexp(0.0, z) - outcomes * z)
        return float(loss + _L2 * np.sum(theta**2))

    result = minimize(objective, np.zeros(design.shape[1]), method="L-BFGS-B", bounds=bounds)
    coefficients: np.ndarray = result.x
    # The L2-regularized log-loss is strictly convex, so a finite solution is the global one;
    # reject a non-finite result rather than persist NaN/inf coefficients downstream.
    if not np.isfinite(coefficients).all():
        return None
    return coefficients


def _fit_platt(p: np.ndarray, y: np.ndarray) -> CalibrationMap:
    logits = np.log(p / (1.0 - p))
    design = np.column_stack([logits, np.ones_like(logits)])
    coefficients = _fit_logistic(design, y, [(0.0, None), (None, None)])
    if coefficients is None:
        return CalibrationMap(method="identity", params={})
    a, b = coefficients
    return CalibrationMap(method="platt", params={"a": float(a), "b": float(b)})


def _fit_beta(p: np.ndarray, y: np.ndarray) -> CalibrationMap:
    design = np.column_stack([np.log(p), -np.log(1.0 - p), np.ones_like(p)])
    coefficients = _fit_logistic(design, y, [(0.0, None), (0.0, None), (None, None)])
    if coefficients is None:
        return CalibrationMap(method="identity", params={})
    a, b, c = coefficients
    return CalibrationMap(method="beta", params={"a": float(a), "b": float(b), "c": float(c)})


def _pav(values: Sequence[float]) -> list[float]:
    levels: list[list[float]] = []
    for value in values:
        levels.append([value, 1.0])
        while len(levels) >= 2 and levels[-2][0] / levels[-2][1] > levels[-1][0] / levels[-1][1]:
            sum2, count2 = levels.pop()
            sum1, count1 = levels.pop()
            levels.append([sum1 + sum2, count1 + count2])
    fitted: list[float] = []
    for total, count in levels:
        fitted.extend([total / count] * int(count))
    return fitted


def _fit_isotonic(p: np.ndarray, y: np.ndarray) -> CalibrationMap:
    order = np.argsort(p, kind="stable")
    xs = p[order]
    fitted = np.array(_pav(y[order].tolist()))
    unique_x = np.unique(xs)
    knot_y = [float(fitted[xs == ux].mean()) for ux in unique_x]
    return CalibrationMap(method="isotonic", params={"x": unique_x.tolist(), "y": knot_y})


def fit(method: Method, p_models: Sequence[float], outcomes: Sequence[int]) -> CalibrationMap:
    p = np.clip(np.asarray(p_models, dtype=np.float64), _EPS, 1.0 - _EPS)
    y = np.asarray(outcomes, dtype=np.float64)
    if method == "platt":
        return _fit_platt(p, y)
    if method == "beta":
        return _fit_beta(p, y)
    if method == "isotonic":
        return _fit_isotonic(p, y)
    return CalibrationMap(method="identity", params={})


def select_method(n: int, configured: RecalibrationMethod, min_n: int) -> Method:
    if n < min_n:
        return "identity"
    if n >= ISOTONIC_MIN_N:
        return "isotonic"
    # isotonic needs n >= ISOTONIC_MIN_N; configured isotonic in [min_n, ISOTONIC_MIN_N) falls back
    # to platt (parametric) — an explicit, tested substitution, not a silent one (F-13).
    return configured if configured in ("platt", "beta") else "platt"


def recalibrate(
    samples: Sequence[Sample],
    asof: datetime,
    *,
    method: RecalibrationMethod,
    min_n: int,
) -> CalibrationMap:
    eligible = [(p, o) for p, o, resolved_at in samples if resolved_at < asof]
    chosen = select_method(len(eligible), method, min_n)
    if chosen == "identity":
        return CalibrationMap(method="identity", params={})
    return fit(chosen, [p for p, _ in eligible], [o for _, o in eligible])


def compare_brier(
    eval_samples: Sequence[tuple[float, int]], cmap: CalibrationMap
) -> RecalibrationReport:
    forecasts = [p for p, _ in eval_samples]
    outcomes = [o for _, o in eval_samples]
    return RecalibrationReport(
        brier_raw=brier_score(forecasts, outcomes),
        brier_recalibrated=brier_score([cmap.apply(p) for p in forecasts], outcomes),
        n=len(eval_samples),
    )
