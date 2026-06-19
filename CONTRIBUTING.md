# Contributing

Thanks for working on Pythia. This file covers the workflow and the gates a change has to clear. The deeper conventions (the 20 coding rules, naming, the operational quirks) live in [`CLAUDE.md`](CLAUDE.md); the architecture is in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Branching & PR flow

- **Nothing is committed directly to `main`.** Every change is one Jira card → one branch → one PR, merged only through review.
- Branch names carry the card key: `<type>/PYT-<n>-<slug>` (e.g. `feat/PYT-33-recalibration`).
- **Conventional Commits** for every commit — `type(scope): subject` in imperative mood (`feat`, `fix`, `chore`, `ci`, `docs`, `refactor`, `test`, `build`). Reference the card in a `Refs: PYT-<n>` footer. One concern per commit.
- **No AI-attribution trailers** in commits or PRs. Commit messages contain only the change and its rationale.
- PR descriptions are narrative: what changed, why, and what was considered and rejected. Link the card. The Jira card moves to *Em andamento* when the branch opens and *Concluído* when the PR merges.

## Local development loop

```bash
uv sync                      # install everything (deps pinned; uv sync is the only install path)
uv run pre-commit install    # ruff lint + format + mypy on each commit
cp .env.example .env          # paper mode, zero secrets
docker compose up -d          # Postgres + pgvector (needed for integration tests)
uv run alembic upgrade head
```

Run the same gates CI runs, before pushing:

```bash
uv run ruff check .            # lint
uv run ruff format --check .   # formatting
uv run mypy                    # strict type checking
uv run pytest                  # 247 unit tests (pure core, no Docker, no secrets)
uv run pytest -m integration   # 76 integration tests (real Postgres + pgvector via testcontainers)
```

For the dashboard: `cd dashboard && npm install && npm run gen && npm run build` (the `gen` step regenerates `src/lib/types.ts` from the API's OpenAPI schema, so a contract break fails the build).

## How we work (test-first, with parallel agents)

The project is built card-by-card with a small, repeatable loop — much of it driven by parallel Claude Code agents:

1. **Brainstorm** the card against its acceptance criteria; settle the genuinely-uncertain design decisions *before* writing code, not during.
2. **Explore in parallel.** Read-only subagents fan out across the relevant modules and return a structured map, so the implementer starts from real file paths and signatures rather than guesses.
3. **TDD.** Write the failing test first (the §4.6 invariants are Hypothesis property tests, not example tests), watch it fail for the right reason, then write the minimum code to pass, then refactor. No production code without a failing test first.
4. **Verify before claiming done.** Run ruff + mypy + the full unit and integration suites; "passes" means you ran it and read the output.
5. **Code review** (often adversarial, sometimes multi-agent): verify each finding against the codebase, push back with technical reasoning when a suggestion is wrong, and reply in the thread with the concrete fix.

Library/framework APIs are checked against current docs (via Context7) before use — training data lags releases.

## PR checklist

Before requesting review, confirm:

- [ ] **Thin route handlers** — validate → delegate → serialize, no business logic in `api/`.
- [ ] **No LLM on the scoring/risk path** — `scoring/` and `risk/` stay pure and deterministic.
- [ ] **No secrets** in the repo, logs, or fixtures; the stub remains the zero-config default. Prices, scores, calibration and measured cost *are* logged on purpose — the line is around credentials.
- [ ] **Point-in-time preserved** — no read returns evidence with `observed_at > asof`; `market_snapshots`/`evidence` keep no update path.
- [ ] **Every new env var documented** in `.env.example`, read only through `config/`.
- [ ] **New cross-boundary types** are the canonical `common/` models; the OpenAPI contract still generates the dashboard types.
- [ ] **ruff + mypy (strict) + pytest** green locally.

## CI gates

GitHub Actions (`.github/workflows/ci.yml`) runs three jobs on every push and pull request; all must pass before merge:

| Job | What it runs |
| --- | --- |
| **lint · type · test** | `ruff check`, `ruff format --check`, `mypy` (strict), `pytest` (unit) |
| **db migration** | `alembic upgrade head` → `downgrade base` → `upgrade head` (migrations are reversible) |
| **integration (testcontainers)** | `pytest -m integration` against real Postgres + pgvector |

CI never requires a VPN or any secret — it runs entirely on the deterministic stub and recorded fixtures.
