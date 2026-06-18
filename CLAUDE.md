# Pythia — Claude Code Guide

A calibration-first research and paper-trading engine for Polymarket prediction markets, with auditable AI probability estimation and a deterministic risk layer.

For the complete system design (architecture, decisions, rationale, acceptance criteria), consult the Notion page **System Design — Pythia**. It is the source of truth. This file covers the day-to-day conventions you need at hand while coding.

The single question the system exists to answer, before risking any capital: **is this system more calibrated than the market in some category?** Brier(system) vs Brier(market) is the headline metric. PnL is a secondary, noisy readout. Measure honesty before chasing profit, keep the critical path deterministic, and make every decision reproducible from stored evidence.

## Stack

**Core:** Python 3.12+, typed throughout, project managed by **`uv`**. FastAPI (REST + SSE). Pydantic AI for agents (model-agnostic). Pydantic v2 + pydantic-settings for types and config.

**Data:** PostgreSQL 18 + **pgvector** as the single stateful store — relational system of record, vector evidence index, and append-only audit ledger in one. SQLModel models, Alembic migrations. Redis is an optional, profile-gated add-on for the scheduler/worker only when an in-process loop no longer suffices.

**AI access:** OpenRouter (one OpenAI-compatible endpoint, 400+ models) behind `LLMPort`. A deterministic stub is the zero-config default so the whole pipeline runs with **no secrets**.

**Scheduling:** APScheduler in-process in v1; Celery + Redis only when scale demands it.

**Observability:** `structlog` (JSON, correlation-ID propagation). Logfire/Langfuse for LLM cost/latency traces. Measured cost (not estimated) captured per call into `cost_events`.

**Quality:** ruff (lint + format), mypy (strict), pytest, **Hypothesis** (property tests for invariants), **testcontainers** (integration against real Postgres+pgvector). GitHub Actions runs ruff + mypy + pytest on every PR; pre-commit mirrors it locally.

**Frontend:** React 19, Vite, TypeScript, Tailwind, shadcn/ui, TanStack Query. Native `EventSource` for SSE. TS types are **generated from the FastAPI OpenAPI schema** — the contract is single-sourced.

**Math:** numpy / scipy (Brier, reliability curves, Monte Carlo growth simulation, recalibration maps).

Versions are pinned in `pyproject.toml`; `uv sync` is the only install path. The Python version is pinned via `.python-version`.

## Repository structure

Target layout — some entries (`docker-compose.yml`, `.env.example`, the React app) arrive in later phases.

```
Pythia/
├── src/pythia/
│   ├── config/         # pydantic-settings — the ONLY place that reads env vars
│   ├── common/         # shared pure types, ports (Protocols), errors, utilities
│   ├── scoring/        # PURE: edge, net_edge, EV, opportunity_score, kelly, sizing
│   ├── risk/           # PURE: caps, gating, correlated-exposure veto
│   ├── eval/           # PURE: brier, calibration bins, reliability + harness/ + simulation/
│   ├── portfolio/      # PURE: available_capital, exposure, capital-aware sizing
│   ├── ingestion/      # market-crawler, evidence-ingest — dumb pipes with a clock
│   ├── rag/            # chunk · embed (pgvector) · point-in-time retrieve(asof)
│   ├── agents/         # Pydantic AI: estimation, critic (+ discovery, resolution)
│   ├── orchestration/  # plain-python pipeline → LangGraph only when justified
│   ├── paper/          # paper ledger: fills at observed prices → PnL on resolution
│   ├── db/             # SQLModel models, Alembic migrations, repositories
│   ├── api/            # FastAPI REST + SSE  — entrypoint: `python -m pythia.api`
│   └── worker/         # scheduler / async research — entrypoint: `python -m pythia.worker`
├── dashboard/          # React + Vite + TS + Tailwind + shadcn/ui + TanStack Query
├── tests/              # property (Hypothesis) + integration (testcontainers)
├── docker-compose.yml  # Postgres+pgvector; Redis behind a `redis` compose profile
├── .env.example        # every Appendix A.3 var documented; no secrets committed
└── pyproject.toml      # uv-managed, deps pinned
```

One codebase, two process entrypoints (`api`, `worker`), one `uv` project. `docker compose up` brings up Postgres+pgvector (and Redis only with the profile enabled).

## Backend: hexagonal where it pays

Apply Ports & Adapters (hexagonal) only where domain reasoning lives. Insulate the deterministic core from Postgres, the LLM, and the market API. Do **not** wrap dumb pipes in ceremony.

