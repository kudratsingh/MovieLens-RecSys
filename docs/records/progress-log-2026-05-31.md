> **Archived, not maintained.** This is the session-by-session progress log the project kept
> before `docs/status/` existed. It stopped on 2026-05-31 and was never resumed — the sentence
> below claiming it is the source of truth is exactly what went stale. The living ledger is
> [`docs/status/`](../status/README.md); this file is kept only so the early record is not lost.

# Progress Log

Session-by-session record of where the project actually stands. Pairs with
CLAUDE.md (the durable north star) and the ADRs (locked-in decisions). When in
doubt about what shipped vs. what is queued, this is the source of truth.

Newest entry on top.

---

## 2026-05-31 — Frontend scaffold (in-flight, not committed)

Parallel frontend-agent session running in the same working tree as the
backend agent's Phase 1 wrap. Scaffold built and verified, nothing committed
yet — a coordination question (ADR numbering) needs to land before the PR.

### What's in the tree (untracked)

- `web/` — Next.js 16 (App Router) + React 19 + TypeScript 5 + Tailwind v4 +
  ESLint 9 (flat config). Generated via `create-next-app`, then trimmed:
  - `package.json` — `dev` and `start` pinned to port `3001` (Grafana owns
    `3000` in `docker-compose.yml`). Added `typecheck = tsc --noEmit`.
  - `app/page.tsx` — placeholder copy; boilerplate stripped.
  - `app/layout.tsx` — metadata set to project title + description.
  - `README.md` — project-specific quickstart, links ADR + CLAUDE.md.
- `docs/adr/0003-frontend-framework.md` — Next.js + Tailwind + ESLint +
  port-3001 decision; alternatives considered: Streamlit, plain React+Vite,
  Remix, SvelteKit.

### Verified

- `npm run typecheck` ✓
- `npm run lint` ✓
- `npm run build` ✓ (compile ~1.6s, static prerender works)

### Blockers / open coordination

