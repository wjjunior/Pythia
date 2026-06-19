# Pythia

A calibration-first research and paper-trading engine for Polymarket prediction markets, with auditable AI probability estimation and a deterministic risk layer.

Pythia answers one question before any capital is risked: **is this system more calibrated than the market in some category?** Brier(system) vs Brier(market), with `n` always reported, is the headline metric. PnL is a secondary, noisy readout. Paper trading is a **gate, not a stage** — the design deliberately ends at the calibration gate and ships no real-money execution path.

The whole stack runs end-to-end with **no secrets**: a deterministic LLM stub stands in for the model, market data replays from recorded fixtures, and the pure scoring/risk/eval core is reproducible from stored evidence.

- **Architecture & rationale:** [`ARCHITECTURE.md`](ARCHITECTURE.md) (condensed) and the Notion **System Design — Pythia** (source of truth).
- **How to contribute:** [`CONTRIBUTING.md`](CONTRIBUTING.md).
- **Day-to-day conventions:** [`CLAUDE.md`](CLAUDE.md).

## Requirements

- Python ≥ 3.12 (the dev/CI baseline is pinned in `.python-version`).
- [uv](https://docs.astral.sh/uv/) for dependency and project management.
- Docker — for the local Postgres + pgvector stack and the testcontainers integration suite.
- Node ≥ 20 + npm — only to build/run the dashboard.

## Quickstart (paper mode, zero secrets)

```bash
uv sync                          # create the venv and install all deps (incl. dev)
cp .env.example .env             # paper mode, no secrets needed (stub LLM, offline)
docker compose up -d             # Postgres + pgvector on :5434 (Redis behind the `redis` profile)
uv run alembic upgrade head      # apply the §4.7 schema migrations
uv run python -m pythia.config   # boot check → "boot ok — mode=paper live_venues=False llm=stub ..."
uv run python -m pythia.api      # FastAPI REST + SSE on http://localhost:8000 (OpenAPI at /docs)
```

`DATABASE_URL` is the only required variable; config **fails fast** if it is missing. LLM/embedding keys are optional — with none set, the deterministic stub runs the pipeline end-to-end. `MODE=paper` is the only supported mode; `ensure_paper_mode()` hard-fails any other value.

**Live venues are off by default (§8.6).** Polymarket/Kalshi are geo-restricted from where this is built, so everything runs offline against the stub and recorded fixtures. `USE_LIVE_VENUES=false` by default; live ingestion is a rare, operator-gated step that needs a VPN and is never required by CI.

### Dashboard

```bash
cd dashboard
npm install
npm run gen      # generate src/lib/types.ts from the FastAPI OpenAPI schema
npm run dev      # Vite dev server (proxies the API on :8000)
```

The six views — opportunity radar, market detail (cited, dated evidence), paper portfolio + capital, calibration (Brier vs market + `n`), cost panel, growth projection — read the REST snapshot as the source of truth and apply SSE updates as advisory live deltas.

## Test & quality gates

The same checks CI runs on every pull request:

```bash
uv run ruff check .            # lint
uv run ruff format --check .   # formatting
uv run mypy                    # strict type checking
uv run pytest                  # 247 unit tests — pure core, no Docker, no secrets, ~3s
uv run pytest -m integration   # 76 integration tests — real Postgres + pgvector via testcontainers
```

Install the static gates locally with `uv run pre-commit install` (ruff lint+format, mypy on each commit).

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

## Requirements-coverage map

Each acceptance criterion from the System Design maps to the files that satisfy it.

| AC | Criterion | Primary files |
| --- | --- | --- |
| AC-1 | Pure domain core; §4.6 invariants property-tested in <5s, no Docker/env | `scoring/functions.py`, `scoring/assembly.py`, `risk/functions.py`, `eval/functions.py`, `portfolio/functions.py`; `tests/{scoring,risk,eval,portfolio}/test_properties.py` |
| AC-2 | Point-in-time integrity: `retrieve` never returns `observed_at > asof`; snapshots immutable | `rag/retrieval.py`, `db/models.py` (`market_snapshots`, no update path); `tests/integration/rag/` |
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
| AC-16 | Post-mortem & harness-validated improvement loop | **Pending — PYT-36** (`postmortems` / `improvement_candidates` not yet implemented) |

## Intentionally absent

The design ends at the calibration gate; these are deliberate non-goals, not gaps:

- **Real-money execution.** `ExecutionPort` is defined as a Protocol but has **no implementation**; it exists only to force the gas/slippage/credentials conversation at the boundary. `MODE=paper` is the only mode.
- **Auto-promotion past paper.** Promotion needs Brier(system) < Brier(market), stable over significant `n`, with realized EV ≈ predicted EV — a human decision, not code.
- **The post-mortem / improvement loop (AC-16).** Tracked as PYT-36.
- **LangGraph / Celery.** Orchestration is plain Python and scheduling is in-process APScheduler until scale genuinely justifies otherwise.
