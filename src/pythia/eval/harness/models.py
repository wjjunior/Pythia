"""Value objects for the evaluation harness (§5.2). Frozen, framework-free."""

from dataclasses import dataclass

from pythia.common import EstimationContext
from pythia.eval.models import CategoryCalibration


@dataclass(frozen=True, slots=True)
class EvalCase:
    market_id: str
    category: str
    context: EstimationContext
    p_market: float
    outcome: int


@dataclass(frozen=True, slots=True)
class CaseResult:
    p_model: float
    cost_usd: float
    latency_ms: float
    errored: bool = False


@dataclass(frozen=True, slots=True)
class ConfigReport:
    name: str
    brier_system: float
    brier_market: float
    brier_delta: float
    brier_per_dollar: float
    mean_latency_ms: float
    error_rate: float
    total_cost_usd: float
    n: int
    by_category: list[CategoryCalibration]


@dataclass(frozen=True, slots=True)
class HarnessReport:
    configs: list[ConfigReport]


@dataclass(frozen=True, slots=True)
class Ablation:
    base: ConfigReport
    variant: ConfigReport
    brier_per_dollar_delta: float
    brier_delta_change: float
