# Evaluation protocol and retrieval-gate contract

## Purpose

This contract prevents a good-looking metric from becoming promotion evidence when it answers a
different question, comes from an incomplete run set, or omits provenance needed to verify it.
The implementation is fail-closed: absence and incompatibility never degrade into warnings.

## Canonical semantic identity

`ProtocolManifest` schema version 1 records the complete evaluation question:

| Area | Fields |
|---|---|
| Data | raw revision, derived snapshot hash, event schema |
| Time | train cutoff, holdout start/end, backtest-window ID, timestamp unit, timezone |
| Labels/population | label contract, relevance, eligible-user policy, catalog fingerprint, unknown-item policy |
| Routing | cold threshold, learned routing, fallback policy |
| Filtering | history, seen-item, dismissal, target, and candidate policies |
| Features | feature contract and point-in-time semantics |
| Evaluation | stage, primary metric, metric contract, aggregation rule, K, and slice definition |

Serialization is canonical UTF-8 JSON with sorted keys and no NaN/Infinity. Its SHA-256 digest is
the semantic protocol hash. Every field participates; unknown or missing fields invalidate the
manifest. Run-specific identity—code SHA, environment, config, seed, hardware, dirty flag, and
timestamps—is stored separately because it is reproduction evidence rather than a different
evaluation question.

## Required MLflow envelope

Every new retrieval run intended for comparison must record:

- tag `evaluation_protocol`: the complete canonical JSON payload;
- tag `model_type`: one normalized model identity;
- parameter `evaluation_protocol_hash`: the recalculated `sha256:<digest>` value;
- parameter `evaluation_protocol_schema_version`: `1`;
- parameter `model_deterministic`: exactly `true` or `false`;
- parameter `train_seed`: required only for stochastic runs and forbidden for deterministic runs;
- metrics `warm_recall_at_k_candidates`, `cold_recall_at_k_candidates`,
  `overall_recall_at_k_candidates`, `n_warm_users`, and `n_cold_users`.

Counts must be finite non-negative integers, recalls must be finite values in `[0, 1]`, and the run
must be `FINISHED`. Legacy runs missing this envelope cannot be grandfathered into promotion. Re-run
them under the frozen protocol when a machine verdict is required.

## Retrieval decision

Invoke:

```bash
make gate-retrieval \
  CANDIDATE="<seed-42> <seed-7> <seed-13>" \
  INCUMBENT="<deterministic-item-item-run>" \
  RETRIEVAL_COLD_TOLERANCE=<measured-relative-fraction> \
  RETRIEVAL_OVERALL_TOLERANCE=<measured-relative-fraction>
```

The CLI intentionally has no tolerance defaults. Candidate runs must be one stochastic model at
exactly the seeds the caller states — by default 42, 7, and 13. The incumbent must be one seedless
`itemitem_cosine` run. Run IDs may not overlap, model identities may not be mixed, protocols and
slice populations must match, and all results must use retrieval recall@500.

After validation, the arithmetic mean across candidate seeds must satisfy:

- warm recall@500: at least +3% relative to item-item;
- cold recall@500: no worse than the supplied measured cold tolerance;
- overall recall@500: no worse than the supplied measured overall tolerance.

Boundaries are inclusive. An incumbent value of zero cannot support a relative claim and refuses
that clause. A retrieval pass still sets `serving_eligible=false`; the paired LightGBM NDCG@10,
artifact, and latency gates remain mandatory.

## The seed regime is the caller's stated policy

How many training runs a verdict rests on is an argument, not a constant. `RETRIEVAL_SEEDS`
(`--seeds`) names the exact seeds the candidate run set must contain; the gate holds the run set to
that list with no missing, extra, or repeated seeds, and reports a partial set as `incomplete`.

The default is the three-seed set, so nobody obtains a one-run verdict by inheriting a default. The
**2026-09-05 standing policy — one run per configuration until the ladder reaches the transformer
rungs** — is exercised by stating a single seed:

```bash
make gate-retrieval \
  CANDIDATE="<seed-42-run>" INCUMBENT="<deterministic-item-item-run>" \
  RETRIEVAL_SEEDS=42 \
  RETRIEVAL_COLD_TOLERANCE=<measured> RETRIEVAL_OVERALL_TOLERANCE=<measured>
```

Every decision carries two fields recording which regime produced it:

| Field | `single_seed` | `multi_seed` |
|---|---|---|
| `seed_regime` | one required seed | two or more |
| `uncertainty_basis` | states that the tolerances could only have covered evaluation-population sampling, that training stochasticity is unmeasured, and that the warm claim is a single draw rather than a mean | states that the tolerances cover across-seed dispersion and population sampling in quadrature |

