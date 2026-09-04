# Rolling-origin backtest contract

## Purpose

One fixed holdout has now decided the cold-start threshold, the promotion-gate slice reading, the
ranker's training-sample size, the tolerance floor, and the two-tower's fate. Each of those was an
honest measurement. Together they are a selection process, and the thing being selected on is the
same 28 days of MovieLens. That is the mechanism behind risk R-02, and it does not announce itself:
a holdout that has been optimized against still returns a number, it just returns one that is a
little too kind to whatever was chosen with its help.

Rolling-origin backtests are the development answer. Instead of one window carrying a verdict,
several do, and a result that only holds in one month is visibly a result that only holds in one
month. The sealed test partition stays sealed; this contract is about everything before it.

Implemented by `src/data/split.py` (window derivation and slicing) and `src/evaluation/aggregate.py`
(reporting). It changes no gate and promotes nothing on its own.

## Window definition

Let `B` be the sealed-test boundary — `temporal_split(ratings).holdout_end`, the timestamp ADR 0001
reserves everything from. Let `L` be 28 days. Window `i`, for `i = 0, 1, 2, …`:

| Region | Interval | Rule |
|---|---|---|
| train | `(−∞, B − (i+1)·L)` | `t < train_cutoff` |
| holdout | `[B − (i+1)·L, B − i·L)` | `train_cutoff ≤ t < holdout_end` |
| unavailable | `[B − i·L, ∞)` | never returned by `apply_backtest_window` |

Three properties follow, and all three are load-bearing:

- **Window 0 is ADR 0001's split.** `B − L` is exactly the 80th-percentile cutoff, so window 0's
  train and holdout are the fixed split's train and holdout, row for row. This is why the window
  length is `HOLDOUT_DAYS` by definition rather than a parameter: give the windows their own length
  and window 0 becomes a window that merely resembles the fixed holdout, and every recorded number
  in `docs/results.md` stops being readable as a window result.
- **Indices count backwards from the anchor.** Asking for a fourth window appends `w3` and renames
  nothing. Numbering forwards would repoint every id the day someone widened the suite, and two
  runs with matching window ids would then be pooled as measurements of different months.
- **The origin expands.** Each window trains on all history before its own holdout, not on a
  fixed-length trailing slice. This is the simulation being run: retrain on everything that has
  happened, then serve the next 28 days.

`train_cutoff` and `holdout_start` are separate fields that are equal today. They are two different
claims — the last instant a model may learn from, and the first instant it is scored on — so an
embargo between them would be a change of value, not a change of shape.

## Window identity

`BacktestWindow.window_id` is `rolling-origin-v1:w<index>:<holdout_start>-<holdout_end>`, and it is
what `ProtocolManifest.backtest_window_id` carries. The manifest also stores `holdout_start` and
`holdout_end` as their own fields, so the interval in the id is redundant on purpose: an id that was
only an index would silently name a different month the day the anchor moved, and the gate would see
two matching ids and conclude the runs were comparable.

The `rolling-origin-v1` prefix moves if the tiling rule changes. That is the whole mechanism for
retiring old evidence — a run made under the old rule then has a different semantic hash and the
gate refuses to pool it, without anyone having to remember to go and do that.

A run against the plain fixed split can and should stamp `fixed_holdout_window(ratings).window_id`.
It has always had a window identity; it just had no way to say so.

## What "no overlap" means

`assert_no_window_leakage` enforces four claims, and `rolling_origin_windows` calls it on every set
it returns. Stated precisely, because "no overlap" is ambiguous enough to be worth pinning:

1. **No window trains on its own future.** `train_cutoff ≤ holdout_start`, checked in
   `BacktestWindow.__post_init__` so it holds for hand-built windows too.
2. **No two windows share a holdout row.** Holdout intervals are pairwise disjoint, so an
   interaction is never scored twice and the mean across windows is a mean over distinct evidence.
3. **No window reads the sealed partition**, and every window in a set agrees on where the boundary
   is. A set mixing boundaries was derived from two different datasets, and its aggregate would be a
   number with no referent.
4. **Indices and ids are unique**, because two windows answering to one id would be pooled as
   repeated measurements of one thing.

### What is deliberately not forbidden

