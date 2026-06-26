"""The LLM gateway (§5.1, §5.3, §3.9): a deterministic stub and an OpenRouter-backed port.

LLMPort is the single choke point where model cost is MEASURED. No secrets are needed: with no
OpenRouter key, the stub runs the pipeline end-to-end.
"""

import time
from collections.abc import Callable
from typing import Any, Literal, Protocol, Self

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from pythia.agents.aggregation import aggregate
from pythia.agents.cost import BudgetExceeded
from pythia.agents.router import CriticDiversityError, ModelRouter
from pythia.common import (
    CostEvent,
    CriticVerdict,
    Estimate,
    EstimationContext,
    Evidence,
    LLMPort,
    Market,
    MemberForecast,
    ResolutionProposal,
)
from pythia.common.ids import deterministic_id
from pythia.common.logging import current_correlation_id, get_logger
from pythia.config import Settings
from pythia.config.settings import AggregationMethod

CostSink = Callable[[CostEvent], None]

# Every agent call returns JSON; ask OpenRouter to force valid JSON at the source. Models that
# do not honour it fall through to the defensive parsing in _parse_payload (PYT-66/PYT-67).
_JSON_RESPONSE_FORMAT: dict[str, str] = {"type": "json_object"}

_log = get_logger(__name__)


class LLMResponseError(RuntimeError):
    """The LLM returned a response that could not be parsed into the expected schema."""


class _EstimationPayload(BaseModel):
    p_model: float
    confidence: float = 0.5
    lower: float
    upper: float
    rationale: str

    @field_validator("p_model", "confidence", "lower", "upper")
    @classmethod
    def _clamp_to_unit(cls, value: float) -> float:
        return min(1.0, max(0.0, value))


