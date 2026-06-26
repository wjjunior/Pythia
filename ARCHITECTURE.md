# Architecture

A condensed map of how Pythia is built and why. The Notion **System Design — Pythia** is the source of truth (with the alternatives considered and rejected); this file is the at-a-glance version for someone reading the code. Section references (§) point at the design.

## The thesis (what shapes every decision)

- **Calibration is the product, not PnL (§3.1).** The system exists to answer whether it is more calibrated than the market in some category — Brier(system) vs Brier(market) per category, with `n`. PnL is a noisy secondary readout. Measure honesty before chasing profit.
- **Paper trading is a gate, not a stage (§3.2).** `MODE=paper` is the only mode, and there is no execution adapter in the code at all. The only path to a paper position is the human review gate; the only path to real money is clearing the calibration gate — a human decision the design deliberately stops short of.
- **No LLM on the critical path (§3.3).** `scoring/` and `risk/` are pure deterministic Python. LLMs estimate and critique; math decides and sizes. An agent that sizes positions is an agent that will, occasionally and convincingly, bet the bankroll.
- **Point-in-time correctness is a storage invariant (§3.4).** `market_snapshots` and `evidence` have no update path; every retrieval is filtered by `asof`. Lookahead is a defect caught by a property test, not a review note.
- **Zero-secret runnability.** A deterministic stub `LLMPort` returns a plausible, low-confidence `Estimate`/`CriticVerdict`, so the whole pipeline — and all of CI — runs offline with no keys.

## Happy-path flow (§2.3)

```
discover → research(asof) → estimate → critic → score → risk → review → paper_open → resolved
            (rag)           (agents)  (agents) (scoring) (risk) (human)   (paper)    (eval)
```

A `pipeline_run` is opened per market; every step writes an append-only `audit_event` and any LLM call writes a measured `cost_event`. The estimate flows through recalibration (a monotone `p_model → p_calibrated` map) and a resolution/dispute-risk grade of `P_resol` before the deterministic risk gate decides the size and whether it may leave paper. A human approves or rejects; an approval becomes a paper fill at the *observed* snapshot price; resolution turns it into PnL and feeds calibration.

## Hexagonal where it pays (§3.8)

Ports & Adapters only where domain reasoning lives — the deterministic core is insulated from Postgres, the LLM, and the market API; dumb pipes are not wrapped in ceremony.

- **Pure core** — `scoring/`, `risk/`, `eval/`, `portfolio/`, and the pure types in `common/`. No framework, no I/O, no `db`, no `datetime.now()`/`random`. Time and randomness enter as parameters (`asof`, `seed`). This is what makes 100% unit coverage possible without mocks and every decision reproducible.
- **Ports** — `typing.Protocol` classes in `common/ports.py`: `MarketDataPort`, `EvidencePort`, `EmbeddingPort`, `LLMPort`, `PaperLedgerPort`, `AuditPort`. (The design reserves an `ExecutionPort` of the same shape for real-money execution; it is deliberately not added to the code.)
- **Adapters** — `ingestion/`, `rag/`, `agents/`, `db/` implement the ports and depend on the core, never the reverse. A reverse import (core importing an adapter) is a defect.
- **Composition roots** — `orchestration/`, `api/`, `worker/` are the only places that wire adapters to the core.

## Module map

| Layer | Module | Responsibility |
| --- | --- | --- |
| Config | `config/` | The only place that reads env vars (pydantic-settings); fail-fast boot, `MODE=paper` guard. |
| Contracts | `common/` | Canonical Pydantic types (§4.1), `ports.py` Protocols (§4.5), structlog + Logfire logging. |
| Pure core | `scoring/` | `edge`, `net_edge`, `confidence`, `opportunity_score`, `kelly_fraction`, `position_size`; `assembly.py` builds a `Score` and grades the resolution source; `divergence.py` is the cross-venue feature. |
| Pure core | `risk/` | Deterministic caps, gating and correlated-exposure veto (`functions.py`); `resolution_risk.py` grades `P_resol` by the measured invalid-rate. |
| Pure core | `eval/` | `brier`, reliability bins, per-category calibration (`functions.py`); `recalibration.py` (Platt/Beta/isotonic maps); `gate.py` (the §8.5 calibration gate); `harness/` (Brier-per-dollar) and `simulation/` (Monte-Carlo growth); `postmortem.py` classifies statistical-surprise failures and `improvement.py` gates candidate fixes on harness validation. |
| Pure core | `portfolio/` | `available_capital`, `exposure_fraction`, exposure aggregation (`functions.py`). |
| Adapter | `ingestion/` | `market_crawler.py` (Gamma fixtures / live, VPN-gated), `venues/` (cross-venue quotes), `evidence/` (news), `falcon/` (smart-money analytics) — every live transport is `require_live`-gated (§8.6). |
| Adapter | `rag/` | Chunk · embed (pgvector) · `retrieve(market, asof, k)` ranked by freshness × reliability; never returns `observed_at > asof`. |
| Adapter | `agents/` | `llm_port.py` (stub + OpenRouter gateway, measured cost, ensemble, `max_price` ceiling); `router.py` (per-agent model routing); `aggregation.py`; `cost.py` (budget guard + caching); `estimation.py`; `critic.py`. |
| Adapter | `smartmoney/` | v2 Falcon candidate sourcing (trigger ≠ judge, §3.2): `metrics.py` (pure Sortino / skew / entry-concentration floors), `scoring.py` + `selection.py` (harness-validated scorecards via `agreed_bpd`), `roster.py` (bounded trial→active→benched→retired lifecycle), `candidate.py` / `backfill.py` / `evidence.py`. |
| Composition | `services/` | Impure companions that wire the pure core to the store: `calibration.py` (eval math + the §8.5 gate + recalibration fit), `portfolio.py` (live capital from the ledger), `postmortem.py` (surprise/drift triggers → improvement candidates). |
| Composition | `orchestration/` | `pipeline.py` (the integration hub; owns `asof = snapshot.ts`) and `review.py` (the human approve/reject gate). |
| Adapter | `paper/` | `ledger.py` opens fills at the observed price and computes PnL on resolution; `pricing.py`. |
| Adapter | `db/` | SQLModel `models.py` (§4.7), `repositories.py` + `smartmoney.py` repos, Alembic migrations in `alembic/versions/`. |
| Entrypoint | `api/` | FastAPI REST + SSE (`app.py`, `routers/`, `events.py`); OpenAPI is the contract that generates the dashboard's TS types. |
| Entrypoint | `worker/` | APScheduler market-crawl loop (`scheduler.py`); a no-op until `USE_LIVE_VENUES` and the VPN are enabled. |

