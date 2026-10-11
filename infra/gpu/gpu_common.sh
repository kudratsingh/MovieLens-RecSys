# shellcheck shell=bash
#
# The phases every rented-GPU script shares: infra/gpu/bootstrap.sh (WO-5 step
# one, the timing) and infra/gpu/sweep.sh (step two, the sweep). Sourced, never
# run. Written for bash 3.2 as well as 5, because the rehearsals run on macOS's.
#
# The caller sets, before sourcing:
#
#   COMMIT_ARG LOCAL_SMOKE ROOT OUT FACTS DEVICE REPO_URL EXPECTED_GPU DATA_URL
#   MAX_WALL_SECONDS MIN_DISK_GB MIN_MEMORY_GB TORCH_FALLBACK_VARIANTS
#   INPUT_MANIFEST STARTED_EPOCH SHA256
#
# and gets: log, fail, facts, elapsed, check_wall_clock, phase, run_logged, and
# the four phases, each of which records what it found in $FACTS:
#
#   wo5_preflight  one GPU named like $EXPECTED_GPU, enough disk and memory
#   wo5_repo       the public repository at exactly the pinned commit -> REPO, COMMIT
#   wo5_venv       a Python 3.11+ venv with infra/gpu/requirements-cuda.txt -> PY
#   wo5_data       ml-25m from GroupLens (published MD5), then the SHA-256 of
#                  ratings.csv and movies.csv against the committed manifest -> DATA_DIR
#
# With LOCAL_SMOKE=1 each phase does its Mac rehearsal instead: no GPU, no clone,
# no venv, no download (WO5_REPO_DIR, WO5_PYTHON and WO5_INPUT_DIR say where).

log() { printf '%s %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }
fail() {
  log "FAILED: $*"
  exit 1
}
facts() { printf '%s=%s\n' "$1" "$2" >>"$FACTS"; }
elapsed() { echo $(($(date +%s) - STARTED_EPOCH)); }
check_wall_clock() {
  if [ "$(elapsed)" -gt "$MAX_WALL_SECONDS" ]; then
    fail "wall clock $(elapsed) s is past the limit of $MAX_WALL_SECONDS s before $1"
  fi
}
PHASE=""
PHASE_STARTED=0
phase() {
  [ -z "$PHASE" ] || facts "${PHASE}_SECONDS" $(($(date +%s) - PHASE_STARTED))
  PHASE="$1"
  PHASE_STARTED="$(date +%s)"
  check_wall_clock "$1"
  log "=== $1"
}

# Runs one step with its own log, under `timeout` where the system has it.
run_logged() {
  local limit="$1" logfile="$2"
  shift 2
  if command -v timeout >/dev/null 2>&1; then
    timeout --kill-after=30 "$limit" "$@" 2>&1 | tee "$logfile"
  else
    "$@" 2>&1 | tee "$logfile"
  fi
}

# --- preflight ------------------------------------------------------------------
wo5_preflight() {
  uname -a >"$OUT/env/uname.txt" 2>&1 || true
  [ -r /etc/os-release ] && cp /etc/os-release "$OUT/env/os-release.txt"
  # The pod's own RUNPOD_* variables say which pod and data centre this was. Any
  # variable whose name suggests a credential is left out: RunPod injects
  # pod-scoped keys, and this file goes into MLflow and a backup.
  env | grep '^RUNPOD_' | grep -viE 'KEY|SECRET|TOKEN|PASS|AUTH|CRED' | sort >"$OUT/env/runpod-env.txt" || true
  while IFS='=' read -r key value; do
    case "$key" in RUNPOD_POD_ID | RUNPOD_DC_ID | RUNPOD_GPU_COUNT | RUNPOD_CPU_COUNT) facts "$key" "$value" ;; esac
  done <"$OUT/env/runpod-env.txt"
  if [ "$LOCAL_SMOKE" = 1 ]; then
    sysctl -n machdep.cpu.brand_string >"$OUT/env/cpu.txt" 2>/dev/null || true
    facts GPU_NAME "local-$DEVICE"
  else
    command -v nvidia-smi >/dev/null 2>&1 || fail "nvidia-smi is not on PATH: this is not a GPU pod"
    nvidia-smi >"$OUT/env/nvidia-smi.txt" 2>&1 || fail "nvidia-smi failed: the driver is not usable"
    nvidia-smi -q >"$OUT/env/nvidia-smi-q.txt" 2>&1 || true
    gpu_count="$(nvidia-smi --query-gpu=name --format=csv,noheader | wc -l | tr -d ' ')"
    gpu_name="$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n 1)"
    driver="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n 1)"
    driver_cuda="$(sed -n 's/.*CUDA Version: *\([0-9.]*\).*/\1/p' "$OUT/env/nvidia-smi.txt" | head -n 1)"
    facts GPU_NAME "$gpu_name"
    facts GPU_COUNT "$gpu_count"
    facts GPU_DRIVER "$driver"
    facts DRIVER_CUDA_VERSION "${driver_cuda:-unknown}"
    log "GPU: $gpu_count x $gpu_name, driver $driver (CUDA ${driver_cuda:-unknown})"
    printf '%s' "$gpu_name" | grep -qi "$EXPECTED_GPU" ||
      fail "expected a GPU named like '$EXPECTED_GPU' (the priced card), got '$gpu_name'"
    [ "$gpu_count" = 1 ] || log "warning: $gpu_count GPUs visible; the timing uses the first"
    lscpu >"$OUT/env/lscpu.txt" 2>&1 || true
    free -m >"$OUT/env/free.txt" 2>&1 || true
    memory_gb="$(awk '/MemAvailable/ {print int($2 / 1048576)}' /proc/meminfo)"
    facts MEMORY_AVAILABLE_GB "$memory_gb"
    [ "$memory_gb" -ge "$MIN_MEMORY_GB" ] ||
      fail "only $memory_gb GB of memory available; cell A peaked at about 5 GB RSS on the Mac and the session needs $MIN_MEMORY_GB"
    for tool in git curl python3 tar; do
      command -v "$tool" >/dev/null 2>&1 || fail "$tool is not installed on this image"
    done
  fi
  facts CPU_COUNT "$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo unknown)"
  mkdir -p "$ROOT"
  df -Pk "$ROOT" >"$OUT/env/df.txt" 2>&1 || true
  disk_gb="$(df -Pk "$ROOT" | awk 'NR == 2 {print int($4 / 1048576)}')"
  facts DISK_FREE_GB "$disk_gb"
  [ "$disk_gb" -ge "$MIN_DISK_GB" ] ||
    fail "only $disk_gb GB free under $ROOT; the venv and the data need about $MIN_DISK_GB"
}

