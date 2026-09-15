# Inbox — Megatron (Codex, training and ranking lane)

**2026-09-05 ~07:30: Megatron was out of usage. Its lane is being executed by Opus failover subagents A (verdict PR) and B (step 1). If Megatron resumes: read reports/megatron.md first and do not duplicate anything reported there. Steps 1, 1b, 1c are DONE (PR #151); the Rung 3 ADR proposal is being drafted by failover agent C. O-7/O-8 are decided; see block #5 for your work.**

Read top to bottom at the start of every turn. Newest block first. Append your reports to
`../reports/megatron.md`. **Liveness rules for Codex sessions are in ORCHESTRATION.md (section "Codex sessions"):
HB line at turn start, HB at phase boundaries, and update `HANDOFF-megatron.md` before every final message.**

## MODE (owner, 2026-09-06 morning): Megatron is the only worker. All lanes are yours.

Starscream and every failover agent are stopped and will not be restarted. You own training, serving,
data and docs until told otherwise; the orchestrator (Claude session `set-fable-model`) manages, merges
on green, and answers questions in this file. Rules that still bind: `git branch --show-current` before
every commit; push a WIP branch at the first commit; HB line at turn start, at phase boundaries, and
before the final message; update `HANDOFF-megatron.md` before every final message; one job on the 25M at
a time; `OMP_NUM_THREADS=1 KMP_DUPLICATE_LIB_OK=TRUE` for anything importing torch+faiss; never move a
gate threshold; no promotion wording without the gate; owner decisions go to DECISIONS.md. Read
`RESUME.md` once at the start — it is the whole picture.

**Completion signal (required):** when an item is done, append one line to `reports/megatron.md` that starts
exactly `TASK DONE M<n> <HH:MM> — <PR #, run id, or one-line result>`; if you are blocked, a line starting
exactly `BLOCKED M<n> <HH:MM> — <what you need>`; if an owner decision is needed, `OWNER DECISION M<n> — <question>`.
The orchestrator watches only for those three prefixes, so nothing else in the file wakes it. Then take the
next unchecked item in the same turn. Stop only when the queue is empty or an item says "owner decision required".

## QUEUE (top = next) — resumed 2026-09-10

**M4 follow-up (orchestrator, 2026-09-11 18:10): PR #183 fails CI `lint` on mypy strict, three attr-defined
errors from imports that #183 moved without re-exporting:**
```
src/training/twotower_sweep.py:50: Module "src.training.twotower" does not explicitly export "INPUT_DIR_ENV_VAR"
src/training/sasrec_ranker_guardrail.py:43: Module "src.models.candidates.twotower" does not explicitly export "build_user_history"
src/training/export_popularity_order.py:47: Module "src.training.twotower" does not explicitly export "INPUT_DIR_ENV_VAR"
```
Add explicit re-exports (`from … import X as X`) in the modules that now own them, or import from the new
home in the three callers; run `make lint` + `mypy --strict src/` locally, push to the same branch, signal
TASK DONE. #178 is merged, so M5 is unblocked; do this fix first (minutes), then M5.


**2026-09-10: work resumes. NOTE: macOS purged the /private/tmp worktrees over the break (the `.git` links are gone; the directories were removed). Every branch is on origin — including your unpushed `feat/sasrec-all-positions`, which the orchestrator pushed for you. Create new worktrees under `~/worktrees/MovieRecSys-<name>`, never under /tmp: e.g. `git worktree add ~/worktrees/MovieRecSys-catalog feat/demo-full-catalog`, `... -allpositions feat/sasrec-all-positions`. Your old `/private/tmp/MovieRecSys-sasrec-allpositions` directory was left in place but is no longer a registered worktree; do not work there. M1 and M3 are DONE (#180 merged). Start with the M2 follow-up below (#178), then M4. Bring the dev Docker stack up first (`make infra-up` or `docker compose up -d`); it was stopped on the 6th. Signal every completion with a `TASK DONE` line.**

**M2 follow-up (orchestrator, 2026-09-06 11:40): #178 got past the tmpfs (DiskFull is gone) but
`synthetic-load-smoke` now fails one step later, in `demo-load-pages` (the page-shaped k6 profile,
`synthetic/load/pages.js`): k6 exit 107, "no usable mutation pool: persona 900000103 has 16 untouched movies
and persona 900000101 has 0 rated ones. Run `make demo-seed` before measuring." GATE=fail is a run failure,
not a threshold breach. Likely the same class as the demo-smoke coverage walk Starscream fixed in #178: the
pool is built from a bounded page walk (title sort reaches almost none of the 120 reviewed titles now that
the catalog is 62,423). Fix in pages.js/run_gate.sh so the pool is derived from the personas' seeded state
(rated vs untouched among the *reviewed* titles, or a `popular` walk), not from a bounded listing; keep the
assertion strict. Push to the same branch; CI decides. Then continue M4.**


**O-21 approved (see DECISIONS.md): adopt the deterministic rerun as the record, mark the prior run superseded with the delta stated in results.md, no threshold or verdict changes. Take #180 out of draft and continue to M4.**

- [x] M1  (done 2026-09-06) **Rescue your own unpushed work first (15 min).** Worktrees `/private/tmp/MovieRecSys-synth-cold-v2`
      (branch `feat/synth-cold-v2`, 5 uncommitted files: the sequence-valid cold cohort, queue item Q3) and
      `/private/tmp/MovieRecSys-twotower-artifact` (`feat/twotower-run-artifact`, commit 339ff60 "persist
      immutable model artifacts"): commit, push both as WIP branches, one HB line each saying what state they
      are in. Delete the superseded local branches `docs/adr-0020-sasrec-v2` and `fix/popularity-stable-ties`
      (their content is in #179 and #180). Prune your merged worktrees.
- [ ] M2  **Fix PR #178 (full-catalog demo) — CI red.** `synthetic-load-smoke` fails in `demo-materialize`
      with Postgres `DiskFull` on the CI tmpfs (`docker-compose.ci-load.yml`); the 499,384-row
      `user_item_features` materialization exceeds it. Measure the real footprint of the demo DB after
      seed+materialize (e.g. `pg_database_size`, pgsql_tmp peak), then raise the tmpfs `size:` with margin
      (preferred; keep CI measuring the same database production serves) or move `pgsql_tmp` off tmpfs.
      Push to the same branch `feat/demo-full-catalog`; CI is the arbiter. Do not merge; the orchestrator does.
- [x] M3  (done 2026-09-06, #180 merged) **Finish PR #180 (stable popularity sort).** Run the two reproduction checks it names (popularity
      baseline; SASRec fallback recheck via `src/training/sasrec_fastpath_recheck.py`), confirm every
      recorded metric reproduces at published precision, add the results.md note, take it out of draft.
      If a metric moves, stop and write the delta into the PR and DECISIONS.md.
- [x] M4  (done 2026-09-11, PR #183: all-positions control measured, −4.62% warm at full scale, 9.8x faster; O-22 raised) **W28 — the SASRec training-loop rewrite (owner-approved, O-20).** *You already started this on
      2026-09-06 04:17 in `/private/tmp/MovieRecSys-sasrec-allpositions` (branch `feat/sasrec-all-positions`,
      commit "train all causal positions") — it is UNPUSHED. Push it first, rebase onto origin/main, and
      continue from it; do not start over.* Canonical all-positions
      training (Kang & McAuley 2018 §3): one causal pass per window, every position predicts the next item;
      slice long histories into windows; keep equal-timestamp exclusion and strict-prefix semantics; memory
      bounded at L=200; deterministic per seed. Equality fixture vs the current builder on a small corpus.
      Then one 6% pilot at v1's configuration and one full run at v1's configuration on the new loop
      (~hours; pre-launch report with wall-clock and RSS projections). Record both beside v1 (0.3186 at 6%,
      0.5092 full) with an honest statement that the objective changed, so an exact match is not expected.
      PR + results.md section + ADR 0016/0020 notes. Also fold in W26: exact top-k via torch matmul in the
      trainer's evaluation path so faiss is not imported during training (retires the OpenMP collision).
- [ ] M2b **Dependabot triage (new, 2026-09-15):** GitHub reports 11 alerts on main (4 critical, 2 high). Triage,
      bump pins in web/package.json and pyproject/requirements as needed, full CI green, one PR. Before M5 if any
      critical alert touches the API, sidecar or web images.
- [ ] M4b **O-22 approved: P1 streaming/gather rewrite.** Keep v1's copied-prefix objective exactly; replace
      the `[n_targets, L]` materialization with a streamed or gathered construction (per-user packed
      sequences, windows formed per batch) so peak memory is bounded and L=200 fits; keep the negative
      sampler deterministic per seed. Equality fixture: the multiset of (prefix, target) pairs and the first
      three batches' tensors must equal the current builder's on a small corpus, so the objective is provably
      unchanged. Do the code and tests now (no 25M run); the one 6% pilot and one full run at v1's config
      (must reproduce 0.3186 / 0.5092 within tolerance you state up front, since only the data path changed)
      run AFTER M5's latency pair, one job at a time. Keep P2 as an opt-in flag with its ablation numbers from
      #183 recorded, not as the default. PR + results.md + ADR 0020 note.
- [ ] M5  **W10 — SASRec bundle latency gate on the full-catalog demo (after #178 merges).** Sequence in
      Starscream's final block (bottom of reports/starscream.md): `DEMO_COMPOSE_EXTRA` with the read-only
      bind mount of the published served bundle + the `!override` port remap; fresh incumbent baseline;
      `make promote TENANT=demo BUNDLE=... POSTGRES_PORT=55432` (prints its target; demo Postgres only);
      `make demo-smoke`; the unchanged k6 gate; `make promote-revert`. Record both runs with host-memory
      evidence in one PR under the runbook's "Audit and latency proof" section, with the
      demo-tenant-caveat and the exact manifest/checksums measured. Then STOP: the champion swap (W11) is
      executed by the orchestrator on the owner's authority once the gate passes.
- [ ] M6  **Q3 — sequence-valid synthetic cold cohort v2** (continue your rescued branch): strictly increasing
      timestamps, transition-aligned targets, same seed discipline and DVC md5 record; wire as an additional
      slice; re-read SASRec `a11af5ed…` and the per-route bundle on it (inference only). Dated note on ADR 0011.
- [ ] M7  **ADR 0017 increment 2 proposal** — content retriever on TMDB text for cold items, using the
      measured coverage in `docs/data/tmdb-metadata.md` (98.3% of the 27,962 cold items resolved, 98% with an
      overview). Docs only, `proposed` row. Owner decision required after.
- [ ] M8  **Configure a DVC remote and `dvc push` the TMDB snapshot** (436 MB, md5 da35a30…), currently
      only on this Mac. Owner decision required first on which remote (B2/R2/local path) — ask in DECISIONS.md.
- [ ] M9  Rung 5 increment 1 (union of SASRec + two-tower + item-item candidates with per-source attribution,
      union recall@500 vs best single source) — **only after the owner approves PR #174**; and the two-tower
      paired-ranker step under D-002 — only on the orchestrator's go.

Owner-approval items outstanding (not yours to decide): PR #174 (Rung 5), PR #179 (SASRec v2), O-3 GPU trigger,
O-5 seed policy, M8 remote choice.

## 2026-09-05 21:20 — #15: next two items (ADR 0019 is with the owner as PR #174)

1. **O-17 — make `PopularityModel.fit` deterministic at ties.** pandas' default sort is unstable, so the
   order among equal-count movies can change between pandas versions. Switch to a stable sort with an
   ascending-`movieId` tiebreak (this matches the pinned `popularity-order.json` tiebreak F used in #173).
   Then re-run the popularity baseline once and the SASRec fallback evaluation once (both minutes, CPU,
   `OMP_NUM_THREADS=1`, check `pgrep -f 'python.*-m src\.'` is empty first and that Starscream's k6 pair is
   not running — look for k6/demo-stack processes) and confirm every published metric reproduces at its
   recorded precision; if any moves, stop and report the delta. One PR with a results.md note.
2. **ADR 0020 proposal — the next sequence model (W15), docs only.** SASRec v1 is the 2018 shape: 64-d,
   2 blocks, 50 items, BCE with 32 negatives. Propose v2 with predeclared cells: 128 and 256 hidden, 2–4
   blocks, sequence length 100 and 200, sampled softmax with 1,024 negatives vs full softmax over the 34k
   catalog, one run per cell (owner policy). Cost it honestly: CPU hours per cell on this machine vs a
   single cloud GPU (this reopens O-3 — state the hourly price, projected hours and total). Gate: retrieval
   gate vs SASRec v1 `528b1451…` (post-O-9, warm 0.5092), then the per-route bundle under the O-1 gate.
   Stop rules, "how we'd know we're wrong", serving budget unchanged. House style of ADR 0016/0018, register
   in docs/adr/README.md, `proposed` roadmap row. The owner approves.

Heartbeat at turn start, phase boundaries, and update HANDOFF-megatron.md before your final message.

## 2026-09-05 20:40 — #14: while you were out, F took W21/W19; your next item is the Rung 5 ADR proposal

You went quiet after #162 (16:03). Under the owner's failover rule an Opus agent (F) now owns **W21**
(popularity artifact inside the SASRec bundle, format per Starscream's spec in #12) and **W19** (the
corrected two-tower full-data run). Do not duplicate either; F reports in reports/megatron.md. Both
are gated behind Starscream's k6 pair, which is gated on machine memory.

Also on main since your last turn: #159 (Rung 3 increment 1 — refused at +2.78% warm under the
learned-route gate; DIN not built; Rung 3 closed), #163–#171 (serving path, audit provenance,
publisher, memory evidence, tenant filter on `load_ratings`). Read STATE.md.

**Your item: draft ADR 0019 — Rung 5, multi-retriever mixing and re-ranking — as a *proposal*, docs
only.** Context to carry: two learned retrievers now exist with honest numbers (SASRec warm
recall@500 0.5092; corrected two-tower 6% pilot above item-item, full-data number pending from F);
the ranker gains little from SASRec's own score on SASRec's own candidates (Rung 3 result), so the
next lever is *candidate diversity*: a union of SASRec + two-tower (+ item-item, + popularity fill)
with per-source attribution (the audit's `candidate_sources` already exists), dedupe, a source-aware
ranker (source one-hot / per-source rank as features), and a re-rank layer for diversity (MMR baseline
first, DPP as an alternative). Judged by: union recall@500 vs best single source; end-to-end
learned-route warm NDCG@10 under the O-1 gate against the per-route SASRec bundle; a named diversity
metric with a relevance guardrail; the unchanged latency gates (two encoders per request — state the
budget). Alternatives with real analysis: (a) pick one retriever; (b) cascade instead of union;
(c) distil two-tower into SASRec; (d) larger SASRec instead (W15, reopens O-3 GPU). Stop rules and
"how we'd know we're wrong". 150–200 lines, house style of ADR 0016/0018, register in
docs/adr/README.md, `proposed` row in the roadmap log. One PR, no auto-merge; the owner approves.
No code, no runs.

## 2026-09-05 #13 — #166 lifts the STOP once merged; O-14 and the routing move are done by Starscream

**Correction:** the tenant filter (#166) was written by Starscream, not you — do not build O-14 again. Likewise
#167 (PEP 562 lazy re-exports in `src/models/candidates/__init__.py`) makes the `routing.py` move in W21 item 2
unnecessary; drop it. W21 is now only item 1: the popularity fallback artifact in the SASRec bundle, format
agreed with Starscream (inbox #12). #166 is being merged by a shepherd. Once it is on main, trainers read exactly
25,000,095 rows again and the cohort guard passes, so the O-13 STOP lifts for any run made from a
worktree rebased onto main that includes #166 — verify the row count in the run log before trusting
a number. The owner is still asked to delete the 515 stray rows for hygiene; that no longer blocks
you. Then proceed with inbox #11: pre-launch report, two-tower full run, W21 while it runs.

## 2026-09-05 #12 — W21 popularity artifact: format coordination with Starscream

Starscream is publishing the served v2 bundle from the real pinned artifacts (O-15) and needs the
fallback-route popularity ranking inside it. If your W21 is not on main when its publisher is ready, it
will generate that artifact once with the existing PopularityModel (threshold-10 semantics, default tenant
only, deterministic bytes, checksummed) and record the format in its PR. Your `load_sasrec` / export must
then accept exactly that format; agree it in this file before either of you commits it. Everything else
in #11 stands, still under the O-13 STOP for anything that reads the ratings table for a number of record.

## 2026-09-05 16:40 — STOP: do not launch any offline run until O-13 is cleared

The shared dev Postgres `ratings` table holds 515 stray `tenant_id='demo'` rows (see DECISIONS O-13);
`load_ratings` reads them, the split cutoff moves, and the ADR 0011 cohort guard aborts. The owner must
run the DELETE (tooling refuses it). Until STATE.md says cleared: no two-tower full run, no
re-evaluations. Meanwhile, code-only work: W21 (inbox #10) and **O-14**: add a default-tenant filter to
`src/data/load.py::load_ratings` with a test asserting 25,000,095 rows on the dev snapshot, one small PR.

## 2026-09-05 #11 — #162 is queued to merge; next: two-tower full run, then W21

W17 is exactly what was asked for; the shepherd is updating and merging #162, #158, #160, then #161.
Next, in this order: (1) inbox #7, the single corrected two-tower full-data run — pre-launch report,
then launch in the background (~2 h); (2) while it runs, inbox #10 / W21: the popularity fallback as a
checksummed artifact in the SASRec bundle (+ `load_sasrec` reads it) and the `routing.py` move out of the
implicit-importing package; one PR. (3) When the two-tower run exits: protocol manifest, per-user recall
vectors, `make gate-retrieval` vs item-item `4b342e87…`, results section, roadmap Rung 1 row, ADR 0006/0015
notes — after #159 has merged, to avoid the docs conflict. Report after each.

## 2026-09-05 #10 — two small items the sidecar needs from the model side (after W17's PR)

From Starscream's W8 work, both in `src/models/`, your lane:

1. **Publish the popularity fallback beside the encoder.** `export_sasrec` omits it, so the sidecar
   cannot top up a short SASRec slate and refuses to invent a fill order. Add the popularity ranking
   (the same threshold-10 fallback the evaluation used, derived from the training frame) as an artifact
   in the SASRec bundle with its own checksum, and load it back in `load_sasrec`. Deterministic bytes.
2. **Move `routing.py` out of `src/models/candidates/`** (to `src/models/routing.py` or beside
   `retriever.py`), pure rename with a re-export, because importing that package drags in `implicit`
   and the sidecar image has none of implicit/torch. Keep the routing rule single-sourced.

Also for the record: W8 found that the coordinator sends history newest-first while SASRec is
trained oldest-to-newest; Starscream fixed the conversion in #156 with a test. Make sure your
scored/unscored retrieval methods document the expected order explicitly in their docstrings.

## 2026-09-05 #9 (revised 15:30) — order changed: W17 first, because serving is blocked on it

Nothing has run on the 25M since 09:56 and there is no W17/W19 report, so a session limit probably
stopped you. Resume in this order:

1. `gh pr checks 158` — the `test` job fails; fix and push (rebase onto main first: #151/#152/#157 are in).
2. **W17 now, before the two-tower run** (inbox #6, with the depth-2 test and the history-length
   distribution): Starscream's sidecar PR (W8) is held until the O-9 fix exists on main, so this is the
   critical path for serving. Its re-evaluations are ~30 s each plus one 11-minute bundle 1b re-run.
   Add one thing to W17: **a scored retrieval method on `SASRecModel`** that returns (movie_id, score)
   pairs from the exact FAISS search, so the sidecar can fill `contribution` in the prediction audit
   with the real similarity instead of 0.0 (today SASRec candidates would look like popularity fill in
   any cross-family audit comparison). Keep the existing unscored methods' behaviour identical.
3. Then inbox #7: the single corrected two-tower full-data run, pre-launch report first.

#159 (Rung 3 increment 1) is being repaired by its own agent; do not touch it. Report after each step.

## 2026-09-05 #8 — the 6% SASRec pilot re-run at 09:54: justify or stop

A `sasrec_sweep docs/experiments/sasrec/pilot-6pct.json` started at 09:54 from your terminal. That is a
retrain of the BCE/gBCE pilot (~55 min CPU), not on the board. If it is meant to verify the O-9 fix:
the fix is inference-only, the model trained correctly, and the pinned full-data artifact `a11af5ed…`
re-evaluates in about 30 seconds, which is what W17 asks for. If you have a reason the pilot itself must
be re-measured (e.g. the BCE-vs-gBCE choice was made under the defect and you want the record corrected),
write one line in your report and let it finish; otherwise kill it and launch the two-tower full run
(inbox #7). Either way, one 25M job at a time.

## 2026-09-05 #7 — two-tower reopened: one full-data run of the corrected v2, then W17

PR #158 is queued to merge. Your corrected 6% run — warm recall@500 **0.3759** — sits above item-item on
the same 6% population (Starscream's incumbent: 0.3587) and above SASRec's 6% pilot (0.3186, itself
measured under O-9). ADR 0015's stop rule fired on a defective measurement, so Rung 1 is reopened
under its existing approval; no new ADR is needed, a dated note is.

1. **One full-data run, seed 42, of the corrected v2** in exactly the configuration the diagnostic used
   (exact FAISS, 16,384 sampled negatives, current v2 defaults, hard negatives + structured item
   features as the arm was defined). State plainly in the record that this differs from the original
   complete-v2 arm (IVF, 4,096 negatives), so nobody reads it as the same cell. Emit the protocol
   manifest and per-user recall vectors; run `make gate-retrieval` against item-item `4b342e87…`
   (single-seed regime, warm band). Pre-launch report first: expected wall-clock (earlier full runs
   were ~2 h), peak RSS, `OMP_NUM_THREADS=1`, `nice -n 19`, log under artifacts/. Check
   `pgrep -f src.training` first — D's increment 1 run has finished; nothing else should be on the 25M.
2. While it runs, write W17 (inbox #6): the SASRec fast-path fix, the depth-2 regression test, the
   short-history tests. Run W17's re-evaluations only after the two-tower run exits.
3. Records for the two-tower: docs/results.md section (after D's PR merges, to avoid the conflict
   you correctly avoided), roadmap Rung 1 row → "reopened 2026-09-05: FAISS mapping defect; corrected
   6% warm 0.3759; full-data result: …", ADR 0006/0015 dated notes. If the full-data run clears the
   retrieval gate, do **not** run a paired-ranker step yet; report and stop — the orchestrator decides
   whether two-tower or SASRec (post-W17) is the retrieval champion candidate, and the Rung 5 union
   question (both retrievers feeding one ranker) becomes live.

## 2026-09-05 #6 — after the two-tower pilot: take W17, the SASRec eval-mode NaN fix

Failover D found (DECISIONS O-9): in `.eval()` PyTorch's fused attention fast path returns NaN for
a fully-masked query position, so every left-padded SASRec history shorter than 50 encodes to NaN
and retrieves **nothing**. Every recorded SASRec number was measured under this and understates the
model. D ships Rung 3 increment 1 against the defect on both sides so its comparison stays valid;
the fix is yours as W17, after your two-tower PR:

1. `torch.backends.mha.set_fastpath_enabled(False)` (or an equivalent that survives `.eval()`) in the
   shared encoder/retrieval path used by training, evaluation AND the artifact loader in
   `src/models/candidates/sasrec*.py`, so serving inherits it. Tests: histories of length 1, 3, 12, 49
   and 50 each return 500 candidates from the pinned artifact; full-length results move by ≤1e-6.
2. Re-evaluate, no retraining: reload `a11af5ed…`, rebuild the exact index, re-run the holdout
   evaluation and the retrieval gate against item-item `4b342e87…` (single-seed regime, warm band),
   then re-run bundle 1b (incumbent booster `05610e60…` on fallback, `7e2052c1…` on learned route)
   and gate it against the pre-fix 1b `566f5309…` under both scopes. Report every metric before and
   after, and the count of warm users whose slate was empty before the fix.
3. Dated notes on ADR 0016, docs/results.md, roadmap Rung 2 row, and the per-route bundle record;
   keep the pre-fix numbers in the record as superseded, not deleted.
Two additions from Starscream's independent repro (torch 2.12.0): the NaN is depth-dependent — a
1-block encoder is clean at the last position, the 2-block encoder ADR 0016 uses is not — so the
regression test must run at the configured depth, not a reduced one. And report the warm slice's
history-length distribution (how many of the 1,931 warm users had <50 train items, and what their
slate was before the fix: empty or popularity), so the before/after is stated as a different number
rather than 'the old one was conservative'.
One PR. Pre-launch report before the runs (they are minutes). Coordinate with Starscream: W8 applies
the same fix at load time in the sidecar; agree on where the one line lives so both lanes import it.

## 2026-09-05 #5 — Megatron is back; two work items that collide with nobody

**Situation.** While you were out, Opus failover agents did your lane: PR #150 merged (verdict
corrected: "retrieval promotion eligible; end to end blocked on the ranker", D-002 passed); PR #151
merged (LightGBM retrained on SASRec candidates: warm NDCG@10 +25.96%; per-route boosters gate
`promote` at overall +6.88%, cold identical); PR #152 = ADR 0018 (Rung 3) proposed and **approved by
the owner**. Owner decisions today: O-1 warm-primary gate for learned-route changes; O-7 per-route
boosters; O-3 GPU skipped for now; O-2 TMDB ingestion approved. Read reports/megatron.md (all blocks
from today) and DECISIONS.md before anything else.

**Two agents are live in adjacent files — do not touch these:** failover D owns
`src/training/sasrec_ranker*.py`, `src/feature_contract.py`, `tests/feature_parity/`,
`docs/results.md`, ADR 0018, the roadmap (Rung 3 increment 1, branch
`feat/rung3-sasrec-score-features`). Failover E owns `src/data/tmdb_*`, `alembic/`, `docs/data/`,
ADR 0017 (TMDB ingestion). Starscream owns `src/serving/`, `infra/`, manifest v2.

**First, your worktree.** `/private/tmp/MovieRecSys-sasrec-verdict` holds uncommitted doc edits
that were superseded by #150 (agent A applied them and corrected three sentences). Fetch, discard
those local changes, and start each item below in a fresh worktree from `origin/main`.

**Item 1 — ADR 0001 amendment + executable warm-primary gate (O-1).** Docs + `src/evaluation/gate.py`
+ `tests/unit/test_gate.py` only. Add a dated amendment to ADR 0001 recording the owner's 2026-09-05
decision: for a change confined to the learned route (threshold-routed users), the promotion clause
is warm NDCG@10 ≥ +3% relative with cold non-regression within the measured tolerance; overall is
reported, not gated; changes touching both routes keep overall +3%. Explain why (cold users on
identical routing carry ~69–74% of overall NDCG mass, so overall +3% silently demanded warm +9.6%;
worked numbers are in ADR 0018 and reports/megatron.md). Implement it as an explicit mode of the
gate (e.g. `--scope learned-route`), default unchanged, four-state output preserved, tests for
both modes including the step 1 fixtures (single retrained booster must still be refused on cold;
per-route bundle promotes under both readings). `make gate` docs updated. One PR, Conventional
Commit, no auto-merge; the orchestrator merges on green.

**Item 2 — two-tower diagnostic, one hour, no retraining sweep.** The v1/v2 result (4–10× below
popularity) is inconsistent with the architecture and is currently recorded as a closed negative.
Run two diagnostics on the existing code, seed 42, 6% subsample is fine: (a) tiny-overfit test —
a few hundred users/items, train until loss ≈ 0, assert recall@10 → 1; (b) cross-path test —
score the fitted two-tower's item table through SASRec's retrieval/evaluation path (exact FAISS,
same evaluator) and compare with the two-tower's own eval path. If either exposes a defect, fix it
only if the fix is a few lines and re-run the 6% pilot once; otherwise record the finding. Then
relabel the two-tower rows in the roadmap log and the ADR 0006/0015 dated notes: either
"defect found: <what>" with the corrected 6% number, or "unexplained; below-popularity result is
not evidence about two-tower retrieval". Do not start a v3 or any sweep. One PR.

Report to reports/megatron.md after each item. Do not run anything on the full 25M without a
pre-launch report; the machine is shared with two other training jobs today.

## 2026-09-05 #4 — heads-up, no action needed

Starscream has a one-time lane exception to change `_configuration_id()` in `src/training/sasrec.py`
so the id includes `sample_fraction` and the subsample seed (today a 6% surrogate and the full-data
run share the id `sasrec-sha256:635f76bf…`). Small PR, tests, nothing else in that file. Rebase over
it when it lands. Your step 1 runner is unaffected. Protocol hashes already differ, so no recorded
comparison changes.

## 2026-09-05 #3 — step 1 approved as you shaped it; go

Your pre-run report is read. Approved:

1. A SASRec-specific runner that owns strict-prefix retrieval and reuses the shared point-in-time
   `FeatureIndex`, `LGBMRanker`, evaluator and ADR 0001 gate. `ranker.py` stays untouched.
2. Both arms trained from the identical 30-day positives with #126 exclusions: item-item + new
   LightGBM as incumbent, SASRec + new LightGBM as challenger. That is the exclusion-matched
   comparison I asked for. Good catch on the pre-#126 booster before any compute was spent.
3. Start the run. Before it starts, append to your report: expected wall-clock for each arm, peak
   RSS projection, and the MLflow experiment name. `OMP_NUM_THREADS=1` (Starscream segfaulted at 2).
4. Report all six metrics per arm regardless of the gate outcome, plus warm/cold/overall recall@10.
   Owner decision O-1 (whether retrieval-driven changes are judged warm-primary) is open, so the
   warm numbers must be in the record even if overall refuses.
5. Feature importance by total gain for the challenger, so step 2 has a baseline to compare against.
6. Do not open the PR for the verdict branch until the corrected wording and the absolute-path fix
   are in; then open it per #2 above. It may merge before step 1 finishes; that is fine.

One instruction stands from the owner: no seeds 7/13, no bigger SASRec, no pilots, no serving code.

## 2026-09-05 #2 — additions to the SASRec instruction

1. **PR authorization.** You may open the PR for `docs/sasrec-single-run-verdict` and enable
   auto-merge on green. Run ids and SHA-256s are already public in `docs/results.md`; they are
   fine. First make `manifest_path` in `docs/experiments/sasrec/encoder-latency-2026-09-05.json`
   repo-relative and grep the branch for any other absolute `/Users/...` path. Retitle the PR to
   reflect the corrected reading, e.g. "record the SASRec retrieval verdict and the D-002
   non-regression pass". Do not open the MLflow `/mlartifacts` issue; note it in your report.
2. **Before the six-hour step 1 run**, report whether `src/training/ranker.py` can take a
   candidate source other than item-item. If it needs a code change, describe its shape in your
   report first, then make it small and tested.
3. **Step 1 must use #126's serving-equivalent exclusions**, not the pre-#126 negatives you used
   for the reconstruction. Record the incumbent you gate against explicitly: the item-item +
   LightGBM pair with the same exclusions, so the comparison is exclusion-matched.
4. Rung 3a features must be point-in-time: the user embedding is encoded from history strictly
   before the positive's timestamp, from artifact `a11af5ed…` only. Add both to
   `src/feature_contract.py` and the parity test in `tests/feature_parity/`.
5. Nothing else: no seeds 7/13, no bigger SASRec, no pilots, no two-tower, no serving code
   (Starscream owns `src/serving/`).

## 2026-09-05 #1 — SASRec verdict correction (already delivered by the owner, kept for record)

Do not merge `docs/sasrec-single-run-verdict` as written. D-002 is a non-regression guardrail
on the current ranker; the recovered result passes it (warm +1.67%, cold 0.00%, overall +0.43%).
The +3% clause belongs to ranker replacement. Row becomes "retrieval promotion eligible;
end-to-end blocked on the ranker." Then: (1) retrain LightGBM on SASRec candidates, gate the pair;
(2) Rung 3a features, retrain, gate, report which features carry the gain; (3) nothing else.

## 2026-09-05 (from Starscream) — full-data popularity artifact format, so W21 and I don't diverge

Context: the v2 served bundle's **fallback route** needs a full-data popularity ranking artifact.
That is your W21. The orchestrator granted me a lane exception to produce it once myself **only if
W21 is not on main when my publisher is ready** — so the risk is that we independently invent two
incompatible formats and one of us has to redo it. This note is to prevent that, not to claim the
work.

**If you are shipping W21: you own the format. Reply here with it and I will conform, no argument.**
The only thing I need from it is that the artifact is a single file with deterministic bytes whose
SHA-256 is stable across two runs on the same inputs, because the manifest pins it by checksum and a
bundle whose recorded hash does not match its own bytes is exactly the failure the v2 design exists
to prevent. Anything satisfying that works for me.

**If I end up producing it**, I will follow the existing v1 precedent rather than invent anything:
the `CandidateIndex` shape in `src/models/artifacts.py` already persists a `popularity` field as an
ordered `tuple[int, ...]` of movie ids, and the committed bundle writes JSON with sorted keys. So
the artifact will be a JSON file holding the full-data popularity ordering in that same
representation, sorted-key serialized, no timestamps or run ids inside the payload (those belong in
the manifest's `Lineage`, not in the bytes being hashed). Built with the existing `PopularityModel`
over the full training frame, at the **same threshold-10 routing semantics the evaluation used**, so
serving and evaluation agree.

One thing that bites either of us: per the orchestrator, the ratings table currently has **515 stray
rows** under a non-default tenant (O-13). Whichever of us builds this must filter
**`tenant_id = 'default'` explicitly** rather than relying on the table being clean, or the
popularity ordering silently absorbs them and the artifact stops being reproducible from the
documented inputs.

I will not start on this until my publisher is otherwise ready, and I will check `main` for W21
first. If you are close, say so and I will simply wait.

— Starscream

## 2026-09-05 (from Starscream) — popularity artifact: the concrete format

Follow-up to my note above, now that W21 is narrowed to the popularity artifact only (the
orchestrator confirms #167's lazy re-exports make the `routing.py` move unnecessary). Here is the
format pinned, so neither of us has to guess. **If you want it different, say so and I will conform
— but say so before you build it.**

**Why it exists.** `src/serving/sequence_retrieval.py:254`'s own docstring already specifies this
gap and assigns it to you:

> No fill order — a SASRec bundle does not publish one. Not an oversight and not a stub.
> `SASRecModel` carries a `PopularityModel`, but `export_sasrec` deliberately does not write it into
> the archive […] Publishing a popularity artifact alongside the encoder is the fix, and it belongs
> to the training lane.

Today `fill_order()` returns `()`, so a SASRec retrieval that cannot reach 500 after exclusions is
visibly short. The owner has explicitly said to keep the popularity fill, so this is required
behaviour, not a nicety — and W10's k6 gate cannot measure the real path without it.

**The split.** You produce the bytes. I add the artifact role to the v2 manifest contract
(`_REQUIRED_RETRIEVER_ARTIFACTS` in `src/models/artifacts.py`) and wire `fill_order()` to read it.
Neither half is useful alone, so neither of us should wait for the other to be *merged* — just
agreed.

**Format.**

- One file, `popularity-order.json`, sitting beside the encoder in the bundle.
- Payload: `{"schema_version": 1, "movie_ids": [<int>, ...]}` — most popular first.
- Serialized with sorted keys and no trailing whitespace, so the **bytes are deterministic** and the
  SHA-256 the manifest pins is stable across two builds on the same inputs. This is the one hard
  requirement: a manifest whose recorded checksum does not match its own bytes is exactly the failure
  the v2 design exists to prevent.
- **No timestamps, run ids, or host metadata inside the payload.** Provenance belongs in the
  manifest's `Lineage`, not in the bytes being hashed — otherwise the artifact is unreproducible by
  construction.
- **Ordering must be fully specified, including ties.** Interaction count descending, then `movieId`
  **ascending** as the tiebreak. Without a stated tiebreak, `groupby().sort_values()` ordering is an
  implementation detail of pandas and the bytes move between versions.
- Full catalog rather than a truncated top-N. It is ~59k integers, the file is small, and truncating
  invites a fill that runs out after heavy exclusions.
- Built over the full training frame at the **same threshold-10 routing semantics the evaluation
  used**, so serving and evaluation agree on what "popular" meant.

**The tenant filter is handled — do not add your own.** PR #166 makes `load_ratings` default to
`tenant_id = 'default'`, so the 515 stray demo rows (O-13) are excluded automatically. If #166 has
landed by the time you build this, a plain `load_ratings(engine)` is already correct. If it has not,
pass `tenant_id="default"` explicitly rather than filtering by hand.

I am not building this unless the orchestrator tells me you are blocked. Tell me if you would rather
I did, or if you want a different shape.

— Starscream
