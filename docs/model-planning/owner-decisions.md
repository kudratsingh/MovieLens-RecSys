# Owner decisions and work items

From 2026-09-05 the modeling work moved fast enough that questions for the owner and the
units of work answering them were numbered as they came up: `O-n` for an owner decision,
`Wn` for a work item. ADRs, [`../results.md`](../results.md), experiment records, tests
and source comments cite those IDs — "see O-6", "W17 landed the fix" — so this page is
where they resolve.

Each entry is short on purpose. The reasoning lives where the decision landed: the ADR,
its dated note, the results section, or the PR. The earlier `D-nnn` decisions are in
[`03-decision-register.md`](03-decision-register.md); `M0`–`M8` and `M0-nn` are this
folder's work packages and backlog, defined in [`README.md`](README.md#program-map) and
[`work-items.md`](work-items.md).

## Owner decisions

`#n` is a pull request. A decision marked *provisional* was taken as the working answer so
work could continue, and the owner may still overrule it. Numbers not listed were
local operational items (a credential to supply, a development-database cleanup) or were
never used.

| ID | Date | Question | Decision | Where it landed |
|---|---|---|---|---|
| O-1 | 2026-09-05 | What does the ranking gate read for a change confined to the learned route, given that cold users on unchanged routing are about three quarters of overall NDCG@10? | **Warm-primary.** A learned-route change needs warm NDCG@10 ≥ +3% with cold non-regression inside ADR 0001's tolerance; overall is reported, not gated. Changes touching both routes keep the overall +3% rule. | ADR 0001 amendment 2026-09-05; `--scope learned-route` in #155 |
| O-2 | 2026-09-05 | Approve TMDB metadata ingestion once, as a shared dependency for content retrieval and item features? | Approved and widened: pull everything useful (details, keywords, credits, release dates, collections, language, runtime, image paths, vote and popularity stats), rate-limit-safe and resumable, DVC snapshot plus Postgres. Time-varying stats are stored but marked not point-in-time-safe as features. | #157 (ingestion), #181 (snapshot and coverage); [`../data/tmdb-metadata.md`](../data/tmdb-metadata.md) |
| O-3 | 2026-09-06 | Rent a cloud GPU for full-data sequence-model runs, accepting CUDA non-determinism? | **Stay on CPU.** Fix the SASRec training loop first. Rent one on-demand GPU only when a predeclared cell costs more than one night on the fixed CPU loop, and write the GPU non-determinism tolerance into that rung's ADR before its first run. | ADR 0020 ("Cost, honestly"); unchanged by O-22 |
| O-4 | 2026-09-05 | What happens to the stale `docs/progress.md`? | Archive it under `docs/records/`. | #164 → [`../records/progress-log-2026-05-31.md`](../records/progress-log-2026-05-31.md); D-014 |
| O-5 | 2026-09-05 | Does ~5% relative warm seed dispersion at 6% change the one-run-per-configuration policy? | **Open.** Working answer: no — the spread averages down over 1,931 full-data warm users, and SASRec's +12.88% lower bound survives it. The three-seed study is recorded as a raw measurement (warm range 9.71%, cold 0 by construction), not a certified tolerance; revisit only if a full-data claim lands within 5% of its threshold. | #153; `results.md` "SASRec seed dispersion at 6%" (M0-14) |
| O-6 | 2026-09-05 | When the ranker trains on a sequence retriever's candidates, how do exclusions and learned-vs-popularity routing work? | Mirror `ranker.py`: unfiltered top-500, then point-in-time exclusions on the negatives pool, so both arms share one protocol hash. Route on the full training history, as the incumbent does; only the sequence query is point-in-time. The count of positives whose strict prefix is below the threshold is logged per arm. | #151; `src/training/sasrec_ranker.py` |
| O-7 | 2026-09-05 | One LightGBM booster retrained on SASRec candidates gains +25.96% warm but loses 53% cold. Serve one ranker per route? | **Per-route boosters.** The bundle is the SASRec encoder and index, a booster retrained on the learned route, and the incumbent booster on the popularity route. It clears the end-to-end gate; the champion swap still waits on the serving gates. | #151; ADR 0016 notes |
| O-8 | 2026-09-05 | Approve Rung 3 (sequence-aware ranking) as the next rung? | Approved: increment 1 exposes the SASRec score as point-in-time LightGBM features; increment 2 (DIN-style attention) is built only if increment 1 gains ≥ 3% warm NDCG@10 and the owner names a target. | #152 (ADR 0018), #159 (increment 1, refused at +2.78%) |
| O-9 | 2026-09-05 | A left-padded sequence encodes to NaN in eval mode, so SASRec retrieved nothing for histories shorter than 50. Fix it inside Rung 3 or separately? | Separately. Rung 3 increment 1 shipped against the defect on both arms, keeping that comparison internally valid; the one-line fix (`torch.backends.mha.set_fastpath_enabled(False)`) landed as its own PR with the SASRec line re-measured. Serving applies the same fix at load. | #162 (W17); ADR 0016, ADR 0018 dated notes |
| O-10 | 2026-09-05 | The two-tower FAISS mapping fix moved the 6% result from 0.0435 to 0.3759. Run one corrected full-data measurement? | Yes, one run at a predeclared configuration (exact FAISS, 16,384 sampled negatives), stated as a different cell from the original arm. No sweep, no seed repeats. | #158 (fix), #182 (full run: warm recall@500 0.5113, gate promote) |
| O-12 | 2026-09-05 | Serving SASRec puts torch and FAISS into the private model sidecar. Accept, or require an ONNX export first? | *Provisional:* accept a pinned CPU-only torch wheel plus `faiss-cpu`, with CI refusing any CUDA package and recording image size. ONNX export becomes W20. The API image stays slim. | #160, #161; `infra/features/requirements.txt` |
| O-14 | 2026-09-05 | Offline training read ratings across tenants, so seeded demo rows reached the trainers. Fix? | Filter the training frame to the MovieLens tenant, with a test; it reproduces every recorded number. | #166 |
| O-15 | 2026-09-05 | What does the k6 gate measure and the champion swap promote: the real pinned full-data artifacts, or a demo-scale SASRec? | *Provisional:* the real pinned artifacts. A p99 measured on a different model cannot justify the swap; the persona-trained bundle stays a named fixture. | #169 (bundle publisher); the served payload is not yet published |
| O-17 | 2026-09-05 | `PopularityModel.fit` used an unstable sort, so tie order could change across pandas versions. Make it stable? | Yes: stable sort with an ascending `movieId` tiebreak, in its own PR, with the popularity baseline and the SASRec fallback evaluation re-run. | #180; see O-21 |
| O-18 | 2026-09-05 | There was no supported way to set a tenant's champion, so the k6 gate could not serve SASRec without promoting it. | *Provisional:* build `make promote` (verify checksums, transactional update, before/after, revert); derive the gate's expected policy from the served bundle. The production swap (W11) uses the same command. | #175, #176, #177 |
| O-19 | 2026-09-05 | The demo tenant held 120 movies, so a full-catalog bundle could not hydrate. Full-catalog demo tenant, or a fixture-published SASRec? | *Provisional:* full-catalog demo tenant (W27), so the gate measures the bundle that would ship. | #178 |
| O-20 | 2026-09-06 | Rewrite the SASRec training loop to canonical all-positions training before any ADR 0020 cell? | Approved; the GPU question (O-3) waits until the fixed loop's CPU cost is measured. | #183 (W28) |
| O-21 | 2026-09-06 | O-17's stable tiebreak moved SASRec cold NDCG@500 by −5.2e-6 and overall by −1.4e-6; recall, warm and all bundle metrics were unchanged. Adopt the deterministic rerun as the record? | Approved: the deterministic run is the record, the earlier run is kept and marked superseded with the delta stated, no threshold or verdict changes. | #180; `results.md` |
| O-22 | 2026-09-11 | All-positions training fired ADR 0020's stop rule (−4.62% warm recall@500 at full scale, 9.8× faster). Which objective do SASRec-v2 cells use? | The copied-prefix objective stays the objective of record; rewrite its data path to be memory-bounded (M4b). All-positions is kept as a named ablation, never the silent baseline. GPU only through O-3's trigger. | #183, #179; ADR 0020 decision note |
| O-23 | 2026-09-15 | Approve ADR 0019 (Rung 5, multi-retriever mixing) and ADR 0020 (SASRec v2, with the O-22 amendment)? | Both approved. Order: W10 and W11 first, then M4b, then Rung 5 increment 1, then ADR 0020 cells. | #174, #179, #186 |
| O-24 | 2026-09-15 | Dependabot reported eleven alerts on `main`, four critical. | Clear the critical and high set in `web/`; two moderate alerts that need a Vitest major and one low torch alert are deferred to their own changes. | #185 |
| O-25 | 2026-10-05 | The 6% pilot subsample computed its own 80th-percentile cutoff, which lands 23.5 days past the sealed boundary 1469256597, so every 6% pilot to date fitted 4,870 and scored 7,528 sealed rows (WO-1 preflight). Which pilot protocol replaces it? | **Cut the 6% sample at the full split's cutoff**: the subsample inherits the full frame's `temporal_split` boundaries instead of its own quantile. The pilot protocol hash changes; the September pilot values 0.3186 / 0.3103 / 0.3258 / 0.2957 are compromised and are replaced by WO-1's four new-path pilots. The earlier reads of the WO-8 window are a contamination fact, recorded in the sealed-test memo, not erased. | WO-1 (PR #194); dated note in `memos/sealed-test-and-dataset-policy.md` |

