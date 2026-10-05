# Project status — the ledger

> Moved out of `CLAUDE.md` on 2026-08-30 so the instruction file stays short; this folder is the full ledger and `CLAUDE.md`'s "Current status" is its summary. Update both when something lands: the ledger with the detail, the summary only when the shape of the project changes.

The dated header paragraph as it stood when the ledger moved, then the current step. Per-phase detail: [`phase-1.md`](phase-1.md), [`phase-2.md`](phase-2.md), [`phase-3.md`](phase-3.md) — the last one carries the two *remaining work* bullets (product track, platform track).

## Where the project stands

**As of 2026-08-30 (kept as written when the ledger moved).** Phase 1 and Phase 2 are complete. Phase 3 is underway: its architecture ADRs (0007–0012), the auth/tenancy foundation, the Feast-backed learned online recommendation path, durable demo personas, prediction audits, the measured k6 latency gate, and the whole movie-discovery frontend — Discover, Browse, movie detail, Library, and Quick Picks behind one shell, with `/` cut over to the product — are all on `main` (PRs #27–#82, the last stretch being a product round on the cutover build). The frontend finish gate has now been run twice, and every criterion a reviewer can settle passes; the remaining HOLD is moderated research with real participants, which is mine to run and not a reviewer's to substitute for. On top of that, the production deployment now exists as an artifact rather than an intention — ADR 0013 pins one Hetzner CX22 running the same `docker-compose.prod.yml` the rehearsal runs, ADR 0014 closes the rate-limiting question with a measured finding, `docs/deployment-runbook.md` is the operational document, and the production-mode rehearsal has been run end to end and its defects fixed — but nothing is deployed, because the machine does not exist yet. The current concrete step (the one to take next) is at the bottom of this section.

## Current step

**2026-10-05 — the Phase A brief is adopted; serving is parked.** The owner's build brief of this date
sets the next phase: the SASRec encoder written by hand, trained at a larger size, and compared fairly
against every existing model, with each headline number backed by three runs and more than one time
window. Its executable form is
[`../model-planning/phase-a-work-orders.md`](../model-planning/phase-a-work-orders.md). Seven decisions
shape it:

- **D1** — larger transformer first (Phase A); the generative recommender second (Phase B, a Rung 7
  proposal as ADR 0021 after WO-9).
- **D2** — stay on MovieLens 25M for Phase A.
- **D3** — serving is parked: nothing is being served.
- **D4** — try to repair the fast trainer before paying for the slow one (ADR 0020 amendment).
- **D5** — three training runs for the final headline models only (`CLAUDE.md` policy note).
- **D6** — hardware order: laptop CPU, then the Mac's GPU, then a rented GPU, with the accepted
  difference written into ADR 0020 before any non-CPU run.
- **D7** — "from scratch" has a written definition: no `nn.Transformer*`, `nn.MultiheadAttention`,
  `nn.LayerNorm`, `F.scaled_dot_product_attention` or `F.multi_head_attention_forward` under `src/`.

**What is open now** is WO-1 to WO-9, in order, one PR each: restore the trainer of record, the
hand-written transformer, repair the fast trainer, trainer upgrades, the larger models, the winner end
to end, repeat runs and rolling windows, the one-time sealed-window read, and the write-up. WO-1 and
WO-2 come first. Rung 5 (ADR 0019) waits behind Phase A.

**Parked, not dropped.** The SASRec bundle's k6 measurement and the champion swap (W10, W11), the
Hetzner deployment, the moderated frontend sessions and `/legacy` retirement are out of scope until
Phase A closes; the 2026-09-15 list and the deployment and moderated-session blocks below stay as the
record of what is owed. One deploy prerequisite belongs with them: `infra/model-bundle-served/` holds
only a placeholder, and production — which points its sidecar at that directory — will not boot until
`make serving-artifacts-publish` writes a bundle into it.

**2026-09-15 — the ledger for the 5–11 September wave, and the step that follows it.** Thirty-three PRs
landed, #149–#183. The modeling numbers of record are now: item-item warm recall@500 0.3991;
SASRec **0.5092** after the eval-mode fast-path defect was fixed (#162 — left-padded histories under 50
items encoded to NaN and got empty slates; 131 warm users were affected) and re-recorded deterministic
at #180; the corrected two-tower v2 **0.5113** (#158 fixed an off-by-one between FAISS rows and item ids
that had made every earlier two-tower number a measurement of a bug; #182 records the full run and its
`promote`). Both learned retrievers clear ADR 0004's retrieval gate under the single-seed regime. The
per-route bundle — SASRec candidates, a LightGBM retrained on them for the learned route, the incumbent
LightGBM kept for the popularity fallback route (owner decision O-7) — clears ADR 0001's end-to-end gate at
overall NDCG@10 **0.2218 vs 0.2008** (+10.4%, warm +39%; #151, re-measured in #162). Rung 3 increment 1
(SASRec score features in the learned-route ranker, ADR 0018) was measured and **refused** at +2.78%
warm against the +3% bar (#159): the encoder's score on its own candidates tells the ranker little beyond
rank; DIN is not built. The canonical all-positions SASRec objective was measured (#183, merged 15 September): 9.8×
faster and 4.62% worse at full scale, so it stays an opt-in ablation and the copied-prefix objective remains
the one of record (O-22). Approved by the owner on 2026-09-15 (O-23): ADR 0019 (Rung 5, multi-retriever mixing, #174)
and ADR 0020 (SASRec v2 capacity cells, #179, with the O-22 amendment), in the order W10 and W11 (the
k6 measurement and the champion swap), then M4b (the memory-bounded data path), then Rung 5 increment
1, then the ADR 0020 cells. *(2026-10-05: that order is replaced by Phase A — see the block above.)*

The serving path caught up with the models in the same wave: manifest v2 with one ranker per route and
lineage, a generic retriever interface, the private sidecar loading a SASRec bundle fail-closed (with the
fast-path fix applied at load and the history order pinned by test after #163 caught it reversed), CPU-only
torch pins with a no-CUDA check (#160), retrieval provenance columns on every audit row (#168, with the
encoder's own time measured by forward hooks), a bundle publisher that bakes the demo fixture and the served full-data bundle at distinct
paths with the production compose selecting the latter (#169), the popularity fallback shipped inside the
SASRec bundle (#173), `make promote` / `promote-revert` with a target guard (#175, #177 — the first manual
champion promotion the project has had), the k6 gate deriving its policy from the bundle under test (#176)
and recording host memory (#170), an executed migration rollback rehearsal (#172 — the hazard is a database
*behind* the image, not ahead), the demo database seeded with the full 62,423-movie catalog (#178), and
the TMDB metadata snapshot for 98.5% of the catalog, DVC-tracked and loaded (#181). The evaluation gate
gained a learned-route scope (#155, O-1: warm NDCG@10 +3% with cold non-regression for changes confined
to the learned route) and the training frame is scoped to the MovieLens tenant (#166).

**What is still open, in order.** (1) The SASRec bundle's authenticated k6 measurement on the
full-catalog demo — its incumbent control is p99 11.06 ms — and then the champion swap by `make promote`,
which the owner has authorised once that gate passes. (2) The copied-prefix data-path rewrite (memory-bounded,
sequence length 200) that O-22 chose over the all-positions objective. (3) The sequence-valid synthetic
cold cohort (ADR 0011's h10 slice is timestamp-tied and cannot measure a sequence model). (4) The owner's
one remaining decision from that list — a DVC remote for the 436 MB TMDB snapshot, which today exists
only on one machine — since ADR 0019 and ADR 0020 were approved on 2026-09-15. The Hetzner deployment and the moderated frontend sessions below remain owed and unchanged.

**2026-09-04 — the modeling track is executing, and it moved ahead of the deployment.** A full-data
SASRec run at seed 42 was launched on the local machine; everything below about approving a roadmap
rung was superseded by the fact that Rung 2 was being measured. That run has since landed and been
adjudicated — see the 2026-09-05 block below, which is the current reading. What landed today is the evaluation
apparatus that run will be judged by, none of which existed this morning: a versioned protocol
manifest and a semantic hash (#125), a retrieval recall@500 gate with four explicit states and no
tolerance defaults (#125), serving-equivalent candidate exclusions in ranker training plus the tested
Python/Feast boundary (#126), the ADR 0009 amendment withdrawn and the feature-source options costed
and deferred (#127), the last-item transition baseline D-003 requires as a control (#128), and the
instrument that measures the gate's outstanding tolerances (#129), the sealed-test policy with the
run template enforcing a partition declaration (#131), rolling-origin backtest windows and their
clustered user bootstrap (#132), the per-user recall vectors the tolerance study consumes (#133),
and a protocol manifest emitted by every candidate trainer (#134) — which is what makes any future
run gate-admissible at all.

Three things were true about that run and worth stating before its number arrived. The gate then
returned `incomplete` on one seed by construction, so seed 42 could authorize seeds 7 and 13 or stop
the line, and nothing else — a constraint the owner lifted on 2026-09-05 (see the experiment cost
policy in CLAUDE.md), with the gate amended to match in #139. The tolerances the gate needs are still unmeasured — the instrument exists, but no
trainer yet exports the per-user recall vectors it consumes. And the pilot that chose this
configuration measured popularity, item-item and SASRec on the same 6% subsample at 0.1974, 0.3619
and 0.3186, which puts SASRec **12.0% below the incumbent** rather than above it; the pilot reads as
a pass because ADR 0016's stop rule named popularity and never named item-item. The advance/stop rule
is therefore the decision that matters most right now, and it is costed in
[`../model-planning/memos/d003-full-run-stop-rule.md`](../model-planning/memos/d003-full-run-stop-rule.md).

**The run then landed, and the predeclared bands fired cleanly.** Against the *matched* incumbent
partition, seed 42 scored warm recall@500 **0.465169** against item-item's **0.399057** —
**+16.57%**, with a one-sided 95% lower bound of **+12.88%** (half-width 3.69%, population-only,
n=1931) against a required floor of +3.00%. Cold is **+0.00%** (0.526273 both sides) and overall
**+11.16%** (0.433257 → 0.481596). The earlier reading of −0.43% cold came from an *unmatched*
incumbent partition (1,939/702 users against the candidate's 1,931/710); matched, cold is exactly
zero, and bit-identical by construction, because threshold-10 routing sends cold users down the same
popularity path in both arms.
The same configuration was 12.0% *below* item-item on the 6% subsample, so the scaling hypothesis
was not merely met but overshot. The four gaps that stood between it and a verdict — the
single-seed `incomplete`, the unmeasured cold tolerance, the missing user counts, and the absent
weights — are all now closed; they are itemised in the D-003 section of
[`../model-planning/03-decision-register.md`](../model-planning/03-decision-register.md).

**2026-09-05 — Rung 2 is adjudicated, and the ladder moved to Rung 3.** The retrieval gate returned
**`promote`** on the single run, under the relaxed seed requirement (#139) that the owner's
experiment-cost policy authorized. The verdict survives worst-case seed noise with room to spare:
the tolerance study's measured dispersion is 7.99%, so +16.57% − 7.99% = **+8.58%** against a 3%
bar. Retrieval is therefore promotion-eligible; the champion swap is still the owner's, and is
sequenced behind the serving gates.

Three artifacts make it real rather than a number in a log. The run produced a reloadable model —
MLflow `a11af5ed0f0745f68572407237cfa4b9`, archive SHA-256
`43320b87e3cbc4a0dfbc90bce2e9d9b033fbd4c6cebe7f09447fa6cd5e1215e6` — in two independently verified
durable locations. The private sidecar can load and serve it (#161), on a CPU-pinned torch build
that fails its own image build if any CUDA wheel appears (#160). And the isolated encoder benchmark
passes its 15 ms p99 budget by a factor of 53, at **p99 0.285 ms**
(`../experiments/sasrec/encoder-latency-2026-09-05.json`).

What is still open on the serving path, in order: the prediction-audit row cannot yet name which
retrieval family answered a request, which is a prerequisite for a champion swap nobody can audit
after the fact; the encoder p99 above was measured on the arm64 host rather than inside the
linux/amd64 image the sidecar actually ships; and the authenticated k6 latency gate has not yet been
run with the SASRec bundle serving. None of those relaxes a threshold — they close the gap between
what was measured and what will run.

Two corrections worth carrying forward, because both were caught late. A retrieval **ordering**
defect — the wire order is newest-first, SASRec trains oldest-to-newest — was fixed, lost in a
squash merge, and restored in #163 with a pinning test; the failure mode is silent in both
directions and shows up only as worse recommendations. And the two-tower line reopened: FAISS row 0
represented dense item id 1, but the recommendation path dropped row 0 and read every remaining
zero-based row as a one-based dense id, so the five-arm pilot evaluated mistranslated item ids
(#158). ADR 0015 now carries the corrected outcome; its below-popularity result is **not** evidence
against two-tower retrieval.

The deployment and the moderated sessions below are unchanged and still owed.

**Create the Hetzner box and run the first deploy.** The rehearsal is done — the release sequence has run from empty volumes with `ENVIRONMENT=production`, the https OIDC round trip works through the Caddy edge, both pgBouncer auth modes were exercised, four deliberate breaks each refused to boot, the sidecar-kill and empty-Redis cases both failed `verify` as they should, a rollback across a migration proved the database-ahead no-op, the restore drill ran with the seed step skipped, and the pinned k6 gate passed at the production topology. What remains is `docs/deployment-runbook.md` §1–§7 and it is an afternoon, not a sprint: create the CX22 (Ubuntu 24.04, **x86 — not a CAX/ARM type**, since every image is `linux/amd64`) with a Cloud Firewall open on 22/80/443; point `app.` and `auth.` A records at it and confirm they resolve, because Caddy's ACME challenge fails rather than falling back; run `infra/host/bootstrap.sh` with the deploy key; clone to `/opt/movielens`, generate `.env.prod` at 0600 with `secrets.token_urlsafe(48)` for every value and `EDGE_TLS=acme`; create the `production` and `production-canary` GitHub environments with `DEPLOY_SSH_KEY` / `DEPLOY_KNOWN_HOSTS` and settle GHCR package visibility; then run **Deploy production** by `workflow_dispatch` and read the log for `DEPLOY-OK`. Then close the ten open owner decisions in the runbook's §0 table, point the backups at a free B2 or R2 bucket and run the restore drill for real, and add the external uptime check on `https://app.<domain>/` and the auth discovery URL — the deployment has no continuous health signal of its own. Three things the rehearsal could not prove on a laptop are the first deploy's real content: ACME issuance against real DNS, the GHCR pull path, and the SSH deploy itself. The two tracks that were the current step before this — the moderated frontend sessions and the platform-track backlog below — are unchanged and still owed.

**Run the moderated sessions.** Bundles 0–7 are delivered and the cutover is done, so the only thing between the frontend and a recorded PASS is validation data — and it is mine to gather, not a reviewer's to substitute for. `docs/frontend/records/finish-gate-passes.md` §10.8 names exactly what to run (summarised on the live `docs/frontend/finish-gate-review.md`): 4–5 movie-focused participants and 3–4 technical reviewers, keyboard-only and small-screen coverage present in the mix, over the seven discovery tasks in §4.2 against the cutover build. Capture what the review deliberately left blank — completion and abandonment, time on task, errors and recovery, movie scan count before a decision, feedback-semantics comprehension, and whether the ML evidence is discoverable without being disruptive. Then replace §4.2 with the observed data and re-record the verdict; if nothing new surfaces it becomes PASS, and retiring `/legacy` becomes eligible as its own PR against the rollback diff in `docs/frontend/README.md`.

In parallel on the platform track, the modeling precondition is met: the three decisions from the 2026-08-30 memo are taken (threshold 10 online and offline; the gate reads overall NDCG@10 with a per-slice no-regression clause at warm 6% / cold 5%; the two-tower is measured and not promoted — its best swept configuration is 6.8× below item-item, with the diagnosis in ADR 0006's note), and the current stack is proven under the gate: **popularity < CF/ALS < LightGBM ranker**, with the ranker promoted at +15.5% overall on the whole-window training set. Rung 2 (SASRec) has since been measured and returned `promote`, and **Rung 3 — a target-attention ranker — was approved on 2026-09-05 (ADR 0018)** in two increments: first the frozen SASRec encoder's user embedding and its score against each candidate as point-in-time LightGBM features, judged against PR #151's per-route bundle; then DIN target attention in the sidecar under an isolated p99 <10 ms budget at 500 candidates, and only if increment 1 gains at least **+3% warm NDCG@10** over that bundle. *(Outcome, recorded 2026-10-05: increment 1 was measured twice and refused at +2.78% warm against the +3% bar (#159), so DIN was not built — see the 2026-09-15 block.)* Rung 1 (two-tower v2) is closed without promotion but is no longer closed on the evidence it was closed on — the FAISS mapping defect (#158) invalidated the pilot that stopped it, so a future revisit starts from ADR 0015's corrected outcome and would still need the temperature fix its sweep exposed. *(Corrected 2026-10-05: that revisit happened. One full-data run of the corrected v2 reads warm recall@500 **0.5113** against item-item's 0.3991, +28.14%, and `make gate-retrieval` returns `promote` (#182; ADR 0015's 2026-09-06 outcome). It is measured, not promoted.)* Two smaller proposals rode the approval gate at the time: ADR 0006's `logit_temperature` default and ADR 0005's training-window widening. *(2026-10-05: both are superseded by the corrected two-tower run and the retrained-ranker result, as `CLAUDE.md` records.)* Of the two remaining platform items, training-time candidate exclusions landed on 2026-09-04 (#126) and Feast-backed ranker training is deferred as D-009 with its alternatives costed (#127) rather than following the modeling decisions. Every new endpoint stays authenticated and uses the RLS-bound request connection.