- **Hexagonal core** — `scoring/`, `risk/`, `eval/`, `portfolio/`, and the estimation/critique contracts. Pure, no I/O, 100% unit-testable, fully property-tested.
- **Dumb pipes** — `ingestion/` (market-crawler, evidence-ingest). A clock and a normalizer; no reasoning, no hexagonal layering.

### The dependency rule

- The pure core (`scoring/`, `risk/`, `eval/`, `portfolio/`, plus the pure types and ports in `common/`) depends on **nothing** — no framework, no I/O, no `db`, no `api`. Time and config enter as parameters.
- Adapters in `ingestion/`, `rag/`, `agents/`, `db/` **implement** ports defined in `common/ports/`. They depend on the core, never the reverse.
- `orchestration/` and `api/`/`worker/` are the composition roots — the only places that wire adapters to the core.

A reverse import (core importing an adapter, or `scoring/` importing `db/`) is a defect, not a style choice.

### Ports are Python Protocols

Ports live in `common/ports/` as `typing.Protocol` classes (§4.5): `MarketDataPort`, `EvidencePort`, `EmbeddingPort`, `LLMPort`, `PaperLedgerPort`, `AuditPort`. `ExecutionPort` is defined the same way **but has no implementation** — it exists to force the gas/slippage/credentials conversation at the boundary, and stays unimplemented until the calibration gate is cleared.

`EvidencePort.retrieve(market_id, asof, k)` **requires `asof`** and must never return evidence with `observed_at > asof`. This is enforced by a Hypothesis property test, not by convention.

## Frontend: dashboard

React + Vite + TS, Tailwind v4, shadcn/ui, TanStack Query. Six views: opportunity radar (ranked by `opportunity_score`), market detail (cited, dated evidence), paper portfolio + capital state, calibration (Brier vs market + `n`), cost panel, growth-projection panel.

- **Types are generated from the FastAPI OpenAPI schema.** A breaking Pydantic change fails the frontend build. Never hand-write a type that crosses the API boundary.
- Components render. TanStack Query orchestrates server state. Business logic (severity, ranking, derived state) lives in pure functions, not in components.
- SSE (`EventSource`) drives live updates within ~1s; a wide-interval refetch is the fallback. SSE payloads are advisory — the REST snapshot is the source of truth.
- shadcn/ui primitives carry accessibility via Radix; do not override `aria-*`/`role` without a documented reason.

## Shared contracts

The canonical types are Pydantic models in `common/` (§4.1): `Side` (YES/NO enum), `Market`, `MarketSnapshot`, `Source`, `Evidence`, `Estimate`, `Score`, `PaperTrade`, `EstimationContext`, `CriticVerdict`, `AuditEvent`. These are the contract that crosses every boundary — agent output, DB row, API response.

- The Pydantic model **is** the domain type when the shape fits. Only introduce a separate domain model when the stored/external shape genuinely disagrees with how the domain wants to work. Don't write mapper boilerplate for shapes that are already correct.
- The same models drive the OpenAPI schema, which generates the frontend types. One change propagates everywhere; a contract break surfaces at build time.
- If a layer needs a different internal shape, transform at the boundary. Never bend a contract model for internal convenience.

---

## Coding rules

Non-negotiable conventions. Apply them from the first commit. mypy strict and ruff enforce many of these in CI.

### 1. No bare `Any` without justification

`Any` is forbidden unless accompanied by a comment explaining why no better type works. Prefer `object` or a `Protocol` for genuinely open input, narrowed via a Pydantic parse or `isinstance` guard. mypy runs in strict mode; untyped defs fail the build.

### 2. Named constants for domain-meaningful numbers

Numbers carrying domain meaning, or appearing more than once, become named constants. The default fractional-Kelly multiplier `0.25` becomes `KELLY_MULT_DEFAULT`. The crawl cadence becomes `CRAWL_INTERVAL_SECONDS` (from config). Trivial one-off numbers in tests stay inline. The rule is about communication, not constant fetishism.

### 3. No hardcoded strings for status, side, or kind

Lifecycle states (`discovered → … → resolved`), `Side`, resolution-source types (`chainlink` / `clean_binary` / `subjective_uma`), routing strategies — all come from `Enum` or `Literal`, never bare string comparisons. `if status == "scored"` is forbidden; compare the typed member. The lifecycle vocabulary (§A.4) is a state machine, not a set of magic strings.

