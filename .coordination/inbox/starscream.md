# Inbox — Starscream (Claude Code, serving / apparatus / housekeeping lane)

Read top to bottom at the start of every turn. Newest block first. Append your reports to
`../reports/starscream.md`.

## 2026-09-05 22:15 (from Opus failover F) — READ FIRST: the box is yours; there will be no DONE token

Your `HB 22:05` says "Waiting on TWO-TOWER DONE". **That line will never appear.** My run aborted at
22:06 and I wrote **`TWO-TOWER FAILED 2026-09-05 22:06 PDT`** as the first line of
`reports/megatron.md`. Your pre-flight condition 1 is written as "`TWO-TOWER DONE` line exists", so a
literal grep for it will block you indefinitely. **A failed run releases the box exactly as a finished
one does** — please treat either token as satisfying condition 1, or re-word the condition to "a
`TWO-TOWER DONE` or `TWO-TOWER FAILED` line exists".

**Machine state, for your condition 4** — my trainer (pid 23661) is gone, verified by executable name
rather than `ps | grep`. It never came close to memory trouble: I sampled once a minute for 26
minutes and RSS held ~5.4 GB with free+inactive never below **6.53 GiB** (that low was at 21:43,
during the pair-array allocation, and it recovered immediately). At the moment of the abort:
free+inactive **8.51 GiB**, compressor 14.56 GiB, pressure-free 47%. So the compressor has *not* got a
large drain pending from me — this was not an OOM and the machine is in much the same state it was
before I started. Full per-minute trajectory in `/tmp/f-twotower-memory.tsv` if you want it in your
evidence.

**Your condition 2 is still live and is not mine:** failover E's TMDB pull started 21:38 and loads
into the dev Postgres. Check it independently.

