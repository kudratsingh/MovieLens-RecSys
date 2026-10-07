# Model-program decision register

This register distinguishes assumptions that let planning continue from choices that require
the owner. Recommended defaults are proposals, not approvals. Once decided, record the outcome
in the governing ADR or a dated ADR note and replace `open` here with a link. Decisions taken
from 2026-09-05 to 2026-10-05 carry `O-n` IDs and are listed in [`owner-decisions.md`](owner-decisions.md).
**From 2026-10-07 this register is the single home for owner decisions:** each one gets a dated row
in [the table below](#owner-decisions-from-2026-10-05--one-dated-row-each), in the pull request that
acts on it (`CLAUDE.md`, non-negotiable 13).

| ID | Decision | Needed by | Recommended default | Status |
|---|---|---|---|---|
| D-017 | Experiment cost policy | Standing | One run per configuration; no repeated runs for seed confirmation until modern advanced transformer-based models | **Answered 2026-09-05**; recorded in CLAUDE.md and ADR 0004's suspension note |
| D-001 | Exact retrieval promotion gate | Before SASRec full-data verdict | Three-seed mean warm recall@500 >= item-item by 3% relative, cold/overall non-regression within measured tolerances | Approved by owner 2026-09-04 and recorded in ADR 0004; retrieval-tolerance measurement remains required |
| D-002 | End-to-end guardrail for retriever promotion | Before serving any new retriever | Current LightGBM NDCG@10 must not regress outside ADR 0001 tolerances on the new candidate set | Approved by owner 2026-09-04 |
| D-003 | SASRec advance/stop rule | **Rule needed before the run landed; it did** | Predeclared bands anchored on item-item's 0.400144 and the pilot's own 12.0% same-sample deficit | Seed 42 landed in **band 1** (warm 0.465169, +16.25% over item-item) on 2026-09-04; owner picks the rule in [`memos/d003-full-run-stop-rule.md`](memos/d003-full-run-stop-rule.md) |
| D-004 | SASRec compute budget | Before full-data run | Local machine or a standard single-cloud-GPU run is allowed; estimate cost/time first and keep the three-seed plan bounded | Partially answered 2026-09-04; exact training-time/RAM ceiling follows profiling |
| D-005 | Test-set unseal trigger and final window | Before first claimed release candidate | Sealed until frozen; the final evaluation reads the 28 days after `holdout_end`, not the whole 3.4-year partition | Written up 2026-09-04 in [`memos/sealed-test-and-dataset-policy.md`](memos/sealed-test-and-dataset-policy.md); audit re-verified across all branches and holds, with three named gaps it cannot cover |
| D-006 | Full 25M model versus compact demo fixture in production | Before M2 architecture | Serve the exact full-data champion; preserve compact bundle only as an explicit demo fixture | Answered 2026-09-04 |
| D-007 | 25M-to-32M migration trigger | Before any dataset expansion | Stay on 25M, and require M0-07's rolling windows to be tried and shown insufficient first | Open; recommendation and settling conditions in [`memos/sealed-test-and-dataset-policy.md`](memos/sealed-test-and-dataset-policy.md) |
| D-008 | Registry source of truth | Before M3 | MLflow owns immutable runs/artifacts/versions; Postgres owns tenant assignment and rollout state | Open |
| D-009 | Feature representation and the training feature source | Before any full-25M materialization, and before M2 | Compact per-user genre-mask counts (measured exact form) plus on-demand computation over the retrieved slate; no user-by-catalog table | Deferred by owner 2026-09-04 to prioritise modeling; costed in [`memos/feature-source-boundary.md`](memos/feature-source-boundary.md) |
| D-010 | Multi-objective labels and utility | Before M5 | Do not invent completion/click labels from MovieLens; wait for observable product events or constrain the rung to rating-derived research proxies labeled as such | Owner input required |
| D-011 | Re-ranking objective | Before M6 | Choose one primary diversity metric plus relevance guardrail; begin with MMR as interpretable baseline | Open |
| D-012 | M5 versus M6 ordering | After M4 | Prefer M6 first if multiple retrievers are useful; prefer M5 first only when utility and labels are ready | Open later |
| D-013 | Frontier compute/provider budget | Before M8 | A fixed-cost research spike; no open-ended foundation-model training | Owner input required later |
| D-014 | Fate of the stale `docs/progress.md` | Before status-doc cleanup | Preserve untouched; owner chooses archive, refresh, or delete in a separate change | **Settled:** archived as [`docs/records/progress-log-2026-05-31.md`](../records/progress-log-2026-05-31.md) (#164, owner decision O-4) and deleted from `docs/` |
| D-015 | Phase 4 automation timing | Before M3 | Stabilize SASRec experiment/export contracts first, then automate; SASRec pilots may continue meanwhile, but promotion/serving waits for M0 | Answered 2026-09-04 |
| D-016 | Meaning of the requested 300–400 ms runtime | Before M2 latency review | Preserve existing stricter p99 targets: SASRec encoder <15 ms and authenticated service <100 ms | Answered 2026-09-04; 300–400 ms was an assumption about growth, not a request to relax gates |

## Owner decisions from 2026-10-05 — one dated row each

**Standing rule from 2026-10-07** ([`CLAUDE.md`](../../CLAUDE.md), non-negotiable 13): every decision
the owner makes gets one row here — what was asked, the options, what was chosen, why, and the record
or ADR it changes — added in the pull request that acts on it. Rows D-018 to D-049 were added together
on 2026-10-07 to bring 5–7 October up to the rule. Their source is the owner's messages in the
coordinating session (quoted where the words survive), cross-checked against the records named in
the last column. The `O-n` table in [`owner-decisions.md`](owner-decisions.md) is kept as history and
gets no new rows. The build brief's own decisions D1–D7 are D-020 to D-026 here; the brief's `Dn`
labels and this register's `D-0nn` IDs are different series.

| ID | Date | What was asked | Options | Chosen | Why | Record or ADR it changes |
|---|---|---|---|---|---|---|
| D-018 | 2026-10-05 | Keep the private `.coordination/` working notes tracked in the public repo? (work orders, open question 4) | keep them tracked; stop tracking them | Stop tracking: "ignore the coordination folder because that should not be public." The files stay on disk locally | The repo is public and those notes were never meant to be | PR #188; `.gitignore`; [`phase-a-work-orders.md`](phase-a-work-orders.md) open question 4 |
| D-019 | 2026-10-05 | Adopt the Next-Phase Build Brief (Phase A WO-1 to WO-9, then Phase B) and bring the public docs up to date with it? | adopt it; adopt with changes; keep the September order (O-23) | Adopted: "write down all docs and make all changes to claude md and organization docs, then build strict work orders"; README and user-facing docs refreshed, nothing private published. Rung 5 waits behind Phase A | One executable plan with stop rules and a done-when per work order | PR #195; [`phase-a-work-orders.md`](phase-a-work-orders.md); `CLAUDE.md`; [`../modeling-roadmap.md`](../modeling-roadmap.md) decision log, 2026-10-05 rows |
| D-020 | 2026-10-05 | Brief D1: a larger transformer first, or a generative recommender first? | larger transformer first (Phase A), generative second (Phase B); the reverse | Larger transformer first, generative second | Phase B reuses Phase A's hand-written layers, and Phase A alone makes the project resume-ready | roadmap decision log (Phase A row); work orders D1 |
| D-021 | 2026-10-05 | Brief D2: stay on MovieLens 25M for Phase A, or move to 32M? | 25M; 32M | Stay on 25M; 32M stays an open question | A new dataset moves the split and invalidates every existing comparison; D-007's memo recommends staying | work orders D2 and open question 6 |
| D-022 | 2026-10-05 | Brief D3: keep working toward serving (champion swap, k6 run, deploy, moderated sessions) during Phase A? | continue; park | Serving is parked; nothing new is served. Gate 3's budgets do not move; encoder latency is reported, not gated | Keeps Phase A on the models | [ADR 0020](../adr/0020-sasrec-v2.md) amendment 2026-10-05 (D3); `CLAUDE.md` current status; `docs/status/README.md`; roadmap row |
| D-023 | 2026-10-05 | Brief D4: train ADR 0020's cells on the copied-prefix trainer, or first try to repair the 9.8× faster all-positions trainer? | the objective of record for every cell; one bounded repair attempt first | One bounded repair attempt first (WO-3). The objective of record stays `strict-prefix-final-position-v1` | The fast trainer's pilots cost about 81 s; the copied-prefix trainer prices the grid at 35–58 CPU-days | ADR 0020 amendment 2026-10-05 (D4), overriding the second half of the 2026-09-15 note (O-22) |
| D-024 | 2026-10-05 | Brief D5: keep the 2026-09-05 one-run-per-configuration policy (D-017)? | one run everywhere; three everywhere; three for the headline models only | Three runs (seeds 42, 7, 13) for the final headline models only, with rolling windows w1 and w2; cells stay one run | One run cannot rule out luck, and the fast trainer makes repeats cheap | `CLAUDE.md` "Purpose" note; ADR 0020 amendment (D5) |
| D-025 | 2026-10-05 | Brief D6: which hardware trains the cells? | laptop CPU; the Mac's GPU; a rented GPU | CPU, then the Mac's GPU, then a rented GPU only on O-3's trigger; an accepted CPU-versus-GPU difference is written into ADR 0020 before any non-CPU run counts | Matches O-3 (rent only when one run costs more than a night) and keeps every published number on one machine | ADR 0020 amendment 2026-10-05 (D6); tolerance set in D-045 |
| D-026 | 2026-10-05 | Brief D7: what does "written from scratch" mean for the encoder? | PyTorch's packaged encoder; a written list of allowed pieces | Allowed: `nn.Module`, `nn.Parameter`, `nn.Linear`, `nn.Embedding`, `nn.Dropout` and plain tensor math. Banned under `src/`: `nn.Transformer*`, `nn.MultiheadAttention`, `nn.LayerNorm`, `F.scaled_dot_product_attention`, `F.multi_head_attention_forward` | Otherwise the claim cannot be checked | ADR 0020 amendment (D7); WO-2 |
| D-027 | 2026-10-05 | Release WO-1's pilots and WO-2's item-3 recheck and item-5 pilot? | run; hold | Approved: WO-1's runs; WO-2 item 3; WO-2 item 5 after WO-1 (#194) and O-25 | The preflights passed on the O-25 partition | [WO-1](experiments/wo1-restore-trainer-of-record.md) and [WO-2](experiments/wo2-hand-written-transformer.md) records, "Owner approval" |
| D-028 | 2026-10-05 | The 6% pilot subsample computed its own cutoff, 23.5 days past the sealed boundary 1469256597. Which pilot protocol replaces it? (O-25) | cut the 6% sample at the full split's cutoff; drop sealed rows before subsampling; use the 0.5% sample; accept the old protocol | "Cut the 6% sample at the full split's cutoff. Approved." The September 6% values are compromised and replaced by WO-1's four pilots | The subsample inherits the full frame's boundaries; the alternatives either changed the split or kept the leak | O-25 in [`owner-decisions.md`](owner-decisions.md); WO-1 (#194); sealed-test memo contamination record; `results.md` marks |
| D-029 | 2026-10-05 | How careful to be before destructive database work (the `movielens` drop, the MLflow import) | proceed; back up first | "Backup first, and verified." A private repo `kudratsingh/movielens-backups`; each dump scanned for secrets and pushed; "Do not drop or change any database until I confirm this backup is pushed"; "Right before the drop, take a fresh dump with today's date and time in the filename, commit and push"; no Docker restart while a pilot runs; questions before the drop | A dropped database cannot be read again | backup repo commits `e4a7adf`, `fe9dc20`, `ffe0479`; generalized by D-049 |
| D-030 | 2026-10-05 | Bring the 51 September runs (`september-mlruns-artifacts.tgz`) into the current store "without overwriting today's runs"? | original run ids; new ids; leave them in a scratch store | "Import the 51 September runs with their original run ids, overwriting nothing." The tarball's SHA-256 recorded and the bundle pushed to the backup repo; runs `528b1451…` and `c1d742c8…` verified; the two boosters located by SHA-256 | Original ids keep every citation in the docs resolvable | backup repo `d9936d9`; sealed-test memo (ids preserved); shared store experiments 1, 3 and 5 |
| D-031 | 2026-10-05 | Orphaned Postgres files filled Docker's disk. Hand-delete them, or drop and recreate `movielens`? | hand-delete the orphan files; drop and recreate; raise the disk limit | The owner raised Docker's disk limit to about 104 GB and declined hand-deletion. "DROP and CREATE the movielens database. Approved." Reload and migrate only while no training run is in progress | Deleting files under a live Postgres risks the cluster; `movielens` rebuilds from the CSVs | host settings; no repo record |
| D-032 | 2026-10-05 | Ground rules for working unattended ("You have my approval to work on your own for the next few hours") | a mandate, not a choice between options | No spending; one full-data run at a time; up to three 6% pilots in parallel with at least two cores free; the sealed boundary enforced; a fresh `mlflow` dump before any drop or import; no hand-deletion of Postgres files; no serving, frontend or infra work beyond the list; one PR per work order plus one docs PR; stop a thread, not the session, if a check fails twice or a result contradicts its expectation; Opus agents do the work under the coordinator | Lets work continue unattended without spending money or risking data | the compute rules in the WO-1 to WO-4 records; no record of the mandate itself |
| D-033 | 2026-10-05 | WO-2's first seed-42 pilot missed WO-1's range (0.3428, −5.45%). What now? | accept; stop; investigate | Training-parity checks (initialization; gradients within 1e-5 on 3 batches with dropout 0; dropout sites and rates); a difference is fixed with a permanent test and s42 re-run; seeds 7, 13, 21 approved; accept if parity passes and the four-seed mean is within 5% of WO-1's (not below 0.353565); otherwise report all eight values and stop | One seed on 108 users cannot tell noise from a real difference | [WO-2 record](experiments/wo2-hand-written-transformer.md); PR #196; accepted |
| D-034 | 2026-10-05 | How are runs that read the sealed window treated? | delete them; keep and mark them; keep them unmarked | Tag each `sealed_window_contaminated=true` and `sealed_window_declared=2026-10-05`, keep it, mark it in `results.md`, never use it as a comparison | Keeps the audit trail without letting a contaminated number decide anything | sealed-test memo, step 4 (29 runs tagged); `results.md` |
| D-035 | 2026-10-05 | What happens to the decisions that rested on the 6% pilots? | re-run each; re-run some; let them stand | The all-positions verdict is re-measured by WO-3; the seed-noise study is superseded by WO-1's four clean seeds; the two-tower pilots are correctness evidence only; BCE over gBCE is re-run once on the clean protocol (`4f87185e…`; the choice holds); no full-data verdict changes | Each decision either already had clean evidence or needed one cheap run | sealed-test memo, step 5; `results.md` gBCE section; PR #199 |
| D-036 | 2026-10-07 (proposed 2026-10-05) | Pilots read the 2026-09-05 final window `[1469256597, 1471675797)`. Which window does the one-time final read use? | keep it; retire it and set a new 28-day window after every subsampled read | Retired. The final window is `[1475668076, 1478087276)`, one second after the latest rating any subsampled run read; each frozen configuration is retrained on all ratings before 1475668076, and the 74 days in between are training data only. "I approve by merging"; "Merge #199. Approved." | A window decisions have touched cannot give an unbiased final number | [ADR 0001](../adr/0001-evaluation-protocol.md) amendment 2026-10-05; sealed-test memo; PR #199, merged 2026-10-07 |
| D-037 | 2026-10-05 | WO-3's scope, once WO-2 is accepted | approve in full; approve in part | Approved in full: up to 8 pilots, seed repeats and one full run, the full run held for a separate go | The brief's pass checks and stop rule bound it | [WO-3 record](experiments/wo3-repair-fast-trainer.md), "Owner approval" |
| D-038 | 2026-10-05 | WO-4's scope, including a paired `cpu`/`mps` pilot | approve in full; approve without the device pilot | Approved in full, the paired pilot as the measurement that proposes the D6 tolerance | The tolerance needs one measured difference | [WO-4 record](experiments/wo4-trainer-upgrades.md), "Owner approval" |
| D-039 | 2026-10-05 | How far does WO-5 go now? | start cells; preparation only | Preparation only: time one pass of cell A, bring the re-priced table, start no cell | The table has to be re-priced before any cell spends nights | [`wo5-cell-repricing.md`](experiments/wo5-cell-repricing.md); coordinator call C-001 |
| D-040 | 2026-10-07 | WO-3's first pilots left a gap. How does WO-3 continue? | stop WO-3; run pilots 5–8 as written; revise the plan | Revised: (a) pilot 4 as written; (b) the all-positions trainer on cell 0b's exact settings, its time projected first, compared with `9b0d4994` (0.4564); (d) the wide-batch variant dropped; (e) if (b) lands within 0.03 of 0.4564, cell 0b at seeds 7, 13, 21 on both trainers, passing when the fast trainer's four-seed mean is within 3% of the strict-prefix mean; (f) a position-mismatch pilot only if (a) and (b) both leave a gap, design sent first; (g) about 4 hours of machine time replaces the 8-pilot count, and no full-data run; (h) a dated ADR 0020 amendment: the pass check is at the WO-5 loss, and the full-data control is cell 0b on both trainers, run as part of WO-5 | The repair has to hold at the loss WO-5 trains with, not only at v1's | ADR 0020 amendment 2026-10-07; WO-3 record; PR #201 |
| D-041 | 2026-10-07 | How large must a single-seed pilot difference be to count? | keep the 0.015 tripwire; an absolute rule; always run seeds | "Noise rule: a single-seed difference under 0.03 is not a finding. This replaces the 0.015 tripwire." | 0.015 was tighter than one WO-1 seed sd (0.0150) on 108 warm users | ADR 0020 amendment 2026-10-07; WO-3 record (P2's −0.0219 is no longer a finding); `results.md` |
| D-042 | 2026-10-07 | When does the sealed-partition guard reach the other subsampling trainers (`itemitem`, `last_item`, `twotower`, `sasrec_ranker`)? | a later pull request; the next one | "In the next pull request, not a later one": it landed in WO-3's #201 | Any of them could still read the new window through a subsample's own cutoff | sealed-test memo, "Closing the path, done"; PR #201; `tests/unit/test_sealed_partition_guards.py` |
| D-043 | 2026-10-07 | What does WO-5 do next? | start cells; re-time first; rent | No cell starts yet. If WO-3 passes: re-time cell A on the fast trainer (2,000 steps, CPU and `mps`) and bring a new table. If WO-3 fails: a rented-GPU proposal with the provider, a same-day price, a 10-minute timing of cell A on that card and a total, and nothing spent before approval | Every capacity cell crosses a night on the CPU | this register; WO-5 itself is not yet re-planned |
| D-044 | 2026-10-07 | The spending ceiling for rented GPU time (work orders, open question 3) | an amount | **$75.** Nothing is spent before the owner approves the specific proposal | ADR 0020 estimated $10–51 for its grid | [`phase-a-work-orders.md`](phase-a-work-orders.md) open question 3 (answered in this PR); `CLAUDE.md` open decisions |
| D-045 | 2026-10-07 | Accept the proposed D6 tolerance (±1.0% at full scale, after a calibration pair of cell 0b on both devices)? | accept; accept with changes; reject | ±1% relative accepted, with changes: calibration is v1's exact cell trained once on the GPU and scored on the CPU against 0.5091713455, not a cell-0b pair, and that run is also the same-device baseline; a miss triggers one more GPU seed before any fallback; cells near the +3% bar are confirmed by WO-7's seeds, not a CPU rerun | Calibrating on v1's cell compares against a full-data number of record | ADR 0020 amendment 2026-10-07 (D6); [WO-4 record](experiments/wo4-trainer-upgrades.md) proposal |
| D-046 | 2026-10-07 | Dependabot's torch bump 2.12.0 → 2.12.1 in the sidecar image (#197) | merge; close | "Close #197. Serving is parked." | The sidecar's CPU-only torch pin changes only with a re-measurement of the served bundle | PR #197 closing comment; D-022 |
| D-047 | 2026-10-07 | Who decides the hardware step? | the agents, within D6; the owner, each time | Standing: "for the hardware step ask me for explicit approval and we will go over the options" | The hardware choice carries money and reproducibility cost | this register; ADR 0020's D6 note leaves the hardware to the owner. **2026-10-07 note (D-050):** choosing to *prepare* the RunPod session is not the approval to spend; the top-up and the pod still need the owner's explicit go-ahead at the session itself, and that approval gets its own row in the pull request that records the timing |
| D-048 | 2026-10-07 | WO-3's cell 0b all-positions pilot stopped at pass 3 with holdout recall still rising. A longer pass budget? | a fixed 8–10 passes; early stopping with a higher minimum; leave it | "cell 0b on the all-positions trainer, seed 42, early stopping off, 10 passes." Conditions: (1) the read is warm recall@500 at pass 10, declared now; log every pass and do not pick the best; (2) if it passes and (e) runs, the fast trainer's three seeds use the same fixed 10 passes, the strict-prefix seeds keep their rule; (3) if the curve peaks before pass 10 and falls by more than 0.03, stop and bring the per-pass numbers before (e); (4) any WO-5 pricing for the fast trainer uses its real pass count (about double) and proposes an early-stopping rule one probe user cannot trigger | 82 probe users let one user end a run while holdout recall still rose | run `02060962064d41fd95e37c8941a596c5` (`phase-a-sasrec`). **2026-10-07 note:** the run passed its read (0.4343 at pass 10 against 0.4564; condition 3 did not fire), and item (e) executed. The fast trainer's four-seed mean is 0.4095 against strict-prefix 0.4513, −9.27%, so the 3% check fails. That verdict applies the owner's check and is not a new decision; see [`experiments/wo3-cell0b-ten-pass.md`](experiments/wo3-cell0b-ten-pass.md) |
| D-049 | 2026-10-07 | Standing rules for keeping runs and recording decisions | rules, not options | RUN PRESERVATION and DECISION RECORD, added to `CLAUDE.md` as non-negotiables 12 and 13 and applied to everything since 2026-10-05 | WO-2's store and two of WO-3's were lost with their worktrees, and decisions lived only in the session | `CLAUDE.md`; this register; [`owner-decisions.md`](owner-decisions.md); [`experiments/run-ledger-2026-10-05..07.md`](experiments/run-ledger-2026-10-05..07.md); this PR |
| D-050 | 2026-10-07 | WO-5's capacity cells cross a night on every free device ([`wo5-cell-repricing.md`](experiments/wo5-cell-repricing.md)); the priced memo ([`memos/wo5-hardware-options-2026-10-07.md`](memos/wo5-hardware-options-2026-10-07.md)) recommends a 10-minute timing on a rented card before any larger spend. Which way? | (1) prepare the rented-GPU timing: RunPod Secure Cloud, 1× RTX 4090 on-demand at $0.74/h, one session of at most 1.0 h and $1.00; (2) the Mac's GPU only: $0, about 8–13 days of wall time for the six cells and no resume; (3) neither: the laptop CPU, 16–32 days | **Option 1, prepared today and bought tomorrow.** Build and test the kit and the runbook now; create no account, add no credit, use no key and spend nothing on 2026-10-07; the owner creates the account and runs the session the next day | The memo's anchors put the card at 8–20× the Mac CPU (up to 25–35× before host-side limits); at ≥ 10.2× every must-have fits the $75 ceiling (D-044) at five passes, and under $1 of timing settles which band it is before any larger spend. `mps` alone holds the laptop awake for one to two weeks with no resume | this PR: the kit (`src/training/sasrec_timing.py`, `src/training/gpu_smoke.py`, `infra/gpu/`), the three cuda cells files and the dataset manifest under `docs/experiments/sasrec/`, [`experiments/wo5-gpu-timing-runbook.md`](experiments/wo5-gpu-timing-runbook.md), and the memo committed as [`memos/wo5-hardware-options-2026-10-07.md`](memos/wo5-hardware-options-2026-10-07.md). The spend still needs explicit approval at the session (D-047's note) |

**Sources.** Eight rows have no record in the repository older than this pull request; their only
source is the owner's messages as relayed by the coordinator: D-029 (corroborated by the backup
repo's commits), D-030 (partly: the sealed-test memo records that the ids were preserved), D-031,
D-032, D-043, D-044, D-047 and D-048 (its run, `02060962…`, finished during the catch-up; its
record is [`experiments/wo3-cell0b-ten-pass.md`](experiments/wo3-cell0b-ten-pass.md)). D-050's source is the owner's choice
relayed by the coordinator on 2026-10-07; its record is the pull request that adds it.

### Coordinator calls, 5–7 October (not owner decisions)

Calls the coordinating agent made itself, with the owner informed. They are listed so they are not
mistaken for the owner's.

| ID | Date | Situation | Call | Where |
|---|---|---|---|---|
| C-001 | 2026-10-05 | WO-5 asked for one timed pass of cell A, projected at 23–24 hours on the CPU | Timed 2,000 steps on the CPU and on `mps` and extrapolated to the pass; the full-pass option is left to the owner | [`wo5-cell-repricing.md`](experiments/wo5-cell-repricing.md), "the deviation" |
| C-002 | 2026-10-05 | The harness blocked an agent from writing the contamination tags (D-034) | The coordinator applied the 29 tags itself | sealed-test memo, tag status |
| C-003 | 2026-10-05 | Experiments created before the server was recreated kept the container-path artifact location `/mlartifacts/<id>`, so host clients could not upload to them | New runs go to fresh experiment names, whose artifact locations are proxied (`mlflow-artifacts:/<id>`); Phase A's runs now log to `phase-a-sasrec` (id 6) | shared store; WO-1 record, Deviations (the `-phase-a` names) |
| C-004 | 2026-10-05 | WO-4's cell 0b CPU pilot ran on the encoder from before WO-2's parity fix | The device pair re-ran its CPU half on the fixed encoder: three pilots, within WO-4's two to three | [WO-4 record](experiments/wo4-trainer-upgrades.md), Deviations |
| C-005 | 2026-10-07 | The WO-5 kit (D-050) was to install "everything else from the existing requirements" on the pod | **Agent call, not an owner decision.** `infra/gpu/requirements-cuda.txt` pins only the slice of `pyproject.toml` the trainer, the timing and the smoke import (13 packages, at the Mac venv's versions, proved sufficient by a clean venv), not the whole list: feast, prefect, evidently and dvc are never imported there and would cost billed install minutes. The torch build is `+cu126`, because PyTorch publishes 2.13.0 for CUDA 12.6, 12.9, 13.0 and 13.2 but not 12.8 | `infra/gpu/requirements-cuda.txt`; the runbook |
| C-006 | 2026-10-07 | The `cuda` smoke's 200-step check of v1's shape on a tiny dataset | **Agent call, not an owner decision.** The memorization check trains v1's architecture and loss with dropout 0, as the repository's memorization tests do: at 200 steps, dropout 0.2 left a working model at chance on a 9-movie history on both `cpu` and `mps`, so the check would have measured dropout rather than the device. The timing cells keep dropout 0.2 | `src/training/gpu_smoke.py` docstring |


## D-017 — Decisions taken 2026-09-05

Four settled in one sitting, recorded so none is re-litigated:

- **Experiment cost policy.** One run per configuration. No repeating a run for seed confirmation
  until the ladder reaches modern advanced transformer-based models — a full-data seed is ~4.5 hours
  and a three-seed set ~13.5, and the priority is reaching advanced architectures. The gate was
  relaxed to accept a stated single-seed policy; the multi-seed path is preserved for when the policy
  reverses.
- **The last-item control keeps its popularity fill.** When an obscure last item has too few recorded
  successors to fill K, the tail comes from the popularity ranking. It matches what serving does
  (`CANDIDATE_SOURCE_POPULARITY_FILL`), keeps the comparison honest at a fixed K, and
  `transitions_only_*` preserves the pure view in the same run. No code change; this ratifies the
  default.
- **The tolerance study's derivation is accepted as designed** — Student-t for small samples, the 3%
  cap borrowed from the gate's own gain requirement, and rounding to 0.1pp. With the standing
  instruction that a tolerance must be founded in data and neither too wide nor too thin, which is
  what M0-13 and M0-14 are about.
- **The `configuration_id` vocabulary is accepted.** The four patterns stand as written; the field is
  load-bearing for the study's anti-circularity check, so it needed a real answer rather than a
  deferral.

One consequence is deliberately left open: the tolerance has only ever guarded the cold and overall
non-regression clauses, so **the warm +3% promotion claim carries no uncertainty band at all**. Under
three seeds it at least read a mean; under one it reads a single draw. Putting a band on the positive
claim is new policy rather than a seed-count relaxation, and it is the place a wrong promotion would
come from.

## D-001 — Retrieval promotion gate

Questions the amendment must answer:

- Is warm recall@500 primary because learned retrieval serves only histories at or above 10,
  or is overall recall primary with attribution?
- Is +3% relative the correct materiality threshold for retrieval?
- What are warm, cold, and overall seed tolerances, and how are they measured?
- Are seeds paired against a deterministic item-item run or compared through bootstrap/user-
  level confidence intervals?
- Which protocol mismatches cause an automatic `not comparable` result?
- Does a large negative pilot allow a one-seed closeout?

Recommended answer: primary warm recall@500, +3% relative over item-item, three-seed mean for a
positive claim, user bootstrap intervals as supporting uncertainty, cold/overall non-regression,
and a hard refusal when protocol fingerprints differ.

**Owner decision, 2026-09-04:** approved. Using the current approximate item-item warm
recall@500 of 0.3991, the illustrative SASRec floor is about 0.4111; the executable gate must
calculate its threshold from the protocol-compatible incumbent rather than hard-code that example.
Before implementation closes, measure retrieval-specific seed tolerances for the cold and overall
guardrails instead of borrowing the ranker's NDCG tolerances.

## D-002 — Joint system guardrail

A retriever can surface more relevant holdout items yet create a candidate distribution the
existing ranker orders poorly. Recommended answer: stage-local recall decides whether retrieval
learned anything; serving promotion also requires the champion LightGBM ranker to preserve
NDCG@10 within ADR 0001's warm/cold tolerances. If it fails, retrain the ranker on the new source
and gate the paired system as a new bundle.

**Owner decision, 2026-09-04:** approved.

## D-003 — SASRec pilot rule

**2026-09-04, later — the run landed and the predeclared bands fire cleanly.** MLflow run
`6958fd082af6462da812ddd4708230c1`, status FINISHED, `sasrec-full-bce-neg32`:

| Slice | Item-item | SASRec seed 42 | Relative |
|---|---:|---:|---:|
| Warm recall@500 | 0.400144 | **0.465169** | **+16.25%** |
| Cold recall@500 | 0.528527 | 0.526273 | −0.43% |
| Overall recall@500 | 0.434269 | 0.481596 | +10.90% |

That clears the gate floor of 0.412148 by 12.86%, which is **band 1: run seeds 7 and 13, and let the
gate decide.** The scaling hypothesis the memo named before the number existed is not merely met but
overshot — the same configuration was 12.0% *below* item-item on the 6% subsample and is 16.3% above
it on the full data.

Four things separate this from a verdict, and none of them are pessimism:

1. **Cold is −0.43%**, which is the guardrail clause going live rather than staying hypothetical. The
   cold tolerance is exactly the number that does not exist yet, so whether this is noise or a
   regression is unanswerable today.
2. **The run does not log `n_warm_users` or `n_cold_users`**, so the gate's population-equality check
   cannot run and the cold comparison in particular cannot be verified as being over the same users.
3. **It carries no `evaluation_protocol` tag**, so `retrieval_run_from_mlflow` refuses it and the
   contract forbids grandfathering. This is strong evidence, not gate-admissible evidence.
4. **No weights were saved.** The run path logs metrics, params and tags only, so even a passing gate
   yields a number rather than a servable artifact — M2-02 remains the gap.

The sequencing consequence is concrete: land protocol emission before spending roughly nine hours on
seeds 7 and 13, or that compute produces more inadmissible evidence.


**2026-09-04 update — the pilot already answered part of this.** The full options memo is
[`memos/d003-full-run-stop-rule.md`](memos/d003-full-run-stop-rule.md). Its central finding: the 6%
pilot measured popularity (0.1974), item-item (0.3619) and SASRec-BCE (0.3186) on the *same*
subsample, so SASRec's arm sits **12.0% below the incumbent**, not above it. The pilot record reads
as a pass because ADR 0016's stop rule named popularity and never named item-item. The full-data run
now executing therefore tests one hypothesis — that a 12% same-sample deficit closes and reverses to
a 3% surplus on 16.7× the data — and the rule for what its single seed authorizes should be fixed
before the number is visible.


The current ADR says the pilot should beat popularity or a last-item nearest-neighbor baseline,
but the latter has not been established and no margin is named. Recommended interpretation:

- pilot is a defect/viability gate, not promotion evidence;
- compare BCE and gBCE to same-sample popularity, item-item, last-item transition, and a shuffled-
  sequence control;
- advance one frozen SASRec configuration only when it beats both simple floors and order is not
  irrelevant;
- if it misses a simple floor by a margin larger than measured seed noise, close without full run;
- if results are close, repeat the winning arm at seeds 7 and 13 before choosing.

## D-004 — Compute budget information needed

The owner should specify:

- available hardware: current CPU/RAM, local GPU, or approved cloud GPU;
- maximum wall-clock per pilot and per full seed;
- maximum total compute/spend for one model family;
- whether overnight unattended local jobs are acceptable;
- the acceptable peak-memory ceiling and minimum free-space reserve.

**Owner direction, 2026-09-04:** use the local machine or cloud GPUs, with a budget typical for
the model being trained. Operationally, that means local correctness/pilot work first, then the
smallest standard single-GPU shape that meets the measured memory requirement for full seeds. The
pre-run review records the provider/instance, hourly price, projected hours, and projected total;
expanding beyond the frozen three-seed plan requires a new estimate and approval. An exact time/RAM
ceiling remains open until the 6% profiler establishes a credible full-data projection.

## D-005 — Test-set policy

**2026-09-04 — re-verified, and three gaps recorded.** The audit holds: searching the entire history
of every branch for `split.test` returns exactly one added line of source, a row-count log, and
`src/evaluation/` has no entry point that can receive a test frame. What the audit cannot establish
is written down in the memo: trainers receive the whole `TemporalSplit` with nothing between them and
the sealed rows; `src/features/materialize.py` defaults its `as_of` to now and `src/release/bootstrap.py`
calls it bare, so a materialized source already aggregates over sealed-window rows — meaning the day
ranker training reads that source, sealed data enters training with no `split.test` reference to grep
for; and `ProtocolManifest` has no sealed-partition field, so the seal is currently established by
reasoning from absence rather than asserted per run. That second gap is an argument the D-009 memo did
not have: deferring Feast-sourced training features also defers a contamination route nothing watches.


Recommended trigger: the model family, data snapshot, configuration, seed aggregation, offline
gates, artifact checks, and serving checks are frozen, and the owner is deciding whether to call
that bundle a release candidate. Record the unseal commit and do not tune against the result. If
the test window has already influenced decisions, declare it contaminated and define a new final
window before proceeding.

**Repository audit, 2026-09-04:** no trainer reads `split.test` for model metrics or decisions.
Existing trainers only log its row count, and the committed result record describes holdout
evaluation. Git history likewise contains no test-evaluation path. The plan therefore treats the
partition as sealed unless the owner later recalls an external/manual inspection that influenced
model choices.

## D-006 — What production is proving

Two legitimate products exist:

1. A compact portfolio demo proving serving behavior with reviewed personas.
2. A full-data model system proving the exact measured MovieLens champion can be deployed.

The current repository robustly demonstrates the first, not the second. The recommended program
keeps both but labels them explicitly and makes the second M2's target. The owner may instead
choose a compact production demo, in which case full-scale feature/materialization work becomes a
research-platform goal rather than a deployment blocker.

**Owner direction, 2026-09-04:** production targets the exact full MovieLens 25M champion. The
compact persona-trained bundle remains useful only as a clearly labeled demo/test fixture. Full-
scale feature representation, artifact export, and serving equivalence are therefore M2 blockers.

## D-016 — Runtime clarification

The owner initially expected runtime below 300–400 ms because larger models and higher usage
normally increase work. On 2026-09-04 the owner approved preserving the project's stricter
requirements: SASRec's isolated encoder p99 below 15 ms and the authenticated end-to-end service
p99 below 100 ms.

Capacity or model growth does not automatically relax either SLO. A larger model must use an
appropriate combination of precomputation, ANN, batching, compilation, quantization, distillation,
caching, concurrency controls, or additional serving capacity. Measure p50/p95/p99 by concurrency,
history length, candidate count, and model version. If the unchanged representative-load gate
fails, the model is not serving eligible even when its offline quality improves.

## D-009 — Feature representation and the training feature source

Costed in full in [`memos/feature-source-boundary.md`](memos/feature-source-boundary.md), written
after the 2026-09-03 ADR 0009 amendment was withdrawn for closing the question without pricing the
alternatives.

The short form. Seven of the eight ranker features are single-entity; only `user_genre_affinity` is
user×item, and it is the one that forces the answer. The current materialization cross-joins users
against the whole catalog — 10,146,296,843 rows at full scale. The exact compact form is a per-user
map from 20-bit genre mask to count: **11,532,291 distinct (user, mask) pairs across all 25M
ratings, 880× smaller**, mean 71 masks per user. This corrects the recommended default in the row
above as originally written: a 20-length per-genre vector cannot reproduce the feature, because the
definition is a union over the candidate's genres.

Deferred, not decided. The modeling ladder does not touch the materialization path, so the deferral
is free until a full-data champion is materialized for serving.

## D-010 — Multi-objective truthfulness

MovieLens contains rating values and timestamps, not impressions, clicks, viewing completion,
watch duration, or skips. The project must not name proxies as real outcomes. Owner choices are:

- defer M5 until the running product collects explicit outcomes;
- perform a clearly labeled research exercise with `interaction` and `rating >= 4` tasks;
- introduce another public dataset with appropriate events under a new data/evaluation ADR.

The selected objectives need an explicit utility, calibration requirements, and a statement of
which trade-offs are unacceptable.
