# Pythia

A calibration-first research and paper-trading engine for Polymarket prediction markets, with auditable AI probability estimation and a deterministic risk layer.

Pythia answers one question before any capital is risked: **is this system more calibrated than the market in some category?** Brier(system) vs Brier(market) is the headline metric; paper trading is a gate, not a stage. The system runs end-to-end with **no secrets** via a deterministic LLM stub.

See `CLAUDE.md` for day-to-day conventions and the Notion **System Design — Pythia** for full architecture and rationale.

## Requirements

- Python ≥ 3.12 (the dev/CI baseline is pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/) for dependency and project management
- Docker — for the local Postgres + pgvector stack (`docker compose up`)

## Getting started

```bash
uv sync                          # create the virtualenv and install all dependencies (incl. dev)
cp .env.example .env             # paper mode, no secrets needed (stub LLM)
docker compose up -d             # Postgres + pgvector (Redis behind the `redis` profile)
uv run python -m pythia.config   # boot check: confirms paper mode with zero secrets
uv run pytest                    # run the test suite
```

## Configuration & live venues

Configuration is read once by `src/pythia/config` (pydantic-settings) from the environment / `.env`; every variable is documented in `.env.example` (System Design Appendix A.3). `DATABASE_URL` is required and the config **fails fast** with a clear error if it is missing; LLM/embedding keys are optional — with none set, the deterministic stub runs the pipeline end-to-end with **zero secrets**.

**Mode is paper-only.** `MODE=paper` is the single supported mode; real-money execution stays gated until the calibration gate is cleared, and `ensure_paper_mode()` hard-fails any other value.

**Live venues are off by default (§8.6).** The builder runs from Brazil, where Polymarket and Kalshi are geo-restricted. The whole stack runs **offline** against the stub and recorded fixtures; `USE_LIVE_VENUES` defaults to `false`. Live ingestion is a rare, **operator-gated** step that requires a VPN — never the default, opted into per session. CI never needs the VPN.

## Quality gates

Every change must pass the same checks CI runs on each pull request:

```bash
uv run ruff check .          # lint
uv run ruff format --check . # formatting
uv run mypy                  # strict type checking
uv run pytest                # tests
```

Install the pre-commit hooks to run the static gates (ruff lint + format, mypy) on each commit; the test suite runs in CI:

```bash
uv run pre-commit install
```

## Process entrypoints

One codebase, two processes:

```bash
uv run python -m pythia.api     # FastAPI REST + SSE  (stub until PYT-26)
uv run python -m pythia.worker  # scheduler / worker  (stub until PYT-14)
```

## Project layout

```
src/pythia/
  config/  common/                  # settings, shared types & ports
  scoring/  risk/  eval/  portfolio/ # pure, deterministic domain core
  ingestion/  rag/  agents/          # adapters: market data, evidence, LLM
  orchestration/  paper/  db/        # pipeline, paper ledger, persistence
  api/  worker/                      # the two process entrypoints
  dashboard/                         # placeholder package; the React + Vite app lands in PYT-27
tests/                               # smoke tests now; Hypothesis + testcontainers arrive with their phases
```