class _CritiquePayload(BaseModel):
    adjusted_confidence: float | None = None
    objections: list[str] = Field(default_factory=list)
    lookahead_leak: bool = False
    notes: str | None = None

    @field_validator("lookahead_leak", mode="before")
    @classmethod
    def _coerce_lookahead_leak(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        return isinstance(value, str) and value.strip().lower() in {"true", "yes", "1"}

    @field_validator("adjusted_confidence", mode="before")
    @classmethod
    def _coerce_adjusted_confidence(cls, value: object) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int | float):
            return float(value)
        try:
            return float(str(value).strip())
        except ValueError:
            return None

    @field_validator("objections", mode="before")
    @classmethod
    def _coerce_objections(cls, value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @field_validator("notes", mode="before")
    @classmethod
    def _coerce_notes(cls, value: object) -> str | None:
        return None if value is None else str(value)


class _DiscoveryPayload(BaseModel):
    shortlist: list[str] = Field(default_factory=list)


class _ResolutionPayload(BaseModel):
    outcome: Literal[0, 1] | None
    ambiguous: bool
    confidence: float = 0.5
    rationale: str

    @model_validator(mode="after")
    def _outcome_matches_ambiguity(self) -> Self:
        if (self.outcome is None) != self.ambiguous:
            raise ValueError("outcome must be set iff the proposal is not ambiguous")
        return self


class ChatTransport(Protocol):
    def complete(
        self, *, model: str, messages: list[dict[str, str]], max_price: float | None
    ) -> dict[str, Any]: ...


class HttpxChatTransport:
    """Live OpenRouter transport — the OpenAI-compatible /chat/completions endpoint."""

    def __init__(self, api_key: str, base_url: str, *, timeout: float = 30.0) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def complete(
        self, *, model: str, messages: list[dict[str, str]], max_price: float | None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "usage": {"include": True},
            "response_format": _JSON_RESPONSE_FORMAT,
        }
        if max_price is not None:
            body["provider"] = {"max_price": {"completion": max_price}}
        response = self._client.post("/chat/completions", json=body)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data


class StubLLMPort:
    """Deterministic, zero-secret estimator/critic: mirrors the market with low confidence."""

    def estimate(self, ctx: EstimationContext) -> Estimate:
        q = ctx.snapshot.yes_price
        return Estimate(
            id=deterministic_id("estimate", ctx.snapshot.id),
            market_id=ctx.market.id,
            snapshot_id=ctx.snapshot.id,
            p_model=q,
            confidence=0.1,
            lower=max(0.0, q - 0.1),
            upper=min(1.0, q + 0.1),
            rationale="deterministic stub — no LLM configured; mirrors the market price",
            evidence_ids=[evidence.id for evidence in ctx.evidence],
            model_name="stub",
        )

    def critique(self, est: Estimate, evidence: list[Evidence]) -> CriticVerdict:
        return CriticVerdict(
            adjusted_confidence=min(est.confidence, 0.1),
            objections=[],
            lookahead_leak=False,
            notes="deterministic stub critic",
        )

    def discover(self, markets: list[Market], limit: int) -> list[str]:
        return sorted(market.id for market in markets)[:limit]

    def grade_resolution(self, market: Market, evidence: list[Evidence]) -> ResolutionProposal:
        return ResolutionProposal(
            market_id=market.id,
            outcome=None,
            ambiguous=True,
            confidence=0.0,
            rationale="no LLM configured — cannot grade a subjective outcome",
            evidence_ids=[item.id for item in evidence],
            model_name="stub",
        )


def _strip_json_fence(content: str) -> str:
    content = content.strip()
    if content.startswith("```"):
        content = content[3:]
        if content[:4].lower() == "json":
            content = content[4:]
        content = content.rsplit("```", 1)[0]
    return content.strip()


def _message_content(response: dict[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMResponseError(f"malformed chat completion envelope: {exc}") from exc
    return _strip_json_fence(str(content))


def _extract_json_object(content: str) -> str | None:
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return content[start : end + 1]


def _parse_payload[T: BaseModel](response: dict[str, Any], schema: type[T]) -> T:
    content = _message_content(response)
    candidates = [content]
    extracted = _extract_json_object(content)
    if extracted is not None and extracted != content:
        candidates.append(extracted)
    last_error: ValidationError | None = None
    for candidate in candidates:
        try:
            return schema.model_validate_json(candidate)
        except ValidationError as exc:
            last_error = exc
    raise LLMResponseError(f"invalid {schema.__name__} from LLM: {last_error}") from last_error


def _estimation_messages(ctx: EstimationContext) -> list[dict[str, str]]:
    evidence = "\n".join(f"- {item.content}" for item in ctx.evidence)
    user = (
        f"Market: {ctx.market.question}\n"
        f"Resolution rule: {ctx.market.resolution_rule}\n"
        f"Market-implied probability (q): {ctx.snapshot.yes_price}\n"
        f"Evidence (as of {ctx.snapshot.ts.isoformat()}):\n{evidence}\n\n"
        'Return JSON: {"p_model", "confidence", "lower", "upper", "rationale"}.'
    )
    return [
        {"role": "system", "content": "You are a calibrated prediction-market forecaster."},
        {"role": "user", "content": user},
    ]


def _critique_messages(est: Estimate, evidence: list[Evidence]) -> list[dict[str, str]]:
    evidence_text = "\n".join(f"- {item.content}" for item in evidence)
    user = (
        f"Estimate p_model={est.p_model}, confidence={est.confidence}.\n"
        f"Rationale: {est.rationale}\nEvidence:\n{evidence_text}\n\n"
        'Attack it. Return JSON: {"adjusted_confidence", "objections", "lookahead_leak", "notes"}.'
    )
    return [
        {
            "role": "system",
            "content": "You are an adversarial critic hunting lookahead and weak sources.",
        },
        {"role": "user", "content": user},
    ]


def _discovery_messages(markets: list[Market], limit: int) -> list[dict[str, str]]:
    catalogue = "\n".join(
        f"- {market.id}: {market.question} [{market.category}]" for market in markets
    )
    user = (
        f"Open markets:\n{catalogue}\n\n"
        f"Select up to {limit} market ids most worth researching now, most promising first. "
        'Return JSON: {"shortlist": ["<market id>", ...]}.'
    )
    return [
        {"role": "system", "content": "You shortlist prediction markets worth researching."},
        {"role": "user", "content": user},
    ]


def _resolution_messages(market: Market, evidence: list[Evidence]) -> list[dict[str, str]]:
    evidence_text = "\n".join(f"- {item.content}" for item in evidence)
    user = (
        f"Market: {market.question}\n"
        f"Resolution rule: {market.resolution_rule}\n"
        f"Evidence (post-resolution reporting permitted):\n{evidence_text}\n\n"
        "Grade the actual outcome. If it is genuinely ambiguous or resolved invalid (50/50), "
        'set ambiguous true. Return JSON: {"outcome": 0|1|null, "ambiguous", "confidence", '
        '"rationale"}.'
    )
    return [
        {"role": "system", "content": "You grade resolved prediction markets from evidence."},
        {"role": "user", "content": user},
    ]


class OpenRouterLLMPort:
    """LLMPort backed by OpenRouter, with per-agent routing, failover and measured cost."""

    def __init__(
        self,
        transport: ChatTransport,
        router: ModelRouter,
        *,
        cost_sink: CostSink | None = None,
        budget_check: Callable[[], None] | None = None,
        correlation_id: str | None = None,
        ensemble_size: int = 1,
        aggregation_method: AggregationMethod = "median",
        escalate_threshold: float = 0.5,
        model_max_attempts: int = 2,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._router = router
        self._cost_sink = cost_sink
        self._budget_check = budget_check
        self._correlation_id = correlation_id
        self._ensemble_size = ensemble_size
        self._aggregation_method = aggregation_method
        self._escalate_threshold = escalate_threshold
        self._model_max_attempts = model_max_attempts
        self._clock = clock

    def _complete_and_parse[T: BaseModel](
        self,
        agent: str,
        task: str,
        messages: list[dict[str, str]],
        candidates: list[str] | None,
        schema: type[T],
    ) -> tuple[T, str]:
        # The slate is a fallback chain (§5.1): try each model and parse its response. A transport
        # error fails over to the next model (don't hammer a down provider); unparseable JSON is
        # retried on the same model up to model_max_attempts before moving on.
        if candidates is None:
            candidates = self._router.candidates(agent, task)
        if not candidates:
            raise ValueError(f"no model candidates configured for agent {agent!r}")
        attempts = max(1, self._model_max_attempts)
        last_error: Exception | None = None
        for model in candidates:
            for _attempt in range(attempts):
                try:
                    response, latency_ms = self._complete_one(model, messages)
                except BudgetExceeded:
                    raise
                except Exception as exc:  # transport failure: fail over to the next model
                    last_error = exc
                    break
                self._record_call(agent, task, model, response, latency_ms)
                try:
                    return _parse_payload(response, schema), model
                except LLMResponseError as exc:  # bad JSON: retry this model, then the next
                    last_error = exc
                    _log.warning("llm_unparseable", agent=agent, model=model, error=str(exc))
        raise LLMResponseError(
            f"{schema.__name__} failed across {len(candidates)} model(s): {last_error}"
        )

    def _complete_one(
        self, model: str, messages: list[dict[str, str]]
    ) -> tuple[dict[str, Any], int]:
        if self._budget_check is not None:
            self._budget_check()
        start = self._clock()
        response = self._transport.complete(
            model=model, messages=messages, max_price=self._router.max_price
        )
        return response, int((self._clock() - start) * 1000)

    def _record_call(
        self, agent: str, task: str, model: str, response: dict[str, Any], latency_ms: int
    ) -> None:
        usage = response.get("usage", {})
        details = usage.get("prompt_tokens_details", {}) or {}
        provider = response.get("provider")
        if provider is None:
            _log.warning("llm_provider_missing", model=model)
            provider = "openrouter"
        provider = str(provider)
        input_tokens = int(usage.get("prompt_tokens", 0))
        output_tokens = int(usage.get("completion_tokens", 0))
        cached_tokens = int(details.get("cached_tokens", 0))
        if usage.get("cost") is None:  # mirror llm_provider_missing — don't measure 0 silently
            _log.warning("llm_cost_missing", model=model)
        cost_usd = float(usage.get("cost", 0.0))
        _log.info(
            "llm_call",
            agent=agent,
            task=task,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )
        if self._cost_sink is None:
            return
        self._cost_sink(
            CostEvent(
                agent=agent,
                task=task,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                correlation_id=current_correlation_id() or self._correlation_id,
            )
        )

    def estimate(self, ctx: EstimationContext) -> Estimate:
        if self._router.strategy == "ensemble":
            return self._estimate_ensemble(ctx)
        if self._router.strategy == "escalate":
            return self._estimate_escalate(ctx)
        return self._run_estimation(ctx)

    def _estimate_ensemble(self, ctx: EstimationContext) -> Estimate:
        models = self._router.ensemble_candidates("estimation", self._ensemble_size)
        if len(models) <= 1:
            return self._run_estimation(ctx)
        return self._aggregate_members(ctx, models)

    def _estimate_escalate(self, ctx: EstimationContext) -> Estimate:
        # Cheap model first; promote to the next slate candidate only while the estimate is
        # under-confident (§5.1). The wider "sources disagree / size high" triggers need an
        # estimate↔score feedback loop and are deferred (PYT-61).
        models = self._router.candidates("estimation")
        if not models:
            return self._run_estimation(ctx)
        estimate = self._run_estimation(ctx, [models[0]])
        for model in models[1:]:
            if estimate.confidence >= self._escalate_threshold:
                return estimate
            estimate = self._run_estimation(ctx, [model])
        return estimate

    def _run_estimation(
        self, ctx: EstimationContext, candidates: list[str] | None = None
    ) -> Estimate:
        payload, model = self._complete_and_parse(
            "estimation", "estimate", _estimation_messages(ctx), candidates, _EstimationPayload
        )
        return Estimate(
            id=deterministic_id("estimate", ctx.snapshot.id, model),
            market_id=ctx.market.id,
            snapshot_id=ctx.snapshot.id,
            p_model=payload.p_model,
            confidence=payload.confidence,
            lower=payload.lower,
            upper=payload.upper,
            rationale=payload.rationale,
            evidence_ids=[evidence.id for evidence in ctx.evidence],
            model_name=model,
        )

    def _aggregate_members(self, ctx: EstimationContext, models: list[str]) -> Estimate:
        estimates: list[Estimate] = []
        for model in models:
            try:
                estimates.append(self._run_estimation(ctx, [model]))
            except BudgetExceeded:
                raise
            except Exception as exc:  # a member provider failed; aggregate the survivors
                _log.warning("ensemble_member_dropped", model=model, error=str(exc))
                continue
        if not estimates:
            return self._run_estimation(ctx)
        forecast = aggregate(estimates, self._aggregation_method)
        members = [
            MemberForecast(model=member.model_name, p_member=member.p_model) for member in estimates
        ]
        return Estimate(
            id=deterministic_id("estimate", ctx.snapshot.id, "ensemble"),
            market_id=ctx.market.id,
            snapshot_id=ctx.snapshot.id,
            p_model=forecast.p_model,
            confidence=forecast.confidence,
            lower=forecast.lower,
            upper=forecast.upper,
            rationale=f"ensemble {self._aggregation_method} of {len(members)} members",
            evidence_ids=[evidence.id for evidence in ctx.evidence],
            model_name="ensemble",
            members=members,
        )

    def critique(self, est: Estimate, evidence: list[Evidence]) -> CriticVerdict:
        candidates = self._router.diverse_candidates("critic", exclude_family_of=est.model_name)
        if not candidates:
            raise CriticDiversityError(est.model_name)
        payload, _model = self._complete_and_parse(
            "critic", "critique", _critique_messages(est, evidence), candidates, _CritiquePayload
        )
        adjusted = payload.adjusted_confidence
        return CriticVerdict(
            adjusted_confidence=adjusted if adjusted is not None else est.confidence,
            objections=payload.objections,
            lookahead_leak=payload.lookahead_leak,
            notes=payload.notes,
        )

    def discover(self, markets: list[Market], limit: int) -> list[str]:
        if not markets:
            return []
        payload, _model = self._complete_and_parse(
            "discovery", "discover", _discovery_messages(markets, limit), None, _DiscoveryPayload
        )
        known = {market.id for market in markets}
        seen: set[str] = set()
        shortlist: list[str] = []
        for market_id in payload.shortlist:
            if market_id in known and market_id not in seen:
                shortlist.append(market_id)
                seen.add(market_id)
        return shortlist[:limit]

    def grade_resolution(self, market: Market, evidence: list[Evidence]) -> ResolutionProposal:
        payload, model = self._complete_and_parse(
            "resolution", "grade", _resolution_messages(market, evidence), None, _ResolutionPayload
        )
        return ResolutionProposal(
            market_id=market.id,
            outcome=payload.outcome,
            ambiguous=payload.ambiguous,
            confidence=payload.confidence,
            rationale=payload.rationale,
            evidence_ids=[item.id for item in evidence],
            model_name=model,
        )


def make_llm_port(
    settings: Settings,
    *,
    cost_sink: CostSink | None = None,
    budget_check: Callable[[], None] | None = None,
    correlation_id: str | None = None,
) -> LLMPort:
    if settings.openrouter_api_key:
        transport = HttpxChatTransport(
            settings.openrouter_api_key.get_secret_value(), settings.openrouter_base_url
        )
        router = ModelRouter(
            strategy=settings.model_routing_strategy, max_price=settings.max_price_per_call
        )
        return OpenRouterLLMPort(
            transport,
            router,
            cost_sink=cost_sink,
            budget_check=budget_check,
            correlation_id=correlation_id,
            ensemble_size=settings.ensemble_size,
            aggregation_method=settings.aggregation_method,
            escalate_threshold=settings.min_confidence,
            model_max_attempts=settings.model_max_attempts,
        )
    return StubLLMPort()