## Data model (§4.7)

PostgreSQL 18 + pgvector is the single stateful store — relational system of record, vector evidence index, and append-only audit ledger in one. The tables (in `db/models.py`, migrated under `alembic/versions/`):

- **v1 core:** `markets` · `market_snapshots` (immutable) · `sources` · `evidence` (`vector(1536)`, HNSW) · `estimates` · `estimate_members` · `scores` · `paper_trades` · `resolutions` (carries `ambiguous`) · `recalibration_models` (one active map per category) · `cost_events` (measured) · `audit_events` (append-only) · `pipeline_runs` · `venue_quotes` · `postmortems` · `improvement_candidates` · `cohort_memberships`.
- **v2 smart-money:** `tracked_traders` · `trader_scorecards` · `trader_entries` · `candidate_signals` · `roster_events`.

Two invariants are load-bearing: `market_snapshots`/`evidence` have **no update path** (point-in-time), and a snapshot's `ts` becomes the downstream `asof` for research. Invalid (50/50) `resolutions` are excluded from Brier and tracked separately as a per-category invalid-rate.

## The dual-type seam (contracts vs storage)

`common/types.py` holds **frozen Pydantic domain contracts** — the same objects cross every boundary (agent output, the pure core, the API/OpenAPI response that generates the dashboard's TS types). `db/models.py` holds **SQLModel `table=True` rows**. They are deliberately separate **same-named** families: a frozen contract cannot be a mutable ORM row, and the storage layer carries fields the domain does not — surrogate PKs on append-only/secondary tables (`audit_events`, `cost_events`, `scores`), storage timestamps (`created_at`/`ts`), the pgvector embedding + JSONB `metadata` on `evidence`, the raw provider JSON on `market_snapshots`, and denormalized FK tags on `cost_events` for cost queries. A few contract fields live in a child table (an `Estimate`'s ensemble `members` → `estimate_members`). Types legitimately differ too (`id: str` vs `uuid.UUID`, enums stored as text).

Conversion is explicit at the repository boundary (`_to_db_*` / `_from_db_*` in `db/repositories.py`). `tests/test_field_parity.py` pins **every** intentional divergence per pair, so a field added to one side and not the other — the silent contract-break `CLAUDE.md` rule 13 warns about — fails the build. Pairs that need not diverge (e.g. `PaperTrade`, `Source`, `CandidateSignal`) carry identical field names and are kept honest by the same test.

## Process topology (§2.2)

One `uv` project, two process entrypoints — `python -m pythia.api` (REST + SSE) and `python -m pythia.worker` (scheduler). `docker compose up` brings up Postgres + pgvector (Redis only with the `redis` profile). The React dashboard is a separate Vite app whose types are generated from the API's OpenAPI schema, so a breaking Pydantic change fails the frontend build.

## Observability (§3.10, §8.4)

`structlog` emits JSON in every environment; a `correlation_id` (the `pipeline_run` id) is bound at run start and propagated via contextvars, so grepping one id reconstructs a decision's full lifecycle and sums its cost. A Logfire hook forwards traces when a token is set and is a no-op otherwise. Cost is **measured** from OpenRouter's response per call — never estimated.

## Where this design deliberately ends

At the calibration gate. Real-money execution and auto-promotion past paper are out of scope by design — see [`README.md`](README.md#intentionally-absent). The post-mortem/improvement loop (`eval/postmortem.py`, `eval/improvement.py`) closes the self-improvement cycle but never mutates scoring or risk autonomously: a flagged failure becomes an `improvement_candidate` adopted only after the harness validates it on held-out resolved markets, and structural changes still pass the human review gate.