They are derived from `required_seeds` rather than passed in, so a decision cannot record a regime
that disagrees with the seed set it was issued under. **What the single-seed regime gives up is
stated rather than implied: a model whose seeds genuinely disagree is not caught.** The paired
user-level bootstrap that replaces the seed term measures sampling noise over the evaluated
population — a different quantity, not a cheaper estimate of the same one. The warm `+3%` clause
never consumed a tolerance in either regime, so under one seed it is read off a single draw whose
spread is unknown in both directions: a genuinely better model can fail on an unlucky seed, and a
genuinely equal one can pass on a lucky one.

Nothing else varies with the seed count. Protocol identity and hash recalculation, population
equality, model identity, the deterministic-incumbent requirement, the four states, the `+3%`
threshold, and `serving_eligible=false` are identical for one seed and for three.

The policy is a pause, not a repeal. A caller who supplies three seeds gets the stronger decision
unchanged, and the intent is to return to it at the transformer rungs.

## Filtering policy vocabulary

The manifest's five filtering fields exist because a candidate set is defined as much by what was
removed as by what was retrieved. These are the values that describe the semantics implemented
today; a run that filters differently records a different value and is therefore not comparable,
which is the point.

| Field | Value | What it asserts |
|---|---|---|
| `positive_history_filter` | `strict-prior-equal-timestamp-excluded-v1` | Only events strictly before the query timestamp are visible. Interactions sharing a timestamp are excluded from one another's context. |
| `seen_item_filter` | `watched-strictly-prior-excluded-v1` | The user's already-watched titles as of that timestamp are removed from the candidate/negative pool. |
| `dismissal_filter` | `dismissals-absent-from-dataset-v1` | The offline dataset carries no dismissal events, so no dismissal exclusion was applied. |
| `target_filter` | `target-retained-never-negative-v1` | The evaluated item is kept as the positive and is never sampled as a negative for its own group. |
| `candidate_filter` | `unfiltered-retrieval-then-point-in-time-exclusions-v1` | Retrieval is asked for candidates unfiltered, and exclusions are applied afterwards against the point-in-time history. |

### The one asymmetry, stated rather than assumed

Serving applies `watched-and-dismissed-excluded-v1` (`src/serving/policy.py`): the caller's whole
never-show set suppresses output, and dismissals additionally remove a retrieval seed. Offline
training and evaluation apply the watched half of that rule and nothing else, because MovieLens
ratings contain no dismissal events to apply — a dismissal is a product signal the running system
collects, not a property of the dataset.

That gap is structural, not an oversight, and naming it is what keeps it honest. It has two
consequences the gate enforces rather than trusts:

- a future model trained or evaluated on a dataset that *does* carry dismissals records a different
  `dismissal_filter` value, so its results cannot be compared against these runs by accident;
- when the running product's dismissal events become training input, changing the value is the
  visible act that invalidates comparisons against everything measured before it.

What is *not* claimed: that the two filters produce identical candidate sets. They cannot, since one
of them has an input the other does not. The claim is only that the difference is recorded.

## Status and exit-code contract

| State | Meaning | Exit |
|---|---|---:|
| `promote` | Complete comparable evidence clears retrieval quality only | 0 |
| `refuse` | Complete comparable evidence fails one or more quality clauses | 1 |
| `not_comparable` | Protocol, stage, K, population, or value validity differs | 2 |
| `incomplete` | Runs, seeds, required metadata, or measured tolerances are absent | 2 |

Automation must branch on the state, not merely treat every nonzero exit as model failure. It must
also read `seed_regime`: a `promote` under `single_seed` and a `promote` under `multi_seed` are not
the same claim, and anything that records or forwards a verdict should carry the regime with it.

## Operator checklist

Before running the gate:

1. Confirm the candidate configuration was frozen before the seed set began.
2. Confirm the test partition was not read or used for selection.
3. Verify all run code/data/config identities and successful terminal states.
4. Derive and publish retrieval tolerances independently; do not tune them to the candidate result.
   Match the regimes: a `single_seed` verdict must be given tolerances from a one-run study
   (`seed_regime: single_seed` in the study report), because a three-seed study's tolerance is wider
   by a term the single-seed verdict has no right to. The gate takes bare floats and cannot check
   this — see the residual gaps in
   [`retrieval-tolerance-measurement.md`](retrieval-tolerance-measurement.md).
5. Run the retrieval gate and retain its JSON output with the experiment record.
6. If it passes, build the identical candidate sets for the paired LightGBM evaluation.
7. Do not move a registry alias or serving assignment until every downstream gate passes.