# --- the repository at the pinned commit --------------------------------------------
wo5_repo() {
  if [ "$LOCAL_SMOKE" = 1 ]; then
    REPO="$(cd "${WO5_REPO_DIR:?WO5_REPO_DIR is required with WO5_LOCAL_SMOKE=1}" && pwd)"
    COMMIT="$(git -C "$REPO" rev-parse "$COMMIT_ARG")"
    if [ -n "$(git -C "$REPO" status --porcelain)" ]; then
      facts REPO_DIRTY true
      log "warning: $REPO has uncommitted changes (recorded; a rehearsal only)"
    else
      facts REPO_DIRTY false
    fi
  else
    REPO="$ROOT/repo"
    COMMIT="$COMMIT_ARG"
    if [ -d "$REPO/.git" ]; then
      git -C "$REPO" fetch --quiet origin
    else
      git clone --quiet "$REPO_URL" "$REPO"
    fi
    # A commit that is no longer on a branch is still fetchable by its SHA.
    git -C "$REPO" cat-file -e "$COMMIT^{commit}" 2>/dev/null ||
      git -C "$REPO" fetch --quiet origin "$COMMIT" ||
      fail "commit $COMMIT is not in $REPO_URL"
    git -C "$REPO" checkout --quiet --detach "$COMMIT"
    [ "$(git -C "$REPO" rev-parse HEAD)" = "$COMMIT" ] || fail "checkout did not land on $COMMIT"
    [ -z "$(git -C "$REPO" status --porcelain --untracked-files=no)" ] || fail "the checkout is not clean"
    facts REPO_DIRTY false
  fi
  facts COMMIT "$COMMIT"
  log "repository $REPO at $COMMIT"
  for file in infra/gpu/requirements-cuda.txt "$INPUT_MANIFEST"; do
    [ -f "$REPO/$file" ] || fail "$file is missing at $COMMIT: is this commit older than the kit?"
  done
  cp "$REPO/infra/gpu/requirements-cuda.txt" "$REPO/$INPUT_MANIFEST" "$OUT/env/"
}

