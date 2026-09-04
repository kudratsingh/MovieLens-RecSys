# ADR 0015 — Two-Tower v2 Repair

**Status:** Accepted — measured, not promoted
**Date:** 2026-09-03

**Outcome (2026-09-04):** The deterministic 6% Gate 1 pilot completed all
five planned arms with no failures. Complete v2 reached warm recall@500 of
0.0435 (NDCG@500 0.0135), below temperature-only at 0.0445 and far below the
same pilot's popularity reference of 0.1974. Hard-negative-only reached 0.0443
with 100% fill across 19,170,576 requested slots, while increasing fit time
from 89.3 to 222.5 seconds. Complete v2 took 250.5 seconds. The differences
inside the two-tower band are below the pilot slice's known noise, while the
4.5x gap to popularity is not. The stop rule fired: Gate 2 is not justified,
item-item remains champion, and this repair line is closed without promotion.
The project proceeds to ADR 0016.

**Implementation note (2026-09-04):** Gate 0 uses bounded, current-model
per-batch mining rather than materializing an epoch-wide neighbour artifact.
After one warm-up epoch, each example selects up to eight valid hard negatives
from a seeded 256-draw log-uniform pool and mixes them with the existing
corrected sampled-softmax negatives. This is deterministic, keeps laptop memory
bounded, and logs selected slots and fill rate. It deliberately supersedes the
initial 3:1 and once-per-epoch defaults below for the pilot; those settings were
written for per-example random negatives, while v1 actually shares 16,384
random samples across a batch. The five-arm pilot decides whether this cheaper
mining signal merits a materialized epoch pool. The accepted stop and promotion
rules do not change.

## Context

[ADR 0006](0006-two-tower-retrieval-architecture.md) defined the first learned
retriever: a mean-pooled history tower, an id-only item tower, sampled softmax
with log-uniform correction, and FAISS IVF-Flat retrieval. The full MovieLens
25M run did not clear [ADR 0004](0004-item-item-before-two-tower.md): warm
recall@500 was 0.0466 against item-item's 0.4001. The best swept configuration
reached 0.0591, still 6.8 times below the current champion.

The 2026-08-30 sweep ruled out three convenient explanations. More epochs made
recall slightly worse; learning rates one decade either side did not recover
the gap; exact retrieval moved recall by less than 0.003. It did find a real
objective defect: cosine logits constrained to `[-1, 1]` could not overcome the
roughly ten-nat spread introduced by sampling correction. A temperature made
the loss trainable, but did not make random negatives teach useful retrieval.

The owner has chosen to repair and measure this architecture before proceeding
to the deferred SASRec proposal in ADR 0016. This ADR approves Rung 1 of the
modeling roadmap. It is a repair experiment, not a commitment to promotion.

## Decision

Build two-tower v2 as a controlled extension of v1 with three changes:

1. Adopt an explicit cosine-logit temperature.
2. Add mined hard negatives alongside corrected sampled negatives.
3. Add item side features so the output representation is not id-only.

Everything else stays fixed unless an ablation demonstrates that it must move:
history length 50, embedding width 64, mean-pooled user representation, the
shared cold-start threshold of ten, temporal split, recall@500 evaluation, and
FAISS retrieval. Holding those boundaries makes the result attributable.

### Temperature

- Set `logit_temperature = 0.05` as the initial default because it produced
  the best full-data v1 result measured so far.
- Run `0.05` and `0.1` on the same deterministic pilot. ADR 0006's sweep found
  lower loss at 0.1 on the pilot but measured only 0.05 on the full dataset.
- Correct the positive and sampled-negative logits consistently. Keep
  `correct_positive_logit = True`; the prior ablation moved loss by 8.5 nats
  without moving retrieval and showed the implementation matches sampled-
  softmax semantics better than ADR 0006's original wording.
- Freeze the chosen value before the full run and log it in MLflow and the
  serving manifest.

### Hard negatives

- Bootstrap mining from the v2 model after one warm-up epoch. For each positive
  prefix, retrieve a bounded nearest-neighbour pool and select high-scoring
  items that are not the target and not in the prefix.
