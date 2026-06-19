"""Live runner for the harness (§5.2): wrap an LLMPort, measure cost and latency per case.

The impure side of the harness — the only thing that calls the model under test. The dataset stays
frozen; an estimation failure is caught and counted as an error (the case falls back to the market
price), so one bad config can't abort the run. The clock enters as a parameter.
"""

import time
from collections.abc import Callable

from pythia.common import CostEvent, LLMPort
from pythia.eval.harness.harness import Runner
from pythia.eval.harness.models import CaseResult, EvalCase

CostSink = Callable[[CostEvent], None]


def llm_runner(
    build_llm: Callable[[CostSink], LLMPort],
    *,
    clock: Callable[[], float] = time.monotonic,
) -> Runner:
    def run(case: EvalCase) -> CaseResult:
        costs: list[float] = []
        llm = build_llm(lambda event: costs.append(event.cost_usd))
        start = clock()
        try:
            estimate = llm.estimate(case.context)
            llm.critique(estimate, case.context.evidence)
            p_model = estimate.p_model
            errored = False
        except Exception:
            p_model = case.p_market
            errored = True
        latency_ms = (clock() - start) * 1000.0
        return CaseResult(
            p_model=p_model, cost_usd=sum(costs), latency_ms=latency_ms, errored=errored
        )

    return run