A newer window trains on an older window's holdout. Under an expanding origin this is unavoidable —
window 0's training data ends at `B − L`, which is after window 2's holdout ended — and the M0-07
brief asked for it to be prevented. It is not prevented, and the reasoning should be on the record
because the requirement as written is reasonable-sounding and wrong.

It is not leakage. Leakage is a model seeing, at training time, information from the period it is
scored on. Every window here trains strictly before its own holdout, so every window's metric is
individually honest, and a mean of honest estimates is not inflated by the fact that they share
history.

Forbidding it costs more than it buys. To make every window's training data disjoint from every
other window's holdout, the pre-test timeline has to be cut into blocks of `[train_i][holdout_i]`
laid end to end. Each window then trains on roughly a third of the history, window 0 stops
reproducing ADR 0001, and comparing window 0 to window 2 becomes a comparison of training-set sizes
wearing a temporal comparison's clothes — which is exactly the confound that
`RANKER_POSITIVE_LIMIT` turned out to be hiding in August.

The real cost of the expanding origin is different and is not zero: **window results are
correlated**, through shared training data and shared users. That is a fact about the uncertainty,
not about the point estimates, and it is why the interval below resamples users as clusters across
all windows at once rather than window by window.

## The sealed test partition

`apply_backtest_window` returns a `BacktestSplit` with `train` and `holdout` and no `test`
attribute. There is nothing to reach for, so evaluating against the sealed partition is not one
typo away; it requires deliberately writing a different filter somewhere else. Window construction
additionally refuses any `holdout_end` past the boundary, so a hand-built window cannot get there
either.

This does not replace D-005. The unseal trigger, the freeze procedure, and the one-shot rule are
policy and live in `../workstreams/evaluation-and-gates.md`. What this contract adds is that the
development path has no accidental route to the test rows.

## Equal timestamps

Boundary behaviour is ADR 0001's, unchanged and shared: `t < train_cutoff` trains,
`[holdout_start, holdout_end)` is scored, and a row landing exactly on a boundary belongs to the
*later* slice. Ties therefore cannot put a model's own target into its training data no matter how
many rows share a second — the guardrail's rule 2, applied at the split boundary rather than inside
a sequence.

This is the detail most likely to be silently wrong, so it is tested three ways: directly on a
hand-built window with duplicated boundary timestamps, by asserting window 0 reproduces
`temporal_split` row for row on a frame whose cutoff second is shared by sixty-one rows, and by an
independent recomputation of the ADR's rule that never calls into `src/data/split.py` at all.

## Aggregate reporting

`backtest_summary(per_user_by_window)` takes `{window_id: {user_id: value}}` and reports:

| Field | Definition |
|---|---|
| `mean` | unweighted mean of the per-window means |
| `stdev` | sample standard deviation across windows |
| `minimum` / `maximum` / `relative_range` | spread; `relative_range` is `None` when the mean is zero |
| `worst_window_id` | the lowest-scoring window, ties broken by id |
| `interval` | user-clustered bootstrap interval for the mean |

Windows are weighted **equally**, not by user count. Each window is one observation of "would this
have held that month?", and user-weighting would let one dense month outvote the other two — which
is the aggregate-carried-by-a-minority failure ADR 0001's 2026-08-30 amendment already had to fix
once, in the warm/cold direction.

`paired_user_deltas(candidate, incumbent)` builds the input for a comparison. It requires identical
window sets and identical per-window populations, which is the same rule `retrieval_gate` applies to
slice populations one level up.

### What the bootstrap resamples, and why

**The unit is the user.** The metric of record is an unweighted mean over users, so the population
an interval should describe is "another draw of MovieLens users" — that is the variation a promotion
decision is actually exposed to. Resampling *interactions* instead would treat one user's items as
independent observations when they share a single taste, and would report an interval narrower than
the evidence supports. Resampling *windows* would give three observations and an interval too wide
to say anything.

**The draw is clustered across windows.** A user is drawn once, and that draw applies to every
window the user appears in. The windows share users, so resampling independently inside each window
would treat them as independent replications and understate the aggregate's width — the specific
error that would make a rolling backtest look *more* conclusive than a single holdout instead of
less, which is the opposite of the point.

**Pairing is preserved.** Users differ from each other far more than two models differ on one user,
so a paired delta removes the between-user variance both models share before the resampling starts.

