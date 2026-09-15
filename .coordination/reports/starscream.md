# Reports — Starscream

Newest first. Append after every turn or run.

## 2026-09-05 — W8 and O-12 up; a bug of mine found and fixed

**PRs open:** #154 (manifest v2), #156 (retriever contract, **now with a fix**), #160 (O-12 image
pins), #161 (W8 sidecar). #161 is **merge-held until W17 is on main** — a SASRec bundle refuses to
boot today by design, naming the module and symbol it needs.

**A defect in my own #156, found by building its consumer.** The coordinator sends
`positive_history_movie_ids` newest-first (`recommendations.py:100`, `ORDER BY watched_at DESC`,
for item-item's seed attribution); SASRec trains oldest-to-newest. `SASRecRetriever` did
`seeds[-max_sequence_length:]`, which on a newest-first list keeps the **oldest** window and feeds
it reversed. Two wrong things, neither raising. Verified both halves against the source before
touching anything, fixed, and pinned with a test asserting the encoder sees oldest-to-newest *and*
that the window holds the newest titles. It would have shipped.

**O-9 is worse than the serving path.** W8's first equivalence run failed on the *offline*
`recommend_from_history` returning `[]`. The boundary is sharp, not gradual: at two blocks every
length 1–49 is NaN, 50 is clean. W17 putting the fix in `SASRecEncoder.encode_positions` is the
right place.

**O-12 measured rather than extrapolated.** linux/amd64 image is **1.78 GB** — torch 754 MB, faiss
66 MB — with **0 nvidia packages** and `torch 2.12.0+cpu` verified inside the built image. Two
guards: the Dockerfile fails on any `nvidia-*` package, CI fails above a 2.2 GB budget. The
like-for-like baseline build failed three times on registry timeouts, so the PR states the
arithmetic baseline (≈960 MB) and explicitly does **not** quote the arm64 image as a before.

**Routed out of my lane (now W21):** publish the popularity fallback as a checksummed artifact
beside the encoder — the sidecar's top-up is inert for SASRec without it and refuses to invent a
fill order rather than write a false `popularity-fill` label into the audit. And move `routing.py`
out of the `implicit`-importing package, the same rename the protocols needed.

**Next:** W9 audit fields plus the in-sidecar encoder benchmark, then W10's k6 gate. W12 housekeeping
still holds on Megatron's two-tower diagnostic.

## 2026-09-05 15:25 — W6 and W7 up; W8 starting; O-9 reproduced with a depth finding

**W6 — PR #154, manifest v2.** Shape as approved: `retriever{family, artifacts, params}`,
`rankers{learned, fallback}` with per-route contracts, `lineage`. Schema 1 and 2 both load; v1
normalises in memory and the committed demo bundle round-trips byte-for-byte, so
`make serving-artifacts --check` sees no drift. 1504 tests green.

**Finding in #154:** the booster-file feature-name check is width-only in practice. `LGBMRanker.fit`
calls `.to_numpy()`, so the committed `ranker.txt` reports `Column_0…Column_7`. A same-width
reordering is undetectable. Verified against the real file, pinned by a test, assigned to failover D.

**W7 — PR #156, one retrieval contract.** Three protocols rather than one, because item-item has no
single object answering both the offline and serving questions. `CandidateIndex` satisfies the
serving protocol unchanged. `SASRecRetriever` drops dismissed seeds *before* the encoder and passes
`excluded | dismissed` as exclusions — merging them naively would suppress a dismissed title while
still letting it steer the query vector, the ADR 0012 failure. Includes the temporary permutation
guard: refuses a same-multiset reordering, allows a strict superset. 1520 tests green.

**Packaging defect found and fixed in #156.** The protocols were first written under
`src/models/candidates/`; importing anything there pulls in `implicit` via the package `__init__`,
and the sidecar image installs none of implicit/torch/faiss. So the module the sidecar must hold
could not be imported by the sidecar. Meta-path probe confirmed it before and after; moved to
`src/models/retriever.py`, a pure rename.

**O-9 reproduced independently, plus a depth finding.** A fully-masked query position returns NaN on
torch 2.12's fast path and `set_fastpath_enabled(False)` clears it. It is **depth-dependent**: at one
block the NaN stays in the padded row and the last position is clean; at **two blocks** — ADR 0016's
configuration — it reaches exactly the vector retrieval reads. A single-layer smoke test would have
passed, which explains how it survived.

**Two W8 decisions I need, both from #156's report:** whether topping up to `limit` is the
retriever's or the sidecar's job (two conforming retrievers currently hand the ranker
different-sized sets — I would put it on the sidecar), and that every SASRec candidate reports
`contribution = 0.0`, the same value popularity-fill uses, which would corrupt a cross-family audit.

**Blocked on nothing.** W8 starting now. The O-9 fix is not yet on main, so I will import it from
wherever W17 lands rather than duplicate it, and hold W8's merge until it exists.

## 2026-09-05 06:05 — three seeds measured; study refused; one cross-lane request

**Measured, 6% surrogate, same 115 warm / 50 cold users, `OMP_NUM_THREADS=1`:**

| run | warm recall@500 | cold recall@500 |
|---|---:|---:|
| item-item (incumbent) | 0.3586651775 | 0.4829587027 |
| sasrec seed 7 | 0.3103315582 | 0.4829587027 |
| sasrec seed 13 | 0.3258245274 | 0.4829587027 |
| sasrec seed 21 | 0.2956681362 | 0.4829587027 |

Warm: mean 0.310608, sd 0.015080, **relative range 9.71%**. Overall range 5.79%. Cold sd is
**exactly 0.0** — bit-identical across all three seeds, as predicted.

Context, not extrapolation: one warm user flipping on 115 is 0.0087 absolute, and the observed
warm range is 0.0302 — about 3.5 users. Per-user seed effects average down, so full-data spread
over 1,931 warm users should be materially smaller. The surrogate assumption is therefore
conservative. I am not stating a full-data figure.

**The SASRec verdict survives this anyway.** Applying the full 6% seed dispersion at full data
gives a seed half-width of 7.09%, combined with the 3.69% population term to 7.99%; 16.57% − 7.99%
= **+8.58%**, still far above the +3% bar. Nobody should reopen it on these numbers.

**The study refused, correctly, and found something.** `INSUFFICIENT EVIDENCE`, two reasons:
seeds 7 and 13 are the gate's own, and — the real one — *"surrogate_delta was supplied but the
study configuration matches the gate's; one of the two declarations is wrong."*

**Cross-lane request (O-?): `_configuration_id()` omits `sample_fraction`.** The full-data run
`a11af5ed` and my 6% runs carry byte-identical configuration ids
(`sasrec-sha256:635f76bf…`). So a surrogate is indistinguishable from the gate configuration, and
the study cannot tell derivation A from B. That is `src/training/sasrec.py` — Megatron's lane. I
have not touched it. Protocol hashes *do* differ (the snapshot hash covers the fitted frame), so
nothing already compared is wrong; the gap is in the label, not the identity.

**Running:** seeds 23 and 29 (outside the gate set 42/7/13), ~40 min, to get a clean derivation
without needing that fix first. Then the M0-14 PR, then M2.

## 2026-09-05 05:30 — M0-14 in flight; closeout line stopped as instructed

**Landed today (my lane):** #125–#134, #136, #138–#148. Evaluation apparatus: protocol manifest
and semantic hash, retrieval gate, tolerance instrument, rolling-origin windows, per-user recall
export, protocol emission, single-seed gate input (#139), warm uncertainty band (#143),
cold-item slice (#146). Also M0-11 measured (#141), ADR 0017 proposed/accepted/falsified (#142,
#145, #146), 28-day final window (#144), and both M0-14 prerequisites (#147, #148).

**Running:** SASRec 6% seeds 7/13/21 in `/private/tmp/MovieRecSys-noise`, `OMP_NUM_THREADS=1`.
Seed 7 = 0.310332, seed 13 = 0.325800, seed 21 finishing (~10 min). 6% item-item incumbent
recorded at warm 0.358665. Next: feed all four to the tolerance study, one PR for M0-14, stop.

**Corrections I owe the record.** My earlier −0.43% cold figure was wrong — an unmatched
incumbent partition (1,939/702 vs 1,931/710). Matched, cold is 0.00%. Megatron's zero-tolerance
argument is sound and my noise study is not load-bearing for that verdict.

**Not anticipated, and it is a finding about the gate rather than about SASRec.** SASRec seed 7
and item-item have *bit-identical* cold recall — 0.4829587027 to ten places, same 50 users —
because every threshold-routed retriever hands cold users to the same popularity fallback. So
cold seed-noise is exactly zero and the measured cold tolerance will be zero. The gate's cold
clause cannot distinguish two models in this class. It is not decorative: it still catches a
model that *changes the routing policy* and serves cold users from the learned path. But it
cannot do the job it looks like it is doing, and that belongs in the record.

**Second unanticipated item:** warm seed dispersion at 6% looks like ~5% relative across two
seeds, against the ~1.5% the pilot record assumed. Third seed pending before I state it.

**Blocked on nothing.** M2 starts after the M0-14 PR, per the owner's confirmation.

**Flag:** #147 edited `src/training/sasrec.py` (13 lines, subsample seed decoupled, no
behavioural change — the constant is 42). Merged before the lane split reached me.

## 2026-09-05 — branch-loss audit (requested by orchestrator)

**Finding: the seed-order fix was the only loss. Nothing else needs restoring; no second PR.**

Method. `git log --oneline origin/main..origin/<branch>` is misleading after a squash merge (it
reports every commit as "ahead"), so the audit was done on content: `git diff origin/main
origin/<branch>` and then, for each line present on the branch but absent from main, a direct
`git grep` against `origin/main` to separate *superseded* from *lost*.

**#156 `feat/generic-retriever-interface`.** #161 branched at `099b8a6`. Exactly one commit landed
after that point: `1a44572` — the encoder-window ordering fix and its pinning test. Confirmed lost
from main (`git show origin/main:src/models/retriever.py | grep -c 'reversed(...)'` → 0). Restored
by **PR #163**, which carries both the fix and the three-assertion pinning test verbatim; CI is
still running (`test`, `frontend` pending — that is the only reason it reads BLOCKED; `mergeable=MERGEABLE`,
no review block).

Everything else the branch held is superseded by main, verified item by item:
- `src/models/candidates/sasrec.py`, `twotower.py` — the branch has the **pre-#158** FAISS row
  mapping (`_index_to_item[int(index) + 1]`). Main has the fix. Superseded.
- `src/training/sasrec.py` — the branch has `_configuration_id(config)`; main has the #149
  signature with `sample_fraction` and `subsample_seed`. Superseded.
- `src/serving/model_server.py` — the branch's private `_warmup_seed_movie_ids`; main moved it onto
  the retriever protocol as `warmup_seed_movie_ids` and added the threshold-aware
  `_warmup_seed_count()` on top. Superseded, and strictly better.
- `docs/adr/0015-two-tower-v2.md` — the branch has the 2026-09-04 outcome as current; main has the
  2026-09-05 **corrected** outcome recording the FAISS mapping defect and demoting 09-04 to
  "superseded". Main is ahead.
- `docs/adr/README.md`, `docs/modeling-roadmap.md` — main's rows are the richer, later ones
  (0017 with both dated notes; rung 3 **approved** rather than "not proposed"). Superseded.

**#154 `feat/serving-manifest-v2`.** One unique commit, `f037a6e`, whose content main carries in
its W7/W8 successor form. Its distinctive artifact — the fail-closed guard refusing any non
item-item family, and its test `test_a_bundle_refuses_to_load_a_family_this_build_cannot_serve` —
was **deliberately** superseded: W8 taught the sidecar to load SASRec, so a build that refuses the
family is no longer the correct behaviour. Nothing lost.

**One correction to the framing of the request.** #154 and #156 are **still open**, not closed
(`gh pr view` reports `state=OPEN`, `merged=null`, `closed=null` for both). That does not change the
audit — it was done on content, not on PR state — but it changes the recommendation: both PRs can
now be closed without further salvage, because #163 carries the only unique content either one held.
Closing them is safe once #163 is on main; closing them before that would again leave `1a44572`
reachable only from a branch nobody is merging.

## 2026-09-05 — W12 worktree housekeeping (done)

41 worktrees → 16. Removed 25, each of which satisfied all four conditions, checked before any
removal: its PR is **merged**, the working tree is **clean**, there are **no unpushed local
commits** (`origin/<branch>..<branch>` empty), and **no stashes**.

I also ran the generalized form of the check that found the seed-order loss — for every merged
branch, any commit whose committer date is later than the PR's `mergedAt`. **Zero hits.** Nothing
was pushed to a merged branch after its merge, so the seed-order loss was a one-off caused by an
*unmerged* PR being superseded, not a recurring pattern.

Deliberately kept: `MovieRecSys-sasrec-fastpath`, `MovieRecSys-sasrec-verdict` (the latter is
dirty — 4 uncommitted files — and is Megatron's), the live agent worktree on
`feat/tmdb-metadata-ingestion`, and every worktree whose PR is still open (#154, #156, #159, #163,
#155 and the others).

**One thing to flag honestly.** My skip filter matched on *branch* name, not directory name, so
`/private/tmp/MovieRecSys-sasrec-artifacts` — directory named sasrec, branch
`feat/learned-retriever-recall-evidence`, merged in #137 — was removed. No loss occurred:
`git worktree remove` was run **without `--force`**, which refuses on a dirty tree or any untracked
file, so the directory provably held no artifacts. Verified after the fact that the pinned SASRec
artifact is intact in the main checkout and its archive still hashes to
`43320b87e3cbc4a0dfbc90bce2e9d9b033fbd4c6cebe7f09447fa6cd5e1215e6`. Flagging it because the owner's
rule is about anything sasrec-shaped, and I matched narrower than the rule.

## 2026-09-05 — W9 status

Half of W9 was already on main and I did not re-do it: `src/evaluation/sasrec_latency.py` with
`ENCODER_P99_BUDGET_MS = 15.0`, and committed evidence at
`docs/experiments/sasrec/encoder-latency-2026-09-05.json` — **p50 0.260 / p95 0.271 / p99 0.2846 ms,
passed**, 53x under budget. The inbox's repo-relative `manifest_path` fix is in it too.

Two gaps remain, both now delegated to agents in fresh worktrees off `origin/main`:

1. **Audit fields** (`feat/retrieval-audit-fields`) — `retriever_family`, `retriever_sha256`,
   `ranker_route`, `encoder_ms` threaded sidecar → coordinator → `recommendation_audits`, with a
   migration and fallback-path coverage. Today an audit row cannot tell an item-item answer from a
   SASRec one, which the pending champion swap needs.
2. **amd64 re-measure** (`exp/encoder-latency-amd64`) — the committed p99 was taken on the host
   (arm64, python 3.13.7, torch 2.12.0). The sidecar ships linux/amd64 with `torch==2.12.0+cpu`, a
   different wheel. Re-running the same CLI inside the real image, recorded as a second evidence
   file beside the host one, with the emulation caveat stated up front.

W10 (unchanged k6 gate with the SASRec bundle) stays blocked until #163 is on main, per your note.

## 2026-09-05 — W9 complete, plus two defects found on the way

**Encoder p99 inside the real linux/amd64 image (PR #165).** p50 0.8636 / p95 0.9149 / **p99 1.0047**
/ max 3.8746 ms. Passes the 15 ms budget with 14.9x headroom; budget untouched. Pinned artifact
`a11af5ed…`, archive SHA re-verified `43320b87…` before the run, history_length 50, full 10,000
iterations after 500 warmups. Image digest `sha256:bca4009266588a7bcdf6763c4ea6a01b41890dbbfb1cfcc549a104f4384d3ce7`,
caps `--cpus=2 --memory=4g`, `OMP_NUM_THREADS=1`, Python 3.11.16 / torch 2.12.0+cpu read from inside
the container.

Emulation is bounded, not assumed: Rosetta rather than QEMU, and the same Dockerfile built native
arm64 and run identically gives p99 0.5988 ms. That control differs in exactly two ways, so **1.68x
is an upper bound on translation overhead** and real amd64 hardware cannot be slower than 1.005 ms.

**Blocker found: a SASRec bundle cannot boot in the sidecar image (PR #167).**
`src/models/candidates/__init__.py` imports `.cf` eagerly; `.cf` imports `implicit` at module scope;
the image does not ship implicit, correctly, since it never runs ALS. `sequence_retrieval.py` reaches
the shared encoder through that package at lines 478 and 554 — inside functions that fire only on an
actual bundle load, which is why importing the sidecar looked fine.

Why the existing guard missed it is the interesting part, and worth keeping:
`test_the_sidecar_loads_sasrec_without_importing_torch_at_module_scope` blocks implicit and asserts
`sequence_retrieval` imports cleanly. It passes *because* the imports are deferred. Importing a
module and exercising it are different events, and only the first was covered. Fixed with PEP 562
lazy re-exports rather than by adding implicit to a serving image. New test blocks only implicit —
torch and faiss are installed there and a bundle load may legitimately import them, so blocking all
three would assert something false. Confirmed the test fails against the old `__init__`.

**W10 is blocked on #167.** Running the gate against an image that cannot load the bundle would
measure the popularity fallback and report it as a SASRec p99.

**Reproducibility bug in the training frame (PR #166).** `src/data/load.py:load_ratings` had no
tenant predicate, and it is the loader behind popularity, CF/ALS, item-item, two-tower, SASRec and
the ranker. 25,000,610 rows in the table: 25,000,095 default + 515 demo-persona rows over 8 personas.
Non-negotiable #5 promises a fixed seed and dataset version reproduce an artifact hash; an unfiltered
read makes the frame depend on whether `make demo-seed` ran on that machine.

Landing it is **restorative**: the full-data SASRec run logged 25,000,095 source ratings — exactly
the default count — so it predates the demo seeding and was never contaminated (xmin 801 for the
bulk load vs 834 for the demo rows). Filtering returns 25,000,095, so the next run reproduces the
recorded one instead of diverging from it. Demo ids are 900000101–900000205 against MovieLens's
1–162541, intersection empty, so no real user's history was altered. Also settles O-13 for the
popularity artifact — the filter is the default now rather than something each builder must remember.

## 2026-09-05 — in flight

- **#164 merged.** #165 / #166 / #167 green but BEHIND, shepherds on them.
- **v2 bundle publisher** (`feat/v2-bundle-publisher`) — agent working. Mechanism only: takes artifact
  paths explicitly, two unconfusable bundles (served vs demo fixture), `serving-artifacts-check` split
  into checksum+lineage for the served bundle and retrain-drift for the fixture, lineage enforced.
- **Audit fields** (`feat/retrieval-audit-fields`) — agent working.
- **Incumbent k6 baseline** (detached worktree on main at `8863157`) — agent working. Not asked for;
  the champion swap needs the item-item p99 measured **on this machine under these conditions**, or
  the comparison is a local number against a CI number taken on different hardware and a different
  storage medium. Its Postgres is on the host disk rather than CI's tmpfs, so the audit commit's
  `fdatasync` sits inside the number — pessimistic relative to CI, which is the honest direction.
- **#159 bundle rebuild** — correctly ordered now. The branch is far behind main (missing
  `retriever.py`, `sequence_retrieval.py`, most of `artifacts.py`, and #160's CPU pins), and its
  `serving-artifacts` failure is `Differing manifest fields: ranker` — booster *bytes*, not the
  feature list, since #159 deliberately leaves `FEATURE_COLUMNS` alone and puts the two SASRec score
  columns in a separate `LEARNED_ROUTE_FEATURE_COLUMNS`. Its own agent merges main first; I rebuild
  after, one commit.
- **Popularity artifact format** pinned in Megatron's inbox: `popularity-order.json`,
  `{"schema_version": 1, "movie_ids": [...]}`, most popular first, sorted-key serialization, no
  timestamps or run ids in the hashed payload, ties broken by ascending `movieId`, full catalog,
  threshold-10 semantics. The split is theirs for the bytes, mine for the manifest role and
  `fill_order()`.

## 2026-09-05 — incumbent k6 baseline (the control for the champion swap)

**GATE=pass, k6 exit 0, not re-measured.** main @ `8863157`, detached worktree.

| | |
|---|---:|
| p50 | 7.205625 ms |
| p95 | 9.975667 ms |
| **p99** | **12.931375 ms** (threshold `p(99)<100`) |
| max | 30.75 ms |
| requests | 3301 over 60.257630792 s |
| throughput | 54.78 req/s (`rate>50` pass) |
| error rate | 0 · check rate 1 · dropped iterations 0 |
| requests over the 100 ms SLO | **0 of 3301** |
| silent learned fallbacks | **0** |

By class: cold p99 8.47, mixed 13.25, warm 12.91. Stage p99s for `item-item-cosine+lightgbm`
(n=2593): candidate 0.28, feature 0.02, ranker 1.61, model 2.03 ms.

Served manifest: schema_version 1, `item-item-cosine` / `demo-itemitem-v1` /
`7fa2f3be3c1544fd2888bf343eb70de6b78ad9c626fab47d330f8b0d1ee0bcf0`; ranker `lightgbm-lambdarank` /
`demo-lgbm-v1` / `7897a151ad364ff900ead5bf119b39fcb0e00a1d5a7bb7cf5ce8c150cd7d98e2`;
`feast-phase3-v1`; tenant `demo`; the eight-column contract. Checksums recomputed independently in
both the committed tree and the live volume — all four match.

Note: `feature-setup` *retrains* into the `movielens-demo_model_artifacts` volume rather than serving
`infra/model-bundle/` directly. The two manifests differ only in `trained_at` (the volume copy uses
the run clock; only `make serving-artifacts` pins `ARTIFACT_AS_OF`). The arm64 retrain came out
**byte-identical** to the committed amd64 bundle.

Correctness from the durable audit export, not just k6: `http_status {200: 3365}`,
`outcome {'success': 3365}`; every warm persona on the learned path 100% of the time; the only
popularity rows are the cold persona's, with `fallback_reason: cold-start`.

No re-measurement and the rule was never reached: `p99_breached: false`, all 57 `/proc/stat` samples
steal 0.0%, `nr_throttled 0` on api-load / model-server / feature-server.

**Storage: `postgres-storage.txt = no`** — host block device, no tmpfs, `DEMO_COMPOSE_EXTRA`
deliberately unset. The audit commit's `fdatasync` is inside this p99. Measured: **0.283 ms per WAL
sync** (3,295 syncs) here vs the runbook's **0.214 ms** on CI tmpfs — about **0.07 ms per commit**.

**W10 must replicate two things exactly:** no tmpfs extra, and the host-port remap (host 5432 is the
dev stack's; a 6-line `DEMO_COMPOSE_EXTRA` override outside the repo moves only the host-side
publication to `55432:5432`, verified as a one-line diff of the resolved Compose config and neutral
to everything measured).

Evidence not committed — it lives under gitignored `/artifacts/`. Intended home:
`docs/demo-runbook.md` "Audit and latency proof", in one PR **alongside** the SASRec number.

## 2026-09-05 — W10 procedure (settled, ready to execute)

The orchestrator's concern is real and here is the mechanism, with the reason it is auditable.

**The finding.** `infra/features/Dockerfile:71` is `COPY infra/model-bundle/ ./models/serving/` — the
image *bakes* the committed bundle, and that is what production serves. In the demo stack the
`model_artifacts` volume is mounted over `/app/models/serving`, which **shadows the baked copy** with
whatever `feature-setup` retrained. So the demo stack does not, by default, serve the committed
bundle at all. That is why "how did the SASRec bundle reach the volume" is the right question.

**The mechanism.** A `DEMO_COMPOSE_EXTRA` override — the same documented hook the CI tmpfs job and
the port remap already use — that for `model-server` replaces the mount at target
`/app/models/serving` with a read-only bind mount of `infra/model-bundle/`. Compose merges `volumes`
by target, so naming the same target replaces it rather than appending. `feature-setup` may then
retrain into the volume as usual and it is simply not read.

Why this rather than mutating the volume: the served bytes *are* the committed bytes, by
construction, so the claim "the served bundle is the published one" is a property of the
configuration rather than of a copy step that has to be trusted. And `model-server` mounts its
artifact dir read-only, so writing into the volume needs a second container anyway.

**The audit trail is built in.** `ServingArtifactBundle` verifies every artifact's SHA-256 against
the manifest at load (`src/models/artifacts.py`, `actual = file_sha256(artifact_path)`, raising on
mismatch). A model-server that boots is therefore already proof the served bytes match the manifest.
I will still recompute the checksums independently from inside the container and report both, as the
baseline run did.

**One piece of luck that keeps the comparison valid.** The baseline served the volume's *retrained*
bundle, not the committed one — but the arm64 retrain came out byte-identical to the committed amd64
bundle, checksums verified in both places. So the incumbent number was taken on the same bytes this
mechanism will serve, and switching mechanisms does not invalidate the control.

**W10 run shape:** `demo-up` → `demo-seed` → bind-mount override in place → confirm `model-server`
booted and its manifest reads the SASRec family → recompute checksums inside the container →
`demo-load-quiesce` → `demo-load-smoke`, with **no tmpfs override** and the **55432 host-port remap**,
exactly as the baseline. Verify the override with `docker compose config` before running, and report
the resolved diff.

Blocked on: #167 on main, and the O-15 published bundle from #169.

## 2026-09-05 — merge queue and the #169 hold

Orchestrator consolidated the shepherds into one serial queue: **167 → 166 → 165 → 155 → 168 → 169**,
merging in order on green.

**#169 converted to draft** so the queue cannot reach it before its baking gap is closed. Its agent
has the settled decision: bake both bundles at distinct paths with `MODEL_ARTIFACT_DIR` selecting;
`docker-compose.prod.yml` defaults to the **served** full-data bundle (D-006 — production serves the
champion, and must not be one forgotten env var away from serving the compact fixture); the demo
compose keeps the fixture; state in the body what each image carries and at which path; and guard
the `COPY` against `infra/model-bundle-served/` not existing yet, since a `COPY` of a missing
directory fails `docker build` and the payload has not landed. It was told not to mark itself ready —
I take it out of draft after reviewing the baking change.

**#168 approved to merge as-is.** The NULL-vs-`not-run` distinction and the popularity-leaning
defaults were both confirmed correct.

**W10 mechanism approved.** Read-only bind mount over `/app/models/serving` via `DEMO_COMPOSE_EXTRA`;
report the resolved `docker compose config` diff and both checksum recomputations.

## 2026-09-05 — #168 green, and a confound in my own baseline

**#168 all green** — ten jobs including the k6 gate (6m37s). Migration `0019` adds four nullable
columns with `ck_audit_encoder_ms`; RLS and the 0008 grants untouched.

**The incumbent baseline I measured is confounded and must be re-run.** p99 12.93 ms was taken on
`8863157`, *before* #168. #168 writes four extra audit columns on **every** request, item-item
included, and the audit commit precedes the response, so that write sits inside the p99 by design.
Comparing a post-#168 SASRec number against a pre-#168 control would confound the retriever change
with the audit-column addition — the O-9 ruling ("do not change one side of a comparison") applied to
latency. Re-run costs 5m21s; procedure, port remap and storage medium are already pinned. Report both
numbers, keeping the pre-#168 one as the record of what the columns cost.

**The encoder hooks are not part of that confound.** #168 times the encoder with a torch forward-hook
pair attached at bundle-load time, so it exists only where an encoder does — item-item loads none.
And measuring SASRec *with* the hooks is correct, since that is what production serves. Consequence
for the record: the **1.0047 ms** in-image encoder figure was measured *without* them, so it is a
floor rather than the served cost. The no-numerics argument holds — `encode_positions` calls
`set_fastpath_enabled(False)` on every encode, so the fastpath decision precedes any hook.

Also on the record from #168:
- `RANKER_ROUTE_*` moved `artifacts.py` → `serving/policy.py` with `from … import X as X` re-exports.
  Necessary: the slim API image cannot import `artifacts.py` under ADR 0008, and that is where the
  column is written.
- **`downgrade` proven only structurally** (recording `op`, not a live database) — matches the repo's
  existing standard for migration tests, and CI runs `alembic upgrade head` against real Postgres, but
  a rollback across 0019 has not been executed anywhere. Worth knowing before a production deploy
  carries it, given ADR 0013's rollback story.
- Public `GET /users/{user_id}/audits` unchanged (`extra="ignore"` drops the new fields); no OpenAPI
  drift. Surfacing them to the frontend's prediction-audit disclosure is an open, deliberately
  deferred decision — and the champion swap is exactly when someone will want it.

### Agreed with the orchestrator (2026-09-05)

- **Re-run the incumbent baseline once #168 is on main** — same procedure, medium and port remap.
  Report both figures; the pre-#168 p99 **12.93 ms** is kept as the measured cost of the four audit
  columns.
- **Measure SASRec with the forward hooks in place**, since that is what production serves. The
  **1.0047 ms** in-image encoder figure is recorded as the hook-free **floor**.
- New work item (orchestrator's board): **execute a real rollback across 0019 in the staging compose
  before the first production deploy.** Structural-only downgrade testing is acceptable to merge, not
  acceptable to deploy on.
- Product-track item for **after** the swap: surface the four provenance fields in the frontend's
  prediction-audit disclosure.

**My queue:** wait for 167 → 166 → 165 → 155 → 168 to merge → re-run the incumbent baseline → W10
against the published bundle once #169's payload exists. #169 stays a draft until I review its
baking change.

## 2026-09-05 — #169 baking gap closed, out of draft

Verified against the branch, not the report. `infra/features/Dockerfile` line 85
`infra/model-bundle/` → `/app/models/serving/` (unchanged, still asserted by
`test_baked_serving_bundle.py`) and line 107 `infra/model-bundle-served/` →
`/app/models/served-bundle/`. `.gitkeep` committed so the COPY works pre-payload, checked with a real
minimal build against the actual context. `docker-compose.prod.yml` sets
`MODEL_ARTIFACT_DIR: /app/models/served-bundle` as a literal on `model-server` and `materialize`.
Demo untouched. All twelve CI jobs pass. Marked ready.

**New dependency created by this PR, for the board:** with no payload, `/app/models/served-bundle`
holds only `.gitkeep`, so a container pointed there has no manifest and **refuses to boot** rather
than falling back to the fixture — ADR 0013's fail-closed rule working as intended. Nothing regresses
today because production is not provisioned, but **the first production deploy is now gated on the
served payload**, which is gated on Megatron's popularity artifact.

**A correction I caught before repeating it.** The agent reported `sasrec-manifest.json` as read by
fixed name and pinned by no checksum, which reads like an open silent-swap hole. It is not:
`load_sequence_retriever` already cross-checks `artifact_manifest.model_filename != encoder.filename`
and raises, with a comment naming exactly that case. The publisher's publish-time check duplicates an
existing load-time one — earlier failure, worth having, not a hole closed.

What *is* real: `_REQUIRED_RETRIEVER_ARTIFACTS[sasrec]` requires `vocabulary` and `config` roles **no
loader reads**, and `test_serving_manifest_v2.py`'s fixture satisfies them with invented standalone
blobs pinning nothing. The publisher points those roles at `sasrec-manifest.json` — which genuinely
*is* the vocabulary fingerprint and the config — so two ceremonial roles now pin a file that matters.

Follow-ups it named rather than hid: `CLAUDE.md`'s repo-structure tree does not list
`infra/model-bundle-served/` (left alone to avoid a merge-queue conflict; one-line follow-up), and
`make serving-artifacts-check` was not run locally, though CI's `serving-artifacts` job ran the real
thing and passed in 4m39s.

### Board items agreed (2026-09-05, later)

- **W25** (orchestrator's board): first production deploy gated on the published served payload,
  which is gated on Megatron's popularity artifact. Orchestrator holds Megatron to it.
- **My housekeeping follow-up, after the queue drains:** add `infra/model-bundle-served/` to
  `CLAUDE.md`'s repo-structure tree (one line, deferred to avoid a merge-queue conflict).

## 2026-09-05 — queue drained to #169; baseline deferred on memory

**Merged:** #163, #164, #155, #167, #166, #165, #168. Main at `944c270`. Only **#169** left.
Verified on main after the merges: lazy re-exports present, the new sidecar-import guard test present,
and the seed-order fix `reversed(positive_history_movie_ids)` **still intact** — worth re-checking,
since it was lost to a squash merge once. Live check: a trainer now reads **25,000,095** rows,
exactly the frame the full-data SASRec run recorded.

**The incumbent re-baseline is deliberately deferred.** Swap is **5,645 / 6,144 MB (92% full,
499 MB free)**, compressor holding 12.77 GB, free pages 1.05 GB. Load is fine (1.90), so this would
*not* surface as CPU contention. Freed what was safe — the two encoder-bench images (3.4 GB),
dangling images, stopped containers, 5.2 GB of build cache — all disk; swap unchanged.

Why this blocks specifically: the re-baseline exists to isolate the cost of #168's four audit
columns, and swap-induced tail latency is indistinguishable from that cost. **The gate records CPU
steal but does not watch memory**, so a bad number would come back looking clean. Re-check headroom
before both the baseline and W10; if pressure does not ease, say so rather than measure.

**#159 rebuild proceeding** — a deterministic build, so memory can slow it but not corrupt it.
Detached worktree at `fad7447` (its own agent holds the branch), real `make serving-artifacts` on
linux/amd64, verified by `make serving-artifacts-check`, one commit via
`git push origin HEAD:feat/rung3-sasrec-score-features`. Instructed to stop and report rather than
iterate if the check fails — a bundle hand-edited to pass its own reproducibility check is a finding,
not a fix. Branch lacks #166/#165/#168; checked and irrelevant, since `demo_artifacts.py` uses its
own `FROM ratings WHERE tenant_id = :tenant_id` rather than `load_ratings`.

**Board:** post-fix increment 1 refused at learned-route warm **+2.78%** vs +3.00%, decomposed by the
eight-column control into **+1.86%** from the two features and **+0.90%** from the retrain. DIN unbuilt.

## 2026-09-05 — gate memory evidence (PR #170), and the run preconditions

**#170** adds `host-memory-before.txt` / `host-memory-after.txt` beside the existing `cpu-stat-*` and
`docker-stats-*` snapshots. 40 lines, one function inside `capture()` plus two call sites. **Evidence
only** — no threshold, no re-measure rule, nothing reaching `decision.json`. Queued to merge right
after #169, so it lands *before* the baseline/W10 pair and the pair is self-documenting.

Platform details that would otherwise mislead a reader: Linux reports `MemAvailable` beside `MemFree`
(MemFree omits reclaimable page cache and reads alarmingly low on a healthy machine — quoting it
alone manufactures false alarms in CI); macOS records `hw.pagesize` explicitly because these are
**16 KB** pages and assuming 4 KB misreports by 4x, and calls out `Pages occupied by compressor`
because on Apple silicon the compressor grows long before swap does. Both paths exercised before
commit — `vm_stat` on the host, `/proc/meminfo` inside a Linux container.

### Preconditions for the baseline + W10 pair (agreed)

1. **#170 on main** — so both runs carry memory evidence.
2. **Real headroom**, re-checked immediately before. Hold and say so if not. Owner closing Chrome and
   VS Code, or restarting Docker Desktop, would likely be enough; the Docker VM plus the compressor
   is the bulk of it.
3. **Back to back, same memory state, nothing else on the machine**, so the comparison isolates
   #168's four audit columns rather than the machine.
4. Same medium as the first baseline (**no tmpfs override**) and the **55432** host-port remap.
5. SASRec measured **with** the encoder forward hooks, since that is what production serves; the
   **1.0047 ms** in-image encoder figure stands as the hook-free floor.

## 2026-09-05 — #159 bundle rebuilt (e2c7d87), two corrections

`make serving-artifacts` on linux/amd64, then `make serving-artifacts-check` **exit 0**: *"Serving
bundle in /app/committed reproduces at as-of 2026-09-01T00:00:00+00:00"*. No hand-editing. One commit
`e2c7d87` pushed to `feat/rung3-sasrec-score-features`, fast-forward from `fad7447`, correct author.
Only `infra/model-bundle/manifest.json` + `ranker.txt` touched.

`ranker.sha256` `7897a151…d98e2` → `e216bc44…18a5`. `ranker.version`, `candidate.*`
(`candidate-index.json` byte-identical, `cmp` clean), `feature_columns` and `trained_at` all unchanged.

**Correction 1 — the staleness was not `gpu_device_id_list`.** What moved is the **feature names**:
`feature_names=Column_0 … Column_7` → the eight real names, plus the six `feature_importances` labels.
`tree_sizes`, `feature_infos`, every split and every importance *count* identical — trees untouched.
No `gpu_device_id_list` line appeared; the amd64 rebuild matched the amd64 baseline on that header.
Both facts are true but distinct: arm64/amd64 differ on that header, which is *why* the build is pinned
to a container; the bundle was stale because #159's `lgbm.py` names the booster's features. A
semantic change, not a build artifact.

**Correction 2 — my own brief was wrong, and it fails silently.** A plain `ports: - "55432:5432"` in a
`DEMO_COMPOSE_EXTRA` override does **not** replace the base publication — Compose **appends** port
lists, so the resolved config keeps `published: "5432"` *and* adds `55432`, and `demo-up` still
collides. It requires the **`!override`** tag (Compose 5.3.0 here). With it the resolved
`docker compose config` diff is exactly one line, `published: "5432"` → `"55432"`, `target: 5432`
untouched so the build still reaches `postgres:5432` over the network.

**Runbook gap is therefore sharper than first described:** its only guidance is "stop the development
stack with `make infra-down`", unusable when another session owns the port, and the obvious workaround
has a silent-failure mode. The W10 PR's runbook addition must carry the **`!override` requirement and
why**, not just the hook.

Build 3m00s from a scratch emulated amd64 image, check 7s, no OOM or retry at 92% swap — as expected
for a deterministic build. Demo stack down cleanly; the dev project's Postgres/MLflow untouched and
still holding 5432.

## 2026-09-05 — #170 merged; pair still held; W10 prep complete

Main at `0dc5dd7` — the gate records memory. **Pair still held:** swap 5,645/6,144 MB unchanged, free
pages down to **0.32 GB**, compressor 7.92 GB, load 2.23 (so again it would not read as contention).

**Port override written and verified by me, both directions** (file at the session scratchpad,
`host-port-relief.yml`):
- `ports: !override` → resolved config diff is exactly one line, `published: "5432"` → `"55432"`,
  `target` untouched; occurrences of `published: "5432"` = **0**.
- plain `ports:` → **four lines appended**, `published: "5432"` **survives** (count = **1**), so
  `demo-up` still collides while the file looks right.
This retro-confirms the first baseline's override was a working form (same single-line diff at 354).

**Runbook fix committed and pushed:** `docs/w10-latency-evidence` @ `e6629e4`. PR deliberately not
opened — it becomes the W10 PR once the numbers exist. Pushed rather than held locally so it survives
context loss. It carries the `!override` tag, the reason, a verification command
(`grep -c 'published: "5432"'` — 0 right, 1 means the tag was dropped), and why a run taken this way
stays comparable.

**Worktrees pruned 23 → 15**, every removal verified merged + clean + pushed + stash-free.
`MovieRecSys-sidecar` skipped: directory name is not SASRec but the branch is, and the broader reading
of the owner's rule is the safer one.

**Watch on #159:** now DIRTY against main. My bundle commit was a clean fast-forward from `fad7447`,
so the bundle is valid *as of that commit* — but if the conflict resolution touches `lgbm.py` or the
feature contract, the booster bytes move and it needs rebuilding. Re-check `serving-artifacts` after
its agent resolves; do not assume the rebuild still holds.

**Everything of mine is merged or waiting on memory.** The pair is ~11 minutes once there is headroom.

## 2026-09-05 — #159 green; only the memory hold remains

#159's agent merged main (`e2c7d87` → `a8dff6d`). **My bundle rebuild survived**: the diff between my
rebuild commit and the new tip across `src/models/ranker/lgbm.py`, `src/feature_contract.py`,
`src/training/ranker.py` and `infra/model-bundle/` is **empty**, so the booster bytes could not move.
CI then confirmed it — **`serving-artifacts: pass`, mergeState CLEAN**. Nothing back to me.

**Everything of mine is now merged or staged.** On main: #163 seed-order restore, #164 status ledger,
#165 amd64 encoder evidence, #166 training-frame tenant filter, #167 sidecar import fix, #168 audit
provenance, #169 v2 publisher, #170 gate memory evidence. Plus the #159 bundle commit on its branch.

**Staged and waiting on numbers:** `docs/w10-latency-evidence` @ `e6629e4` (runbook port-conflict fix)
and the verified `host-port-relief.yml`.

**The only open item is the memory hold.** Swap 5,645/6,144 MB and free pages 0.36 GB — unchanged
across every check this session. The pair is ~11 minutes once there is headroom.

## 2026-09-05 — new work while held, and a scheduling conflict to resolve

**#171 open** — CLAUDE.md repo-structure entry for `infra/model-bundle-served/`. Deliberately more
than one line: it distinguishes the two bundles by what they *are* (fixture vs served, schema 1 vs 2
with required lineage, trained vs assembled, and the two different checks), because the directory
split only prevents the failure it exists to prevent if a reader knows which is which. Also corrects
the `features/` line — the image bakes **both**, and `MODEL_ARTIFACT_DIR` selects.

**W23 running** against `movielens-staging` (own Postgres and volumes, no published data-store ports;
isolation verified before starting, not assumed). Scope extended beyond the ask in two ways:
- **Rows inserted before the downgrade**, so the exercise observes what happens to *data*, not just
  to columns. A downgrade that silently drops populated columns is the finding worth having and
  structure-only testing cannot see it.
- **RLS and the 0008 grants verified across the cycle.** `recommendation_audits` carries forced RLS;
  cross-tenant leakage is the highest-severity bug class here; and nothing tests that a *reverse*
  migration preserves it. A rollback that quietly drops RLS would be a tenant-isolation defect.
A failing downgrade is a **finding plus a fix PR**, not a runbook note — and no hand-editing the
database to get past one.

### Scheduling conflict raised with the orchestrator

Failover agent **F** has started in Megatron's lane (W21 popularity artifact, then the two-tower full
run), gated on swap below 70%. **F and my latency pair are waiting on the same event**, and "F waits
if the k6 pair is running" does not cover it: at the instant memory frees my pair is not yet running,
so F would see a clear field and take it — and a 25M load is exactly the condition that makes a p99
untrustworthy.

Proposed: **my pair first (~11 minutes), then F**, as an explicit handoff — "do not start a 25M load
until Starscream reports the pair is done" — rather than a check that can be true at the wrong
instant. If F goes first instead, I stop watching for headroom rather than produce a p99 taken during
a 25M load.

Also told the orchestrator: the popularity format is already pinned in `megatron.md` (F should read
before writing; it owns the artifact and I will conform to whatever it chooses deliberately), and F
must **not** add its own tenant filter — #166 made `load_ratings` default to `tenant_id = 'default'`.

Memory is worse, not better: free pages **0.14 GB**, swap unchanged at 5,645/6,144 all session.

---

# ⚠ STANDING OBLIGATION — F IS BLOCKED UNTIL I POST THE BLOCK

Agreed with the orchestrator 2026-09-05: **my baseline/W10 pair runs before F's 25M load.**
F's hard rule is that it may not start a 25M load until **a dated block in this file says the
baseline/W10 pair is complete.**

**So after the pair finishes I MUST append a dated block here saying so, in plain words.**
If I do not, failover agent F is blocked indefinitely on W21 and the two-tower full run — and F
cannot tell the difference between "not done yet" and "done but never written down."

Write it even if the pair **fails** or is abandoned: the block must say what happened either way, so
F is released rather than waiting on a run that is never coming. The wording F is looking for is that
the baseline/W10 pair is **complete** — say that explicitly, with the date.

---
- HB 20:27  latency-pair  HELD: swap 5645M/6144 (need <4000). W23 rollback rehearsal running on movielens-staging. #171 with shepherd. Watch armed; pair runs unattended on headroom, then I post the completion block F is waiting on.
- HB 20:34  review-173  reviewing F's W21 PR for W8/W10 conflicts in test_sidecar_sasrec_load.py and test_serving_manifest_v2.py

## 2026-09-05 — #173 review: clean, but it deadlocks the handoff

**The two lane files are clean.** `test_sidecar_sasrec_load.py` keeps `_without_the_shared_fix`
(5 occurrences), so O-9 stays observable and W17 is unaffected; the rest is additive.
`test_serving_manifest_v2.py` is additive and updates the required-roles assertion correctly. F did
**not** touch `src/serving/sequence_retrieval.py` — `fill_order()` is still mine, and it rewrote that
test's docstring to say so rather than leave a stale rationale.

**The deadlock.** #173 makes `popularity` a **required** role for SASRec (good reasoning: refusing a
bundle beats a false `popularity-fill` audit label). But:

1. required role → any SASRec manifest needs `popularity-order.json`;
2. W10 serves a SASRec bundle through `ServingManifest.load`, which validates required roles;
3. the served bundle is full-data (decision (a)), so the ordering must come from the full 25M;
4. `src/training/export_popularity_order.py` reads full ratings via `load_inputs` — a 25M load;
5. F may not start a 25M load until my block says the pair is complete;
6. my block requires W10 to have run.

**W10 needs an artifact F cannot produce until W10 has run.** It would present as two lanes idling
politely, not as an error.

**Proposed fix:** the rule is scoped one step too wide. The popularity export is a *prerequisite* of
my pair, not a competitor with it; what needs protecting from is F's **two-tower full run** (hours of
sustained load), not a one-off ordering export. Order when memory frees: F exports popularity →
I publish the served bundle and run the pair → I post the block → F runs two-tower. Amend F's rule to
gate the two-tower full run, or carve out the export.

Moot until memory frees (F is under the 70% rule too), so no urgency — but resolve before #173 merges
or the first thing after memory relief is mutual waiting.

Also flagged: once #173 is on main, **every SASRec manifest without a popularity artifact is
invalid**, including anything #169's publisher emits. Intended, but the window between #173 merging
and the artifact existing is real.
- HB 20:36  review-173  reviewed: lane files clean, deadlock raised with orchestrator; pair still HELD on memory

### Sequence after the deadlock fix (agreed 2026-09-05)

1. Memory frees.
2. **F** runs the popularity export (one read), then writes the artifact **path, SHA-256 and format**
   into `.coordination/inbox/starscream.md`.
3. **Me:** publish the served bundle with that artifact, then run the baseline/W10 pair back to back.
4. **Me:** post the dated completion block — which releases F's two-tower full run.

**My trigger is now F's inbox write, not headroom.** Headroom only releases *F*. Watching the inbox.
The standing obligation above is unchanged and still binds: post the block even if the pair fails.
- HB 20:36  latency-pair  sequence updated: F exports popularity first, writes to my inbox, then I publish+run. Watching inbox and memory.
- HB 20:39  latency-pair  MEMORY SIGNAL CORRECTED: swap-used is a high-water mark on macOS and never shrinks; now tracking free+inactive and memory_pressure. Measured free+inactive 7.25GB, pressure-free 40%, compressor 17.21GB. Chrome(44) and VSCode(18) still running; Docker VM RSS halved 1.16->0.66GB. Awaiting F popularity export.

## W10 EXECUTION PLAN — run this when F's inbox block lands

Trigger: F writes the popularity artifact's **path, SHA-256, format** plus **free+inactive,
memory_pressure and compressor size** into `.coordination/inbox/starscream.md`. Inbox watch armed.

**Certification rule agreed with the orchestrator:** my reading of the machine is what counts, not a
single quoted `vm_stat` line. The two tools disagree by ~6 GB (`vm_stat` free 0.07 GB vs
`memory_pressure` 6.55 GB), so quote **both** and never one alone.

Steps:
1. **Re-read memory** (free+inactive, `memory_pressure`, compressor) and record it. Compare against
   what F reports post-export — a 25M read moves the compressor and I need the state it hands me.
2. **Publish the served bundle** with F's popularity artifact via `make serving-artifacts-publish`
   (`src/models/bundle_publisher.py`, spec-driven). Verify with `make serving-artifacts-verify`.
   Requires #173's `popularity` role, which is required for SASRec.
3. **Bring up the demo stack** with `DEMO_COMPOSE_EXTRA` pointing at the scratchpad's
   `host-port-relief.yml` (**`ports: !override`** — a plain list appends and the conflict survives).
   Verify with `docker compose config | grep -c 'published: "5432"'` → expect **0**.
4. **Serve the published bundle** by adding to the same override a read-only bind mount replacing
   `model-server`'s volume at target `/app/models/serving`. Compose merges `volumes` by target, so
   naming the same target replaces it. `feature-setup` may retrain into the volume; nothing reads it.
5. **Confirm the sidecar booted on the SASRec family** and recompute both checksums inside the
   container. `ServingArtifactBundle` verifies SHA-256 at load and raises on mismatch, so a booting
   model-server is itself proof — but recompute anyway and report both.
6. **Run the incumbent re-baseline first, then W10, back to back**, same memory state, nothing else
   on the machine. **No tmpfs override** (`DEMO_COMPOSE_EXTRA` must not include
   `docker-compose.ci-load.yml`) — the first baseline was on the host block device and the comparison
   requires the same medium.
7. **Refuse to publish** a control that disagrees with its partner by more than the four audit
   columns could plausibly explain. Re-run instead, and say so.

Reference numbers: pre-#168 incumbent **p99 12.931375 ms** (p50 7.205625 / p95 9.975667, 3301 reqs,
0 over SLO, 0 silent fallbacks, steal 0). In-image encoder p99 **1.0047 ms** = hook-free **floor**;
SASRec is measured **with** the forward hooks because that is what production serves.

Deliverable: one PR on `docs/w10-latency-evidence` (already has `e6629e4`, the runbook `!override`
fix) carrying both numbers under the runbook's "Audit and latency proof" section.

**Then post the dated completion block** — F's two-tower full run is blocked on it. Post it even if
the pair fails.
- HB 20:40  latency-pair  execution plan written; awaiting F popularity export; inbox watch armed
- HB 20:45  latency-pair  RUNNING re-baseline on main c71ef4b (machine verified fit: free+inactive 10.23GB, pressure 56%, load 1.50). W10 half blocked on #173 merging (main has 0 occurrences of RETRIEVER_ARTIFACT_POPULARITY); asked orchestrator to expedite it ahead of docs-only #172.

## 2026-09-05 — W23 result (PR #172): the finding is not the one it was chartered for

All four downgrades exit 0; **no migration fix needed**. RLS and the 0008 grants survive every
revision in both directions — verified live as `app_user` at 0019, at 0017, and after the re-upgrade
(`SET LOCAL app.tenant_id` gives 2 / 1 / 0 rows as designed). **No tenant-isolation defect.** After
the round trip the schema diff is identical except `ordinal_position` 31–34 → 35–38, since Postgres
never reuses a dropped column's attnum.

**The unasked-for finding, and the important one: the dangerous state is "database BEHIND the running
image", not ahead.** At schema 0017 with the current writer serving, every audit insert fails with
`column "retriever_family" ... does not exist` — and since every request commits a durable audit row
*before* it answers, that is a **total serving outage, not a degraded path**. ADR 0013's
rollback-by-image is safe precisely *because* it leaves the database ahead; §9's "additive migrations
only" rule is now backed by measurement rather than assumption.

Costs pinned: retrieval provenance is **unrecoverably** lost if the schema is rolled back (only a
pre-rollback dump reconstructs it); the TMDB catalog returns empty but is recoverable via
`make tmdb-load`. The agent also **corrected its own wrong claim mid-task** — it had written that
losing `tmdb_*` degrades the product's detail pages; nothing in `src/serving/` or `web/` reads those
tables (detail pages read `movie_catalog_metadata`), and it fixed that in a second commit.
- HB 20:45  latency-pair  re-baseline in flight; #173 moved to front of queue; memory holding (free+inactive 10.48GB, load 1.49) even with the gate running

## 2026-09-05 — false confound alarm, and a matcher trap worth remembering

A TMDB pull was reported as having run 20:44–20:47 alongside the re-baseline. **It had not.** The
orchestrator's `pgrep` had matched its own monitor's shell text, whose argv contains the tmdb command
lines.

I reached the same conclusion independently from evidence: at 03:47Z the gate had **not opened its
window** — the evidence directory still held the *first* baseline's files (`gate-started-at.txt` =
`2026-09-06T01:01:32Z`, mtime 18:01), and `run_gate.sh` clears that directory on start. The stack was
still coming up (model-server 6s, api 55s). Setup is not measured; only the window is.

**And I fell into the identical trap while checking.** My own `ps | grep -iE "[t]mdb|[i]ngest"`
matched **my own shell command**, because its command line contains the pattern. The `[t]` bracket
trick suppresses the *grep* process but not a wrapper shell whose argv holds the pattern. **Do not
trust `pgrep` / `ps | grep` for "is X running" on this box** — match the executable, or check the
artifact the process would produce.

**Hazard in the new handoff trigger.** The original false start fired because my *heartbeat* text
said "then I post the completion block". So: **never write the literal token except in the real
block.** Asked the orchestrator to anchor its matcher to start-of-line. A false fire contaminates a
measurement; a missed fire idles F for hours.
- HB 20:48  latency-pair  re-baseline in setup; false confound alarm cleared from evidence; machine improved (free+inactive 11.74GB, pressure 65%)

**Non-overlap is now provable from evidence, not recollection.** The re-baseline's gate opened at
`gate-started-at.txt` = **2026-09-06T03:47:41Z**. The claimed (and since-retracted) TMDB window was
03:44–03:47Z, killed at ~03:47:00Z. The measurement therefore began **41 seconds after** the claimed
interference ended — and since no pull actually ran, the point is moot twice over. This is exactly
why the gate timestamps its window: two sessions' recollections disagreed and the file settled it.

Stopped the W23 agent — its work was complete (PR #172 up) and it had degraded to polling CI, which
I can do myself at no cost.
- HB 20:49  latency-pair  re-baseline window OPEN since 03:47:41Z on main c71ef4b; #173 still pending CI

## 2026-09-05 — QUEUE S1–S6 acknowledged, with one reorder

Read the standing rule and the queue at source in the inbox (line 111+) and in `ORCHESTRATION.md`
(§ Work queues, lines 98–103). **On S1 now** — the re-baseline window opened 03:47:41Z; W10 follows
once #173 merges.

**Reorder, and the reason — it is machine-quietness, exactly as the rule anticipates.**

The binding constraint is not "is the machine free" but **"the two halves of the pair must see the
same machine."** There is dead time between the re-baseline finishing and #173 landing, and anything
heavy in that gap perturbs memory *between* the two halves and re-confounds the very comparison the
pair exists to make. So:

- **In the gap (S1 mid-flight): only pure writing.** S6's ONNX memo needs no compute — an evaluation
  memo, not an implementation — so it fits, as does drafting S5's ledger text. Neither builds,
  trains, nor starts a container.
- **Explicitly NOT in the gap: S4.** The frontend item needs `npm`, a type regeneration and a
  Playwright run. That is a real memory and CPU footprint and it would land squarely between the
  baseline and W10. **S4 moves to after `PAIR COMPLETE`.**
- **S2 and S3 stay in order** — both are downstream of the pair by definition.

Net: **S1 → (S6 draft, S5 draft while blocked) → S2 → S3 → S4 → finish S5/S6.** Same set, same
dependencies, and nothing heavy between the two measurements.

**S6 is flagged "owner decision required" after** — so I write the memo and stop there rather than
acting on it.
- HB 20:51  S1  queue S1-S6 acknowledged; reordered S4 to after PAIR COMPLETE so no build lands between the two halves

## 2026-09-05 — S1a: post-#168 incumbent control (GATE=pass)

main `c71ef4b`, window `2026-09-06T03:47:58Z`, 5m07s wall clock. **Not re-measured**
(`p99_breached: false`, `steal_peak_pct 0.0`).

| | pre-#168 | post-#168 | delta |
|---|---:|---:|---:|
| **p99** | **12.931375 ms** | **13.395416 ms** | **+0.464041 (+3.59%)** |
| p95 | 9.975667 | 10.765958 | +0.790291 |
| p50 | 7.205625 | 7.115500 | −0.090125 |
| requests | 3301 | 3301 | 0 |
| handler p99 | 13.06 | 11.85 | **−1.21** |
| implied outside handler | −0.13 | **+1.55** | +1.68 |
| ms / WAL sync | 0.283 | 0.303 | +0.020 |
| tmpfs | no | no | same medium |

**The shape corroborates the attribution:** the *handler* got faster while the whole increase moved
*outside* it — which is what paying four extra columns in the audit INSERT and commit looks like,
rather than in scoring.

**All four #168 columns populated — no defect.** 3365 rows created by this run, **0 NULLs** in all
four. `retriever_sha256` on learned rows equals the manifest's `candidate.sha256` exactly;
`encoder_ms` is 0 on every row (a measured zero for item-item); `ranker_route` is `learned` for the
three warm personas and `fallback` for the cold one. The 3367 older rows with
`retriever_family IS NULL` are the pre-#168 run's — exactly the designed back-fill-free behaviour.

Correctness: `http_status` 200×3365, `outcome` success×3365, `silent_learned_fallbacks: 0`, all warm
personas on the learned path every request, cold on `popularity` with `fallback_reason: cold-start`.

### Three caveats that must travel with this number

1. **The ranker artifact is not identical across the two baselines.** Pre-#168 served ranker
   `7897a151…`; this run served `e216bc44…` — my own #159 rebuild, which landed on main in between.
   That rebuild changed **only** `feature_names` (`Column_0…7` → the real names) and the matching
   `feature_importances` labels; `tree_sizes`, `feature_infos`, every split and every importance
   *count* are identical, so inference is numerically the same and the latency effect should be nil.
   But the comparison is **not** strictly artifact-identical and should not be described as such.
2. **One run per side.** No dispersion estimate brackets +0.464 ms, and p95 moved further in relative
   terms than p99. Treat it as an upper-bound-ish reading, not a tight estimate.
3. **Memory is not comparable across the pair.** The pre-#168 baseline predates #170, so it has no
   `host-memory-*` files. This run's: free pages 23497 → 12225, compressor ~532–536k pages, no
   container CPU-throttled (`nr_throttled 0` throughout). Tight but stable.

**Gap found in the harness:** the gate's `server-side.json` export does **not** carry the four new
columns, so the evidence directory alone cannot answer "what did the audit record". Those figures
came from a direct psql read. Worth a small PR — evidence should be self-contained.

**Also worth noting for query semantics:** the popularity route records `retriever_sha256 = ''`
(empty string, **not** NULL, not a sentinel). Anything asking "which artifact served this" must treat
`''` as "no artifact" rather than as missing.

Evidence backed up out of the overwrite path: post-#168 → `<scratchpad>/control-post168/`,
pre-#168 → `<scratchpad>/incumbent-pre168/`.
- HB 20:53  S1  post-#168 control done: p99 13.395416ms (+0.464 vs pre-#168). W10 waiting on #173 (4 checks pending).
- HB 20:56  S1  export gap closed on the PR branch (4 provenance cols + empty-string semantics, 4 tests, full suite 1707 passed). NOT active during either measurement so both halves used identical apparatus. Waiting on #173.
- HB 21:02  S1  W10 RUNNING: publishing the served SASRec bundle then the gate, on main 84d144b. Memory at start free+inactive 11.77GB / 67% (control window was 11.86->11.75GB, so the two halves match).

### S1b (W10) launched — artifacts verified present before starting

| role | path | sha256 |
|---|---|---|
| encoder | `artifacts/sasrec/a11af5ed…/sasrec-model.zip` | archive `43320b87…` |
| vocabulary + config | `artifacts/sasrec/a11af5ed…/sasrec-manifest.json` | — |
| popularity | `artifacts/sasrec/a11af5ed…/popularity-order.json` | **`9bc82187…`** (matches F's report — verified myself) |
| ranker learned | `artifacts/sasrec-ranker-step1/50d97188…/ranker.txt` | `7e2052c1…` |
| ranker fallback | `artifacts/sasrec-ranker-step1/bff5f86e…/ranker.txt` | `05610e60…` |

Both boosters located **by checksum**, not by filename. Both routes use the **eight**-column contract
(PR #151's per-route bundle) — *not* the ten-column learned-route contract, since ADR 0018 increment 1
was refused and is not being served.

**One caveat on the pair that I will carry into the PR:** the control ran on `c71ef4b`, W10 runs on
`84d144b` — they differ by #173. Its serving-path changes are confined to the SASRec family (the
`popularity` role is required for `sasrec` only; the item-item path is untouched), so the control's
number stands, but the two halves are **not** on an identical tree and I will not describe them as
such.
- HB 21:04  latency-pair  auto: free+inactive 12.3GB, pressure-free 68%, training procs 0; awaiting F popularity export
- HB 21:29  latency-pair  auto: free+inactive 12.1GB, pressure-free 67%, training procs 0; awaiting F popularity export
- HB 21:33  S1  W10 STATUS: bundle PUBLISHED + VERIFIED (schema 2, sasrec family, all checksums match in-container). Gate NOT run - blocked on champion registration; no promotion mechanism exists in the tree. Escalating.

## 2026-09-05 — S1b (W10): bundle published and verified; GATE NOT RUN

**Published and proven.** `serving-artifacts-publish` + `serving-artifacts-verify` + `--realise` all
exit 0. Schema **2**, family `sasrec`, tenant `demo`. All six artifacts re-hashed **from inside the
container**: every one matches, including `bundle-kind.json`'s pin of the manifest's own hash. A
direct `/rank` probe returns **500 candidates, all `sasrec`, zero popularity fill**, `encoder_ms`
1.833. Compose override is exactly two hunks (bind mount replacing the named volume at
`/app/models/serving`; the 55432 port line); `published: "5432"` count **0**.

Served manifest: encoder `43320b87…`, vocabulary/config `2fd61493…`, popularity `9bc82187…`,
learned ranker `7e2052c1…` (8 cols), fallback ranker `05610e60…` (8 cols). Lineage carries
`protocol_hash sha256:b4ed5afa…`, `run_id a11af5ed…`, `torch 2.12.0+cpu`, `faiss 1.14.3`.

**BLOCKED — and it is an ordering problem, not a missing command.** `ModelRankingService.rank`
refuses any request whose `ChampionCoordinates` ≠ the loaded manifest, then the API degrades to
popularity. Migration `0016` seeds `public.tenants` for `demo` with the item-item coordinates, and
its own comment says moving that row **"is a promotion — Phase 6's gate owns that"**. **No supported
promotion mechanism exists in the tree.**

Proved rather than assumed: with the bundle mounted and no registration, `demo-smoke` fails
(`Action Fan did not use learned two-stage serving: popularity`) and the audit rows read
`policy=popularity, fallback_reason=champion-mismatch, retriever_family=popularity-fallback,
encoder_ms=0`. **A gate run in that state measures the popularity fallback and labels it SASRec** —
and would look valid in a summary. Not run. Correct call.

So **W10 as specified may be impossible before W11**: measuring the bundle requires promoting it, and
serving has no notion of "serve this bundle without it being champion". Owner/orchestrator decision.

**Permission boundary:** a single `UPDATE public.tenants SET champion_*` on the *ephemeral demo*
Postgres was refused three times by the session's classifier. Stopped rather than routing it through
another tool or another session — that would bypass intent, not mechanism. Statement saved at
`scratchpad/w10/register-champion.sql`. Surfaced to the owner, **not** delegated to a peer.

### Two further findings, each worth its own item

1. **`synthetic/smoke/demo.py:244` hardcodes `item-item-cosine+lightgbm`.** A SASRec champion yields
   `sasrec+lightgbm`, so the runbook's smoke step **cannot pass for any non-item-item champion**.
   A real pin to the incumbent that W11 must clear.
2. **The sidecar cannot report which retrieval family it loaded.** `sasrec_retriever_loaded` never
   reaches stdout — uvicorn configures only its own loggers, so that module's logger has no handler
   in the container. `/healthz` plus a `/rank` probe were the only confirmation available. A real
   observability gap for a champion swap.

### A weakness that survives even once the gate runs

The demo tenant materializes **8 users / 120 items**; the encoder's vocabulary is **34,461**. SASRec
retrieves full-catalog candidates that are almost entirely feature-less, while the item-item fixture
can only retrieve inside the 120 materialized items. Latency stays measurable, but **no ranking-quality
conclusion is available from this stack**, and the two arms do measurably different work per request.
The honest cost of a full-catalog retriever — but not a like-for-like swap, and not to be reported as one.

Evidence: `scratchpad/w10/` (34 files). Control's backup at `scratchpad/control-post168/` untouched.
- HB 21:35  W11-mech  building promotion mechanism (src/release/promote.py + make promote, smoke policy derivation, sidecar logger handler) - one PR, then W10 retry

## 2026-09-05 — building the promotion mechanism (items 1–3), then W10

Orchestrator ruling: the missing piece is W11's own mechanism and it is my lane (`src/release/`).
One PR for three things that all block the same measurement:

1. **`src/release/promote.py` + `make promote TENANT=<t> BUNDLE=<dir>`** — verify every artifact
   checksum *before* writing, then set the three `champion_*` columns to the manifest's identities in
   one transaction via the migrator DSN, printing before/after, idempotent, with `--revert` restoring
   from a JSON snapshot written first. Explicitly **not** Phase 6's routing gate — the manual step
   ADR 0001 already assumes exists.
   Useful prior art the agent was pointed at: migration 0016 already maintains `updated_at` by trigger,
   added precisely because *"when did this tenant's champion last move is the first question a bad
   promotion raises"* — so verify the trigger fired rather than duplicate it. And
   `ck_tenants_champion_complete` means a champion is all three columns or none.
2. **`synthetic/smoke/demo.py:244`** — derive the expected policy from the served manifest's retriever
   family instead of hardcoding `item-item-cosine+lightgbm`. Must stay strict: it still has to fail
   when a warm persona silently degrades to popularity, which is the bug it exists to catch.
3. **Sidecar logger handler** — `sasrec_retriever_loaded` never reaches stdout because uvicorn
   configures only its own loggers. Without it an operator cannot tell which family the sidecar loaded.

**A boundary I am keeping:** a promotion on the *ephemeral demo* Postgres is a measurement setup, not
the production decision, and the W10 evidence will say so. If the classifier refuses `make promote` in
my session too, I stop and surface it to the owner rather than routing it through a peer.
- HB 21:38  W11-mech  F two-tower running 2-3h; building promote PR meanwhile; pair deferred until TWO-TOWER DONE + memory settled. NOTE: control taken 03:47Z predates the two-tower run, so the pair will need a THIRD baseline for a same-machine comparison.

## PRE-FLIGHT CHECKLIST for the baseline+W10 pair — all must hold before the window opens

Not one condition but four. Each is checkable; none is a judgment call I ask a reader to trust.

1. **`TWO-TOWER DONE` line exists** in `.coordination/reports/megatron.md` (watch armed).
2. **TMDB pipeline finished** — `TMDB_PIPELINE_DONE` in `artifacts/tmdb/logs/`, or `coverage.md`
   exists. Started 21:38, ~52 min, network-bound at nice 15, **with a Postgres load into the dev
   database.**
   *Why this one matters more than its nice level suggests:* the gate's Postgres runs on the **host
   block device**, not tmpfs, and every request commits a durable audit row before it answers. A
   concurrent Postgres load on the same disk contends on exactly the `fdatasync` that sits inside the
   p99. Low CPU priority does not make it harmless — it is an I/O neighbour, not a CPU neighbour.
3. **No live processes** — no trainer, no k6, no other compose stack. Verified by checking the
   *artifacts* those processes produce, **not** by `ps | grep`, which produced a false positive for
   two sessions earlier today because a wrapper shell's argv contains the pattern being searched for.
4. **Memory settled** — free+inactive and `memory_pressure` compared against what the control window
   actually recorded (**11.86 → 11.75 GB**), and the compressor drained rather than merely stable.
   A run that has just exited leaves a compressor that takes time to release. If materially tighter,
   **wait and say so** rather than measure.

Then: fresh baseline → W10, back to back, same medium (no tmpfs), the `!override` 55432 remap.
#170 writes host memory into the evidence for both, so the machine's fitness is a fact a reader can
check rather than a claim I make.
- HB 21:39  W11-mech  pre-flight checklist recorded (4 conditions incl. TMDB Postgres load as an I/O neighbour on the same disk as the gate's fdatasync)
- HB 21:58  S1  auto: free+inactive 8.6GB; W10 BLOCKED on champion registration (no promotion mechanism exists); bundle published+verified
- HB 22:01  W11-mech  PR #175 up (promote + smoke + sidecar logging). BLOCKER FOUND for W10: synthetic/load/recommendations.js:16 hardcodes item-item policy and asserts it per response - the k6 gate itself fails on a SASRec champion.

## 2026-09-05 — PR #176: the gate asserted the incumbent by name

`recommendations.js:16` and `pages.js:59` held `LEARNED_POLICY = "item-item-cosine+lightgbm"` and
asserted it on **every warm response**. Right strictness, wrong constant: a SASRec champion answers
`sasrec+lightgbm`, so **the gate would have failed the very model it exists to measure**, and reported
it as a latency verdict.

`run_gate.sh` now reads the family off the manifest **the sidecar actually loaded** (probed from the
running container — the demo stack mounts a volume over the baked bundle, so a path on disk can
disagree with what is served) and exports the composed policy; both scripts default to the old literal.

Not a whitelist: schema 2 names the family, schema 1's `candidate.artifact_type` *is* the same string,
and an unheard-of family composes correctly — a future two-tower bundle needs no change here.

**Every** failure path returns the incumbent literal. A gate that cannot read a manifest must behave
as it did before; refusing would have made this unsafe to land ahead of the bundle it is for.

Logic is a module (`synthetic/load/expected_policy.py`) rather than inline shell so CI covers it —
10 tests, and verified against both **real** manifests: fixture → `item-item-cosine+lightgbm`,
published SASRec bundle → `sasrec+lightgbm`.

Deliberately unchanged: the assertion. A warm request that degrades to popularity **still fails**.
The probe runs while the stack comes up, before warm-up — nothing touches the measured window.

1743 passed, lint/mypy clean.
- HB 22:05  W11-mech  PR #176 open (gate policy from manifest). #175 + #176 both needed before W10. Waiting on TWO-TOWER DONE.
- HB 22:23  S1  auto: free+inactive 12.3GB; W10 BLOCKED on champion registration (no promotion mechanism exists); bundle published+verified
- HB 22:23  pair-preflight  cond1 SATISFIED (TWO-TOWER FAILED 22:06, nothing running). Waiting on: TMDB_PIPELINE_DONE + #176 merge. Stale DONE-only watch killed - it would never have fired.
- HB 22:48  S1  auto: free+inactive 12.3GB; W10 BLOCKED on champion registration (no promotion mechanism exists); bundle published+verified
- HB 22:50  PAIR RUNNING  fresh baseline -> promote -> smoke -> W10, one agent so there is no gap between halves. Preconditions verified: #176 merged (main 6c3e5a4), 0 active Postgres queries (TMDB load not started), free+inactive 11.72GB vs control 11.86->11.75, load 1.73.

## 2026-09-05 22:49 — the pair is running

All four pre-flight conditions verified **independently**, not taken from the relay:

1. Two-tower run over — `TWO-TOWER FAILED 22:06`. (My watch was polling for `DONE` and would never
   have fired; the condition was miswritten as a success marker when what the pair needs is only that
   nothing competes for the box.)
2. TMDB **pull** still running but network-bound — **0 active Postgres queries** on the dev database,
   so the load step has not started. Recorded in both runs' evidence notes.
3. Nothing else live, checked via artifacts rather than `ps`.
4. Memory **11.72 GB** free+inactive against the control window's 11.86 → 11.75 GB; `memory_pressure`
   59% free; load 1.73. Comparable.

Plus #176 merged (main `6c3e5a4`), so the gate now derives its warm-policy assertion from the served
bundle instead of asserting the incumbent by name.

**Both halves are in one agent deliberately** — "back to back" is the requirement, and a single agent
is the only way to guarantee nothing slips between them.

Run B additionally: `make promote` (first real use of #175), the bind mount replacing the volume,
`demo-smoke` under a SASRec champion (first time that can pass), the newly-visible
`sasrec_retriever_loaded family=sasrec` boot line, and **`make promote-revert` afterwards — the first
exercise of the revert path against a real Postgres.**

PAIR COMPLETE 2026-09-05 23:05 PDT — the box is free; F may start the two-tower full run.

Stated plainly because F cannot distinguish "not done" from "done but unrecorded": **the pair is
finished and nothing of mine is running.** One half produced a number, the other could not run at all.
Both outcomes are final; neither is waiting on anything.

- **Run A (fresh incumbent baseline): PASS, p99 11.056625 ms.**
- **Run B (W10, SASRec): no number.** Not a timing or config problem — see below. It will not be
  retried on this stack, so there is nothing for F to wait behind.

## 2026-09-05 — the pair: Run A passed, Run B is structurally unrunnable on the demo stack

### Run A — fresh incumbent baseline, `GATE=pass`

main `6c3e5a4`. p50 **6.984333** / p95 **9.214459** / **p99 11.056625** / max 30.11 ms. 3301 requests,
throughput 54.767 rps, error rate 0, check rate 1, **0 over the 100 ms SLO**, `silent_learned_fallbacks`
0, single window, `remeasure: false`, `steal_peak_pct 0.0`. Not tmpfs; **0.268 ms per WAL sync** across
3,334 syncs. #176 confirmed working — the gate's own stderr derived
`item-item-cosine+lightgbm` rather than asserting it.

All four provenance columns correct, **0 NULLs**: `retriever_sha256` = `7fa2f3be…` on every learned
row, matching the candidate index exactly; `encoder_ms` 0.0 for item-item, which is the right answer.

### Run B — no p99, and the reason is structural

`make promote` worked. The bind mount worked. The sidecar booted on SASRec and — for the first time —
**said so out loud**: `sasrec_retriever_loaded family=sasrec … items=34461 index_type=flat-ip-exact`.
All five pinned artifacts re-verified in-container. `make promote-revert` restored cleanly **on its
first ever run against a real Postgres**.

Then `demo-smoke` failed, and the audit row names it exactly: `fallback_reason =
empty-learned-result`. A direct sidecar probe with Action Fan's real history shows the retriever is
**working perfectly** — 12 seeds, `encoder_ms` 1.0759, 500 candidates all from `sasrec`. But:

> **of the sidecar's top-50 ranked ids, 0 are present in the demo catalog.**
> demo tenant: **120 movies**. SASRec vocabulary: **34,461** items.

`hydrate_ranked_movies` joins against the tenant's `movies` table, finds nothing, and the API falls
back to popularity. **That is fail-closed behaviour working as designed.**

**So W10 cannot be run on this stack at all.** Every warm request would answer `popularity`, the gate
would fail on `checks rate==1`, and it would report that as a gate failure with a healthy p99
underneath — a number that looks like a latency result and is not one. Running it was declined.

**This is worse than the caveat I wrote earlier.** I said "latency comparable, ranking quality not
concluded". In fact the *serving path itself* cannot complete end to end on a 120-item catalog.
W10 needs either a full-catalog demo tenant or a bundle published against the fixture — an owner
decision, not a retry.

### A sharp edge in #175 worth fixing

**`make promote` needed `POSTGRES_PORT=55432` to reach the demo database.** Without it
`Settings.database_url` resolves to `localhost:5432` — **the other session's Postgres**. Nothing in
`make promote` warns which database it is about to repoint. The agent caught it by running `--dry-run`
first and matching `champion_before.updated_at` against the row it had already read. That defensive
step is the only reason this is a note rather than an incident.

### Machine state

Both runs under **real memory pressure** — swap pinned at 6628 MB / 7168 MB throughout, compressor
490–680k pages. It did not move Run A's verdict (steal 0.0%, `storage_stall: no`, 9x headroom under
the threshold), but it is exactly the kind of pressure that moves a tail without moving steal. Run B
produced no `host-memory-*` files because no window opened, so the like-for-like memory comparison
does not exist.

## 2026-09-05 — W27 design: a `demo-full` tenant is impossible; checked before choosing

I was leaning toward a second tenant to protect the frontend screenshots. **The schema forbids it.**

`movies` columns are exactly `movieId, title, genres` — **no `tenant_id`.** It is a global table; the
only tenant-scoped movie table is `user_movie_state`. Two tenants in one database therefore **cannot**
have different catalogs. **The catalog is a property of the database, not of the tenant.**

**This also corrects my own last report.** I said "the demo tenant's catalog is 120 movies" — right
number, wrong reason. The demo *stack* runs its own Postgres in the `movielens-demo` project, seeded
from `synthetic/personas/catalog.json` with 120 movies; the dev database holds all **62,423**.
Hydration joins global `movies` plus a `LEFT JOIN` on RLS-scoped `ratings`, so the failure was never
tenant filtering — those 62k rows simply are not in the demo stack's database.

**So W27 part 1 = seed the demo stack's `movies` *and* `links`** (hydration joins `links` for
`tmdbId`) with the full catalog. No new tenant, no RLS work, no isolation surface change.

### Two couplings to check before building, not after

1. **`demo_artifacts.py:43` is `SELECT "movieId", genres FROM movies` with no filter**, feeding
   `FeatureIndex.build`. Growing the demo database's catalog 120 → 62,423 changes the feature index
   the demo bundle is built from, which could move `ranker.txt`'s bytes and break
   `serving-artifacts-check` — the gate guarding non-negotiable #5. Must be confirmed, and if it moves
   the booster that is a real coupling to decide rather than absorb.
2. Whether `demo-smoke` encodes catalog size anywhere beyond the policy string #175 fixed.

### Named consequence for the frontend

Growing the catalog changes what `/browse` renders for every persona, so the evidence sets in
`docs/frontend/evidence/` will describe a build that no longer exists. Not a reason to stop — the
README already tracks which sets describe the current build — but it belongs in the PR rather than
being found later by whoever opens those screenshots. `enrich_details.py` also assumes 120 titles of
TMDB enrichment; enriching 62k is a different proposition and is not in scope, so poster coverage
outside the original 120 will be absent and Browse will look **sparser**, not richer.

**Order:** the `make promote` guard first (small, and the only thing standing between someone and
repointing the wrong database), then W27 while the two-tower run has the box.
- HB 23:10  guard+W27  both launched: promote target guard (print host/port/db/tenant, refuse likely-wrong target, near-miss as a test) and W27 full demo catalog (seed movies+links, materialize, check the demo_artifacts.py:43 fixture coupling)
- HB 23:13  S1  auto: free+inactive 12.4GB; W10 BLOCKED on champion registration (no promotion mechanism exists); bundle published+verified

## 2026-09-05 — PR #177: the promote target guard

Closes the near-miss where `make promote TENANT=demo` would have repointed **the other session's
Postgres** with a normal success message, because every migrated database carries a `demo` row so
"the tenant exists" is not a safety check.

Prints before any read or write, to stderr (stdout stays a parseable summary):

```
[promote] TARGET  localhost:5432/movielens  tenant=demo  operation=promote
[promote]         POSTGRES_PORT is unset, so 5432 is the built-in default
```

**Two design calls better than what I specified.**

1. **Keys on whether the port was *stated*, not its value** — `"postgres_port" in
   settings.model_fields_set`. So `POSTGRES_PORT=5432 make promote` **passes**: that operator named
   their target. Keying on the number would refuse a deployment legitimately on 5432, and a guard that
   fires on legitimate use trains `--yes` by reflex until it protects nothing.
2. **Deliberately does NOT derive the default tenant from `dev_bypass_tenant`'s instance value**,
   because `DEV_BYPASS_TENANT=demo` would then **silently disable the guard** — an auth setting
   switching off a database-safety check. Added `DEFAULT_TENANT_ID` to `src/config.py` and compares
   against that one constant rather than a denylist, so a tenant added by a future migration is
   guarded on arrival.

Verified it did not damage #166: `TRAINING_TENANT_ID = DEFAULT_TENANT_ID`, `WHERE tenant_id` intact.

**Proved the guard is load-bearing** by deleting its call, confirming two tests fail, and restoring.
The ordering test reads captured stderr *from inside* `create_engine`, dating the banner against the
first moment the tool could touch anything; the refusal test replaces `create_engine` with something
that fails if called, so "never reads the wrong database" is proven, not asserted.

`--dry-run` prints the target but is ungated — it writes nothing, and gating it would block the
rehearsal that caught this.

1784 passed, lint/mypy clean.
- HB 23:22  guard done (#177); W27 still running

## 2026-09-05 — W27 done (PR #178): the demo stack carries the full catalog

**The fixture booster did NOT move.** `serving-artifacts-check` exits 0 before *and* after; committed
bundle untouched, no rebuild needed. `FeatureIndex.build` uses `movies` only for the genre map and the
fixture's queries only name the reviewed 120. What *would* have moved it: **raw MovieLens differs from
the reviewed fixture on 31 titles and 3 genre strings** (upstream `IMAX` on 150/364/595). The insert
order prevents it — reviewed fixture first, bulk snapshot second with `ON CONFLICT DO NOTHING` — and
that ordering is **pinned by a test**, not left as a comment.

**Seeded from a committed snapshot** (`movielens-catalog.csv.gz`, 1.3 MB, deterministic) rather than
`data/raw/` at seed time: the seeder runs inside the API image which `COPY`s `synthetic/` and never
`data/`, the runbook promises a clean checkout suffices, and a catalog present only where someone ran
`dvc pull` would make the reproducibility gate **machine-dependent** — the same failure #166 fixed
from the other side.

| | before | after |
|---|---:|---:|
| `movies` / `links` | 120 | **62,423** (62,316 with tmdbId) |
| `movie_catalog_metadata` | 120 | 62,423 |
| `user_item_features` / generation | 960 | **499,384** |

Materialization **81.25 s**; seeding ~15 s, idempotent, no workarounds. `item_features` stays at 120
**deliberately** — `_ITEM_SQL` groups over ratings, and offline an unrated item is popularity 0 / age 0,
exactly what `_finite_float(None)` yields online; growing it would *introduce* offline/online skew.

**Hydration proven through the real path:** `hydrate_ranked_movies` on an RLS-scoped connection,
50 ids from outside the reviewed 120 → `requested=50 hydrated=50 order_preserved=True`.

**Best catch, and it was unasked-for:** `demo-smoke`'s coverage walk under the **title** sort reached
**1 of 120** reviewed titles (only reviewed titles carry seeded interactions); under **popular** it
reaches 120/120. *"Without this the check would have passed by looking at nothing."* A check that
passes by examining nothing is worse than none, because it reports confidence — the same class as the
k6 gate asserting the incumbent's name.

**Only CI can settle two things**, both named rather than assumed: whether `demo-seed`'s extra ~95 s
is acceptable in `synthetic-load-smoke` / `browser-auth-e2e`, and whether `/browse`'s page budgets hold
at 62,423 rows. If seed time pushes those jobs, that is a real cost of W27 to decide deliberately.

**It correctly declined to run the k6 gate** beside a possible two-tower run. W10 is unblocked in
principle, still unmeasured; needs #178 on main and a quiet box.

Named, not fixed: 13 frontend evidence sets now describe a `/browse` that no longer exists; Browse is
**sparser** (62,303 titles without posters); `feature_store.*` appends 499,384 rows per release
instead of 960 (runbook D8 moved from principle to schedule).
- HB 23:35  W27 done (#178). Open: #177 guard, #178 catalog. W10 needs #178 on main + a quiet box.
- HB 23:38  S1  auto: free+inactive 8.3GB; W10 BLOCKED on champion registration (no promotion mechanism exists); bundle published+verified

---

# FINAL BLOCK — 2026-09-05 wind-down (Starscream / serving lane)

Nothing of mine is running. Heartbeat loop stopped, all watches stopped, **no demo or staging stack
exists**, and the owner's dev containers are untouched (`postgres` + `mlflow`, up 19 h, still holding
`0.0.0.0:5432`). I keep **no** `HANDOFF-starscream.md`; this block is the handoff.

## Merged to main today

`#163` seed-order restore · `#164` status ledger · `#165` amd64 encoder evidence · `#166`
training-frame tenant filter · `#167` sidecar `implicit` import fix · `#168` audit provenance columns
+ migration 0019 · `#169` v2 bundle publisher · `#170` gate host-memory evidence · `#171` CLAUDE.md
tree · `#172` rollback rehearsal (W23) · `#175` promotion mechanism + smoke policy + sidecar logging ·
`#176` gate policy from manifest · `#177` promote target guard. Plus the `#159` bundle rebuild
(`e2c7d87`) pushed to its branch.

**Open: `#178`** (W27, full demo catalog) — with the orchestrator's shepherd, merges on green.

## Numbers of record

| | |
|---|---|
| **Current incumbent control** | **p99 11.056625 ms** (fresh, main `6c3e5a4`), p50 6.984333, p95 9.214459, 3301 reqs, 0 over SLO, 0 silent fallbacks, steal 0.0%, not tmpfs, 0.268 ms/WAL sync |
| Cost of #168's four audit columns | **+0.464041 ms** at p99 (12.931375 → 13.395416), handler *faster*, whole increase outside it, WAL sync 0.283 → 0.303 |
| In-image encoder (amd64) | **p99 1.0047 ms** vs a 15 ms budget; hook-free **floor** — SASRec must be measured *with* #168's forward hooks |
| SASRec retrieval verdict (unchanged) | warm +16.57%, cold +0.00%, overall +11.16%, `promote` |

## W10 — still unmeasured, and why

Bundle **published and verified**: schema 2, family `sasrec`, all six artifacts re-hashed inside the
container, `/rank` returns 500 candidates all from sasrec, `encoder_ms` 1.0759. Two blockers were
found and fixed (champion registration → #175; the gate asserting the incumbent by name → #176). The
third was structural: the demo database held **120 movies** against a **34,461**-item vocabulary, so
hydration returned nothing and the API fell back to popularity, correctly. **#178 fixes that.**

**Preconditions after #178 lands:** a quiet box, then fresh baseline → `POSTGRES_PORT=<port> make
promote TENANT=demo BUNDLE=infra/model-bundle-served` → `demo-smoke` → W10, back to back, **no tmpfs**,
the `!override` 55432 remap. Both halves must run the same gate build.

**Twice an agent declined to produce a p99** — once against an unregistered champion, once against a
catalog the retriever could not hydrate into. Both would have yielded a number that looked valid in a
summary and measured the popularity fallback.

## Proven for the first time tonight

- `make promote` — all three champion columns in one transaction, five checksums verified first.
- **`make promote-revert` against a real Postgres, first try**, restoring from its snapshot.
- The sidecar finally *says* which family it loaded: `sasrec_retriever_loaded family=sasrec items=34461
  index_type=flat-ip-exact`.
- A **database-ahead rollback is safe**; the dangerous state is the database *behind* the writer,
  which is a total serving outage because the audit commit precedes the response (#172).

## Open decisions / owner's call

1. **W10's remaining run** — needs #178 on main and a quiet machine.
2. **Frontend evidence:** 13 sets in `docs/frontend/evidence/` now describe a `/browse` that no longer
   exists; Browse is **sparser** (62,303 titles without posters). Named, not fixed.
3. **CI cost of W27:** `demo-seed` gains ~95 s; whether that is acceptable in `synthetic-load-smoke`
   and `browser-auth-e2e` is for CI to settle on #178.
4. **Queue S2–S6 untouched** — S4 (frontend audit-provenance disclosure) and S6 (ONNX memo, *owner
   decision required after*) not started, per the wind-down.

## Evidence

`scratchpad/evidence/runA-load-smoke/` (the current control), `scratchpad/control-post168/`,
`scratchpad/incumbent-pre168/`, `scratchpad/w10/` (34 files), `scratchpad/host-port-relief.yml`,
`scratchpad/served-bundle-mount.yml`. **`PAIR COMPLETE` was posted at 23:05 and F is released.**
