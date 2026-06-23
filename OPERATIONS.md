# Operating Pythia for real

This runbook takes Pythia off the deterministic stub and runs it against **real data** — live OpenRouter LLM estimates, real evidence/markets — so it starts accruing the **calibration record** that the whole system exists to measure. Read [`README.md`](README.md) first for the zero-secret quickstart; this document is the next step.

## 1. What "real" means here — and what it never means

"Real" means **real data in paper mode**. Two surfaces leave the offline sandbox, and each has a guard:

- **Live LLM (OpenRouter)** — costs real money. Guarded by per-run and daily budget caps with a hard kill switch.
- **Live venues (Polymarket / Kalshi)** — geo-restricted from where this is operated (Brazil), so they need a **VPN** and are operator-gated (§8.6).

What it **never** means: real-money trading. `MODE=paper` is the only mode; `ensure_paper_mode()` hard-fails anything else, and there is **no `ExecutionPort` implementation** in the codebase. Running "for real" still ends at the paper ledger and the calibration gate. The goal is an honest Brier(system)-vs-Brier(market) record, not PnL.

## 2. Configure `.env` for real mode

Copy `.env.example` to `.env` and flip the stub→real switches. Leave anything you are not ready for unset — each subsystem independently falls back to its deterministic stub.

| Variable | Stub default | Real value | Effect |
| --- | --- | --- | --- |
| `OPENROUTER_API_KEY` | unset → stub LLM | `sk-or-…` | `make_llm_port` returns the OpenRouter port; estimates/critique/discovery hit real models and bill `cost_events`. |
| `MODEL_ROUTING_STRATEGY` | `static` | `static` \| `escalate` \| `ensemble` | per-agent model selection; `ensemble`/`escalate` cost more (see `ENSEMBLE_SIZE`). |
| `MAX_PRICE_PER_CALL` | unset | e.g. `0.01` | hard per-call price ceiling passed to OpenRouter. |
| `EMBEDDING_PROVIDER` + `EMBEDDING_API_KEY` + `EMBEDDING_MODEL` | unset → stub embedder | all **three** set | `make_embedder` returns a real provider port; RAG retrieval uses real vectors. With any one missing it stays on the deterministic stub embedder (RAG still works). |
| `USE_LIVE_VENUES` | `false` | `true` | the **master switch** for the worker. While `false` the worker logs `worker_disabled` and exits; while `true` it crawls **live Polymarket** (`HttpxTransport`) on the cadence. **Requires VPN.** |
| `EVIDENCE_SOURCES` + `NEWSAPI_KEY` | unset → no evidence | `newsapi` + key | real news evidence is ingested point-in-time for RAG. |
| `CROSS_VENUE_SOURCES` (+ venue bases/keys) | unset | `kalshi,metaculus,manifold` | cross-venue quotes feed the divergence signal. |
| `MODE` | `paper` | `paper` | **leave it paper.** `live` is rejected at boot. |

Keep the cost guards sane before the first real run (§4). **Never commit `.env`** — it is gitignored and holds the keys.

## 3. The VPN / geo gate (§8.6)

Polymarket's Gamma API is geo-blocked from Brazil. The crawler classifies a connection/geo failure as `LiveVenueAccessError` and **does not retry it** — that is deliberate, so a geo-block surfaces instead of hammering the venue.

**Procedure for any live-venue session:**

1. Enable your VPN to a permitted region **before** setting `USE_LIVE_VENUES=true` or running the worker.
2. Run the crawl. If you see `LiveVenueAccessError` ("enable the VPN before a live Polymarket call"), the VPN is off or in a blocked region — fix it; do not loop.
3. CI and the whole test suite **never** need the VPN — they run on recorded fixtures. Keep it that way.

The OpenRouter LLM path does **not** need a VPN; only the venues do. You can exercise the real LLM end-to-end while offline from venues.

## 4. Cost discipline

The LLM is the only thing that spends money, and `LLMPort` is the single choke point where every call's **measured** `cost_usd` is written to `cost_events`.

- `COST_CAP_PER_RUN` (default `1.0`) — a single pipeline run halts (`BudgetExceeded`) once its measured spend reaches the cap.
- `COST_BUDGET_DAILY` (default `10.0`) — the daily kill switch; new calls refuse once the day's total reaches it.
- **Start frugal:** a small `DISCOVERY_SHORTLIST_SIZE` (e.g. `5`) so only a handful of markets enter the pipeline per run, `ENSEMBLE_SIZE=1`, a cheap model slate, and a low `COST_BUDGET_DAILY` for the first sessions. Raise them only once the spend-per-resolution is understood.
- **Verify the key cheaply first:** the OpenRouter `GET /models` endpoint is free; one tiny completion confirms billing works. Watch the **cost panel** (`GET /api/cost`) and `cost_events` after the first run.

