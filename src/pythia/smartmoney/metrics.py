"""Pure trader-selection math (v2 §4.2). No clock, no I/O.

Asymmetric on purpose: **Sortino** (downside-deviation only) + **max drawdown** drive the risk
adjustment — symmetric Sharpe would *reward* the convergence-scalper profile (many tiny wins, a rare
catastrophic loss). Two explicit guards hard-cap the wallet to ``-inf`` regardless of its ratio: a
strongly negative **return-skew**, and a high **entry-price concentration** at the tails (the
scalper buys near 0/1 to harvest convergence). All return stats are on **per-resolution** returns.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import stats

# What counts as an "extreme" entry price (the convergence-scalper's habitat) — domain math.
_EXTREME_PRICE_LO = 0.10
_EXTREME_PRICE_HI = 0.90


@dataclass(frozen=True, slots=True)
class SelectionPolicy:
    """Tunable thresholds/weights for the score (from config); kept out of the pure math itself."""

    min_resolved: int
    skew_hard_cap: float  # return-skew below this ⇒ never promotable (scalper tell, §4.2/AC-S5)
    extreme_price_share_cap: float  # extreme-price entry share above this ⇒ never promotable
    w_edge: float
    w_agree: float
    w_cov: float
    market_class_allow: frozenset[str]
    market_class_min_share: float


def extreme_price_share(entry_prices: Sequence[float]) -> float:
    """Fraction of entries at extreme prices (<= LO or >= HI) — the convergence-scalper tell; 0 if
    empty. A wallet that only buys near-certain outcomes harvests convergence, not forecasting edge.
    """
    if not entry_prices:
        return 0.0
    extreme = sum(1 for p in entry_prices if p <= _EXTREME_PRICE_LO or p >= _EXTREME_PRICE_HI)
    return extreme / len(entry_prices)


def sortino(returns: Sequence[float], *, target: float = 0.0) -> float:
    """Mean excess return per unit of downside deviation; ``inf`` when there is no downside."""
    if not returns:
        raise ValueError("returns must be non-empty")
    excess = np.asarray(returns, dtype=float) - target
    mean_excess = float(excess.mean())
    downside = excess[excess < 0.0]
    if downside.size == 0:
        return float("inf") if mean_excess > 0.0 else 0.0
    downside_dev = float(np.sqrt(np.mean(np.square(downside))))
    if downside_dev == 0.0:
        return float("inf") if mean_excess > 0.0 else 0.0
    return mean_excess / downside_dev


def max_drawdown(returns: Sequence[float]) -> float:
    """Largest peak-to-trough drop of the cumulative (additive) return curve; non-negative."""
    if not returns:
        raise ValueError("returns must be non-empty")
    equity = np.cumsum(np.asarray(returns, dtype=float))
    running_peak = np.maximum.accumulate(equity)
    return float(np.max(running_peak - equity))


def skewness(returns: Sequence[float]) -> float:
    """Return skewness; ~0 for symmetric, strongly negative for the scalper profile. 0 if n < 3."""
    if len(returns) < 3:
        return 0.0
    return float(stats.skew(np.asarray(returns, dtype=float)))


def risk_adj_edge(returns: Sequence[float]) -> float:
    """Sortino, penalized by drawdown — the realized risk-adjusted edge term of the score."""
    return sortino(returns) / (1.0 + max_drawdown(returns))


def market_class_ok(categories: Sequence[str], policy: SelectionPolicy) -> bool:
    """A wallet's category mix must skew to research-amenable classes (§3.4)."""
    if not categories:
        return False
    allowed = sum(1 for category in categories if category in policy.market_class_allow)
    return allowed / len(categories) >= policy.market_class_min_share


def trader_score(
    *,
    risk_adj_edge: float,
    agreed_bpd: float,
    coverage: float,
    n_resolved: int,
    market_class_ok: bool,
    skew: float,
    extreme_price_share: float,
    policy: SelectionPolicy,
) -> float:
    """Weighted blend of risk-adjusted edge, pipeline-agreement and coverage, with hard floors: a
    wallet failing the market-class filter, with ``n < min_resolved``, strongly negative skew, or
    too high an extreme-price entry concentration is **never promotable** (``-inf``)."""
    if (
        not market_class_ok
        or n_resolved < policy.min_resolved
        or skew < policy.skew_hard_cap
        or extreme_price_share > policy.extreme_price_share_cap
    ):
        return float("-inf")
    return policy.w_edge * risk_adj_edge + policy.w_agree * agreed_bpd + policy.w_cov * coverage
