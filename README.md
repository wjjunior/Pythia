# Pythia

A calibration-first research and paper-trading engine for Polymarket prediction markets, with auditable AI probability estimation and a deterministic risk layer.

Pythia answers one question before any capital is risked: **is this system more calibrated than the market in some category?** Brier(system) vs Brier(market) is the headline metric; paper trading is a gate, not a stage. The system runs end-to-end with **no secrets** via a deterministic LLM stub.

See `CLAUDE.md` for day-to-day conventions and the Notion **System Design — Pythia** for full architecture and rationale.

## Requirements

- Python ≥ 3.12 (the dev/CI baseline is pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/) for dependency and project management
- Docker (for the local Postgres + pgvector stack — added in PYT-8)

## Getting started

```bash
uv sync            # create the virtualenv and install all dependencies (incl. dev)
uv run pytest      # run the test suite
```

The local infrastructure stack is brought up with Docker Compose (added in PYT-8):

```bash
docker compose up  # Postgres + pgvector (Redis behind the `redis` profile)
```

## Quality gates

Every change must pass the same checks CI runs on each pull request:

```bash
uv run ruff check .          # lint
uv run ruff format --check . # formatting
uv run mypy                  # strict type checking
uv run pytest                # tests
```

Install the pre-commit hooks to run these locally on commit:

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
dashboard/                           # React + Vite frontend (PYT-27)
tests/                               # property (Hypothesis) + integration (testcontainers)
```