- Each batch contains both random corrected negatives and hard negatives. Start
  at a 3:1 random-to-hard ratio so mining sharpens the boundary without letting
  early model mistakes dominate the objective.
- Refresh the mined pool once per epoch, not per batch. This keeps CPU cost
  bounded and makes the pool an inspectable artifact with a checksum.
- False-negative controls are mandatory: exclude every known positive for that
  user strictly before the example timestamp, the current target, padding, and
  duplicates. Future interactions remain unknown to the training example and
  are not used as an exclusion oracle.
- Log pool size, unique-item coverage, rejection rate, mean popularity rank,
  random/hard loss components, and the fraction of examples with fewer hard
  negatives than requested.

### Item side features

- Add multi-hot MovieLens genres and normalized release year to the item tower.
  These are local, deterministic, and available for the full catalog.
- Project the side-feature vector to width 64 and combine it with the id
  embedding through a learned gated residual. An item with a known id may use
  both paths; an unseen id uses the side-feature projection alone.
- Represent `(no genres listed)` with an explicit missing-genre bit rather than
  an all-zero vector indistinguishable from malformed input.
- Parse release year from the canonical catalog metadata and carry an explicit
  missing-year bit. Fit normalization parameters on the train catalog only and
  record them in the artifact.
- Text embeddings are deferred. They add a model/provider, cache, dimensionality
  choice, and licensing/reproducibility surface before genres and year establish
  whether item content helps at all.

### Point-in-time construction

- Training examples remain `(history strictly before t, positive at t)`.
  Equal-timestamp interactions are excluded from the prefix.
- Item metadata must be restricted to fields that are static or known by `t`.
  Movie title, genres, and release year qualify; interaction-derived popularity
  does not enter the item tower in this version.
- Sequence construction and hard-negative exclusions share one tested helper so
  their definition of “known before t” cannot drift.
- The synthetic cohort remains evaluation-only and cannot teach the embeddings.

## Experiment plan

The experiment is staged to avoid another expensive full-data sweep that
answers only that the implementation was broken.

### Gate 0 — deterministic correctness

- Exact prefix tests around train/holdout and equal-timestamp boundaries.
- A tiny dataset must overfit: positive logits rise above both random and mined
  negatives, and retrieval returns the held-out target.
- Two runs with the same seed produce identical training examples, mined-pool
  checksums, metrics, and saved artifacts.
- Exact FAISS search is used here so ANN approximation cannot conceal defects.

### Gate 1 — bounded pilot

Use the existing deterministic 6% user pilot and compare these cumulative arms:

- v1 reproduced at temperature 1.0;
- temperature only at 0.05 and 0.1;
- chosen temperature plus hard negatives;
- chosen temperature plus item side features;
- complete v2 with both additions.

Every arm uses the same users, examples, seed, evaluation set, and compute
budget. An arm that cannot beat popularity@500 on the pilot does not receive a
full-data run unless its ablation is needed to interpret another arm.

### Gate 2 — full MovieLens 25M

Run the frozen complete configuration at seeds 42, 7, and 13. If CPU cost makes
three full fits impractical, run seed 42 first; additional seeds are required
before any promotion claim, not before recording a negative result.

Report:

- recall@500 and NDCG@500 overall, warm, and cold;
- ADR 0011 h0/h1/h3/h10 synthetic slices;
- popularity@500, two-tower v1, and item-item under the same routing policy;
- catalog coverage, unique retrieved items, and popularity distribution;
- cold-item recall on a new slice whose target item has no train interaction;
- fit, mining, index-build, and evaluation wall-clock plus peak memory.

## Promotion rule

Item-item remains champion unless v2 clears ADR 0004 on warm recall@500 under
the same temporal split, K, threshold, exclusions, and eligible users. The
candidate must also avoid a material cold-slice regression and pass the
unchanged authenticated service p99 gate after export.

The primary experiment can succeed without promotion. Beating v1 and
popularity while remaining below item-item would demonstrate a functioning
learned retriever and identify whether another iteration is justified. Failing
to beat popularity ends this two-tower line for now and moves the project to
ADR 0016.

## Serving and artifact contract

- Extend the manifest with temperature, side-feature schema, normalization
  values, vocabulary fingerprint, and mined-pool/training configuration.
