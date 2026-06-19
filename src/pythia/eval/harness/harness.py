"""Evaluation harness (§5.2): replay frozen markets through configs, rank by Brier-per-dollar.

Pure: the dataset is frozen point-in-time cases and each config's runner is injected, so this module
only aggregates measured results into metrics. The decision metric is Brier-per-dollar — a config's
calibration improvement over the market baseline per dollar spent, where higher is better.
"""

from collections.abc import Callable
from dataclasses import dataclass

from pythia.eval.functions import brier_score, calibration_by_category
from pythia.eval.harness.models import (
    Ablation,
    CaseResult,
    ConfigReport,
    EvalCase,
    HarnessReport,
)

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