# --- the Python environment ---------------------------------------------------------
wo5_venv() {
  python_tag() { "$1" -c 'import sys; print(f"{sys.version_info[0]}{sys.version_info[1]}")'; }
  if [ "$LOCAL_SMOKE" = 1 ]; then
    PY="$(cd "$(dirname "${WO5_PYTHON:?WO5_PYTHON is required with WO5_LOCAL_SMOKE=1}")" && pwd)/$(basename "$WO5_PYTHON")"
    facts VENV_HOW "existing WO5_PYTHON"
    facts TORCH_WHEEL_VARIANT local
  else
    VENV="$ROOT/venv"
    PY="$VENV/bin/python"
    if [ -x "$PY" ] && "$PY" -c 'import sys, torch; sys.exit(0 if torch.cuda.is_available() else 1)' 2>/dev/null; then
      log "reusing $VENV (torch already sees the GPU)"
      facts VENV_HOW reused
      facts TORCH_WHEEL_VARIANT "$(cat "$VENV/.wo5-torch-variant" 2>/dev/null || echo unknown)"
    else
      rm -rf "$VENV"
      venv_how=""
      for candidate in python3.11 python3.12 python3.13 python3; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null || continue
        if "$candidate" -m venv "$VENV" 2>/dev/null; then
          venv_how="$candidate -m venv ($("$candidate" --version 2>&1))"
          break
        fi
        rm -rf "$VENV"
      done
      if [ -z "$venv_how" ]; then
        # No Python 3.11+ with a working venv module on this image: uv fetches one.
        log "no python >= 3.11 with venv on the image; creating one with uv"
        # A throwaway pod's system Python may be marked externally managed (PEP 668).
        python3 -m pip install --quiet --upgrade uv ||
          python3 -m pip install --quiet --upgrade --break-system-packages uv ||
          fail "no Python 3.11+ and uv could not be installed"
        python3 -m uv venv --quiet --seed --python 3.11 "$VENV" || fail "uv could not create a Python 3.11 venv"
        venv_how="uv venv --python 3.11"
      fi
      facts VENV_HOW "$venv_how"
      log "venv: $venv_how"
      "$PY" -m pip install --quiet --upgrade pip
      requirements="$REPO/infra/gpu/requirements-cuda.txt"
      torch_pin="$(grep -E '^torch==' "$requirements")"
      torch_version="$(printf '%s' "$torch_pin" | sed -n 's/^torch==\([0-9.]*\).*/\1/p')"
      pinned_variant="$(printf '%s' "$torch_pin" | sed -n 's/^torch==[0-9.]*+\(cu[0-9]*\).*/\1/p')"
      tag="$(python_tag "$PY")"
      # Probe PyTorch's index for the wheel before downloading anything. Its
      # simple index lists every file, with '+' written as %2B.
      variant=""
      driver_major="$(printf '%s' "${driver_cuda:-}" | cut -d. -f1)"
      case "$driver_major" in '' | *[!0-9]*) driver_major="" ;; esac
      for candidate in $pinned_variant $TORCH_FALLBACK_VARIANTS; do
        # A CUDA 13 build needs a driver that reports CUDA 13; minor versions of
        # one major release are compatible, majors are not. A driver that did not
        # say is not held against any build: the CUDA check after the install decides.
        candidate_major="$(printf '%s' "$candidate" | cut -c3-4)"
        if [ -n "$driver_major" ] && [ "$candidate_major" -gt "$driver_major" ]; then
          log "skipping torch +$candidate: the driver reports CUDA ${driver_cuda:-unknown}"
          continue
        fi
        # Fetched to a file, not piped: under pipefail, `curl | grep -q` fails
        # whenever grep stops reading early, which is exactly when it matched.
        index_page="$OUT/env/torch-index-$candidate.html"
        if curl -fsSL -o "$index_page" "https://download.pytorch.org/whl/$candidate/torch/" &&
          grep -Eq "torch-${torch_version}(%2B|\+)${candidate}-cp${tag}-cp${tag}-(manylinux[^\"]*|linux)_x86_64\.whl" "$index_page"; then
          variant="$candidate"
          rm -f "$index_page"
          break
        fi
        rm -f "$index_page"
        log "no torch $torch_version+$candidate wheel for cp$tag on PyTorch's index"
      done
      installed_requirements="$OUT/env/requirements-installed.txt"
      if [ -n "$variant" ]; then
        sed -e "s#download.pytorch.org/whl/$pinned_variant#download.pytorch.org/whl/$variant#" \
          -e "s#^torch==$torch_version+$pinned_variant#torch==$torch_version+$variant#" \
          "$requirements" >"$installed_requirements"
      else
        # Last resort: PyPI's own Linux wheel of the same version, also a CUDA build.
        variant="pypi-default"
        sed -e '/^--extra-index-url/d' -e "s#^torch==.*#torch==$torch_version#" \
          "$requirements" >"$installed_requirements"
      fi
      [ "$variant" = "$pinned_variant" ] || log "warning: installing torch $torch_version as $variant, not the pinned $pinned_variant"
      facts TORCH_WHEEL_VARIANT "$variant"
      log "installing $(grep -c '^[A-Za-z]' "$installed_requirements") pinned packages (torch $torch_version, $variant)"
      "$PY" -m pip install --quiet --progress-bar off -r "$installed_requirements"
      printf '%s\n' "$variant" >"$VENV/.wo5-torch-variant"
    fi
  fi
  "$PY" -m pip freeze >"$OUT/env/pip-freeze.txt" 2>/dev/null || true
  "$PY" - "$DEVICE" <<'PYCHECK' | tee "$OUT/env/torch.txt"