- Export one item matrix after training; side-feature computation never runs on
  the request path for known catalog items.
- A live user is encoded from the same ordered positive ids and threshold rule
  used offline. Unknown item ids in history use their content representation
  when metadata exists and are ignored with an audited count otherwise.
- Continue through the existing FAISS and coordinator interfaces so exclusion,
  ranking, auditing, and fallback behavior do not fork by retriever.
- Benchmark encoder p50/p95/p99 separately, then run the unchanged end-to-end
  k6 profile. No latency threshold moves to accommodate the new model.

## Alternatives considered

### Tune v1 further

Rejected. The sweep covered learning rate, temperature, budget, positive-logit
correction, and exact versus approximate retrieval. Eight epochs did not help,
and fixing the loss did not fix recall. More scalar tuning would avoid the
missing training signal rather than address it.

### Hard negatives without side features

This is an ablation, not the final v2. It directly tests whether random
negatives caused the weak boundary and may provide most of the warm-user gain.
It cannot represent a new item, which is the principal capability that makes a
learned item tower strategically different from item-item.

### Side features without hard negatives

Also retained as an ablation. It tests cold-item representation cleanly but may
still learn popularity from easy random negatives. Shipping it alone would
leave the sweep's central diagnosis unresolved.

### SASRec now

ADR 0016 argues that sequence is the largest unused user signal and remains the
next model. Deferred because the owner wants one disciplined repair of the
existing learned retriever first. The stop conditions below prevent this repair
from becoming an indefinite detour.

### Add pretrained text embeddings now

Deferred until structured metadata establishes value. Text would likely improve
semantic cold-item representations, but it makes a negative experiment hard to
interpret and introduces an external artifact whose version must be pinned.

## Consequences

- The two-tower trainer gains an epoch-level mining stage and catalog feature
  encoder, increasing CPU time and artifact complexity.
- The model can produce embeddings for catalog items unseen in interactions,
  enabling an honest cold-item evaluation for the first time.
- The comparison remains readable because every major addition is an ablation
  and the unchanged v1 is reproduced in the same pilot.
- The sidecar interface stays stable; most serving risk is artifact validation
  and per-request user encoding rather than coordinator changes.
- SASRec is delayed by one bounded experiment. If the stop rule fires, the
  result is recorded and the project moves on without another tuning round.

## Risks and mitigations

- **False hard negatives:** filter the point-in-time prefix and target; report
  rejection rates; inspect a fixed sample of mined pools.
- **Popularity feedback loop:** mix random negatives, report popularity and
  coverage metrics, and compare the hard-negative-only contribution.
- **Mining cost:** refresh once per epoch, cap the pool, and stop at the pilot
  unless it beats popularity.
- **Metadata leakage:** admit only static catalog fields in v2 and test unknown
  and missing metadata explicitly.
- **Ablation explosion:** run only the five named cumulative arms; any new arm
  requires a written reason before compute is spent.
- **Serving skew:** serialize preprocessing parameters and compare offline and
  loaded-model embeddings on a fixed fixture.

## How we would know this decision is wrong

- Complete v2 cannot beat popularity@500 on the deterministic pilot.
- Hard-negative mining improves training loss but not recall or coverage.
- Side features improve only head items and fail the cold-item slice.
- Full-data v2 remains within seed noise of v1 or materially below popularity.
- Mining or full fitting exceeds the recorded compute budget without a pilot
  gain large enough to justify it.
- Exported online encoding breaks the p99 gate or produces embeddings that do
  not match offline inference.
- Any result depends on future interactions, duplicate positives, or already-
  seen leakage. Such a run is invalid, not a model win.

If the complete model fails the popularity floor after correctness checks, the
two-tower repair is closed as measured and not promoted. ADR 0016 becomes the
next approval decision; no third two-tower tuning cycle begins without new
evidence.

## References

- Yi et al., “Sampling-Bias-Corrected Neural Modeling for Large Corpus Item
  Recommendations,” RecSys 2019.
- Yang et al., “Mixed Negative Sampling for Learning Two-tower Neural
  Networks,” 2020.
- [ADR 0006](0006-two-tower-retrieval-architecture.md), including its
  2026-08-30 full-data sweep note.