## 5. Bring it up

```bash
make up            # Postgres + pgvector on :5434, migrated
make config        # boot check — should now report llm=openrouter (not stub)
make api           # REST + SSE on :8000 — radar, review gate, resolution endpoint, /api/run
# VPN ON, USE_LIVE_VENUES=true:
make worker        # live Polymarket crawl + venue/evidence ingest + pipeline on the cadence
make dashboard     # watch the radar / calibration / cost views update live
```

`make api` is the on-demand + SSE surface: `POST /api/run` scores open markets and publishes radar updates. `make worker` is the autonomous loop (crawl → snapshots → pipeline) on `CRAWL_INTERVAL_SECONDS`; it writes scores but, being a separate process from the in-process SSE broadcaster, does not publish SSE — the dashboard's wide-interval refetch covers it.

## 6. The operating loop

```
crawl (live Polymarket) ─▶ market_snapshots ─▶ discovery shortlist ─▶ estimate (LLM)
                                                                          │
   paper ledger ◀─ human review gate ◀─ risk ◀─ score ◀─ critic (different model family)
        │
        ▼
   resolution ─▶ calibration (Brier vs market + n) ─▶ post-mortem ─▶ improvement candidates
```

Nothing becomes a paper position automatically: the **human review gate** (`POST /api/review/{estimate_id}`, surfaced in the dashboard) is the only path from a scored opportunity to a paper trade. The deterministic math (score, risk caps) decides sizing; the LLM only estimates and critiques.

## 7. Recording resolutions — the fuel for calibration

Calibration accrues only from **resolved** markets. Recording resolutions is the single most important operational habit for "collecting data":

- **Clean / binary outcomes:** `POST /api/markets/{market_id}/resolve` with `{ "outcome": 0|1, "ambiguous": false }` (PYT-45). This records the resolution, settles open paper trades to PnL, publishes `calibration_changed`, and triggers the post-mortem loop.
- **Subjective / UMA outcomes:** the resolution agent (PYT-60) proposes an outcome and an `ambiguous` flag for optimistic-oracle markets; ambiguous (50/50) resolutions are **excluded from Brier** and tracked as a per-category invalid-rate.
- A category stays **paper-gated** until it clears the §8.5 calibration gate: Brier(system) < Brier(market) by `GATE_BRIER_MARGIN` over ≥ `GATE_MIN_N` resolutions, with realized EV ≈ predicted EV (`GATE_EV_TOLERANCE`). That gate clearing is the design's terminal milestone — and it is a human's call, not the system's.

## 8. Monitoring

- **`GET /health`** — DB reachability and data freshness.
- **Calibration view / `GET /api/calibration`** — the headline: Brier(system) vs Brier(market) per category **with `n`**, reliability curve, per-category invalid-rate, EV predicted vs realized.
- **Cost panel / `GET /api/cost`** — measured spend by agent / model / category / day, and cost-per-opportunity.
- **Logs** — `structlog` JSON; grep a single `correlation_id` to reconstruct one pipeline run's full lifecycle across api and worker and sum its cost. `audit_events` is the append-only decision ledger.

## 9. Capturing real cassettes ("real tests")

Real tests come from **recording real interactions once and replaying them offline** — never from hitting the network in CI.

1. With the VPN on (for venues) or the key set (for the LLM), run the crawl/pipeline and capture the raw provider responses into `tests/fixtures/` (e.g. `tests/fixtures/polymarket/` for Gamma payloads).
2. Point an integration test's transport at the recorded fixture (`FixtureTransport` for the crawler; a recorded chat-completion envelope for `OpenRouterLLMPort`). The test now exercises the real-shaped data **deterministically and offline**.
3. CI replays these fixtures with **no keys and no VPN** — that invariant is load-bearing; do not add a test that needs either.

This is how "real data" becomes durable test coverage: the live session produces fixtures, the fixtures become regression tests, and the calibration record itself becomes the honest measure of whether the system beats the market.

## 10. Safety rails

- **`MODE=paper` only.** There is no execution path; `live` is rejected at boot.
- **Never commit `.env`.** Keys live only there (gitignored).
- **Never retry a geo error.** A `LiveVenueAccessError` means "enable the VPN," not "try again."
- **CI is offline and keyless.** Never make CI depend on the VPN, a venue, or an LLM key.
- **Cost caps are not optional.** Run with `COST_BUDGET_DAILY` set; treat a `BudgetExceeded` as the system working, not a bug.
