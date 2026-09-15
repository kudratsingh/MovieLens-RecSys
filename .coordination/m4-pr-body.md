## Summary

- replace copied final-position prefixes with canonical all-position SASRec windows while preserving strict timestamp groups and bounded L=200 storage
- evaluate training checkpoints with exact torch top-k and defer FAISS import until artifact loading/serving
- version the training objective in configuration ids and model manifests while keeping legacy artifacts loadable
- retain immutable 6% and full-data evidence, including failed/preflight attempts, and record the comparison without moving any gate

## Verification

- Black check: pass
- Ruff: pass
- strict mypy: pass for all eight touched source modules
- focused candidate/training suite: 103 passed, 1 skipped
- both local and MLflow copies of the full model reload; their three files are byte-identical and return identical scored candidates

## Measurements

- 6%: run f837955c832440069dd8c1316a2ad0c6, warm recall@500 0.182165 versus v1 0.318641, 80.9 s fit
- full: run fd2ee9f6f6794449a31ea3f50e600a48, protocol sha256:b4ed5afa..., warm recall@500 0.485648 versus corrected v1 0.509171 (-4.62%), 1,797.4 s fit, 7.22 GB max RSS
- cold full-data metrics are bit-identical to v1; all four synthetic routing buckets pass

The rewrite is 9.8x faster at full scale but does not replace the saved v1 model on quality. No threshold, promotion verdict, or serving champion changes. ADR 0020's open proposal must absorb this control result before any capacity cell runs.
