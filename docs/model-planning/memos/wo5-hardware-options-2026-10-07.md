# WO-5 hardware options: a rented GPU against the Mac, priced 2026-10-07

**Status:** proposal only. Nothing has been spent, no account exists, and no key was used. Prices were checked on 2026-10-07 between 12:27 and 12:35 UTC; the sources are at the end.
**Inputs:** `docs/model-planning/experiments/wo5-cell-repricing.md` (CPU hours per pass, ADR-model column, the conservative one), ADR 0020 D6 (2026-10-07), decisions D-044, D-045 and D-047.

**Decision (D-050, 2026-10-07):** the owner chose the recommendation's step one, to be *prepared*
today and bought tomorrow: the kit is `src/training/sasrec_timing.py`, `src/training/gpu_smoke.py`
and `infra/gpu/`, and the session is run from
[`../experiments/wo5-gpu-timing-runbook.md`](../experiments/wo5-gpu-timing-runbook.md). Nothing was
spent and no account exists. The spend itself still needs the owner's explicit approval at the
session ([D-047](../03-decision-register.md)). The register row is
[D-050](../03-decision-register.md#owner-decisions-from-2026-10-05--one-dated-row-each).

**Committed** to the repository on 2026-10-07 as written that day, prices and sources unchanged. One
detail the memo left open was settled while the kit was built: PyTorch publishes 2.13.0 for CUDA 12.6,
12.9, 13.0 and 13.2 but not 12.8, so the pod installs `torch==2.13.0+cu126` (`infra/gpu/requirements-cuda.txt`).

## 1. GPU-hours (an estimate until the 10-minute timing)

**Assumption.** A modern card runs the capacity cells (A–E) at **8–20× the Mac CPU's step time**. The small shapes (v1's cell and 0b, 0.23–0.30 s per CPU step) are bound by kernel launches and Python, so they are priced at **5×**.

Why that band:
- **Cell A's cost.** A pass is about 5.8 PFLOP (3 × 2·A(100,128,2) × 19.74 M examples, plus the full-catalog logits). The Mac does 0.11 TFLOP/s on the CPU and 0.27 on `mps`.
- **Public anchor.** The HGN paper (Ma et al., KDD 2019, Table 5) reports SASRec on ML-20M at 39.9 s per epoch on a GTX 1080 Ti (L=300, d=50, 2 blocks): about 28 TFLOP, so 0.7 TFLOP/s, about 6% of FP32 peak.
- **Projection.** At 6% of peak, an A10G or L4 (about 31 TFLOPS FP32) is about 17× the Mac CPU. An RTX 4090 (82.6) would be about 45×, but host-side work caps it near 25–35×.
- **FP32 only** (no TF32 or AMP), to match the CPU numerics. An A100 (19.5 TFLOPS FP32) is then slower than an A10G and lists at $1.19–3.67/h, so it is excluded.

Overheads included: 0.5 h to set up each session, 0.25 h per run.

| Line | Class | GPU-h at 8× | GPU-h at 20× |
|---|---|---:|---:|
| 10-minute timing session (setup, 10 min of A, 3 min each of 0b and B, `cuda` smoke test) | **must, step one** | 1.0 | 1.0 |
| Sweep session setup | must | 0.5 | 0.5 |
| Calibration: v1's cell, 2 passes (4.9 CPU-h ÷ 5, plus 0.25) | must | 1.23 | 1.23 |
| One more GPU seed if calibration misses ±1% | must (contingent) | 1.23 | 1.23 |
| Six cells (0b, A–E) at **3 passes** | must | 60.6 | 26.3 |
| Six cells at **5 passes** | must (protocol ceiling) | 100.0 | 42.8 |
| **Must-have total, 3 / 5 passes** | | **64.5 / 103.9** | **30.2 / 46.8** |
| A′, only if A fails gate 1 | conditional | 2.7–4.4 | 1.1–1.8 |
| One combination cell, only if two of B–E clear gate 1 (C+E at 3 passes up to B+D at 5) | conditional | 11.9–75.5 | 4.7–30.2 |
| WO-7: the winner × 2 more seeds (A up to B) | later | 11.3–71.1 | 4.5–28.5 |
| WO-7: v1 × 2 seeds, two-tower v2 × 3 (its trainer has no `device` setting) | Mac CPU, $0 | 9.8 + 5.75 CPU-h | — |

Early stopping picks 3–5 passes, so the budget must cover 5. A fixed 3-pass cap would change ADR 0020's protocol; that is the owner's call.

## 2. Prices checked today (on-demand, one GPU)

