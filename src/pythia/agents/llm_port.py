"""The LLM gateway (§5.1, §5.3, §3.9): a deterministic stub and an OpenRouter-backed port.

LLMPort is the single choke point where model cost is MEASURED. No secrets are needed: with no
OpenRouter key, the stub runs the pipeline end-to-end.
"""

import json
import time
import uuid
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from pythia.agents.router import ModelRouter
from pythia.common import CostEvent, CriticVerdict, Estimate, EstimationContext, Evidence, LLMPort
from pythia.config import Settings

CostSink = Callable[[CostEvent], None]


class ChatTransport(Protocol):
    def complete(
        self, *, model: str, messages: list[dict[str, str]], max_price: float | None
    ) -> dict[str, Any]: ...


class HttpxChatTransport:
    """Live OpenRouter transport — the OpenAI-compatible /chat/completions endpoint."""

    def __init__(self, api_key: str, base_url: str, *, timeout: float = 30.0) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def complete(
        self, *, model: str, messages: list[dict[str, str]], max_price: float | None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model, "messages": messages, "usage": {"include": True}}
        if max_price is not None:
            body["provider"] = {"max_price": {"completion": max_price}}
        response = httpx.post(
            f"{self._base_url}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout,
        )
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


def _parse_content(response: dict[str, Any]) -> dict[str, Any]:
    content = _strip_json_fence(str(response["choices"][0]["message"]["content"]))
    parsed: dict[str, Any] = json.loads(content)
    return parsed


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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._router = router
        self._cost_sink = cost_sink
        self._clock = clock

    def _complete(
        self, agent: str, messages: list[dict[str, str]]
    ) -> tuple[dict[str, Any], str, int]:
        last: Exception | None = None
        for model in self._router.candidates(agent):
            start = self._clock()
            try:
                response = self._transport.complete(
                    model=model, messages=messages, max_price=self._router.max_price
                )
            except Exception as exc:  # failover to the next candidate model
                last = exc
                continue
            return response, model, int((self._clock() - start) * 1000)
        assert last is not None
        raise last

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
            )
        )

    def estimate(self, ctx: EstimationContext) -> Estimate:
        response, model, latency_ms = self._complete("estimation", _estimation_messages(ctx))
        self._emit_cost("estimation", "estimate", model, response, latency_ms)
        payload = _parse_content(response)
        return Estimate(
            id=_deterministic_id("estimate", ctx.snapshot.id, model),
            market_id=ctx.market.id,
            snapshot_id=ctx.snapshot.id,
            p_model=float(payload["p_model"]),
            confidence=float(payload.get("confidence", 0.5)),
            lower=float(payload["lower"]),
            upper=float(payload["upper"]),
            rationale=str(payload["rationale"]),
            evidence_ids=[evidence.id for evidence in ctx.evidence],
            model_name=model,
        )

    def critique(self, est: Estimate, evidence: list[Evidence]) -> CriticVerdict:
        response, model, latency_ms = self._complete("critic", _critique_messages(est, evidence))
        self._emit_cost("critic", "critique", model, response, latency_ms)
        payload = _parse_content(response)
        return CriticVerdict(
            adjusted_confidence=float(payload.get("adjusted_confidence", est.confidence)),
            objections=[str(objection) for objection in payload.get("objections", [])],
            lookahead_leak=bool(payload.get("lookahead_leak", False)),
            notes=payload.get("notes"),
        )


def make_llm_port(settings: Settings, *, cost_sink: CostSink | None = None) -> LLMPort:
    if settings.openrouter_api_key:
        transport = HttpxChatTransport(settings.openrouter_api_key, settings.openrouter_base_url)
        router = ModelRouter(
            strategy=settings.model_routing_strategy, max_price=settings.max_price_per_call
        )
        return OpenRouterLLMPort(transport, router, cost_sink=cost_sink)
    return StubLLMPort()
