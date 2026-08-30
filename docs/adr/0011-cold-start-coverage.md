# ADR 0011 — Cold-Start Coverage Methodology

**Status:** Accepted
**Date:** 2026-07-04
**Note:** 2026-08-30 — the threshold moved to 10, so the h10 bucket now sits exactly *on* the boundary and the divergence this cohort found is closed. See [the note at the bottom](#2026-08-30--the-threshold-is-10-so-h10-sits-on-the-boundary).

## Context

CLAUDE.md non-negotiable #3: *"Cold-start handling. Explicit answers for new users (no history) and new movies (no interactions). The synthetic cold-start harness (Phase 3) makes this measurable, not assumed."* Every candidate model in the codebase — `PopularityModel`, `CFModel`, `ItemItemModel`, `TwoTowerModel` — embeds a popularity fallback for users below the cold-start threshold ([ADR 0001](0001-evaluation-protocol.md) pins `COLD_START_THRESHOLD = 5`). The claim that fallback is used correctly, and that the learned path activates above threshold, is currently *asserted* in ADR text and *inferred* from the warm/cold slicing on MovieLens's natural distribution — but not *measured under controlled conditions*.

MovieLens's natural cold-user distribution is unusable for the specific claim we want to make. The 26.6% of holdout users below `COLD_START_THRESHOLD` are unevenly distributed across the interaction-count spectrum — most are 1–4 interactions with only a handful at exactly 0. That distribution is fine for the aggregate `cold` metric ADR 0001 defines, but it does not let us say things like "at exactly 0 interactions, the model uses popularity fallback" or "at exactly 10 interactions, the model uses its learned path" with statistical support. The per-bucket claim needs controlled buckets.

This ADR fixes the shape of that control. It answers:

- **What synthetic cold users are** (history sizes, item selection, target construction).
- **Where they live** (which tenant, how they're stored, how they're versioned).
- **What claims they prove** (falsifiable — not "the model does well on cold users" but specific statements about fallback activation, learned-model activation at the boundary, and monotonic behavior).
- **How they integrate** with ADR 0001's `EvalResult` and MLflow logging.

## Decision

Cold-start coverage is measured via a **fixed-seed synthetic cohort of 2 000 users** with the following shape:

- **History-size buckets: {0, 1, 3, 10}.** 500 users per bucket, 2 000 total. Buckets are chosen (per CLAUDE.md) to span the cold-start boundary: 0/1/3 sit below ADR 0001's threshold of 5 (fallback territory); 10 sits just above (learned-model territory at the boundary). *(The threshold became 10 on 2026-08-30, so h10 now sits exactly **on** the boundary rather than just above it — the buckets and expected counts are unchanged. See the note at the bottom of this file.)* Statistical power at n=500 per bucket is sufficient to detect a ~5% recall difference between buckets at α = 0.05.
- **Item selection: popularity-weighted (Zipfian) over the train catalog.** Each synthetic user's history is drawn without replacement from the item catalog with sampling probability proportional to each item's train interaction count. This matches the empirical pattern where new users first interact with popular items — a uniform-random alternative was rejected as unrealistic (see Alternatives), and a genre-clustered alternative is deferred to the persona harness (`synthetic/personas/`) which serves a different job.
- **Target: single held-out next-item per user, from the same popularity-weighted distribution, excluded from that user's history.** Recall@K = "did the model return this one item in top-K." Clean per-user pass/fail signal; aggregates to per-bucket recall directly.
- **Dedicated `synth_cold` tenant.** Realm-per-tenant (ADR 0007) provisions the tenant; RLS (ADR 0008) scopes the users' rows. Isolated from `demo` (portfolio walkthroughs) and the default MovieLens tenant. Synthetic users are tagged `synthetic=true` on the users table for filtering by any downstream analysis.
- **Timestamps: all synthetic history rows have `timestamp = split.cutoff - 86 400` (24 hours before the temporal split cutoff).** Uniform timestamps mean all synthetic users have the same "age" at eval time — the variable of interest is history size, not history recency. Timestamps sit inside train so the candidate/ranker models see them at fit time.
- **Reproducibility: fixed seed `SYNTH_COLD_SEED = 42`, DVC-tracked parquet at `data/synthetic/cold_start/v1/users.parquet`.** Generation is deterministic given seed + `pyproject.toml`'s Feast entity schema + the MovieLens data version. Regeneration is a Prefect task in the retraining flow; the parquet is the DVC-tracked source of truth so cold-start metrics across runs are comparable.
- **Eval harness extension.** [`src/evaluation/protocol.py`](../../src/evaluation/protocol.py)'s `EvalResult` gains a `synthetic_cold_slices: dict[int, MetricPair]` field, keyed by history size (0, 1, 3, 10). The existing `evaluate()` function accepts an optional `synthetic_cold_users: dict[int, dict[user_id, target_set]]` param and populates the field.
- **MLflow logging: one metric per bucket per stage.** `synth_cold_recall_at_k_candidates_h0`, `..._h1`, `..._h3`, `..._h10` for the candidate stage; `synth_cold_recall_at_k_h0` etc. for the ranker end-to-end. Fallback attribution is logged per bucket too: `synth_cold_fallback_served_h0` counts how many of the 500 users at bucket `h0` went through the popularity fallback path (should be 500 at `h0`, 500 at `h1`, 500 at `h3`, 0 at `h10` for a correctly-configured model).

## Rationale

1. **Buckets 0/1/3/10 span the threshold on purpose.** The specific value 10 is not "well above cold-start" — it is 10 interactions, one above the borderline (`COLD_START_THRESHOLD = 5`) at which the routing switches. Testing at 10 rather than at 20 or 50 is deliberate: the interesting claim is that routing switches correctly *at the boundary*, and the interesting failure mode is that a model with a bug in `was_served_by_*` misclassifies a 10-interaction user as cold or a 4-interaction user as warm. If we tested at 50, a routing bug at the 5-interaction boundary would still pass the eval — we'd never see it.

2. **Popularity-weighted item selection is what real cold users actually do.** Cold-start research (Herlocker et al., 2004; Schein et al., 2002; the industry lit generally) consistently shows that users' first-few interactions concentrate on popular items — recommendation systems surface popular items to unknown-preference users, and users click on what they're shown. A uniform-random synthetic cold user is testing a distribution that doesn't exist in production; the model that "does well" on that distribution is the model that surfaces the tail, which is not what the popularity fallback is supposed to do. The Zipfian sampling matches production reality; the popularity-fallback "winning by construction" on the fallback buckets is not a bug — it's the fallback correctly recommending what the user is most likely to want.

3. **Single-target simplifies the recall statistic and makes per-user pass/fail crisp.** With one target per user, recall@K is either 1.0 or 0.0 for that user; per-bucket recall is the mean, which is directly interpretable as "fraction of users where the target appeared in top-K." A 5-item target set would introduce partial-credit noise (a user with 2/5 targets is a different signal than a user with 5/5) without a corresponding sharpening of the claim. The trade is per-user signal density (weaker per user) for per-bucket signal cleanliness (stronger per bucket) — and per-bucket is what this ADR is scoping.

4. **500 users per bucket is where statistical power meets eval-run cost.** With a target rate of ~0.15 (a rough recall@500 estimate on the warm slice, per PR #19's item-item numbers), the standard error at n = 500 is ≈ 0.016, so a 5% recall difference between buckets is detectable at α = 0.05. Doubling to n = 1 000 would tighten the interval to ~0.011 — buys another decimal of precision for another 15% of eval-run wall time. 100 users would loosen to ~0.036, which is enough to miss real regressions between buckets. 500 is the honest middle.

5. **Dedicated `synth_cold` tenant, not interleaved with the default tenant.** ADR 0008's RLS scopes queries by `tenant_id`; interleaving synthetic users with real MovieLens users in the default tenant would either (a) leak synthetic users into every real analysis (per-tenant metrics would show inflated user counts) or (b) require every query to add `WHERE synthetic = false`, which is exactly the class of bug ADR 0008 was written to prevent. A dedicated tenant costs one Keycloak realm entry and one row in `public.tenants` — a rounding error against the isolation clarity it buys.

6. **Uniform timestamps isolate history size as the variable of interest.** A synthetic user with 10 interactions spread over 5 years and a synthetic user with 10 interactions in the last week are two different users — the ranker's `days_since_last_interaction` feature reads them differently. Fixing all history rows at `cutoff - 24h` means the recency feature is constant across the cohort; the only thing varying is history *size*. That's what the coverage test is measuring; any other axis of variation would confound the claim.

## Alternatives considered

- **Genre-clustered / persona-based item selection.** Each synthetic user assigned a genre affinity; history and target drawn from within that genre. Rejected as the primary shape *for this ADR* because it tests genre-affinity features (a legitimate but different question) rather than cold-start routing (this ADR's question). The persona-based shape is not thrown away — the demo persona harness in `synthetic/personas/` uses exactly this construction for portfolio walkthroughs, where the genre affinity is the point. Splitting the jobs cleanly (coverage → popularity-weighted; personas → genre-clustered) keeps each harness's claim sharp.

- **Uniform-random item selection.** The simplest generation code. Rejected on rationale #2: it tests a distribution that does not exist in production. A model that scored well on uniform-random cold users would be a model that surfaces the tail — the opposite of what the popularity fallback is designed to do.

- **Top-K target set (5 held-out items).** Denser per-user signal, mirrors warm-user holdout shape. Rejected on rationale #3: partial-credit noise weakens the per-bucket signal without sharpening any specific claim this ADR is scoping. The warm-user holdout can afford partial credit because it has thousands of users per slice — the synthetic cold cohort at n = 500 per bucket cannot.

- **1 000 users per bucket.** Rigorous, tight confidence intervals (~0.011 standard error). Rejected on rationale #4: the marginal precision doesn't buy a different decision. If cold-start coverage becomes a promotion-gate metric in a future ADR, revisit — for the current claim ("does routing switch correctly at the boundary"), 500 is enough.

- **100 users per bucket.** Fast, cheap. Rejected on rationale #4: standard error too loose to detect a 5% difference between buckets, which is the granularity at which routing bugs and threshold misconfigurations show up.

- **Interleaved with real users in the default tenant.** Rejected on rationale #5: leaks or requires application-level filtering, both of which fight ADR 0008's isolation model.

- **No synthetic users, use MovieLens's natural cold tail.** The status quo before this ADR. Rejected because MovieLens's natural distribution does not give per-bucket support — most cold users cluster around 1–3 interactions, few at exactly 0 or exactly 10. The aggregate cold metric in ADR 0001 stays; this ADR adds a *complementary* per-bucket view that MovieLens can't provide on its own.

- **Time-varying history (interactions spread across a window).** Rejected on rationale #6: introduces recency as a confounder. Would be the right shape for a Phase 5 *drift* harness, where time-varying user state is the point — but that's a different job, spelled `synthetic/drift/`.

## Consequences

- **Repo layout.** `synthetic/cold_start/` gets a `generator.py` (deterministic given seed + config), a `config.py` (`SYNTH_COLD_SEED = 42`, bucket sizes, timestamp offset), and a small CLI (`python -m synthetic.cold_start.generator --out data/synthetic/cold_start/v1/users.parquet`).
- **DVC.** The generated parquet is DVC-tracked. Regeneration produces a bit-identical file given fixed seed + input data version — otherwise, non-negotiable #5 (reproducibility) is violated.
- **Prefect DAG.** The retraining flow gains a `generate_synth_cold_cohort` task that runs before evaluation. Runs are idempotent — if the parquet exists and the DVC hash matches, the task is a no-op.
- **Eval harness.** [`src/evaluation/protocol.py`](../../src/evaluation/protocol.py)'s `EvalResult` gains `synthetic_cold_slices: dict[int, MetricPair]`. `evaluate()` gains a `synthetic_cold_users` param. Existing call sites (item-item, two-tower, LightGBM ranker training scripts) pass the loaded cohort; if omitted, the field defaults to `{}` and no metrics are logged — no test breakage.
- **MLflow.** Per-bucket recall, ndcg, and fallback-served counts logged as metrics per run. Grafana dashboards (Phase 5) gain a "cold-start bucket panel" that charts recall_h0 / recall_h1 / recall_h3 / recall_h10 over model versions.
- **Keycloak / tenant provisioning.** `synth_cold` tenant added as a seeded realm JSON under `infra/keycloak/realms/` (alongside `default-realm.json` and `demo-realm.json`) so ADR 0007's Keycloak instance has the tenant on first boot. Users don't need Keycloak identities — they're used only for offline evaluation, not for authenticated API requests. Their `synth_cold` tenant tag is the RLS-required isolation, not a login credential.
- **Two-tower / item-item / ranker integration.** Each candidate model already exposes `was_served_by_*(user_id)`; the eval harness calls the predicate per synthetic user and asserts the expected routing (buckets 0/1/3 → False, bucket 10 → True). A count-mismatch is logged as an MLflow tag `synth_cold_routing_ok = false` for surfacing in the run browser.
- **Deferred.** Cold-*item* coverage (new movies with no interactions) is not addressed here — the same methodology extends but item catalogs are less variable than user histories, so this ADR scopes to cold users. Cold-item is a Phase 4 addition once the item tower gets side features (per ADR 0006's deferred work).

## Risks

- **Popularity fallback wins by construction on fallback buckets.** Because targets are popularity-weighted, the popularity fallback — which recommends the most popular items — will hit synthetic-cold-user targets at exactly the rate popularity intersects popularity. This is not a bug: the ADR is testing that fallback *activates* at buckets 0/1/3, not that the fallback beats the learned model on those buckets. Risk: a reader interprets "high recall on bucket 0" as "the model does great on cold users" when it actually means "the popularity fallback covered popular targets, as designed." Mitigation: MLflow metric names include `fallback_served` counts per bucket, and the ADR text (this document) is the reference. If misreading persists, add a `synth_cold_learned_recall_h*` metric that only counts users whose `was_served_by_*` returned True.
- **The single-target statistic is high-variance per user.** With one target, a single user's recall is 0 or 1; the per-bucket mean is a Bernoulli-average and its variance is `p(1-p)/n`. At n = 500 and p = 0.15, variance is ~0.00026, standard error ~0.016 (rationale #4). Acceptable but not tight — if we ever need tighter, target set size is the lever to pull, not cohort size (per rationale #3).
- **Synthetic distribution ≠ real cold-user distribution.** The synthetic cohort proves things about controlled buckets; it does not prove that the real production cold users (whatever those look like on this system) behave the same. Mitigation: ADR 0001's aggregate `cold` metric on MovieLens's natural distribution stays as the "does this generalize to reality" signal. The two metrics answer different questions and neither substitutes for the other.
- **Generator determinism depends on the item catalog version.** If MovieLens's item catalog changes between generation and evaluation, the target items in the parquet may not exist in the model's item vocabulary. Mitigation: the generator writes the MovieLens data version into the parquet metadata; the eval harness asserts a match at load time and fails loud on mismatch.
- **Bucket sizes may be inadequate as coverage becomes a promotion gate.** If we ever gate promotion on "cold recall at bucket 10 doesn't regress by > 2%," n = 500 is not enough (SE ≈ 0.016 covers a 2% shift with wide overlap). Mitigation: promotion-gate use case would trigger a follow-up ADR bumping cohort size or introducing a paired-sample statistical test. Not blocking now.
- **The 24-hour timestamp offset assumes the temporal-split cutoff is stable.** ADR 0001's `T = percentile_disc(0.8)` shifts as new data arrives (Phase 4 retraining introduces new rows). The synthetic cohort's timestamps are anchored to the current cutoff; a regenerate is required whenever the split shifts. Mitigation: the Prefect task regenerates on every retraining flow, which is exactly when the cutoff shifts.

## How we'd know we're wrong

- **The `was_served_by_*` routing assertion fails frequently.** Would mean the threshold-based routing has a bug (or, less likely, a bug in `was_served_by_*` itself). The correctness of the ADR is unaffected — that's the ADR working as intended. But if the failure signal is *ignored* (people mute the alert), the ADR's teeth are gone.
- **Cold recall on the synthetic slice diverges from cold recall on MovieLens's natural slice.** Would suggest the popularity-weighted distribution is a bad proxy for real cold users on this dataset. Fix by comparing to a genre-clustered variant (or a hybrid); the synthetic distribution is the parameter, not the load-bearing decision. The buckets and cohort size stay.
- **Regeneration is not deterministic.** Would mean seed + dataset version does not uniquely determine the parquet — some transitive dependency (numpy RNG API changes, a hash function's Python version) leaked in. Fix by pinning the RNG version explicitly and asserting `parquet_hash == expected_hash` in CI.
- **Standard-error at n = 500 turns out too loose for the claim we actually want to make.** Would mean either the cohort size needs to grow, or the target set size needs to grow. Prefer growing the target set (per rationale #3) — 500 users with 5 targets each gives 2 500 (user, target) pairs at similar wall-cost, and the variance calculation improves.
- **Cold-item coverage becomes the primary gap.** Would signal that this ADR's scope was too narrow — cold users are only half the cold-start problem. Follow-up ADR extends the same methodology to items.

---

## 2026-08-29 — implementation note: what the harness settled, and what it found

Written when `synthetic/cold_start/` landed. Status stays **Accepted**; nothing
above is retracted. This records the choices the ADR left open, one deliberate
deviation from its Consequences, one deferral, and one finding — the first
thing the cohort measured turned out to falsify a claim this ADR was written to
check, which is the harness working rather than the harness failing.

### The target is the *first* draw, not the last

The ADR says each user's history and target come from the same
popularity-weighted draw, with the target excluded from the history. It does
not say in which order. That turns out to matter. The generator draws
`history_size + 1` distinct items in one weighted sample-without-replacement
pass and takes **the first as the target**, the rest as history.

The reason is comparability across buckets, which is the entire point of having
buckets. In a weighted sample without replacement, the first element has exactly
the marginal distribution of a single popularity-weighted draw, regardless of
how many items follow it. Taking the *last* element instead would make bucket
10's target the eleventh-ranked draw and bucket 0's the first — a systematically
less popular, therefore harder, target in the deeper buckets. Per-bucket recall
would then differ partly because of target difficulty rather than history size,
which is the same class of confound that rationale #6 fixes timestamps to avoid.
`test_the_target_distribution_does_not_drift_across_buckets` holds the property.

### No Keycloak realm for `synth_cold` — a deviation, recorded

The Consequences section says the tenant arrives as a seeded realm JSON under
`infra/keycloak/realms/`, alongside `default-realm.json` and `demo-realm.json`.
It does not. Migration `0015_synth_cold_tenant` registers the row in
`public.tenants` and stops there.

The ADR's own sentence is the argument against its own conclusion: *"Users don't
need Keycloak identities — they're used only for offline evaluation, not for
authenticated API requests."* A realm nobody can log into is an idle
authentication surface, and it is not free — the realm-drift CI job enumerates
every realm and compares it to what the stack imports, so a third realm becomes
a permanent extra row in an inventory that exists to catch drift. The tenant tag
is the RLS isolation ADR 0008 asks for and it does that job without a realm. If
a future phase ever needs a synthetic cold user to make an authenticated
request, the realm is one file and this note is why it wasn't there.

### The Prefect regeneration task is Phase 4 work

Consequences names a `generate_synth_cold_cohort` task in the retraining flow.
`pipelines/` is empty until Phase 4 — Prefect DAGs are that phase's scope — so
the regeneration is a Make target (`make synth-cold-cohort`) and the
idempotency the ADR asks of the task is instead a property of the generator: it
is deterministic, so a regeneration on an unchanged dataset rewrites a
byte-identical file and `dvc status` stays clean. When the Prefect flows land,
the task wraps that target; the "no-op if the hash matches" behaviour is
already there.

### `MetricPair` is `UserMetrics`, wrapped

The ADR names the new field `synthetic_cold_slices: dict[int, MetricPair]`.
There is no `MetricPair` in the codebase; the pair of recall and NDCG has been
`UserMetrics` since Phase 1. The field is
`dict[int, SyntheticColdSlice]`, where a slice carries the `UserMetrics` plus
the two counts fallback attribution needs — `n_users` and `n_fallback_served`.
The latter is `int | None`, not an `int` defaulting to zero, because "no routing
predicate was supplied" and "the predicate said zero users fell back" are
different claims and a coverage harness may not blur them. The popularity
baseline is the case that makes this concrete: it *is* the fallback, has no
learned path to route to, and so logs per-bucket recall with no routing tag at
all.

### The finding: the offline models' fallback boundary is not ADR 0001's threshold

This is what the cohort was built to detect, and it fired on first use.

ADR 0001 says *"Cold-start users fall back to the popularity baseline at serving
time until they cross the threshold"*, and the deployed online path does exactly
that — `src/serving/recommendations.py` routes on unique watched titles against
`COLD_START_THRESHOLD = 5`. The four **offline** candidate models do not. Their
`was_served_by_*` predicates all reduce to *"is this user in the fitted user
index at all?"* — `_knn is not None and user_id in self._user_to_index` for
item-item, and the same shape for CF/ALS and the two-tower. A user with a single
interaction is in that index, so the learned path serves them.

Measured, on the full 25M dataset, with item-item at `K_CANDIDATES = 500`:

| bucket | fallback-served | expected | recall@500 | ndcg@500 |
|---|---|---|---|---|
| h0 | 500 | 500 | 0.4760 | 0.0823 |
| h1 | 0 | 500 | 0.1440 | 0.0264 |
| h3 | 0 | 500 | 0.2880 | 0.0470 |
| h10 | 0 | 0 | 0.3900 | 0.0619 |

`synth_cold_routing_ok = false`. Buckets 1 and 3 are the disagreement: users the
evaluation protocol calls cold, served by the learned path.

The recall column says something the fallback counts alone do not. **A
1-interaction user scores 0.1440 where the same user routed to the fallback would
have scored something near h0's 0.4760** — the two buckets differ only in
history size and routing, since the target distribution is identical across
buckets by construction (see the target-ordering note above). Serving one watched
film's cosine neighbours to a user is, on this cohort, roughly three times worse
than serving them the popular head. h3 recovers to 0.2880 and h10 to 0.3900, a
monotone climb back toward the fallback but still short of it at ten
interactions.

Read that with the caveat this ADR's own Risks section insists on: the targets
are popularity-weighted, so the popularity fallback hits them at the rate
popularity intersects popularity, and h0 is flattered by construction. The
comparison that *is* clean is h1 against h0, because those two buckets are
identical in everything except how many items the user had and which path served
them. It is evidence for a direction, not a verdict — but it is the first
evidence there has ever been, which is the point of building the cohort.

Nothing is being quietly repaired here. `expected_fallback_served` derives the
contract from `COLD_START_THRESHOLD` rather than from whatever a model happens
to do, so the mismatch stays visible as a number instead of being defined away —
and the per-bucket counts, the expectation, and the tag all go to MLflow, so the
run says what was measured next to what should have been. Changing four models'
routing would move every warm/cold and per-policy metric already logged in
`phase-1-baselines` and `phase-2-candidates`, which is its own unit of work with
its own before-and-after, not a side effect of the PR that first measured the
gap. It is on the Phase 3 platform-track backlog.

It is worth being precise about which of the two behaviours is wrong, because
that is not yet settled. Serving 1-interaction users from item-item is not
obviously worse than serving them popularity — one interaction is a real signal
and the cosine neighbours of one watched film are a defensible recommendation.
The defect is that two parts of the same system answer "is this user cold?"
differently while ADR 0001 states one answer, so the offline metrics are not
measuring the policy production runs. Either the models adopt the threshold or
ADR 0001 is amended to say that offline retrieval routes on index membership and
only the serving path applies the threshold. That decision needs a run of both
to compare, which is precisely what this cohort now makes cheap.

### The popularity baseline is the control, and it comes out flat

`src/training/popularity.py` logs the same buckets with **no routing predicate**
— `synth_cold_fallback_served_h*` reads `unmeasured` and no `synth_cold_routing_ok`
tag is set. That is not an omission: `PopularityModel` *is* the fallback, has no
learned path to route to, and a fallback count for it would be a tautology.

What it does buy is a control. On the same cohort at `K = 10` it scores h0
0.0340, h1 0.0420, h3 0.0160, h10 0.0240 — non-monotone, and inside roughly
±0.008 of each other at the ±1 standard error a Bernoulli mean at n = 500 and
p ≈ 0.03 carries. A single unpersonalized policy serving every bucket produces
no trend in history size, which is what it should: the only thing that varies
across buckets for it is how many of a user's own items the seen-filter removes
from a ten-item window.

That matters for reading the item-item table above. The buckets are not
intrinsically unequal in difficulty — if they were, this run would slope too.
So the 0.4760 → 0.1440 → 0.2880 → 0.3900 profile is a property of retrieval and
routing rather than of the cohort's construction. (The two runs are at different
K and their *levels* are not comparable; the claim here is only about the
presence or absence of a slope within each run.)

### Ranker end-to-end coverage is included

`synth_cold_recall_at_k_h*` comes out of `src/training/ranker.py` as well as
the candidate trainers — the cohort's users are ranked through the same
candidate → features → LightGBM path as holdout users, at `as_of == split.cutoff`,
which their `cutoff - 86400` history rows sit strictly before. The ranker's
routing predicate is its candidate model's, because a ranker only reorders what
retrieval handed it. Ranker *training* positives are still sampled from the real
train slice only: the cohort's rows fall inside the trailing sampling window and
would otherwise be eligible, and a synthetic user is here to be scored, not to
teach LambdaRank what a group looks like.

### What the first real generation produced

Against MovieLens 25M at `data_version c3ce6309f6f0ec347a9e0a662c640021.dir`,
split cutoff `1466837397` (train 20 000 075 rows), seed 42: 9 000 rows, 2 000
users, 7 000 history rows, 1 313 distinct target titles, cohort fingerprint
`ae4475f0e063dd4b430092100491838737ee03c8554e68b78cc551efa2e6cfe2`. Regenerating
it produced a byte-identical parquet. The 7 000 attached rows are 0.035% of
train and no cohort user appears in holdout, so the warm/cold metrics every
earlier run reported are unchanged.

## 2026-08-30 — the threshold is 10, so h10 sits *on* the boundary

[ADR 0001's amendment](0001-evaluation-protocol.md#amendment-2026-08-30--the-cold-start-threshold-is-10-online-and-offline)
moved `COLD_START_THRESHOLD` to 10 and made it the offline candidate models'
routing rule as well as the deployed one. The divergence the section above
reported is therefore closed rather than merely documented, and this cohort was
the instrument that made closing it a decision instead of a preference.

**The buckets do not change, and neither do the expected counts.**
`HISTORY_BUCKETS` stays `{0, 1, 3, 10}` and
`expected_fallback_served` stays **500 / 500 / 500 / 0**, because
`synthetic/cold_start/harness.py` derives it from the constant
(`bucket.n_users if bucket.history_size < COLD_START_THRESHOLD else 0`) rather
than restating a number. At a threshold of 10, `10 < 10` is false, so h10 is
still the one bucket the learned path is expected to serve.

What changes is how sharp that last bucket is. Rationale #4 above chose 10 over
20 or 50 precisely because "the interesting claim is that routing switches
correctly *at the boundary*" — and h10 now sits **exactly on** the boundary
rather than one step past it. A model whose fallback fires at 11 instead of 10,
or an off-by-one in `>=` versus `>`, shows up here as `synth_cold_routing_ok =
false` where before the bucket had a step of slack to absorb it. That is a
strictly stronger test of the thing the bucket exists for, and it arrived
without touching the cohort: the parquet, the seed, and fingerprint
`ae4475f0e063…` are all unchanged, so every per-bucket recall number above is
still directly comparable.

**Expected effect on the numbers.** Under the new default, the fallback counts
become 500/500/500/0 on every learned candidate run and `synth_cold_routing_ok`
becomes true — the state this cohort was built to be able to assert. The
per-bucket recalls for h1 and h3 rise to the popularity control's, since those
buckets are now fallback-served; h0 and h10 are unmoved. The routing memo
recorded exactly this, run both ways: item-item h1 0.1440 → 0.4600 and h3
0.2880 → 0.4560 (`docs/cold-start-routing-decision.md`).

**The Risks-section caveat still binds, and binds harder now.** The targets are
popularity-weighted, so every fallback-served bucket is flattered by
construction — and after this change three of the four buckets are
fallback-served. `synth_cold_routing_ok` remains a *routing* tripwire, not
evidence that the fallback is the better product answer below the boundary; the
recall column on h1 and h3 should be read as "the popularity control's number,
reproduced" rather than as a model improvement.