### 4. Config and constants live near their domain

No `common/constants.py` dumping ground. Risk caps live in `risk/`, scoring thresholds in `scoring/`. Runtime configuration is read once, in `config/`, via pydantic-settings — every other module imports the typed settings object. Thresholds that are domain math (not operator-tunable) are module-level constants beside the code that uses them.

### 5. Validate at boundaries; trust types inside

Every external input is parsed by Pydantic before becoming a typed value: HTTP bodies, market-API JSON, LLM responses, env vars, DB rows that arrive as `dict`/`Row`. Inside the core, trust the type — boundary validation is the only validation needed. A `cast()` is a code smell: if you reach for one, ask whether the boundary should run a Pydantic parse instead.

### 6. Explicit signatures on public APIs

Functions exported from a module have explicit parameter and return types. Internal helpers can rely on inference when obvious. mypy strict requires this at boundaries anyway — make the contract legible.

### 7. One function does one thing

A function does one thing, and its name says exactly what. If the name needs "and" or "then", split it. `score_and_persist` becomes `score` + `persist`, with the orchestration in the caller (`orchestration/`).

### 8. No business logic in React components

Components render. Hooks and TanStack Query orchestrate. Logic — ranking, severity, capital math — lives in pure TS functions or comes pre-computed from the API. A component computing a Brier delta or an exposure fraction is a refactor signal.

### 9. Early return over nested conditions

Guard clauses at the top, happy path at the bottom. A vetoed or gated opportunity returns early. Nesting beyond two levels is a refactor signal.

### 10. Test signal, not coverage

Cover **behaviors and invariants**, not lines. The §4.6 invariants are the spec, and they are property tests, not example tests:

- **Score bounds & confidence-as-AND** — `confidence` is multiplicative; any factor of 0 zeroes it; result in `[0,1]`.
- **Kelly safety** — `kelly_fraction >= 0`, and `== 0` when `edge_net <= 0`; `position_size <= cap_per_trade` always.
- **Brier bounds** — in `[0,1]`; `brier(o, o) == 0`.
- **Point-in-time** — `retrieve` never returns evidence with `observed_at > asof`.
- **Risk caps** — hard caps always dominate the model's suggestion.
- **No silent bets** — `confidence == 0` or any penalty `== 0` ⇒ `size == 0`.

The pure-core suite runs in **under 5 seconds with no Docker and no env**. Integration tests (testcontainers, real Postgres+pgvector) run separately and prove immutability and idempotency. Do not re-prove a primitive in every consumer — prove it once where it lives.

### 11. `os.environ` only in the config module

No scattered `os.getenv("FOO")`. `config/` reads and validates every env var (Appendix A.3) via pydantic-settings at boot and exports a typed settings object; everywhere else imports it. The app fails fast on a missing or invalid required var.

### 12. No secrets in logs — but the operational data IS the product

API keys, the OpenRouter key, and tokens are never logged. In this system, **prices, probabilities, scores, calibration, paper PnL, and measured costs are logged on purpose** — they are the product, not sensitive data. The line is around credentials, not around the quant data itself.

### 13. Use Pydantic models as domain types when they fit

The `common/` models are canonical. The Pydantic type **is** the domain type for `Market`, `Estimate`, `Score`, etc. Only add a separate model when the stored shape genuinely disagrees with the working shape (e.g., a denormalized read). Don't manufacture mapper boilerplate.

### 14. Adapters for external integrations

Every external dependency — the Polymarket Gamma/CLOB client, OpenRouter, the embedding provider, the Postgres driver, cross-venue APIs — enters as an adapter in its module, implementing a `Protocol` from `common/ports/`. Core code never imports a client library directly. This is what keeps the core testable and the store swappable.

### 15. Code speaks for itself; comments are rare

Default to zero docstrings on trivial helpers and zero inline comments. Function names, parameter names, and types carry the spec. Reserve a comment for the narrow case where the reader could not derive the *why* (a constraint, a non-obvious quirk) from the code in five seconds. Formula reasoning, design rationale, and sensitivity analysis live in the README or the Notion system design — not in code comments.

Placeholder modules ship as an empty `__init__.py` — no docstring. Keep any module or function docstring you do write to a single line. The dense, explain-every-line commenting style is a smell to remove, not to emulate.

### 16. The domain layer is pure — this is load-bearing

