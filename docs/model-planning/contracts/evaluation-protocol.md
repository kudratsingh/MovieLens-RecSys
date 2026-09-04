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
exactly seeds 42, 7, and 13. The incumbent must be one seedless `itemitem_cosine` run. Run IDs may
not overlap, model identities may not be mixed, protocols and slice populations must match, and all
results must use retrieval recall@500.

After validation, the arithmetic mean across candidate seeds must satisfy:

- warm recall@500: at least +3% relative to item-item;
- cold recall@500: no worse than the supplied measured cold tolerance;
- overall recall@500: no worse than the supplied measured overall tolerance.

Boundaries are inclusive. An incumbent value of zero cannot support a relative claim and refuses
that clause. A retrieval pass still sets `serving_eligible=false`; the paired LightGBM NDCG@10,
artifact, and latency gates remain mandatory.

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

Automation must branch on the state, not merely treat every nonzero exit as model failure.

## Operator checklist

Before running the gate:

1. Confirm the candidate configuration was frozen before the seed set began.
2. Confirm the test partition was not read or used for selection.
3. Verify all run code/data/config identities and successful terminal states.
4. Derive and publish retrieval tolerances independently; do not tune them to the candidate result.
5. Run the retrieval gate and retain its JSON output with the experiment record.
6. If it passes, build the identical candidate sets for the paired LightGBM evaluation.
7. Do not move a registry alias or serving assignment until every downstream gate passes.