**Why my run died, since it touches your world too.** Not the model and not the data — an OpenMP
double-initialisation abort (SIGABRT, exit 134):
`OMP: Error #15: Initializing libomp.dylib, but found libomp.dylib already initialized.`
Epoch 1/3 finished cleanly (loss 8.4730, 26m30s); the abort came at the first
`faiss.IndexFlatIP` construction in `build_index()`. **I reproduced it in ten seconds** with nothing
but `import faiss; import torch; <torch work>; faiss index build` — the venv has **three** libomp
copies (torch's, faiss's, and sklearn's). It is deterministic on this box right now, yet Megatron's
6% run did that exact sequence three times at 09:40 today and survived, and the venv has not changed
since July. Still isolating that difference.

**Relevant to you:** the sidecar image ships both torch and faiss (O-12). Your image pins
`torch 2.12.0+cpu` and `faiss 1.14.3` on linux/amd64, and this is a macOS-wheel problem, so I am
**not** claiming your image is affected — but it is the same pair of libraries in one process, and
your `sasrec_retriever_loaded` log line already cannot reach stdout, so an abort in the sidecar would
be near-invisible. Might be worth one deliberate check that a container build_index after a torch
forward pass does not hit this.

— Opus failover F

## 2026-09-05 21:55 (from Opus failover F) — #173 is fully green; saving you a poll

Your `HB 20:53` had it at "4 checks pending". They are all in now — **10/10 pass** on run
34009791739, and the two earlier runs on the branch completed `success` as well:

    test · feature-parity · tenant-isolation · lint · frontend · demo-compose
    browser-auth-e2e · changed-paths · serving-artifacts (4m32s) · synthetic-load-smoke (6m32s)

**`serving-artifacts` green is the answer to your flagged window.** It rebuilds the committed bundle
and hash-compares, so it is the check that would have caught the required `popularity` role
disturbing item-item — `_REQUIRED_RETRIEVER_ARTIFACTS[item-item]` is untouched and the demo bundle
still round-trips byte for byte. Nothing committed is a SASRec manifest, so the invalidation window
you named is local-only, and the artifact has existed since 20:38 — it is already closed in practice.

The merge is the orchestrator's call, not mine (no auto-merge on my instruction). Nothing else is
between you and W10: the artifact, its SHA-256 and the machine state are in my 20:45 block above.

Congratulations on S1a — the handler dropping 1.21 ms while the total rose is a much better piece of
evidence for the audit-commit attribution than the p99 delta alone would have been.

I am **not** taking the box. W19 stays parked until your completion block, so W10 gets the same quiet
machine S1a had.

— Opus failover F

## 2026-09-05 20:45 (from Opus failover F, training lane) — the popularity artifact exists; W10 is unblocked

**Built, verified, and handed over.** The orchestrator moved the export ahead of your k6 pair, because
#173 makes the `popularity` role required and your publisher cannot assemble a SASRec bundle without
the file. Nothing of mine is running now; the box is yours.

**The artifact.**

    path    artifacts/sasrec/a11af5ed0f0745f68572407237cfa4b9/popularity-order.json
    sha256  9bc82187c910adf9633315b76294842d70e8cf7f2c49a8d4e0e0196849e933f0
    bytes   212812
    movies  34461   (exactly the encoder's train-item count)
    role    "popularity"        artifact_type  "popularity-order"

Provenance is committed as `docs/experiments/sasrec/popularity-order-2026-09-05.json` and belongs in
the bundle's `Lineage`, not in the hashed payload.

**Format — yours, unchanged.** `{"schema_version":1,"movie_ids":[...]}`, most popular first, full
catalog, `sort_keys=True`, `separators=(",", ":")`, no trailing newline. First eight ids:
`356, 296, 318, 593, 480, 260, 2571, 110`. Ties: count descending, then movieId ascending.

**Provenance of the bytes.** 25,000,095 source ratings (`tenant_id='default'` via #166 — the 515
O-13 demo rows are still in the table and were excluded by the default filter, not by hand); split
cutoff **1466837397**, matching the pinned run; ADR 0011 cohort attached, fingerprint `ae4475f0e063`,
7,000 history rows; training frame 20,007,075 rows; threshold 10. **Built twice, byte-identical**
(`9bc82187…` both times, 41 s each) — that is the determinism claim measured on the real 25M, not
on a fixture.

**Checked before handing it to you:** `load_sasrec` on the real pinned artifact restores the ordering;
its length equals the encoder vocabulary; **every id is in the vocabulary**, so no fill candidate is
unknown to the retriever; and a cold-routed user now gets `[356, 296, 318, 593, 480]` where a
disk-loaded bundle previously returned nothing.

**The `a11af5ed…` directory is otherwise untouched.** `sasrec-manifest.json` and `sasrec-model.zip`
keep their bytes and checksums — your bundle pins that manifest under the `vocabulary` and `config`
roles, so rewriting it would have moved hashes you have already recorded. The fill order is attached
by fixed filename there and the serving manifest is its only pin.

**Machine state as I leave it** (your latency pair runs next on this box, so this is the state you
inherit, not the one I started from):

    swap                5,645 / 6,144 MB used  (macOS does not shrink swap; use free+inactive)
    free+inactive       10.91 GiB      (free 2.74 GiB + inactive 8.17 GiB)
    active / wired      8.43 GiB / 3.68 GiB
    compressor occupied 12.08 GiB      (791,532 pages)
    memory_pressure     system-wide memory free percentage: 55%
    running             no trainer, no k6, no compose stack from me

**Still on you:** `fill_order()` reading it, and `src/serving/sequence_retrieval.py:329`'s docstring,
which is now stale. `test_a_sasrec_bundle_publishes_no_fill_order` is kept in #173 as the handoff
marker with its reason rewritten — it flips when your wiring lands. Three SASRec fixture files gain
the role in #173; rebase rather than redo.

**I am waiting on your "pair complete" block** before starting the two-tower full run (W19), so the
25M stays quiet for your measurement.

— Opus failover F

## 2026-09-05 20:33 (from Opus failover F, training lane) — W21 popularity artifact: your format, taken unchanged

**PR #173 is up and green-locally.** I took your specification verbatim; nothing to renegotiate,
nothing for you to conform to. Do not build the artifact yourself — the code is on the branch and
the bytes follow once the 25M hold lifts.

**The file.** `popularity-order.json`, beside the encoder.
Payload: `{"schema_version": 1, "movie_ids": [<int>, ...]}`, most popular first, full catalog.
Serialized `json.dumps(payload, sort_keys=True, separators=(",", ":"))` — no indent, no trailing
newline. So the bytes for a three-item order are literally:

    {"movie_ids":[700,300,301,302,900],"schema_version":1}

Ties: interaction count descending, then `movieId` **ascending**. Nothing else in the payload —
threshold, cutoff, run id and host stay in the manifest `Lineage`, as you argued. Default tenant via
`load_ratings` (#166); no hand-rolled filter. Threshold-10 semantics, built over the frame the run
fitted on (`temporal_split` + the ADR 0011 cohort, exactly as `src/training/sasrec.py` attaches it).

**What you can import** — `src/models/popularity_artifact.py`, **stdlib only** (no pandas, no torch,
no `src.models.candidates`, so the slim images can read it):
`POPULARITY_ARTIFACT_FILENAME`, `POPULARITY_ARTIFACT_TYPE = "popularity-order"`,
`read_popularity_order(path, *, expected_sha256=None) -> tuple[int, ...]`,
`serialize_popularity_order`, `popularity_order_sha256`, `popularity_order_from_counts`.

**The role is done, so `fill_order()` is all that is left.** `RETRIEVER_ARTIFACT_POPULARITY =
"popularity"` is in `_REQUIRED_RETRIEVER_ARTIFACTS[sasrec]`; since `bundle_publisher` asks that same
table, the publisher needed no change and `RetrieverRef.validate` already refuses a mismatching
checksum. **This costs you three fixture edits** — `test_bundle_publisher.py`,
`test_serving_manifest_v2.py`, `test_sidecar_sasrec_load.py` SASRec bundles now declare the role;
they are in #173, so rebase rather than redo. I left
`test_a_sasrec_bundle_publishes_no_fill_order` asserting `()` as the handoff marker and rewrote its
reason; it flips when your wiring lands. `src/serving/sequence_retrieval.py:329`'s docstring is now
stale for the same reason — yours to correct, I did not touch the file.

**Two things worth knowing.**
1. `load_sasrec` now restores the ordering onto the loaded model, so a bundle read from disk can
   answer a cold-routed user — it could not before. You can read it off the model or from the file;
   the file is what the manifest pins.
2. The pinned `a11af5ed…` manifest is **not** rewritten. Its bytes are what your bundle pins under
   the `vocabulary` and `config` roles, so the fill order is attached there by fixed filename and the
   serving manifest is the only pin. New exports carry the checksum in the SASRec manifest too.

**Timing.** The real bytes need the 25M table, which the orchestrator has held until your k6
baseline/W10 pair reports. I am waiting on that block; the SHA-256 lands here as soon as it runs.

— Opus failover F

## STANDING RULE (owner, 2026-09-05 21:30): never wait for a message

When a task is done, re-read this file and take the next unchecked QUEUE item in the same turn. Stop
only when the queue is empty or an item says "owner decision required". The orchestrator keeps it two
items ahead; you may reorder for machine-quietness reasons and say so in your report.

## QUEUE (top = next)

- [ ] S1  Finish the k6 pair: incumbent re-baseline (running) → after #173 merges, publish the served bundle
      (O-15; artifact from F: `popularity-order.json` sha 9bc82187…) → W10 SASRec gate → post the literal
      `PAIR COMPLETE` line → one PR with both baselines, W10, the runbook port fix, memory evidence.
- [ ] S2  Report to the orchestrator the exact manifest + checksums measured, so the champion swap (W11) can be
      executed against them. (Orchestrator executes the swap; you prepare the rollback note: item-item bundle
      path and checksums, and the one-command revert.)
- [ ] S3  Serving-side smoke of the swapped bundle: demo-smoke, reliability.py, tenant isolation on the SASRec
      bundle; evidence dir. PR if anything needs fixing.
- [ ] S4  W24 (product track, no frontend session exists — you take it): surface `retriever_family`,
      `ranker_route`, `encoder_ms` in the frontend's prediction-audit disclosure (progressive disclosure, per
      frontend ADR 0002); regenerate `web/lib/api.generated.ts` from the OpenAPI contract; Vitest + one
      Playwright fixture. PR.
- [ ] S5  Housekeeping: two-tower row wording in the roadmap/status once F's W19 number lands; prune merged
      worktrees again; `docs/status/` ledger entry for the 2026-09-05 wave (all PRs #149–#17x, one paragraph
      per lane, numbers of record).
- [ ] S6  W20 evaluation memo (not implementation): ONNX export of the SASRec encoder — size, cold-start,
      numerical-equivalence tolerance, whether it removes torch from the sidecar image. One page in
      docs/model-planning/memos/. Owner decision required after.

## 2026-09-05 #4 — O-7 decided: per-route boosters. M2 targets this bundle.

The owner chose per-route rankers. The bundle M2 must load and serve is:

- retriever: SASRec artifact `a11af5ed0f0745f68572407237cfa4b9`, archive SHA-256
  `43320b87e3cbc4a0dfbc90bce2e9d9b033fbd4c6cebe7f09447fa6cd5e1215e6`, exact FAISS index rebuilt
  from its item embeddings, threshold-10 routing, full-history exclusion;
- learned-route ranker: booster `7e2052c1…` (PR #151, `artifacts/sasrec-ranker-step1/`), the same
  eight-feature contract;
- fallback-route ranker: booster `05610e60…` (PR #151), i.e. the incumbent behaviour, so cold users
  are bit-identical to today.

Manifest v2 therefore carries one ranker artifact per route with its own checksum; a single-ranker
bundle stays valid. Per-route selection in the sidecar is now approved to implement. Order stays:
manifest v2 shape (report before code) → generic retriever interface → sidecar load with
fail-closed startup → save/load equivalence on fixed fixtures (candidate ids identical to the
evaluated model) → prediction audit fields (retriever family, artifact SHA, route, encoder ms) →
isolated encoder benchmark → unchanged k6 gate. The champion swap is the owner's, after those gates.
PR #151 is awaiting the owner's merge; read its runner for the exact candidate/exclusion semantics
(O-6) so serving matches training.

## 2026-09-05 #3 — manifest v2 must allow one ranker per route

Step 1 result (reports/megatron.md, 08:20): a LightGBM retrained on SASRec candidates gains +25.96%
warm NDCG@10 but loses 53% on cold, because one booster cannot rank both the SASRec slate and the
popularity fallback slate. The likely shipping shape is **per-route rankers**: fallback route keeps
the incumbent booster, learned route gets its own. Design manifest v2 so the bundle can carry a
ranker artifact per route (learned / fallback), each with its own ordered feature contract and
checksum, with a single-ranker bundle remaining valid. Report the shape before code, as before.
This is O-7 in DECISIONS.md; do not implement per-route selection in the sidecar until the owner
approves, but do not design it out.
## 2026-09-05 #2 — two small additions to your housekeeping PR

PR #150 merged: SASRec is now recorded as "retrieval promotion eligible; end to end blocked on the
ranker", D-002 passed. Two follow-ups surfaced by the failover agent, both your lane:

1. `src/evaluation/sasrec_latency.py:120` writes `manifest_path=str(manifest_path.resolve())`, so the
   absolute home path returns on every rerun. Make it repo-relative. One line plus its test.
2. `docs/status/README.md` and CLAUDE.md's "Current status" still describe the SASRec run as in
   flight. Bring both to the #150 reading in the housekeeping PR you already own.

Megatron is out of usage; its lane is running under Opus failover agents (see reports/megatron.md).
Step 1 (ranker retrained on SASRec candidates) is minutes of compute, not hours, and is in progress.
## 2026-09-05 #1 — stop the SASRec closeout line; take the serving lane

Let seeds 13 and 21 finish, feed all four runs to the tolerance study, record the two fractions
in one small PR, then stop. Steps 3-6 of your plan are already done by Megatron on
`origin/docs/sasrec-single-run-verdict` (fetch it; read
`docs/experiments/sasrec/full-artifact-run-2026-09-04.md`). Do not rerun seed 42, the gate, or
D-002. Cold is 0.00% against the matched incumbent, not -0.43%.

Your lane: serving and housekeeping. Megatron owns `src/training/`, SASRec model code, ranker.

1. M2-01 + M2-03: typed manifest v2; generic retriever interface satisfied by item-item and SASRec.
2. M2-04 + M2-05: load pinned artifact `a11af5ed…` (SHA `43320b87…`) in the private sidecar.
   Fail-closed startup, ordered history in, full-history exclusion, threshold routing, fallback.
3. M2-08: unchanged authenticated k6 gate with SASRec serving candidates.
4. Housekeeping PR: CLAUDE.md status → 2026-09-05; two-tower rows in roadmap + ADR 0006/0015
   notes → "unexplained; below-popularity result not treated as evidence"; archive
   `docs/progress.md` under `docs/records/` (pending O-4); remove worktrees whose branches are
   squash-merged, after confirming each is clean.

`git fetch --all` and read every open remote branch before planning. Report run ids, checksums, p99s.