`scoring/`, `risk/`, `eval/`, `portfolio/`, and the pure parts of `common/` import nothing from a framework, the DB, or async I/O. **No `datetime.now()`, no `datetime.utcnow()`, no `random`, no network, no file I/O.** Time and randomness enter as parameters (an `asof` timestamp, a `seed`). This is what makes 100% unit coverage possible without mocks, and it is what makes every decision reproducible from stored evidence. The growth simulation is deterministic given `GROWTH_SIM_SEED` precisely because of this rule.

### 17. Composition over inheritance

No `class X(Y)` for code reuse. Compose via functions, ports, and strategy selection. The `ModelRouter` picks a strategy (`static` / `escalate` / `ensemble`) by configuration, not by subclassing. Shared behavior lives in pure functions, not base classes.

### 18. Rule of three before extracting

Duplication twice is acceptable. On the third occurrence, extract. Extracting after the first or second produces an abstraction shaped wrong, which then resists the real third case. Prefer controlled duplication over premature abstraction.

### 19. Errors are typed, not strings

Domain errors are exception classes (`RiskCapExceeded`, `LookaheadViolation`, `BudgetExceeded`), compared via `isinstance` / `except`. String matching with `in` or `==` breaks silently when a message changes. Define the type; raise the type.

### 20. Log levels carry semantics

Levels are a contract, set correctly at the call site:

- `debug`: granular detail useful only when reproducing a problem; off by default.
- `info`: lifecycle and state transitions an operator may want to correlate later — pipeline-run start/finish, opportunity state changes, review decisions, crawler heartbeat.
- `warning`: recoverable boundary failures — provider down with backoff, malformed payload dropped, budget-cap refusal, low-confidence stub fallback. The system kept working; an operator may want to know.
- `error`: unhandled exceptions and anything that would page someone. Use sparingly.

Every log line carries `service`, `event`, `correlation_id` when context exists, plus `market_id` / `estimate_id` where applicable. Bind static context once per scope with a `structlog` bound logger rather than repeating fields. Grepping one `correlation_id` must reconstruct a decision's full lifecycle and sum its cost.

---

## Naming conventions

- Files and modules: snake_case (`market_crawler.py`, `opportunity_score.py`).
- Classes and Pydantic models: PascalCase (`MarketSnapshot`, `CriticVerdict`).
- Functions and variables: snake_case.
- Constants: `SCREAMING_SNAKE_CASE` for truly immutable globals (caps, thresholds, env keys).
- Database tables and columns: snake_case, matching §4.7 **exactly** (`market_snapshots`, `cost_events`, `yes_price`, `opportunity_score`). The schema names are a contract — do not "improve" them.
- Tests: `test_*.py` colocated under `tests/`. Property tests grouped and named for the invariant they prove.

## Git, branching and PR conventions

- **Nothing is committed directly to `main`.** Every change follows the Jira-card → branch → PR flow: one branch per card, opened against `main`, merged only through a PR.
- Branch names carry the card key: `<type>/PYT-<n>-<slug>` (e.g. `chore/PYT-7-bootstrap-uv-tooling`).
- **Conventional Commits** for every commit: `type(scope): subject` in imperative mood — `feat`, `fix`, `chore`, `ci`, `docs`, `refactor`, `test`, `build`. Reference the card in a `Refs: PYT-<n>` footer. One concern per commit.
- The Jira card moves with the work: → *Em andamento* when the branch opens, → *Concluído* when the PR merges.
- PR descriptions are narrative: what changed, why, what was considered and rejected. Link the card.
- PR checklist: route handlers stay thin (validate → delegate → serialize, no business logic); no secrets; no LLM on the scoring/risk path; ruff + mypy strict + pytest green in CI.
- No AI-attribution trailers in commits or PRs.

## Operational quirks

