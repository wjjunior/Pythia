"""Evaluation harness (§5.2): replay frozen markets through configs, rank by Brier-per-dollar.

Aggregates injected, measured per-case results into metrics; the dataset is frozen point-in-time
cases. The decision metric is Brier-per-dollar — a config's calibration improvement over the market
baseline per dollar spent, where higher is better. A config that reports **zero measured cost**
(a missing ``usage.cost``) would score ``+inf`` and top the ranking, so it is **warned**, not
silently ranked first.
"""

from collections.abc import Callable
from dataclasses import dataclass

from pythia.common.logging import get_logger
from pythia.eval.functions import brier_score, calibration_by_category
from pythia.eval.harness.models import (
    Ablation,
    CaseResult,
    ConfigReport,
    EvalCase,
    HarnessReport,
)

_log = get_logger(__name__)

Runner = Callable[[EvalCase], CaseResult]


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    name: str
    runner: Runner


def brier_per_dollar(brier_delta: float, total_cost_usd: float) -> float:
    if total_cost_usd > 0.0:
        return brier_delta / total_cost_usd
    if brier_delta > 0.0:
        return float("inf")
    if brier_delta < 0.0:
        return float("-inf")
    return 0.0


def config_report(name: str, dataset: list[EvalCase], results: list[CaseResult]) -> ConfigReport:
    valid = [
        (case, result) for case, result in zip(dataset, results, strict=True) if not result.errored
    ]
    forecasts = [result.p_model for _, result in valid]
    markets = [case.p_market for case, _ in valid]
    outcomes = [case.outcome for case, _ in valid]
    brier_system = brier_score(forecasts, outcomes) if forecasts else 0.0
    brier_market = brier_score(markets, outcomes) if markets else 0.0
    brier_delta = brier_market - brier_system
    total_cost = sum(result.cost_usd for result in results)
    if valid and total_cost <= 0.0:
        # Successful estimations but no measured cost ⇒ Brier-per-dollar is +inf and would top the
        # ranking. Surface it (R-11) rather than letting an unmeasured config silently win.
        _log.warning("harness_zero_cost", config=name, n=len(valid))

    records: dict[str, list[tuple[float, float, int]]] = {}
    for case, result in valid:
        records.setdefault(case.category, []).append((result.p_model, case.p_market, case.outcome))

    return ConfigReport(
        name=name,
        brier_system=brier_system,
        brier_market=brier_market,
        brier_delta=brier_delta,
        brier_per_dollar=brier_per_dollar(brier_delta, total_cost),
        mean_latency_ms=sum(r.latency_ms for r in results) / len(results) if results else 0.0,
        error_rate=sum(1 for r in results if r.errored) / len(results) if results else 0.0,
        total_cost_usd=total_cost,
        n=len(valid),
        by_category=calibration_by_category(records),
    )


def run_harness(dataset: list[EvalCase], configs: list[HarnessConfig]) -> HarnessReport:
    reports = [
        config_report(config.name, dataset, [config.runner(case) for case in dataset])
        for config in configs
    ]
    reports.sort(key=lambda report: report.brier_per_dollar, reverse=True)
    return HarnessReport(configs=reports)


def ablate(dataset: list[EvalCase], base: HarnessConfig, variant: HarnessConfig) -> Ablation:
    report = run_harness(dataset, [base, variant])
    by_name = {config.name: config for config in report.configs}
    base_report = by_name[base.name]
    variant_report = by_name[variant.name]
    return Ablation(
        base=base_report,
        variant=variant_report,
        brier_per_dollar_delta=variant_report.brier_per_dollar - base_report.brier_per_dollar,
        brier_delta_change=variant_report.brier_delta - base_report.brier_delta,
    )
