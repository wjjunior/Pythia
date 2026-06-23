# Pythia

A calibration-first research and paper-trading engine for Polymarket prediction markets, with auditable AI probability estimation and a deterministic risk layer.

[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![Claude Opus 4.8](https://img.shields.io/badge/Claude-Opus%204.8-D97757?style=flat&logo=anthropic&logoColor=white)](https://www.anthropic.com/claude)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-CLI-D97757?style=flat&logo=anthropic&logoColor=white)](https://claude.com/claude-code)
[![Context7 MCP](https://img.shields.io/badge/Context7-MCP%20server-6C63FF?style=flat&logo=readthedocs&logoColor=white)](https://context7.com/)
[![Notion](https://img.shields.io/badge/Notion-System%20Design-000000?style=flat&logo=notion&logoColor=white)](https://www.notion.so/)
[![Jira](https://img.shields.io/badge/Jira-PYT%20project-0052CC?style=flat&logo=jira&logoColor=white)](https://www.atlassian.com/software/jira)

Pythia answers one question before any capital is risked: **is this system more calibrated than the market in some category?** Brier(system) vs Brier(market), with `n` always reported, is the headline metric. PnL is a secondary, noisy readout. Paper trading is a **gate, not a stage** — the design deliberately ends at the calibration gate and ships no real-money execution path.

The whole stack runs end-to-end with **no secrets**: a deterministic LLM stub stands in for the model, market data replays from recorded fixtures, and the pure scoring/risk/eval core is reproducible from stored evidence.

- **Architecture & rationale:** [`ARCHITECTURE.md`](ARCHITECTURE.md) (condensed) and the Notion **System Design — Pythia** (source of truth).
- **How to contribute:** [`CONTRIBUTING.md`](CONTRIBUTING.md).
- **Day-to-day conventions:** [`CLAUDE.md`](CLAUDE.md).

## The friction

Turning "ask an LLM for a probability and bet on the edge" into a system you can trust takes four constraints that shape the whole design:

- **Honesty before profit.** A model that looks profitable can simply be lucky. The headline is calibration — Brier(system) vs Brier(market) per category with `n` — measured on resolved markets, not PnL. Promotion past paper needs realized EV ≈ predicted EV over significant `n`, a human decision.
- **No lookahead, ever.** Research must only ever see what was knowable at the time. `market_snapshots` and `evidence` are append-only (enforced in the database), and every evidence retrieval is filtered by an `asof` timestamp. A leak is a property-test failure, not a code-review note.
- **The model estimates; math decides.** An LLM that sizes positions will, occasionally and convincingly, bet the bankroll. The scoring and risk layers are pure deterministic Python with hard caps; the LLM only proposes a probability and a critique.
- **Reproducible and free to run.** The pure core takes time and randomness as parameters (an `asof`, a `seed`) and touches no I/O — so it is 100% unit-testable without mocks, and the whole pipeline runs offline with zero secrets via a deterministic stub.

## Architecture

```
   ingestion — point-in-time          pipeline — plain Python                 eval · api
  ┌────────────────────────┐   ┌──────────────────────────────┐   ┌────────────────────────┐
  │ market-crawler ▶ snaps  │   │ estimate ▶ critic ▶ score ▶  │   │ calibration            │
  │ evidence-ingest ▶ evid. ├─▶ │ (LLMPort)  (diff    risk ▶    ├─▶ │ (Brier vs market + n)  │
  │ venue-crawler  ▶ quotes │   │            family)  review    │   │ growth sim · cost      │
  └─────────── asof ────────┘   └───────────────┬──────────────┘   └────────────────────────┘
        model estimates  ·  math decides        ▼
  ┌─────────── PostgreSQL + pgvector ──────────┐  paper ledger ▶ resolution ▶ post-mortem loop
  │ system of record · vector evidence index   │
  │ · append-only audit ledger (one store)     │  api:    python -m pythia.api    (REST + SSE)
  └─────────────────────────────────────────────┘  worker: python -m pythia.worker (APScheduler)
```

One **PostgreSQL + pgvector** is the relational system of record, the vector evidence index, **and** the append-only audit ledger — no separate vector store, no separate queue. The deterministic core (`scoring/`, `risk/`, `eval/`, `portfolio/`) is insulated from Postgres, the LLM, and the market API by ports; only `orchestration/` and the `api`/`worker` entrypoints wire adapters to the core. Full detail in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Key design decisions

- **One PostgreSQL + pgvector store** — relational record, vector evidence index, and append-only audit ledger in one. No separate vector DB; Redis is an optional, profile-gated add-on only if an in-process loop ever stops sufficing.
- **A pure, deterministic domain core.** `scoring/`, `risk/`, `eval/`, `portfolio/` import no framework, no DB, no clock, no RNG — time and seeds enter as parameters. 100% unit-testable without mocks; every decision reproducible from stored evidence.
- **No LLM on the critical path.** LLMs estimate and critique; deterministic math sizes and decides. Hard risk caps always dominate the model's suggestion.
- **The critic always runs, on a different model family.** Critique is mandatory between estimation and scoring; the `ModelRouter` enforces critic-diversity and lowers confidence on detected lookahead leaks or weak sources.
- **`LLMPort` is the single cost choke point and the zero-secret seam.** With no key, the deterministic stub runs the whole pipeline; with a key, OpenRouter returns the **measured** `cost_usd` per call, written to `cost_events` with a per-run cap and a daily kill switch.
- **Point-in-time is a storage invariant.** `market_snapshots` and `evidence` are append-only (DB-enforced trigger); every retrieval is filtered by `asof`; recalibration is fit only on resolutions strictly before the estimate it corrects.
- **Paper trading is a gate, not a stage.** `MODE=paper` is the only mode and hard-fails any real-execution path; `ExecutionPort` has no implementation. The only path to a paper position is the human review gate.

## Running locally

**Prerequisites:** Python ≥ 3.12 (pinned in `.python-version`), [uv](https://docs.astral.sh/uv/), Docker (Postgres + pgvector, and the testcontainers integration suite), and Node ≥ 20 + npm (only to build/run the dashboard). Run `make help` for every command.

```bash
make install      # create the uv venv and install all deps (incl. dev)
cp .env.example .env
make up           # Postgres + pgvector on :5434, then migrate → ready database
make config       # boot check → "boot ok — mode=paper live_venues=False llm=stub db=…"
make api          # FastAPI REST + SSE on http://localhost:8000 (OpenAPI at /docs)
make worker       # (optional, another terminal) APScheduler crawl + pipeline
```

`make up` brings up Postgres+pgvector and applies the `§4.7` migrations; `make down` stops it (keeping data), `make reset-db` starts fresh. The dashboard:

```bash
make dashboard         # Vite dev server (generates TS types from OpenAPI, proxies the API on :8000)
make dashboard-build   # gen types → typecheck → bundle (what CI runs)
```

The six views — opportunity radar, market detail (cited, dated evidence), paper portfolio + capital, calibration (Brier vs market + `n`), cost panel, growth projection — read the REST snapshot as the source of truth and apply SSE updates as advisory live deltas.

## Configuration

`DATABASE_URL` is the only required variable; config **fails fast** if it is missing. LLM/embedding keys are optional — with none set, the deterministic stub runs the pipeline end-to-end. `config/` is the only place that reads the environment (validated once at boot via pydantic-settings); `.env.example` documents every variable. `MODE=paper` is the only supported mode; `ensure_paper_mode()` hard-fails any other value.

**Live venues are off by default (§8.6).** Polymarket/Kalshi are geo-restricted from where this is built, so everything runs offline against the stub and recorded fixtures. `USE_LIVE_VENUES=false` by default; live ingestion is a rare, operator-gated step that needs a VPN and is never required by CI.

## Tests & quality gates

The same checks CI runs on every pull request — all behind `make`:

```bash
make lint              # ruff
make format            # ruff format (use `format` to fix; CI checks formatting)
make typecheck         # mypy, strict
make test              # pure unit/property suite — no Docker, no secrets, ~seconds
make test-integration  # integration suite — real Postgres + pgvector via testcontainers
make verify            # lint + format-check + typecheck + test (the CI gate)
```

The pure core proves the `§4.6` invariants as **property** tests (Hypothesis): score bounds, confidence-as-AND, Kelly safety, Brier bounds, point-in-time `retrieve`, risk caps dominating, no silent bets. Integration tests run against a real Postgres+pgvector via testcontainers and prove immutability and idempotency. CI additionally builds the dashboard so a breaking Pydantic change fails the generated-types contract. Install the local hooks with `make hooks` (ruff + mypy on each commit).

## Requirements-coverage map

Each acceptance criterion from the System Design maps to the files that satisfy it.

| AC | Criterion | Primary files |
| --- | --- | --- |
| AC-1 | Pure domain core; §4.6 invariants property-tested in <5s, no Docker/env | `scoring/functions.py`, `scoring/assembly.py`, `risk/functions.py`, `eval/functions.py`, `portfolio/functions.py`; `tests/{scoring,risk,eval}/test_properties.py`, `tests/portfolio/test_functions.py` |
| AC-2 | Point-in-time integrity: `retrieve` never returns `observed_at > asof`; snapshots immutable | `rag/retrieval.py`, `db/models.py` (`market_snapshots`, no update path); `tests/integration/{rag,db}/` |
| AC-3 | Full pipeline runs with zero secrets via the stub; the critic always runs | `agents/llm_port.py` (`StubLLMPort`), `orchestration/pipeline.py`, `config/settings.py` |
| AC-4 | Calibration (the headline): Brier(system) vs Brier(market) per category + reliability + EV | `eval/functions.py`, `eval/service.py`, `api/routers/calibration.py`, `dashboard/src/views/calibration-view.tsx` |
| AC-5 | Risk gate **and** human review before any paper position; everything in `audit_events` | `risk/functions.py`, `orchestration/review.py`, `orchestration/pipeline.py`, `db/models.py` (`audit_events`) |
| AC-6 | Dashboard & contracts: TS types generated from OpenAPI; radar ranked; SSE + refetch | `api/app.py`, `api/events.py`, `dashboard/scripts/gen-types.mjs`, `dashboard/src/views/radar-view.tsx` |
| AC-7 | Quality gates in CI; no secrets; `.env.example` documents every var; thin handlers | `.github/workflows/ci.yml`, `.pre-commit-config.yaml`, `.env.example`, `pyproject.toml` |
| AC-8 | Measured `cost_events` per LLM call; spend panel; daily budget kill switch | `agents/cost.py`, `agents/llm_port.py`, `db/models.py` (`cost_events`), `api/routers/cost.py`, `dashboard/src/views/cost-view.tsx` |
| AC-9 | `LLMPort`→OpenRouter; `ModelRouter` per agent + failover; harness ranks by Brier-per-dollar | `agents/llm_port.py`, `agents/router.py`, `eval/harness/` |
| AC-10 | Capital state from the ledger; sizing against `available_capital`; exposure caps | `portfolio/functions.py`, `portfolio/service.py`, `orchestration/pipeline.py` |
| AC-11 | Growth simulation from the **measured** distribution; P5/P50/P95, drawdown, ruin; seeded | `eval/simulation/montecarlo.py`, `api/routers/simulation.py`, `dashboard/src/views/growth-view.tsx` |
| AC-12 | Forecast aggregation: ensemble → final `p_model`; members in `estimate_members` | `agents/aggregation.py`, `agents/llm_port.py` (ensemble), `db/models.py` (`estimate_members`) |
| AC-13 | Recalibration: monotone point-in-time `p_model→p_calibrated`, versioned, raw-vs-recalibrated | `eval/recalibration.py`, `eval/service.py`, `db/models.py` (`recalibration_models`), `orchestration/pipeline.py` |
| AC-14 | Cross-venue signal: matched point-in-time `venue_quotes` + divergence feature; no execution | `ingestion/venue_match.py`, `scoring/divergence.py`, `db/models.py` (`venue_quotes`) |
| AC-15 | Resolution & dispute risk: invalid-rate per category grading `P_resol`; poor sources paper-gated | `risk/resolution_risk.py`, `scoring/assembly.py`, `eval/functions.py`, `orchestration/pipeline.py` |
| AC-16 | Post-mortem on statistical surprise → harness-validated improvement loop | `eval/postmortem.py`, `eval/improvement.py`, `eval/service.py`, `db/models.py` (`postmortems`, `improvement_candidates`) |

## Observability

`structlog` emits JSON everywhere, each line carrying `service`, `event`, and `correlation_id` (plus `market_id` / `estimate_id` where applicable). Grepping a single `correlation_id` reconstructs a pipeline run's full lifecycle across the api and worker and sums its cost. `cost_events` is the measured-spend ledger (real `cost_usd` per call, tagged agent / model / provider / run); `audit_events` is the append-only decision ledger (estimate → critic → score → risk → review → resolution). `GET /health` reports DB reachability and data freshness. Logfire/Langfuse traces are emitted only when a token is set.

## Repository layout

```
src/pythia/
  config/  common/                    # settings (the only env reader); shared types, ports, logging
  scoring/ risk/ eval/ portfolio/     # pure, deterministic domain core (no I/O, no clock, no RNG)
  ingestion/ rag/ agents/             # adapters: market crawler, point-in-time evidence, LLM gateway
  orchestration/ paper/ db/           # pipeline + review gate, paper ledger, SQLModel persistence
  api/ worker/                        # the two process entrypoints (FastAPI; APScheduler crawl)
dashboard/                            # React 19 + Vite + TS + Tailwind v4 + shadcn/ui + TanStack Query
tests/                                # Hypothesis property tests + testcontainers integration tests
```

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the module-by-module map and the data model.

## Intentionally absent

The design ends at the calibration gate; these are deliberate non-goals, not gaps:

- **Real-money execution.** There is **no execution path and no execution adapter** in the code; `MODE=paper` is the only mode and `ensure_paper_mode()` hard-fails anything else. The design reserves an `ExecutionPort` to force the gas/slippage/credentials conversation if the calibration gate is ever cleared — deliberately not added yet.
- **Auto-promotion past paper.** Promotion needs Brier(system) < Brier(market), stable over significant `n`, with realized EV ≈ predicted EV — a human decision, not code. The post-mortem/improvement loop (AC-16) proposes fixes but never mutates scoring or risk autonomously, and structural changes still pass the human review gate.
- **LangGraph / Celery.** Orchestration is plain Python and scheduling is in-process APScheduler until scale genuinely justifies otherwise (Redis stays behind a compose profile).
