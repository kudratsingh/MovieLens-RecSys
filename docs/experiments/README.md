# Experiment evidence

The committed inputs and outputs behind the offline numbers: the run grids each
sweep was launched from, the gate verdicts as the gate emitted them, and the
benchmark records. The numbers themselves are read and argued in
[`docs/results.md`](../results.md) and in the ADRs; this directory is what those
sections point back to. Nothing here is hand-edited after the run that wrote it.

Two kinds of file live here. **Grids** (`_comment` plus `cells`) are run
specifications a trainer's sweep runner reads; several are loaded by the unit
suite, so they have to stay parseable by the code that consumed them. **Records**
are what a run, a gate or a benchmark produced. Section links below point at
`docs/results.md` as of 2026-10-05.

## `sasrec/`

| File | What it is | Backs |
|---|---|---|
| [`pilot.json`](sasrec/pilot.json) | Grid: BCE against gBCE on a deterministic 0.5% user sample. **Read by `tests/unit/test_sasrec_sweep.py`.** | [SASRec bounded loss pilot](../results.md#sasrec-bounded-loss-pilot--2026-09-04); [ADR 0016](../adr/0016-sasrec-sequential-retrieval.md) |
| [`pilot-6pct.json`](sasrec/pilot-6pct.json) | Grid: the same two arms on the established 6% sample, which chose BCE. **Read by `tests/unit/test_sasrec_sweep.py`.** | [SASRec bounded loss pilot](../results.md#sasrec-bounded-loss-pilot--2026-09-04); ADR 0016's pilot outcome |
| [`full.json`](sasrec/full.json) | Grid: the frozen full-data cell (BCE, 32 negatives, two epochs, seed 42, exact FAISS). **Read by `tests/unit/test_sasrec_sweep.py`.** | [SASRec first full-data run](../results.md#sasrec-first-full-data-run--2026-09-04); ADR 0016 |
| [`full-artifact-run-2026-09-04.md`](sasrec/full-artifact-run-2026-09-04.md) | Record: the artifact-backed full-data run `a11af5ed…`, its lineage, metrics, artifact checksums and recovered gate evidence. | [SASRec artifact-backed full-data reproduction](../results.md#sasrec-artifact-backed-full-data-reproduction--2026-09-04) |
| [`single-run-retrieval-verdict-2026-09-05.json`](sasrec/single-run-retrieval-verdict-2026-09-05.json) | Record: `make gate-retrieval` output for that run against item-item, with the population bootstrap band. | [SASRec artifact-backed full-data reproduction](../results.md#sasrec-artifact-backed-full-data-reproduction--2026-09-04); ADR 0016's retrieval-quality verdict |
| [`encoder-latency-2026-09-05.json`](sasrec/encoder-latency-2026-09-05.json) | Record: the isolated encoder benchmark on the host (macOS arm64), p99 0.285 ms. | [SASRec artifact-backed full-data reproduction](../results.md#sasrec-artifact-backed-full-data-reproduction--2026-09-04); ADR 0016's isolated latency verdict |
| [`encoder-latency-amd64-2026-09-05.json`](sasrec/encoder-latency-amd64-2026-09-05.json) | Record: the same benchmark inside the `linux/amd64` sidecar image under emulation, p99 1.0047 ms (PR #165). | [SASRec artifact-backed full-data reproduction](../results.md#sasrec-artifact-backed-full-data-reproduction--2026-09-04) |
| [`paired-ranker-guardrail-2026-09-05.json`](sasrec/paired-ranker-guardrail-2026-09-05.json) | Record: the fixed-ranker D-002 check — the incumbent LightGBM unchanged, only the candidate source swapped to SASRec. | [Fixed-ranker D-002 guardrail](../results.md#fixed-ranker-d-002-guardrail); ADR 0016 |
| [`ranker-on-sasrec-candidates-2026-09-05.json`](sasrec/ranker-on-sasrec-candidates-2026-09-05.json) | Record: `make gate` refusing a LightGBM retrained on SASRec candidates (cold −53.11%). | [The ranker retrained on SASRec candidates](../results.md#the-ranker-retrained-on-sasrec-candidates--2026-09-05); ADR 0016's paired-ranker outcome |
| [`ranker-per-route-bundle-2026-09-05.json`](sasrec/ranker-per-route-bundle-2026-09-05.json) | Record: `make gate` promoting the per-route bundle, overall NDCG@10 0.2008 → 0.2146 (pre-fast-path-fix). | [Both repairs work](../results.md#both-repairs-work-and-both-were-predicted-before-they-were-run); ADR 0016 |
| [`ranker-union-booster-2026-09-05.json`](sasrec/ranker-union-booster-2026-09-05.json) | Record: `make gate` promoting the union booster trained on both arms' rows. | [Both repairs work](../results.md#both-repairs-work-and-both-were-predicted-before-they-were-run); ADR 0016 |
| [`fastpath-reevaluation-2026-09-05.json`](sasrec/fastpath-reevaluation-2026-09-05.json) | Record: before and after the evaluation fast-path fix — retrieval 0.4652 → 0.5092 warm recall@500, the per-route bundle 0.2146 → 0.2218 overall NDCG@10. | ADR 0016's evaluation fast-path correction |
| [`ranker-sasrec-score-features-2026-09-05.json`](sasrec/ranker-sasrec-score-features-2026-09-05.json) | Record: Rung 3 increment 1 before the fast-path fix (superseded). | [The ranker given the SASRec score](../results.md#the-ranker-given-the-sasrec-score--2026-09-05-adr-0018-rung-3-increment-1); [ADR 0018](../adr/0018-sequence-aware-ranking.md) |
| [`ranker-sasrec-score-features-postO9-2026-09-05.json`](sasrec/ranker-sasrec-score-features-postO9-2026-09-05.json) | Record: Rung 3 increment 1 re-measured after the fix, with the eight-column control; both gate scopes refuse. | [The same question, asked again after O-9](../results.md#the-same-question-asked-again-after-o-9--2026-09-05); ADR 0018 |
| [`popularity-order-2026-09-05.json`](sasrec/popularity-order-2026-09-05.json) | Record: provenance for the `popularity-order.json` fill artifact shipped inside the SASRec bundle (PR #173), kept outside the hashed bytes on purpose. | PR #173; [ADR 0016](../adr/0016-sasrec-sequential-retrieval.md)'s serving contract |
| [`all-positions-pilot-6pct.json`](sasrec/all-positions-pilot-6pct.json) | Grid: the all-positions training objective on the 6% sample. **Read by `tests/unit/test_sasrec_sweep.py`.** | [SASRec canonical all-position training](../results.md#sasrec-canonical-all-position-training--2026-09-1011); [ADR 0020](../adr/0020-sasrec-v2.md) cell 0 |
| [`all-positions-full.json`](sasrec/all-positions-full.json) | Grid: the same objective at full scale. **Read by `tests/unit/test_sasrec_sweep.py`.** | [SASRec canonical all-position training](../results.md#sasrec-canonical-all-position-training--2026-09-1011); ADR 0020 cell 0 |
| [`all-positions-training-2026-09-11.json`](sasrec/all-positions-training-2026-09-11.json) | Record: both all-positions runs against the copied-prefix v1 — 9.8× faster, −4.62% warm recall@500 at full scale (PR #183). | [SASRec canonical all-position training](../results.md#sasrec-canonical-all-position-training--2026-09-1011); ADR 0020's cell 0 outcome |

## `twotower-sweep/`

| File | What it is | Backs |
|---|---|---|
| [`pilot.json`](twotower-sweep/pilot.json) | Grid: the 2026-08-30 learning-rate and temperature sweep on a 6% sample. **Read by `tests/unit/test_twotower_sweep.py`.** | [The two-tower's learning rate and budget, swept](../results.md#2026-08-30-fourth-session--the-two-towers-learning-rate-and-budget-swept) |
| [`full.json`](twotower-sweep/full.json) | Grid: the full-data budget run chosen from that pilot. **Read by `tests/unit/test_twotower_sweep.py`.** | Same section. Its negative result is voided by [ADR 0006](../adr/0006-two-tower-retrieval-architecture.md)'s 2026-09-05 correctness amendment |
| [`full2.json`](twotower-sweep/full2.json) | Grid: the second full-data cell, resized to the day's remaining budget. **Read by `tests/unit/test_twotower_sweep.py`.** | Same section, same caveat |
| [`v2-pilot.json`](twotower-sweep/v2-pilot.json) | Grid: [ADR 0015](../adr/0015-two-tower-v2.md)'s cumulative Gate 1 arms on the 6% sample. | [Two-Tower v2 bounded pilot](../results.md#two-tower-v2-bounded-pilot--2026-09-04) |
| [`faiss-row-mapping-diagnostic-2026-09-05.json`](twotower-sweep/faiss-row-mapping-diagnostic-2026-09-05.json) | Record: the FAISS row-to-item-id defect (PR #158), the cross-path check that exposed it and the overfit canary that passed after the fix. | ADR 0006's 2026-09-05 correctness amendment |
| [`openmp-flag-validation-2026-09-05.json`](twotower-sweep/openmp-flag-validation-2026-09-05.json) | Record: the reproduction check that `KMP_DUPLICATE_LIB_OK=TRUE` leaves two-tower results unchanged, run before any number was measured under it. | ADR 0006's 2026-09-05 environment note |
| [`fulldata-retrieval-gate-2026-09-06.json`](twotower-sweep/fulldata-retrieval-gate-2026-09-06.json) | Record: `make gate-retrieval` for the corrected v2 at full scale, warm recall@500 0.5113 against item-item's 0.3991. | [Two-tower v2 on the full dataset](../results.md#two-tower-v2-on-the-full-dataset-after-the-faiss-mapping-fix--2026-09-06); ADR 0015's full-data outcome; ADR 0006's 2026-09-06 note |

## `tolerance/`

| File | What it is | Backs |
|---|---|---|
| [`surrogate-seed-noise-6pct.json`](tolerance/surrogate-seed-noise-6pct.json) | Grid: three SASRec cells differing only in seed, at the frozen configuration on the 6% sample, for the retrieval-tolerance study. Raw dispersion, not a certified tolerance. | [SASRec seed dispersion at 6%](../results.md#sasrec-seed-dispersion-at-6--2026-09-05-m0-14-raw-not-a-certified-tolerance) |