| Provider / instance | $/h | Billing | Storage | Egress |
|---|---:|---|---|---|
| Lambda 1× A10 / A6000 / A100 40 GB | 1.29 / 1.09 / 1.99 | per minute | local SSD included | none |
| RunPod **Secure**: RTX 4090 / L4 / A5000 / A100 PCIe 80 GB | **0.74** / 0.49 / 0.27 / 1.59 | per second, prepaid | $0.10/GB-mo disk; $0.07 network volume | none |
| RunPod **Community**: RTX 4090 / L4 / A5000 / A100 PCIe | **0.34** / 0.44 / 0.16 / 1.19 | same | same | none |
| Vast.ai verified RTX 4090 | 0.33–0.47 (median 0.457 of the 50 cheapest); interruptible 0.20–0.39 | per second | ≈$0.14–0.53/GB-mo | $0.003–0.039/GB, per host |
| GCP `g2-standard-4` (L4), us-central1 | 0.7068 (spot 0.4241) | per second (not re-fetched) | PD extra | ≈$0.12/GB (not re-fetched) |
| GCP `a2-highgpu-1g` (A100 40 GB), us-central1 | 3.6734 (spot 2.204) | same | PD extra | same |
| AWS `g5.xlarge` (A10G, 4 vCPU, 16 GiB) | **1.006** (spot 0.495) | per second, 60 s minimum | EBS extra | 100 GB/mo free |
| AWS `g6.xlarge` (L4) / `p3.2xlarge` (V100) / `p4d.24xlarge` (8× A100, no 1-GPU size) | 0.805 / 3.06 / 21.96 | per second | | |
| DigitalOcean RTX 4000 Ada / L40S | 0.76 / 1.57 | per second, 5-min minimum | 500 GiB included | 10,000 GiB included |
| Paperspace (now DigitalOcean) A4000 / A5000 / A6000 | 0.76 / 1.38 / 1.89 | hourly | $0.29/GB-mo overage | free |
| Hetzner GEX45 (RTX PRO 4000 Blackwell) | €214/month **+ €209 setup** | monthly | | |

Hetzner's setup fee alone exceeds the $75 ceiling. GCP's figures come from a third-party mirror (updated 2026-10-04) because Google's page did not render.

**Data movement is free.** The box downloads `ml-25m.zip` (250 MB, MD5 published) from GroupLens and checks `ratings.csv` and `movies.csv` (the only files `load_inputs` reads) by SHA-256 against the Mac's copies. Results come back under 1 GB (the eight-run catch-up was 52.9 MiB): $0 on RunPod, Lambda and AWS's free tier, under $0.05 on Vast.

## 3. Three choices against the $75 ceiling

**Formula:** total = H × (rate + storage) × 1.2. H is the must-have hours above (timing and the contingent seed included). Storage is a 50 GB disk at $0.0068/h on RunPod and $0.0055/h on AWS gp3 (gp3 not re-fetched).

| Option | 3 p, 8× | 5 p, 8× | 3 p, 20× | 5 p, 20× | Break-even speed-up (5 p / 3 p) |
|---|---:|---:|---:|---:|---:|
| RunPod Secure RTX 4090 @ $0.7468 | 64.5 × 0.7468 = $48.19 → **$57.83** | 103.9 × 0.7468 = $77.61 → **$93.13 ✗** | $22.58 → **$27.10** | $34.92 → **$41.91** | 10.2× / 6.0× |
| RunPod Community RTX 4090 @ $0.3468 | $22.38 → $26.86 | $36.04 → **$43.25** | $10.49 → $12.59 | $16.22 → $19.46 | 4.4× / 2.6× |
| AWS `g5.xlarge` @ $1.0115 | $65.27 → **$78.32 ✗** | $105.10 → **$126.12 ✗** | $30.59 → $36.70 | $47.30 → $56.76 | 14.3× / 8.4× |

**If Secure misses (8×, 5 passes), any one of these fits:** B on `mps` with its own free 1.8 h `mps` calibration ($61.03 for the rest); a 3-pass cap ($57.83, a protocol change); or Community ($43.25).

**Conditionals on Secure 4090, each approved separately from the headroom:** A′ $0.79–3.28; one combination $3.51–55.87; the WO-7 winner's two seeds $3.35–52.63.

## 4. Factors other than price

