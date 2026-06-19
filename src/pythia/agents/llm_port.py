"""The LLM gateway (§5.1, §5.3, §3.9): a deterministic stub and an OpenRouter-backed port.

LLMPort is the single choke point where model cost is MEASURED. No secrets are needed: with no
OpenRouter key, the stub runs the pipeline end-to-end.
"""

import time
import uuid
from collections.abc import Callable
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from pythia.agents.router import CriticDiversityError, ModelRouter
from pythia.common import CostEvent, CriticVerdict, Estimate, EstimationContext, Evidence, LLMPort
from pythia.config import Settings

CostSink = Callable[[CostEvent], None]


class LLMResponseError(RuntimeError):
    """The LLM returned a response that could not be parsed into the expected schema."""


class _EstimationPayload(BaseModel):
    p_model: float
    confidence: float = 0.5
    lower: float
    upper: float
    rationale: str


class _CritiquePayload(BaseModel):
    adjusted_confidence: float | None = None
    objections: list[str] = Field(default_factory=list)
    lookahead_leak: bool = False
    notes: str | None = None


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
        body: dict[str, Any] = {"model": model, "messages": messages, "usage": {"include": True}}
        if max_price is not None:
            body["provider"] = {"max_price": {"completion": max_price}}
        response = self._client.post("/chat/completions", json=body)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data


def _deterministic_id(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(parts)))


class StubLLMPort:
    """Deterministic, zero-secret estimator/critic: mirrors the market with low confidence."""

    def estimate(self, ctx: EstimationContext) -> Estimate:
        q = ctx.snapshot.yes_price
        return Estimate(
            id=_deterministic_id("estimate", ctx.snapshot.id),
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


def _parse_payload[T: BaseModel](response: dict[str, Any], schema: type[T]) -> T:
    content = _message_content(response)
    try:
        return schema.model_validate_json(content)
    except ValidationError as exc:
        raise LLMResponseError(f"invalid {schema.__name__} from LLM: {exc}") from exc


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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._router = router
        self._cost_sink = cost_sink
        self._budget_check = budget_check
        self._correlation_id = correlation_id
        self._clock = clock

    def _complete(
        self,
        agent: str,
        messages: list[dict[str, str]],
        candidates: list[str] | None = None,
    ) -> tuple[dict[str, Any], str, int]:
        if self._budget_check is not None:
            self._budget_check()
        if candidates is None:
            candidates = self._router.candidates(agent)
        if not candidates:
            raise ValueError(f"no model candidates configured for agent {agent!r}")
        errors: list[Exception] = []
        for model in candidates:
            start = self._clock()
            try:
                response = self._transport.complete(
                    model=model, messages=messages, max_price=self._router.max_price
                )
            except Exception as exc:  # failover to the next candidate model
                errors.append(exc)
                continue
            return response, model, int((self._clock() - start) * 1000)
        raise errors[-1]

    def _emit_cost(
        self, agent: str, task: str, model: str, response: dict[str, Any], latency_ms: int
    ) -> None:
        if self._cost_sink is None:
            return
        usage = response.get("usage", {})
        details = usage.get("prompt_tokens_details", {}) or {}
        self._cost_sink(
            CostEvent(
                agent=agent,
                task=task,
                provider=str(response.get("provider", "openrouter")),
                model=model,
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
                cached_tokens=int(details.get("cached_tokens", 0)),
                cost_usd=float(usage.get("cost", 0.0)),
                latency_ms=latency_ms,
                correlation_id=self._correlation_id,
            )
        )

    def estimate(self, ctx: EstimationContext) -> Estimate:
        response, model, latency_ms = self._complete("estimation", _estimation_messages(ctx))
        self._emit_cost("estimation", "estimate", model, response, latency_ms)
        payload = _parse_payload(response, _EstimationPayload)
        return Estimate(
            id=_deterministic_id("estimate", ctx.snapshot.id, model),
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

    def critique(self, est: Estimate, evidence: list[Evidence]) -> CriticVerdict:
        candidates = self._router.diverse_candidates("critic", exclude_family_of=est.model_name)
        if not candidates:
            raise CriticDiversityError(est.model_name)
        response, model, latency_ms = self._complete(
            "critic", _critique_messages(est, evidence), candidates
        )
        self._emit_cost("critic", "critique", model, response, latency_ms)
        payload = _parse_payload(response, _CritiquePayload)
        adjusted = payload.adjusted_confidence
        return CriticVerdict(
            adjusted_confidence=adjusted if adjusted is not None else est.confidence,
            objections=payload.objections,
            lookahead_leak=payload.lookahead_leak,
            notes=payload.notes,
        )


def make_llm_port(
    settings: Settings,
    *,
    cost_sink: CostSink | None = None,
    budget_check: Callable[[], None] | None = None,
    correlation_id: str | None = None,
) -> LLMPort:
    if settings.openrouter_api_key:
        transport = HttpxChatTransport(settings.openrouter_api_key, settings.openrouter_base_url)
        router = ModelRouter(
            strategy=settings.model_routing_strategy, max_price=settings.max_price_per_call
        )
        return OpenRouterLLMPort(
            transport,
            router,
            cost_sink=cost_sink,
            budget_check=budget_check,
            correlation_id=correlation_id,
        )
    return StubLLMPort()
