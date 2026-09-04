# Memo — where ranker features come from (D-009)

**Status:** deferred by the owner on 2026-09-04. Written after the 2026-09-03 ADR 0009 amendment was
withdrawn for closing this question without costing the alternatives. The analysis and the measured
numbers below stand; only the timing is settled.

**Deferral and trigger.** The owner is prioritising the modeling ladder over serving work. Deferring
costs the modeling track nothing: ranker training computes features through `FeatureIndex` in Python
and never touches the materialization path, so the cross join is not on any training or evaluation
critical path. It becomes blocking the first time a full-data champion is materialized for serving —
M2, which is where D-006 already commits production to the exact full-data model. **Trigger: decide
this before any full-25M feature materialization, and before M2 opens the serving path.** Until then
the feature-parity test remains the compensating control for having two implementations, and it only
binds them at a materialization timestamp.

**The read at the time of deferral was Option C, sequenced as below.** Recorded so it does not have
to be re-derived: the latency objection to C is weaker than it looks, because M2 is already spending
that budget on a transformer encoder with its own 15 ms target — a bitmask intersection over ~71
masks is noise next to a forward pass. And C's shape is what M4's sequence-aware ranker needs anyway
(request-time features over a retrieved slate, point-in-time correct at training), so building the
user×item fix as bespoke serving code means building the same pattern twice. The counterpoint that
should be weighed when this comes back: `user_genre_affinity` is a hand-crafted feature a sequence
model may retire outright, so the goal is to establish the pattern, not to perfect the feature.

## The question

Two questions that look separate and are not:

1. Does ranker training read its features from Feast's `get_historical_features`, as ADR 0009's
   Decision bullet 6 says it will, or from the `FeatureIndex` bisect, as the code actually does?
2. What is the user×item feature representation at full MovieLens 25M scale (D-009)?

They meet at one feature. Seven of the eight ranker features are single-entity. Only
`user_genre_affinity` is user×item, and it is the one that forces both answers.

## Why it is open again

ADR 0009 does not treat point-in-time joins as one benefit among several. Its Rationale #1 calls
them "the single feature we're paying Feast for," and Decision bullet 6 commits ranker training to
`get_historical_features` specifically so the project stops owning a hand-written point-in-time
lookup. The 2026-08-21 implementation note then recorded that `FeatureIndex` was kept. A 2026-09-03
amendment made that permanent. Withdrawing the amendment restores the honest state: the code departs
from the ADR, and the departure has not been decided.

## What is actually built today

All three feature views read snapshot tables (`src/features/feast_repo/features.py:16-28`), and
`build_snapshots` stamps every row with `event_timestamp = as_of` — one materialization instant
(`src/features/materialize.py:71`).

The withdrawn amendment was right about the consequence, and this part survives: **a snapshot-grained
source cannot be point-in-time correct for an arbitrary timestamp in either direction.** An as-of
join at a 2015-era training timestamp finds no row at or before it and returns null. Materialize
earlier and it returns a value computed from data *after* that timestamp, which is leakage. Feast is
not the obstacle here; the source shape is.

Two further facts belong on the record:

- The user×item snapshot cross-joins users against the whole catalog (`materialize.py:44`). At full
  scale that is 162,541 × 62,423 = **10,146,296,843 rows**. This is already flagged in
  `00-current-state.md`, and D-006 commits production to the exact full-data champion, so it is a
  blocker rather than a wart.
- Serving reads the online store, so its values are as of the last materialization. The parity test
  proves Python equals Feast-historical equals Feast-online **at a materialization timestamp**. It
  does not prove serving is exact at request time. Batch staleness is normal and expected; it should
  still be named rather than implied away.

## What each of the eight features actually needs

| Feature | Entity | Exact from event-grained rows? | What exactness requires |
|---|---|---|---|
| `user_interaction_count` | user | yes | running count at each event |
| `user_days_active` | user | yes | max−min at each event |
| `user_days_since_last_interaction` | user | no | clock-dependent; exact from a stored last-event timestamp minus request time |
| `item_popularity_all_time` | item | yes | running count at each event |
| `item_age_days` | item | no | clock-dependent; exact from a stored first-event timestamp |
| `item_popularity_30d` | item | no | sliding window moves with the clock, not only with events |
| `item_popularity_7d` | item | no | same |
| `user_genre_affinity` | user×item | no | the user's prior-history genre distribution, intersected with the candidate's genres |

So: three are exactly materializable as event-grained rows; three more are exact if a single anchor
timestamp is stored and subtracted at request time; two are not. The sliding-window pair is the
genuinely hard case — for a rarely-rated item the last event can be years before the query, and the
value stamped at that event is simply wrong by then. No purely materialized source fixes that. Only
computation at request time does.