## Work items

| ID | What it was | Outcome |
|---|---|---|
| W5 | TMDB metadata pull, Postgres load and coverage record (O-2) | #157, #181 |
| W8 | The private model sidecar loads and serves a per-route SASRec bundle, applying the O-9 fix at load | #161 |
| W10 | The unchanged authenticated k6 gate with the SASRec bundle serving, on the full-catalog demo | Open — incumbent control measured at p99 11.06 ms; the SASRec bundle not yet measured |
| W11 | Champion swap to the per-route SASRec bundle, item-item kept as the rollback | Open — waits on W10 |
| W15 | The next sequence-model proposal (wider or longer SASRec), which reopens O-3 | Became ADR 0020 (#179, approved 2026-09-15); no cell run yet |
| W17 | The SASRec fast-path (NaN) fix and the re-measurement of the SASRec line (O-9) | #162 — warm recall@500 0.4652 → 0.5092 |
| W18 | Two-tower FAISS row-mapping fix | #158 |
| W19 | Corrected two-tower v2 full-data run and its retrieval gate (O-10) | #182 — warm recall@500 0.5113, gate promote |
| W20 | ONNX export of the SASRec encoder, removing torch from the serving image (O-12) | Open — not started |
| W21 | Publish the full-data popularity fill order inside the SASRec bundle | #173 |
| W26 | The torch/FAISS OpenMP collision on the training Mac; `OMP_NUM_THREADS=1` alone does not fix it | Discharged by #183: the training loop scores by exact `torch` top-k, and FAISS is built once after fitting |
| W27 | Full-catalog demo tenant, so the gate can hydrate a full-data bundle (O-19) | #178 |
| W28 | Rewrite the SASRec training loop to predict all positions per window (O-20) | #183 — 9.8× faster, −4.62% warm recall@500; see O-22 |
| M4b | Memory-bounded data path for the copied-prefix SASRec objective (sequence length 200 feasible, equality-tested against the current builder). Despite the name it is not part of the program map's M4. | Open |