import sys
import torch

device = sys.argv[1]
print("python", sys.version.split()[0])
print("torch", torch.__version__, "cuda", torch.version.cuda)
if device == "cuda":
    if not torch.cuda.is_available():
        sys.exit("torch.cuda.is_available() is False: the wheel and the driver do not agree")
    print("device", torch.cuda.get_device_name(0), "capability", torch.cuda.get_device_capability(0))
    print("cudnn", torch.backends.cudnn.version())
elif device == "mps" and not torch.backends.mps.is_available():
    sys.exit("torch.backends.mps.is_available() is False")
if not torch.__version__.startswith("2.13.0"):
    sys.exit(f"torch {torch.__version__} is not the pinned 2.13.0")
PYCHECK
  facts TORCH_VERSION "$("$PY" -c 'import torch; print(torch.__version__)')"
  facts PYTHON_VERSION "$("$PY" -c 'import platform; print(platform.python_version())')"
}

# --- the data -------------------------------------------------------------------------
wo5_data() {
  export PYTHONPATH="$REPO"
  export OMP_NUM_THREADS=1
  cd "$REPO" || fail "cannot enter $REPO"
  timing() { "$PY" -m src.training.sasrec_timing "$@"; }
  if [ "$LOCAL_SMOKE" = 1 ]; then
    DATA_DIR="$(cd "${WO5_INPUT_DIR:?WO5_INPUT_DIR is required with WO5_LOCAL_SMOKE=1}" && pwd)"
    facts DATASET_SOURCE "local WO5_INPUT_DIR"
    timing verify-inputs --dir "$DATA_DIR" | tee "$OUT/logs/data.log"
  else
    DATA_DIR="$ROOT/data/ml-25m"
    if [ -f "$DATA_DIR/ratings.csv" ] && timing verify-inputs --dir "$DATA_DIR" >"$OUT/logs/data.log" 2>&1; then
      log "reusing $DATA_DIR (both SHA-256s match)"
      facts DATASET_SOURCE cached
    elif [ -n "${WO5_DATA_TARBALL:-}" ]; then
      # The fallback when GroupLens cannot be reached: a tarball of the two CSVs
      # sent from the Mac. The SHA-256 check below is the same either way.
      mkdir -p "$DATA_DIR"
      tar -xzf "$WO5_DATA_TARBALL" -C "$DATA_DIR"
      timing verify-inputs --dir "$DATA_DIR" | tee "$OUT/logs/data.log"
      facts DATASET_SOURCE "tarball WO5_DATA_TARBALL"
    else
      mkdir -p "$ROOT/data"
      # Certificate verification stays on. GroupLens's lapsed once (docs/results.md,
      # 2026-08-29); if it has again, use the tarball fallback in the runbook.
      curl -fsSL --retry 3 --retry-delay 5 -o "$ROOT/data/ml-25m.zip.md5" "$DATA_URL.md5" ||
        fail "could not fetch $DATA_URL.md5 (see the runbook's dataset fallback)"
      if [ ! -f "$ROOT/data/ml-25m.zip" ]; then
        curl -fSL --retry 3 --retry-delay 5 --progress-bar -o "$ROOT/data/ml-25m.zip.part" "$DATA_URL" ||
          fail "could not download $DATA_URL (see the runbook's dataset fallback)"
        mv "$ROOT/data/ml-25m.zip.part" "$ROOT/data/ml-25m.zip"
      fi
      cp "$ROOT/data/ml-25m.zip.md5" "$OUT/env/"
      timing prepare-inputs --zip "$ROOT/data/ml-25m.zip" \
        --published-md5-file "$ROOT/data/ml-25m.zip.md5" --dest "$DATA_DIR" | tee "$OUT/logs/data.log"
      facts DATASET_SOURCE grouplens
      md5s="$("$PY" -c 'import json, sys; d = json.load(open(sys.argv[1])); print(d["zip_md5"], d["published_md5"])' "$OUT/logs/data.log")"
      facts ZIP_MD5 "${md5s% *}"
      facts PUBLISHED_MD5 "${md5s#* }"
    fi
  fi
  export TWOTOWER_INPUT_DIR="$DATA_DIR"
  log "data: $DATA_DIR matches $INPUT_MANIFEST"
}

