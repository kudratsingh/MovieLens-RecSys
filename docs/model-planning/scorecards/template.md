# Model scorecard — <model/version>

**Stage:** retrieval / ranking / re-ranking / representation

**Lifecycle state:** <allowed state>

**Governing ADR:** <link>

**Protocol hash:** <value>

**Data/code identity:** <DVC hash / commit>

## Purpose and mechanism

One paragraph: what signal this model adds over the immediate baseline and why it could help.

## Evidence

| Metric/gate | Candidate | Incumbent | Delta / tolerance | Verdict |
|---|---:|---:|---:|---|
| Primary | | | | |
| Warm | | | | |
| Cold | | | | |
| End-to-end | | | | |

Runs: <MLflow IDs>

Full record: <results.md section>

## Required diagnostics

- seed/window uncertainty:
- synthetic h0/h1/h3/h10:
- coverage/reachability/head bias:
- history/item/source slices:
- resource use:
- latency, if serving eligible:

## Artifact and serving identity

- manifest/version:
- artifact checksums:
- ranker/feature compatibility:
- save/load equivalence:
- fallback and rollback predecessor:

## Known limitations

State dataset, label, slice, calibration, scaling, and deployment limitations directly.

## Decision

**Decision:** advance / stop / promote / hold / supersede

**Rule applied:** <predeclared rule and values>

**Approved by/date:** <owner or pending>

**What this does not authorize:** <next boundary>