- **ADR number collision.** Backend's PR #15 is `0003-two-stage-architecture`.
  My ADR is also `0003-frontend-framework`. Per the Phase 1 wrap's own note,
  frontend bumps to `0004` (backend's is older and Phase 2 depends on it).
  Need to rename the file, update the heading, and update the link in
  `web/README.md` before committing.
- **Branch state.** Working tree was switched to
  `feat/per-policy-attribution-metrics` by the backend agent mid-session.
  `feat/frontend-scaffold` still exists locally at the pre-CF main commit
  (`20709ed`) — needs to be checked out and brought up to current `main`
  (which now includes PRs #14 CF baseline + #11 CLAUDE.md frontend scope).
  Untracked files survive the branch switch, so no work is lost.

### Next session — exact steps

1. `git switch feat/frontend-scaffold` (untracked `web/` and the ADR persist).
2. `git pull --rebase origin main` (or merge — bring in PR #14 CF baseline and
   whatever else has landed). If PR #15 has merged by then, the
   two-stage ADR will be at `0003` — confirm before renaming.
3. Rename `docs/adr/0003-frontend-framework.md` → `0004-frontend-framework.md`.
   Update the H1 heading inside the file (`# ADR 0004 — Frontend Framework`).
4. Update `web/README.md` link: `ADR 0003` → `ADR 0004`, path updated.
5. `git add web/ docs/adr/0004-frontend-framework.md`
6. Commit: `feat(web): scaffold Next.js frontend + ADR 0004`. Conventional
   Commits, no AI attribution.
7. `git push -u origin feat/frontend-scaffold`
8. `gh pr create --base main --head feat/frontend-scaffold` — body explains
   scope (scaffold + framework ADR only; Makefile + CI integration deferred
   to PR 2; real surfaces deferred to Phase 3 baseline UI PR).

### Queued after scaffold PR merges

- PR 2: `Makefile` targets (`web-dev`, `web-build`, `web-lint`,
  `web-typecheck`), `web` job in `.github/workflows/ci.yml` (typecheck +
  lint + build), Prettier config.
- ADR — API contract between Next.js app and FastAPI service. Lands when
  Phase 3 serving work begins.
- Phase 3 baseline UI: user selector → top-K poster grid + watch history.
  TMDB proxied through FastAPI (API key stays server-side).

### Working-tree etiquette (multi-agent)

- Two agents share one working tree. Branch switches by either agent leave
  the other's untracked files alone (good), but **tracked file edits will
  surface as "modified" under the other agent's branch** (foot-gun).
- Frontend stays scoped to `web/` and `docs/adr/000X-frontend-*.md`. Backend
  owns everything else. CLAUDE.md, Makefile, `.github/`, `src/` are shared
  surfaces — coordinate before touching.

---

## 2026-05-31 — Phase 1 wrap

Phase 1 is done end-to-end. Both baselines are trained, evaluated through the
harness, and visible side-by-side in the `phase-1-baselines` MLflow experiment.

### What shipped

**Decisions (ADRs in `main`):**
- `0001-evaluation-protocol` — cutoff T at 80th-percentile timestamp, 28-day
  holdout, K=10, warm/cold split at 5 train interactions.
- `0002-implicit-feedback-label` — every rating is a positive interaction;
  no rating-value weighting in Phase 1 baselines.

**Code (in `main`):**
- `src/evaluation/` — `recall_at_k`, `ndcg_at_k`, warm/cold slicing, the
  single `evaluate()` entrypoint. Every metric goes through here; nothing
  is computed ad-hoc.
- `src/data/` — `download.py`, `ingest.py` (with `--reset`), `load.py`,
  `schema.py`, `split.py` (`temporal_split` → train / holdout / test).
- `src/models/candidates/popularity.py` — `PopularityModel`, also serves as
  the cold-start fallback.
- `src/models/candidates/cf.py` — `CFModel` (implicit ALS) with embedded
  popularity fallback for cold users. Requests `k + |seen|` candidates from
  ALS and post-filters seen items because
  `filter_already_liked_items=True` only re-ranks, it does not drop.
- `src/training/popularity.py`, `src/training/cf.py` — same skeleton:
  load → temporal_split → fit → recommend → evaluate → log to MLflow.
- `notebooks/eda.py` + `docs/eda.md` — saved findings, not throwaway notebook
  output. `make eda` runs it via `python -m notebooks.eda`.
- 49 unit tests, ruff + black + mypy strict, GitHub Actions CI green.

**Infra:**
- `docker-compose.yml` — postgres, redis, mlflow, prometheus, grafana.
- `infra/postgres-init/01-create-mlflow-db.sql` — creates the separate
  `mlflow` database on a fresh postgres volume.
- `infra/mlflow/Dockerfile` — extends the official MLflow image with
  `psycopg2-binary` so it can reach its Postgres backend store.

**Data:**
- MovieLens 25M downloaded, ingested into Postgres (25,000,095 ratings),
  DVC-tracked at `data/raw/ml-25m`.

**MLflow runs (experiment `phase-1-baselines`):**
- `popularity-baseline`
- `cf-als-baseline` — factors=64, regularization=0.01, iterations=15.

### Open / in-flight

- **PR #15** — `docs/adr/0003-two-stage-architecture.md`. Pins the
  candidate-generator + ranker split before any Phase 2 code lands.
- **PR #16** — CLAUDE.md "Current status" update to mark Phase 1 complete and
  queue Phase 2 next steps. Docs-only.
- **`feat/per-policy-attribution-metrics`** (local, not pushed) —
  `was_served_by_als(user_id) -> bool` predicate added to `CFModel`. The
  intent is to partition holdout users by which policy actually served them
  and log `als_served_*` / `fallback_served_*` metrics so we can isolate
  ALS's contribution from the popularity fallback's. Training-script wiring
  in `src/training/cf.py` is not done. Decide before resuming: finish,
  discard, or rebase onto `main` after #15/#16 merge.
- **Frontend agent work** — `docs/adr/0003-frontend-framework.md` and a
  `web/` scaffold are in the working tree but untracked. The frontend agent
  picked `0003` for its ADR, which collides with PR #15's `0003`. One side
  has to renumber before either lands. Frontend is the natural one to bump
  (PR #15 is older and is a backend decision Phase 2 depends on).

### Findings worth carrying forward

- Cold-user NDCG (~0.49) is much higher than warm-user NDCG (~0.03) for both
  baselines. Cold users are new and rate the canonical popular titles those
  baselines surface; warm users have already consumed that head and are
  looking for tail. Don't read the cold number as a sign the model is good.
- `implicit.als.recommend(filter_already_liked_items=True)` only pushes seen
  items to the tail of the returned ranking, it does not drop them. Whenever
  N approaches the catalog size, seen items leak into top-K. Always
  post-filter.
- `.gitignore` must anchor `models/` as `/models/`. The unanchored form
  silently shadows `src/models/` and untracked model code disappears from
  `git status`.

### Phase 2 — queued

1. ADR 0004 — item-item similarity before two-tower. Why classical first,
   alternatives considered.
2. ADR 0005 — LightGBM over neural ranker. Standard tradeoff but worth
   pinning before the code makes it implicit.
3. Item-item candidate generator + tests + MLflow run in a new
   `phase-2-candidates` experiment.
4. LightGBM ranker on the candidate set. Stage-specific metrics:
   recall@k for candidates, NDCG/MAP for ranker.
5. Watch for leakage: any feature touching post-cutoff data inflates offline
   metrics silently. Point-in-time correctness is the standard.