# --- the Mac's acknowledgements (sweep.sh and watchdog.sh) -------------------------------
#
# The Mac's harvester appends "<seq> <run id> <sha256> OK <utc>" to out/HARVESTED once a
# bundle is home, verified, imported, re-scored and backed up. A bundle line in out/INDEX
# is "<seq> <step> <run id> <tgz> <sha256> <status>".

utc_of() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ; }

# Bundle lines of INDEX whose ACK is not in HARVESTED yet, one "seq run sha" per line.
wo5_unacked() {
  local sweep_root="$1" seq run sha status
  [ -f "$sweep_root/out/INDEX" ] || return 0
  while read -r seq _ run _ sha status; do
    # Only bundle lines: a numeric seq; the DONE/HALT line is not one.
    case "$seq" in "" | *[!0-9]*) continue ;; esac
    [ -n "${status:-}" ] || continue
    grep -q "^$seq $run $sha OK" "$sweep_root/out/HARVESTED" 2>/dev/null || echo "$seq $run $sha"
  done <"$sweep_root/out/INDEX"
}

# Waits until every bundle is acknowledged, or ack_wait seconds pass, or the hard
# deadline arrives -- whichever is first. The deadline always wins: at the hard limit
# the pod stops at once (owner, 2026-10-11).
wo5_wait_for_acks() {
  local sweep_root="$1" ack_wait="$2" deadline="$3" poll="${WO5_ACK_POLL_SECONDS:-30}"
  local until now pending
  until=$(($(date +%s) + ack_wait))
  [ "$until" -le "$deadline" ] || until="$deadline"
  while :; do
    pending="$(wo5_unacked "$sweep_root")"
    if [ -z "$pending" ]; then
      log "every bundle is acknowledged by the Mac"
      return 0
    fi
    now="$(date +%s)"
    if [ "$now" -ge "$until" ]; then
      if [ "$now" -ge "$deadline" ]; then
        log "hard limit reached: not waiting for the Mac (unacknowledged seq: $(echo "$pending" | awk '{ print $1 }' | tr '\n' ' '))"
      else
        log "no acknowledgement after ${ack_wait} s for seq $(echo "$pending" | awk '{ print $1 }' | tr '\n' ' '); stopping anyway (the bundles stay on this volume)"
      fi
      return 1
    fi
    sleep "$poll"
  done
}
