# The cold-start routing divergence, measured both ways

**Date:** 2026-08-30
**Decision (owner, 2026-08-30): option (a), at threshold 10.** ADR 0001's
threshold becomes the offline routing rule as well as the online one, and the
threshold itself moves from 5 to 10 — *"the first ten should be popular, then
personalized."* `cold_start_threshold=COLD_START_THRESHOLD` is the constructor
default on all three learned candidate models and `threshold` is the default
`SYNTH_COLD_ROUTING` policy; `None` / `index` is kept as the explicit opt-out,
and a run made under it is renamed `<base>-index-routing`. Recorded in
[ADR 0001's 2026-08-30 amendment](adr/0001-evaluation-protocol.md#amendment-2026-08-30--the-cold-start-threshold-is-10-online-and-offline)
and [ADR 0011's note](adr/0011-cold-start-coverage.md#2026-08-30--the-threshold-is-10-so-h10-sits-on-the-boundary).

**Status:** closed. Everything below is the measurement the decision was taken
from, and it is left exactly as it was written — including the tense. It was
made at threshold 5; the head-to-head tables are still the right comparison
between the two *policies*, because both arms were run at the same threshold.
**Companion:** [`promotion-gate-slice-decision.md`](promotion-gate-slice-decision.md) — the other ADR 0001 question this measurement session surfaced, summarised at the bottom of this page.
**Runs:** all in [`results.md`](results.md), "2026-08-30 — the two-tower finished, and both cold-start routing policies were run".

## The two policies, stated precisely

Two rules in this repository answer "is this user cold enough that the learned
path should not serve them?", and they do not agree.

**Index membership** — what the offline candidate models do. `CFModel`,
`ItemItemModel` and `TwoTowerModel` each embed a `PopularityModel` fallback and
route to it exactly when the fitted index has never seen the user. In code, all
three `was_served_by_*` predicates reduce to *"is this user in the fitted user
index at all?"* — `self._knn is not None and user_id in self._user_to_index` for
item-item, the same shape for the other two. A user with **one** training
interaction is in that index, so one interaction is enough to be served that
film's cosine neighbours (or that film's embedding) as though it were a taste
profile.

**ADR 0001's threshold** — what the protocol and the deployed service do.
`src/evaluation/protocol.COLD_START_THRESHOLD` is 5; `evaluate` slices warm from
cold on it, and `src/serving/recommendations.py` routes on the same number,
counted over unique watched movie ids. Below five signals, a live request is
answered by the popularity fallback and the response says so in
`serving_policy`.

[ADR 0011](adr/0011-cold-start-coverage.md)'s cohort measured the gap on first
use rather than assuming it. Its `expected_fallback_served` is derived from
`COLD_START_THRESHOLD` — deliberately, so that a model whose boundary sits
somewhere else shows up as a mismatch instead of defining its own pass mark —
and every learned run since has carried `synth_cold_routing_ok = false`, with
fallback counts of 500/0/0/0 where the threshold implies 500/500/500/0.

It is worth being exact about what is wrong, because it is not obviously the
models. ADR 0001's own sentence is *"Cold-start users fall back to the popularity
baseline **at serving time** until they cross the threshold"* — which the
deployed path does. What the ADR does not say is what the *offline* models should
do, and the consequence is that the offline metrics are not measuring the policy
production runs. Serving a 1-interaction user their one film's neighbours is a
defensible product decision; measuring it and calling the result "the deployed
recommender's recall" is not.

## What was run

Both policies, on the full dataset, through the trainers — no metric on this
page was computed by hand (non-negotiable #5).

The switch is opt-in and non-default: every candidate model gained a
`cold_start_threshold: int | None` field where `None` (the default) is the
index-membership rule unchanged, and the trainers read `SYNTH_COLD_ROUTING`
(`index`, the default, or `threshold`) through
`src/models/candidates/routing.py`. A default run of `main` produces the same
numbers, under the same MLflow run name, as it did before this document existed;
only a run with the variable set is renamed, tagged and behaves differently. The
one shared helper, `learned_path_serves`, is what all three models call, and
`recommend` now calls its own `was_served_by_*` predicate rather than restating
the condition — so the predicate the per-policy metrics and the ADR 0011 bucket
counts are computed from cannot disagree with the branch that actually routed
the request.

Seven runs on MovieLens 25M at DVC version `c3ce6309f6f0ec347a9e0a662c640021.dir`,
with the ADR 0011 cohort at fingerprint `ae4475f0e063…` attached to every one:
item-item, CF/ALS and the two-tower under **both** policies, and the popularity
baseline as the control that has no policy to choose — it *is* the fallback, so a
routing predicate for it would be a tautology. Run ids, fit times and wall-clocks
are in [`results.md`](results.md)'s 2026-08-30 section.

**The default reproduces `main` bit-for-bit.** The index-policy item-item run made
after the switch existed (`ab1fe49d…`) and the item-item run of record made before
it (`65faeebb…`, 2026-08-29) agree to the last digit MLflow stores on every metric
— warm recall@500 `0.4001438271370617`, warm NDCG@500 `0.13924022499505487`,
overall `0.43438668830444793` / `0.21896241214812664`. That is the claim the
opt-in design is making, checked rather than asserted. Each of the four short runs
below was also made twice during this session, on two separate checkouts, and each
pair agrees on **all 38 logged metrics** with no differences at all.

### Item-item cosine, K_CANDIDATES = 500

Runs `ab1fe49d…` (index) and `006224c4…` (threshold).

| Slice | Users | index | threshold | change |
|---|---:|---:|---:|---:|
| Warm recall@500 | 1,939 | 0.400144 | 0.400144 | **0.00%** |
| Warm NDCG@500 | 1,939 | 0.139240 | 0.139240 | **0.00%** |
| Cold recall@500 | 702 | 0.528969 | 0.528527 | −0.08% |
| Cold NDCG@500 | 702 | 0.439164 | 0.438946 | −0.05% |
| Overall recall@500 | 2,641 | 0.434387 | 0.434269 | −0.03% |
| Overall NDCG@500 | 2,641 | 0.218962 | 0.218905 | −0.03% |

Per-policy attribution — the only row that moves is which side of the line one
user sits on:

| | Learned-served | recall@500 | NDCG@500 | Fallback-served | recall@500 | NDCG@500 |
|---|---:|---:|---:|---:|---:|---:|
| index | 1,940 | 0.400275 | 0.139354 | 701 | 0.528789 | 0.439278 |
| threshold | 1,939 | 0.400144 | 0.139240 | 702 | 0.528527 | 0.438946 |

### CF / ALS, K = 10

Runs `8b8b86d7…` (index) and `c491a823…` (threshold).

| Slice | Users | index | threshold | change |
|---|---:|---:|---:|---:|
| Warm recall@10 | 1,939 | 0.033841 | 0.033841 | **0.00%** |
| Warm NDCG@10 | 1,939 | 0.057850 | 0.057850 | **0.00%** |
| Cold recall@10 | 702 | 0.063780 | 0.063829 | +0.08% |
| Cold NDCG@10 | 702 | 0.487981 | 0.488107 | +0.03% |
| Overall recall@10 | 2,641 | 0.041799 | 0.041812 | +0.03% |
| Overall NDCG@10 | 2,641 | 0.172182 | 0.172216 | +0.02% |

| | Learned-served | recall@10 | NDCG@10 | Fallback-served | recall@10 | NDCG@10 |
|---|---:|---:|---:|---:|---:|---:|
| index | 1,940 | 0.033841 | 0.057869 | 701 | 0.063822 | 0.488542 |
| threshold | 1,939 | 0.033841 | 0.057850 | 702 | 0.063829 | 0.488107 |

### The two-tower, K_CANDIDATES = 500

Runs `5628ab0b…` (index) and `3286c968…` (threshold).

| Slice | index | threshold | change |
|---|---:|---:|---:|
| Warm recall@500 | 0.046581 | 0.046581 | **0.00%** |
| Warm NDCG@500 | 0.014575 | 0.014575 | **0.00%** |
| Cold recall@500 | 0.528085 | 0.528527 | +0.08% |
| Cold NDCG@500 | 0.438672 | 0.438946 | +0.06% |
| Overall recall@500 | 0.174569 | 0.174686 | +0.07% |
| Overall NDCG@500 | 0.127304 | 0.127376 | +0.06% |

| | Learned-served | recall@500 | NDCG@500 | Fallback-served | recall@500 | NDCG@500 |
|---|---:|---:|---:|---:|---:|---:|
| index | 1,940 | 0.046574 | 0.014575 | 701 | 0.528789 | 0.439278 |
| threshold | 1,939 | 0.046581 | 0.014575 | 702 | 0.528527 | 0.438946 |

The two-tower's cold slice moves **up** here where item-item's moved **down**, and
it is the same single user in both. Item-item served that 1-to-4-interaction user
at recall@500 0.6552, better than the fallback's 0.5288; the two-tower served them
at 0.0345, far worse. Routing them to popularity therefore costs one model a
little and buys the other a little. At n = 1 that is an anecdote, not a finding —
it is here because it explains a sign flip that would otherwise read as an
inconsistency, and because it is a useful reminder that "the learned path is worse
below the threshold" is a claim about a distribution, not about every user in it.

Two checks fall out of the same run. Its **cold row is identical to item-item's
threshold cold row to the last digit** (`0.5285270914041289` /
`0.4389458974142213`) — as it must be, since under the threshold all 702 cold
users are fallback-served and that row is the popularity model regardless of what
sits in front of it. And its three epoch losses are `10.3542 → 10.2726 → 10.2718`,
identical to the index run's, which is the check that the switch changes only
where a request is routed and never what the model learned.

### The ADR 0011 cohort, both policies, against the popularity control

This is where the two policies actually differ, because this is the only
population that has users in the disputed band. Recall per history bucket, 500
users each:

| Model / policy | K | h0 | h1 | h3 | h10 | `routing_ok` |
|---|---:|---:|---:|---:|---:|---|
| Popularity (control) | 10 | 0.0340 | 0.0420 | 0.0160 | 0.0240 | *no predicate* |
| CF/ALS — index | 10 | 0.0340 | 0.0160 | 0.0080 | 0.0180 | false |
| CF/ALS — threshold | 10 | 0.0340 | **0.0420** | **0.0160** | 0.0180 | **true** |
| Item-item — index | 500 | 0.4760 | 0.1440 | 0.2880 | 0.3900 | false |
| Item-item — threshold | 500 | 0.4760 | **0.4600** | **0.4560** | 0.3900 | **true** |
| Two-tower — index | 500 | 0.4760 | 0.1040 | 0.1260 | 0.1280 | false |
| Two-tower — threshold | 500 | 0.4760 | **0.4600** | **0.4560** | 0.1280 | **true** |

Fallback-served counts, which is what `synth_cold_routing_ok` is computed from —
`expected` is derived from `COLD_START_THRESHOLD = 5` and is the same for every
row:

| Policy | h0 | h1 | h3 | h10 |
|---|---:|---:|---:|---:|
| expected | 500 | 500 | 500 | 0 |
| index (all three learned models) | 500 | 0 | 0 | 0 |
| threshold (all three learned models) | 500 | 500 | 500 | 0 |

## What the numbers say

Three findings, in the order they matter.

**1 — The holdout cannot see this decision. Exactly one of its 2,641 users can.**
Every warm figure is *identical* under the two policies, to the last digit, and
every cold and overall figure moves by less than a tenth of a percent. The reason
is a population fact rather than a modelling one: 702 holdout users are cold, and
under index membership 701 of them are fallback-served — so precisely **one**
holdout user has between one and four training interactions, and that single user
is the entire difference between the two policies on this table. MovieLens's
natural holdout is, for this question, empty.

That retires the objection that has blocked the decision until now. Both
[ADR 0011's addendum](adr/0011-cold-start-coverage.md) and `results.md` say
closing the gap "moves every warm/cold and per-policy number already logged",
and that was the reason for treating it as its own unit of work. Measured, that
is true of exactly one table and false of the rest. The **warm / cold / overall
and per-policy tables** move by at most **0.08%** — a fourth-decimal restatement,
on a slice where a single user changed hands. The **ADR 0011 coverage table** does
move substantially, and moves toward what the protocol says should happen. So the
blast radius is real but it is confined to the one table built to detect this
exact thing, which is a very different proposition from "every number already
logged".

**2 — The cohort can see it, and the gap is large.** ADR 0011 exists precisely
because "MovieLens's natural distribution" does not populate the 1–4 interaction
band, and this is the first time that argument has been demonstrated rather than
asserted. On the cohort, routing a 1-interaction user to the fallback instead of
the learned path takes item-item's h1 recall@500 from **0.1440 to 0.4600** — 3.2×
— and h3 from 0.2880 to 0.4560. CF at K = 10 shows the same direction at its own
scale: h1 0.0160 → 0.0420, h3 0.0080 → 0.0160.

**3 — The fallback path is literally the popularity list, and the cohort proves
it.** Under threshold routing, CF's h0, h1 and h3 buckets read 0.0340 / 0.0420 /
0.0160 — the popularity control's numbers, to four decimals, on all three. h10
does not match (0.0180 against 0.0240) and should not: a 10-interaction user is
above the threshold and ALS serves them under both policies. That is the
embedded-fallback wiring reproduced as a measurement instead of a claim about the
code, and it is also the check that the switch does what it says.

**The caveat ADR 0011's own Risks section insists on still applies.** The cohort's
targets are popularity-weighted, so a popularity fallback hits them at the rate
popularity intersects popularity, and every fallback-served bucket is flattered by
construction. Finding 2 is therefore evidence that the two paths *differ* by a
large margin, and evidence about the *direction*, but it is not proof that the
popularity fallback is the better product answer for a 1-interaction user. What
it does establish beyond argument is that the choice is consequential for the
users the threshold is about — which is exactly what a decision needs before it
can be taken on something other than taste.

## What each option would mean

### (a) Adopt the threshold offline — make `cold_start_threshold=COLD_START_THRESHOLD` the default

**For the numbers in [`results.md`](results.md).** Almost nothing. The item-item
candidate row becomes warm recall@500 0.400144 (unchanged), overall recall@500
0.434387 → 0.434269, overall NDCG@500 0.218962 → 0.218905. The CF row's overall
recall@10 goes 0.041799 → 0.041812; the two-tower's overall recall@500 goes
0.174569 → 0.174686. Every warm figure is unchanged exactly. The
per-policy attribution tables change one user's row assignment. The cold-start
coverage tables change a lot, and change toward what the protocol says should
happen: `synth_cold_routing_ok` becomes true on every learned run and the
fallback counts become 500/500/500/0.

**For ADR 0001's gate.** It removes an inconsistency rather than creating one. The
gate reads NDCG@10 on a holdout sliced at `COLD_START_THRESHOLD`; under this
option the models route on the same number the slicing uses, so a "cold" user in
the report is a user the model actually treated as cold. Today those two
sentences are not the same sentence. The gate's *verdict* on anything measured so
far does not change — see the companion memo; the movement here is far below the
+3% threshold and below any plausible noise floor.

**For the deployed serving path.** Nothing changes in `src/serving/`; it already
routes on the threshold. What changes is that the offline metrics start describing
the policy production runs. That is the argument, and it is the same argument the
feature-parity test (non-negotiable #2) exists for, applied to routing instead of
to features: the failure mode this project takes most seriously is offline and
online disagreeing about what the system does.

### (b) Amend ADR 0001 to say offline retrieval routes on index membership by design

**For the numbers.** Nothing at all — every published figure stands as measured.

**For ADR 0001's gate.** It becomes explicit that "cold" in the report means
"below five interactions" while "fallback-served" offline means "unseen by the
fit", and that a user can be one without the other. The gate itself is unaffected;
the reporting vocabulary gains a documented ambiguity instead of an undocumented
one.

**For the deployed serving path.** Also nothing — but the divergence is now
permanent and sanctioned, and every future offline number carries an asterisk
that says "the online system would have answered differently for users with 1–4
signals". ADR 0011's `expected_fallback_served` would have to be re-derived from
the offline rule, or `synth_cold_routing_ok = false` accepted as the expected
state, which costs the cohort the tripwire it was built to be.

### (c) Keep both, and require every reported run to name its policy

**For the numbers.** Nothing changes; the switch stays as shipped here, opt-in
and default-off, and `results.md` gains a policy column.

This is the status quo dressed as a decision, and it is worth stating only to be
rejected: two policies that both stay live means the question "what does this
system do for a 1-interaction user?" still has two answers, and the one thing the
measurements above establish is that those answers are far apart. A switch is a
good instrument and a bad policy.

## Recommendation

**Adopt (a): make ADR 0001's threshold the offline routing rule too, and delete
the divergence.**

The reasoning is that (a) is the only option that makes the offline metrics a
measurement of the deployed policy, and the objection that has stood against it —
that it would invalidate everything already measured — is now measured and false.
The cost is a fourth-decimal restatement of the warm/cold/overall and per-policy
rows in `results.md`, plus a real change to the ADR 0011 coverage table — which is
the table built to detect this and the one place a change is the point. The
benefit is that "is this user cold?" has one answer in the whole system.

Concretely, in this order:

1. **Take the decision, then land it as its own PR** — flip the default in
   `src/models/candidates/{cf,itemitem,twotower}.py` from `None` to
   `COLD_START_THRESHOLD`, keep `routing.py` and the `SYNTH_COLD_ROUTING` switch
   so the old policy stays *measurable*, and re-run the affected trainers —
   item-item, CF, the two-tower and the ranker, which inherits its candidate
   model's policy — so `results.md` gains a dated table under the new default. The
   switch was built opt-in precisely so that this flip is a one-line change with a
   before-and-after already on the page.
2. **Amend ADR 0001's one ambiguous sentence** — the exact text is below.
3. **Do not treat this as settling what the threshold should be.** Five is
   ADR 0001's number and this memo does not reopen it. The cohort now makes that
   a measurable question (run the buckets at thresholds of 3, 5 and 10 and read
   the h1/h3 rows), and it is a product question about how much signal is enough,
   not a consistency question. Worth its own unit if the answer matters.

**What would change this recommendation.** If the owner's view is that serving a
1-interaction user their one film's cosine neighbours is the *product* behaviour
wanted — a defensible position, and the popularity-weighted-target caveat means
the cohort does not refute it — then the right move is not (b) but a change to the
threshold itself, applied in both places. (b) should be chosen only if there is a
reason for the offline and online boundaries to genuinely differ, and no such
reason has surfaced.

## The exact ADR text change each option implies

All are edits to [ADR 0001](adr/0001-evaluation-protocol.md)'s **Cold-start
slicing** section, whose fourth bullet currently reads:

> Cold-start users fall back to the popularity baseline at serving time until
> they cross the threshold.

ADR 0001 stays **Accepted** in every case — this is a sentence that was always
meant to be unambiguous.

- **(a) — recommended** — replace with: *"Cold-start users fall back to the
  popularity baseline — offline and at serving time alike — until they cross the
  threshold. Every candidate model's fallback predicate reads the same
  `COLD_START_THRESHOLD` the warm/cold slicing uses, so a user reported as cold is
  a user the model treated as cold."* Add a Consequences bullet: *"A candidate
  model may not define its own cold-start boundary; `was_served_by_*` is derived
  from `COLD_START_THRESHOLD`."* [ADR 0011](adr/0011-cold-start-coverage.md)'s
  2026-08-29 addendum gains a dated line saying the finding it recorded is closed
  and how.
- **(b)** — replace with: *"Cold-start users fall back to the popularity baseline
  at serving time until they cross the threshold. Offline retrieval routes
  differently and by design: a user the fitted index has seen is served by the
  learned path whatever their history size, because one interaction is a real
  signal. The two boundaries therefore disagree for users with 1–4 interactions,
  and offline metrics for those users do not describe the deployed policy."* Add a
  Consequences bullet pointing at this page for the measured size of the
  disagreement, and amend ADR 0011 so `expected_fallback_served` derives from the
  offline rule — otherwise `synth_cold_routing_ok` is permanently false by design,
  which is a tripwire that has been disconnected rather than a check that passes.
- **(c)** — replace with: *"…until they cross the threshold. Offline models may
  route on either index membership or the threshold; every run records which
  under `cold_start_routing_policy`, and no figure may be reported without it."*
  Add the policy to `results.md`'s tables as a required column.

## The other ADR 0001 question this session left open

The same runs surfaced a second ambiguity in the same ADR, treated in full in
[`promotion-gate-slice-decision.md`](promotion-gate-slice-decision.md) and
summarised here so a reader of one memo has both answers.

ADR 0001 gates promotion on "≥ +3% relative NDCG@10 **on the holdout**" while also
requiring warm and cold to be "reported separately". Those produce one number and
three, and the ADR does not say which the gate reads. On the first full-dataset
comparison it decides the verdict: the LightGBM ranker against CF/ALS is **−4.16%
on warm NDCG@10, +15.39% on cold, +10.57% overall** — so the gate promotes or
rejects the same model depending on a sentence nobody has yet chosen the meaning
of. The aggregate clears only because 26.6% of the users carry 78.6% of the NDCG
mass; had the cold slice held flat, the overall figure would read −1.03%.

| Option | What it would say about the ranker today |
|---|---|
| (a) warm NDCG@10 | rejected (−4.16%) |
| (b) overall NDCG@10 — the status-quo reading | promoted (+10.57%) |
| (c) overall, plus a per-slice no-regression clause | rejected at any tolerance under 4.16%; promoted above it |
| (d) each slice independently at +3% | rejected (warm fails) |

**Recommended: (c)** — the aggregate stays the headline and no slice may regress
by more than a stated tolerance, with the tolerance set from a measured
seed-to-seed spread rather than chosen. Until that spread exists the gate stays
documented rather than automated, which is what an empty `pipelines/` already
means in practice. The companion memo carries the derivation, the mass
decomposition, the counterfactuals, and the exact ADR text for all four options.

## What this document does not claim

- **It does not claim the popularity fallback is better for a 1-interaction
  user.** The cohort's targets are popularity-weighted and every fallback-served
  bucket is flattered by that. What is established is that the two policies
  produce very different outcomes for those users, and that the holdout cannot
  tell you which.
- **It does not claim the holdout movement is zero.** It is 0.08% at most, on one
  user out of 2,641 — small enough that the fourth decimal is the only place it
  appears, and reported rather than rounded away.
- **It does not decide the value of the threshold.** Five is ADR 0001's number and
  stays it. Whether five is right is a separate, now-measurable question.
- **Nothing changed by default.** Every model still routes on index membership
  unless `SYNTH_COLD_ROUTING=threshold` is set. This memo is the evidence for a
  decision, not the decision.