| Factor | What it means here |
|---|---|
| Reproducibility | CUDA is not bit-identical to the CPU (or reliably to itself: atomic-add backward kernels). D6's ±1% covers it. WO-4's `mps` pair measured −0.83% at 6%, so keep the contingent seed. Every model is re-scored on the Mac CPU from its saved weights. |
| Environment | A separate venv with the torch 2.13.0 CUDA wheel (the Mac's dev version); the sidecar's `torch==2.12.0+cpu` pin is untouched. `resolve_device("cuda")` has never run on NVIDIA, so the timing session is its first smoke test. |
| MLflow and run preservation | The box cannot reach `localhost:5001`, and a reverse tunnel can drop mid-pass. Log to a local SQLite store. After **each** run, bundle the store, console log, archive with its SHA-256, per-user file and per-pass numbers; copy to the Mac and checksum both ends. Import with the repo's import tool (original run ids, `imported_from`), verify through the REST API as the 2026-10-07 catch-up did, then push the backup. Tag provider, instance id, GPU, driver, CUDA version and rate. |
| Credit stops | At a $0 balance RunPod stops pods, and a pod without a network volume loses its disk. Pulling after each run limits the loss to the run in flight. |
| Spot / interruptible | The trainer has no checkpoint and resume, only a final archive. A preemption (AWS 2 min notice, GCP 0–120 s) loses the whole run; B at 5 passes and 8× is one 36 h process. A resume would also need its RNG state restored. **On-demand only.** |

## 5. The `mps` alternative: $0

| Work | `mps` hours |
|---|---:|
| Calibration (4.9 h ÷ 2.7) | ≈1.8 |
| Six cells, 3–5 passes | 182–303 (7.6–12.6 days) |
| A′ | 8–14 |
| WO-7 winner × 2 seeds | 36–58 (A) to 134–222 (B) |

**What the owner gives up:** about 8–13 days of wall time instead of 1.3–4.3. The Mac stays awake and plugged in for two weeks. B alone is a 67–111 h process with no resume. The one-full-data-run rule queues the CPU-only WO-7 seeds behind it. `mps` saves money but not reproducibility: it is non-CPU and needs the same ±1% calibration.

## 6. Recommendation

**RunPod Secure Cloud, 1× RTX 4090, on-demand, $0.74/h.** It is data-center hosted, billed per second, egress-free and prepaid, and has the most FP32 per dollar of the reputable options. Every must-have fits at 5 passes if the timing shows ≥10.2× (the anchors suggest 20–35×). Between 6× and 10.2×, fall back to Community 4090 (≤$43.25) or B on `mps`. Below about 5×, rent nothing and run on `mps`.

**The approval needed now, step one only:**
> RunPod Secure Cloud, one RTX 4090 on-demand pod (not spot) at $0.74/h, one session, at most 1.0 h and $1.00, with the smallest credit top-up and auto-pay off. Time cell A for 10 minutes and 0b and B for 3 minutes each on `cuda`, pull the timing JSON back, terminate.

**Step two, approved separately on the measured speed:** the same pod type; the calibration (seed 42, plus one contingent seed) and cells 0b and A–E with early stopping at 3–5 passes; planned ≤83 GPU-hours; hard cap $74.00 (≤$75 with step one); on-demand only; artifacts pulled after each run.

## Sources (fetched 2026-10-07, 12:27–12:35 UTC)

- Lambda: https://lambda.ai/pricing, https://docs.lambda.ai/public-cloud/billing/, https://lambda.ai/service/gpu-cloud
- RunPod: https://www.runpod.io/pricing, https://docs.runpod.io/pods/pricing, https://docs.runpod.io/accounts-billing/billing
- Vast.ai: https://console.vast.ai/api/v0/bundles/ (public offer search, no key; on-demand and interruptible queries for verified 1× RTX 4090). https://vast.ai/pricing rendered no prices.
- GCP: https://gcloud-compute.com/g2-standard-4.html, https://gcloud-compute.com/a2-highgpu-1g.html (mirror, updated 2026-10-04 01:50 GMT). https://cloud.google.com/compute/gpus-pricing and /vm-instance-pricing did not render. Spot notice: https://docs.cloud.google.com/compute/docs/instances/spot
- AWS: https://instances.vantage.sh/aws/ec2/g5.xlarge, /g6.xlarge, /p3.2xlarge, /p4d.24xlarge (updated 2026-10-07 08:37); the free egress tier: https://aws.amazon.com/ec2/pricing/on-demand/
- DigitalOcean: https://www.digitalocean.com/pricing/gpu-droplets. Paperspace: https://www.paperspace.com/pricing
- Hetzner: https://www.hetzner.com/dedicated-rootserver/gex45/ (no figures rendered); figures from https://dohohub.com/news/hetzner-gex45-entry-level-gpu-server (2026-09-01)
- Dataset: https://grouplens.org/datasets/movielens/25m/
- Speed anchor: Ma et al., *Hierarchical Gating Networks for Sequential Recommendation*, KDD 2019, Table 5: https://ar5iv.labs.arxiv.org/html/1906.09217