- **Calibration is the product, not PnL.** Brier(system) vs Brier(market) per category, with `n` always reported, is the headline. PnL is a secondary, noisy readout. Build and prioritize accordingly.
- **Point-in-time is a storage invariant.** `market_snapshots` and `evidence` have **no update path**; snapshots are immutable. Every retrieval is filtered by `asof`. A snapshot's `ts` becomes the downstream `asof` for research. Lookahead is a defect caught by a property test, not a review note.
- **Paper trading is a gate, not a stage.** `MODE=paper` is the only mode and it hard-fails any real-execution path. `ExecutionPort` has no implementation. The only path to a paper position is the **human review gate**; the only path to real money is clearing the calibration gate (Brier(system) < Brier(market), stable over significant `n`, with realized EV ≈ predicted EV). The design deliberately ends at that gate.
- **No LLM on the critical path.** `scoring/` and `risk/` are pure deterministic Python. An agent that sizes positions is an agent that will, occasionally and convincingly, bet the bankroll. LLMs estimate and critique; math decides.
- **The critic always runs, on a different model family.** Critique is mandatory between estimation and scoring, and the `ModelRouter` must select a different model family than the estimator (the critic-diversity rule). It lowers confidence on detected lookahead leaks or weak sources — not by silent averaging.
- **Deterministic stub fallback preserves zero-secret runnability.** With no LLM key, `LLMPort` returns a plausible, low-confidence-flagged `Estimate`/`CriticVerdict` and the whole pipeline still runs. The selector lives at composition time; the orchestration code path is identical with or without secrets. CI runs entirely on the stub.
- **Cost is measured, not estimated.** `LLMPort` is the single choke point; OpenRouter returns the real `cost_usd` per request, written to `cost_events` tagged with agent / model / provider / `correlation_id`. A per-run cap (`COST_CAP_PER_RUN`) and a daily kill switch (`COST_BUDGET_DAILY`) halt new calls when breached; caching by `(market, snapshot, evidence_hash)` avoids re-billing.
- **VPN / geo gate (§8.6) — never call a live venue silently.** The operator runs from Brazil, where Polymarket and Kalshi are geo-restricted. Almost all work runs offline against the deterministic stub and recorded fixtures (VCR-style cassettes: record once, replay offline). Before any **live** call to Polymarket or another geo-restricted venue, **stop, state the exact call, and ask the operator to enable VPN — then proceed only after confirmation.** Never retry a geo error blindly. CI must never require VPN.
- **Invalid (50/50) resolution is a first-class outcome.** UMA settlements can be ambiguous. `resolutions.ambiguous = true` rows are **excluded from Brier** and tracked separately as a per-category **invalid-rate**. Resolution-source type grades the `P_resol` penalty; poor-resolution categories are sized down and cannot be promoted past paper.
- **Available capital is recomputed from the ledger, never static.** `available_capital = starting_bankroll + realized_pnl − locked_in_open`. Sizing runs against `available_capital`, not the static `BANKROLL_PAPER`. A category already at its cap is vetoed *before* any model is called.
- **Recalibration is point-in-time too.** A `p_model → p_calibrated` map is fit only on resolutions strictly **before** the estimate it corrects. Platt/Beta for small `n`, isotonic only with a large set; below `RECALIBRATION_MIN_N` the raw `p_model` is used and the category stays paper-gated.
- **Orchestration starts as plain Python.** Promote to LangGraph only when the critic loop + HITL checkpoint + retries genuinely justify it — not preemptively.

## Library and integration documentation

Before implementing against any third-party library, framework, SDK, API, CLI, or cloud service — even well-known ones (FastAPI, Pydantic / Pydantic AI, pydantic-settings, SQLModel, Alembic, pgvector, APScheduler, structlog, OpenRouter, numpy/scipy, Hypothesis, testcontainers, uv, React, Vite, Tailwind, shadcn/ui, TanStack Query) — fetch current documentation via the **Context7 MCP server**. Training data lags releases by months; Context7 returns the version-correct API surface, configuration shape, and migration notes. The pattern is `resolve-library-id` → `query-docs`. Prefer it over web search and over recall, even when confident.

Prefer the latest stable release (the LTS line where a project publishes one) of every dependency and tool. Let `uv` resolve and lock the newest compatible versions rather than hand-pinning stale ones.

Do **not** use Context7 for refactoring, writing scripts from scratch, debugging our own business logic, code review, or general programming concepts.

## Engineering process

Each phase compiles, lints, type-checks, and tests green on its own. Implementation typically follows: refine the ticket against its Acceptance Criteria → write the property/integration tests that encode the invariants → implement against them → remove over-abstraction in a refactor pass → smoke test against the local stack (`docker compose up`, `uv sync`) in paper mode with zero secrets → PR. The phases and their dependency order live in the Jira board (project **PYT**) and §6 of the system design.

## When in doubt

Consult the **System Design — Pythia** page in Notion. It has the full rationale for every decision — including the alternatives considered and rejected — and the 15 acceptance criteria the implementation is measured against. The document deliberately ends at the calibration gate; real-money execution is out of scope.