The method is the percentile bootstrap: 2000 replicates, 2.5/97.5 percentiles, no recentring, so a
skewed replicate distribution gives an asymmetric interval. BCa was considered and not implemented —
it needs a jackknife pass per window and buys accuracy that matters at the tails, and nothing here
reads a tail.

If a resample ever leaves a window with no drawn users, the whole summary refuses rather than
averaging the windows that survived, because dropping a window silently changes what the number
means. At realistic populations this cannot happen; it exists for the degenerate case where one
window is carried by a handful of users, which is a situation the reader should be told about rather
than smoothed over.

### Determinism

The seed is fixed (`BOOTSTRAP_SEED`, deliberately not one of the training seeds so a report never
has two meanings for "seed 42"), users and windows are sorted before they index the draw, and the
generator is `numpy.random.default_rng`. Two readings of the same numbers produce identical bounds,
and a differently-ordered dictionary produces identical bounds. Both are tested.

## Alternatives considered

- **Windows strictly older than the ADR 0001 cutoff**, keeping the fixed holdout in reserve as a
  quasi-confirmation set. Rejected: the holdout has already absorbed months of decisions, so
  treating it as reserve would be self-deception, and it would leave the most recent pre-test month
  unused by development while the windows all measured older regimes.
- **Disjoint train/holdout blocks**, so no window's holdout is any window's training data. Rejected
  above: it turns a temporal comparison into a training-set-size comparison and breaks continuity
  with ADR 0001.
- **A fixed-length sliding training window.** Standard practice, and it would hold training-set size
  constant across windows, which is a genuine advantage. Rejected for now because it makes no window
  reproduce the fixed split, and because choosing the trailing length is a new tunable nobody has
  measured. Worth revisiting if the windows turn out to disagree in a way that tracks history length.
- **An embargo between `train_cutoff` and `holdout_start`.** Necessary when labels have a lookahead
  horizon; MovieLens ratings are point events, so there is nothing to purge. The two fields exist so
  this stays a value change if that ever stops being true.
- **Weighting windows by user count.** Rejected above.
- **Reporting a t-interval across the three window means.** Three observations, and they are
  correlated, so the assumption is wrong twice over.

## Consequences

- Development evidence still includes the fixed holdout, as window 0. This reduces selection
  pressure on it — a verdict now has to survive three windows — but it does not remove it. Anyone
  claiming the fixed holdout has been retired should be corrected.
- Older windows train on less history and score a catalog with fewer items in it. Some of the
  window-to-window spread will be that, not model instability, and the report does not currently
  separate the two. Catalog and user counts per window are on the workstream's list of things a
  window report should carry; they are not in `BacktestSummary` yet.
- Three windows is a floor enforced in two places (`rolling_origin_windows` and
  `backtest_summary`). A dataset that cannot support three windows fails loudly rather than
  returning a suite of confident zeros.
- Nothing is wired into the trainers. `src/training/*` still calls `temporal_split` and is
  unchanged; making a rolling run cheap enough to be routine is separate work, and on 25M rows it
  means three trainings per model per seed, which is a real compute decision rather than a wiring
  detail.

## How we would know this is wrong

- **If the windows never disagree.** If every comparison moves all three windows the same way by
  similar margins, the suite is costing 3× the compute to restate what one window said, and the
  honest response is to say so and go back to the fixed holdout plus a seed mean.
- **If the spread across windows is dominated by history length rather than by the model.** The tell
  would be window rank order staying fixed across unrelated models — window 2 always worst, for
  every architecture. That would mean the windows are measuring the era, not the model, and the
  sliding-window alternative becomes the better design.
- **If a decision taken on the rolling suite is later contradicted by the sealed test.** That is the
  outcome this exists to prevent, and it would mean the windows are correlated enough with each
  other that three of them are not three pieces of evidence. The response would be wider spacing
  between windows, or fewer decisions before the seal opens — not a wider interval.
- **If the interval is routinely so wide that nothing clears it.** Then the paired, clustered design
  is not buying the precision it was supposed to, and the next thing to check is whether the
  per-user metric is too coarse — recall on a single held-out target is 0 or 1, and a mean of
  Bernoulli draws needs a lot of users before its interval closes.