That is a real caveat to ADR 0009's headline rationale: "Feast owns point-in-time correctness" is
reachable for these eight features only with on-demand feature views, not with batch sources alone.

## The compact exact form of `user_genre_affinity`, measured

The feature is the fraction of the user's strictly-prior movies sharing at least one genre with the
candidate (`src/features/pipeline.py:190-203`).

Measured against `data/raw/ml-25m` on 2026-09-04:

| Quantity | Measured |
|---|---|
| Distinct genre labels | 20 |
| Distinct genre combinations in the catalog | 1,639 |
| Movies with 1 / 2 / 3 genres | 30,631 / 18,326 / 9,852 |
| Distinct (user, genre-mask) pairs across all 25,000,095 ratings | **11,532,291** |
| Mean distinct masks per user | 71.0 |
| Full user × catalog cross join | 10,146,296,843 |
| Reduction | **880×** |

Group a user's history by 20-bit genre mask. Then affinity is the summed count over the masks that
intersect the candidate's mask, divided by the user's total. That is exact, and it costs one bitwise
AND per (user mask, candidate) pair — about 71 masks against a 500-candidate slate.

**This corrects D-009's recommended default.** A "compact user genre vector" read literally — 20
per-genre counts — cannot reproduce this feature. The definition is a union over the candidate's
genres, so recovering it from per-genre counts needs inclusion–exclusion over every subset. The exact
compact form is the per-user mask→count map above, not a 20-length vector. Anything else silently
changes the feature.

## Options

### Option A — status quo, defer both questions

Snapshot sources stay, `FeatureIndex` owns training, the cross join stays.

- **Cost to build:** none.
- **Correctness:** training values are exact today.
- **Full scale:** blocked. 10.1B rows is not a materialization anyone runs, so the full-data champion
  D-006 commits to cannot be served without revisiting this anyway.
- **Design review:** the weakest answer. Feast's headline justification goes unused, and two
  implementations of eight features coexist with a parity test that only binds them at
  materialization timestamps.

### Option B — fix the user×item representation, keep `FeatureIndex` for training

Replace the cross-join snapshot with the measured mask→count map (11.5M rows) plus request-time
intersection. Leave the training source alone.

- **Cost to build:** one new materialization query, one online read shape, a serving-side
  intersection, and a parity test extension. Contained; touches no training code.
- **Correctness:** exact, by the argument above.
- **Full scale:** unblocked — this is the piece full-scale serving needs regardless of what is
  decided about training's feature source.
- **Design review:** honest and defensible, but still two implementations.

### Option C — event-grained sources plus on-demand feature views, Feast owns both paths

Point the user and item views at event-grained SQL, store anchors for the clock-dependent features,
and express the sliding windows and genre affinity as on-demand transformations. Training and serving
then call the same Feast definitions.

- **Cost to build:** the largest by a wide margin, and it lands in the serving path.
- **Correctness:** one definition per feature, which is the drift class non-negotiable #2 exists for.
- **Latency risk:** on-demand transforms execute inside the p99 < 100 ms budget for every candidate
  in the slate. Unmeasured. This is the reason not to commit to C before measuring.
- **Design review:** the strongest answer, and the one ADR 0009 actually promised.

## My read

Take **B now, hold C as the target**, and reject A.

The two questions separate in the direction that matters. The cross join has to go regardless of what
is decided about training's feature source, because D-006 commits production to the exact full-data
champion and 10.1B rows will not materialize. So the storage half is urgent and independent; the
training-source half is not urgent and gets much cheaper later, when M2 opens the sidecar for a
learned retriever anyway.

Option C is the right end state and I would not argue otherwise — but committing to it now means
putting unmeasured per-request computation inside a latency gate the project treats as
non-negotiable, during the phase where the modeling ladder is supposed to be the priority.

What I would *not* do is what the withdrawn amendment did: declare the question closed in the
direction of least work. "Feast holds snapshots, Python holds arbitrary timestamps" is an accurate
description of today. It is not a decision, because it never priced the alternative.

## How we would know this read is wrong

- Measure the intersection cost at a 500-candidate slate with a 71-mask user. If it does not fit
  comfortably inside the serving budget, B's request-time computation is wrong and the affinity has
  to be precomputed for the retrieved candidates instead.
- Measure a Feast as-of join over a 25M-row event-grained source for ~154k entity rows. If it is slow
  enough to make training impractical, C is wrong on cost, not only on latency.
- If the mask-map row count grows faster than the ratings table as users arrive, B's storage claim is
  wrong. It is bounded by distinct (user, mask) pairs, so this would be surprising.
- If a future feature is added whose definition is not decomposable this way, B's boundary moves
  again and the case for C strengthens.

## What is being asked

1. A, B, or C.
2. Whether ADR 0009's Decision bullet 6 is amended to match the choice, or left standing as the
   target with the departure recorded.
