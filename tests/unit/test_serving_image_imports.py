"""Guard the slim API image's dependency budget.

ADR 0008 splits serving in two: the API process talks HTTP to a private
sidecar, and only the sidecar carries numpy, pandas, LightGBM, Feast, FAISS,
and Torch. ``infra/api/requirements.txt`` is sized for that split, so a new
import edge from ``src.serving`` into ``src.models`` or ``src.features`` does
not fail a unit test — it fails the container at startup, which is where this
was previously caught.

The check runs in a subprocess because the rest of the suite has already
imported the heavy libraries into ``sys.modules``; only a fresh interpreter can
tell whether the API's import graph actually needs them.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Every top-level package the API image deliberately does not install.
FORBIDDEN_IN_API_IMAGE = (
    "numpy",
    "pandas",
    "lightgbm",
    "feast",
    "faiss",
    "torch",
    "implicit",
    "sklearn",
    "mlflow",
)

# What the sidecar image installs but must not import to start. Torch and
# faiss-cpu are in that image so a SASRec bundle can be rebuilt; implicit is not
# installed there at all and is listed with them because the one import edge
# that drags it in — ``src/models/candidates/__init__`` importing ``.cf`` — is
# the same edge that would drag in the other two.
DEFERRED_IN_SIDECAR_IMAGE = ("torch", "faiss", "implicit")

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

_IMPORT_PROBE = """
import sys

FORBIDDEN = {forbidden!r}


class _RefuseForbidden:
    \"\"\"Stand in for the packages the slim image does not ship.\"\"\"

    def find_module(self, fullname, path=None):  # pragma: no cover - legacy hook
        return None

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in FORBIDDEN:
            raise ModuleNotFoundError(f"No module named {{fullname!r}}", name=fullname)
        return None


sys.meta_path.insert(0, _RefuseForbidden())

import {module}  # noqa: E402,F401

leaked = sorted(name for name in FORBIDDEN if name in sys.modules)
if leaked:
    raise SystemExit(f"heavy modules reached the API process: {{leaked}}")
print("ok")
"""


def _import_with_blocked(
    module: str, forbidden: tuple[str, ...]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE.format(forbidden=forbidden, module=module)],
        cwd=_REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _import_under_slim_image(module: str) -> subprocess.CompletedProcess[str]:
    return _import_with_blocked(module, FORBIDDEN_IN_API_IMAGE)


def test_api_entrypoint_imports_without_the_model_stack() -> None:
    result = _import_under_slim_image("src.serving.app")

    assert result.returncode == 0, (
        "src.serving.app pulled a package the API image does not install. "
        "Keep contract modules the API imports dependency-free rather than "
        "reaching into src.models or src.features.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip().endswith("ok")


def test_the_api_entrypoint_never_reaches_the_sidecar_modules() -> None:
    """The image boundary, enforced by name rather than only by side effect.

    The sidecar image now installs torch and faiss-cpu so it can rebuild a
    SASRec encoder; the API image still installs neither. The test above proves
    no forbidden *package* reached the API process, which is the property that
    matters — but it would keep passing if the API grew an import edge to a
    sidecar module whose own heavy imports happen to be lazy, and that edge is
    the thing that goes wrong next. Naming the modules makes the boundary an
    assertion instead of a coincidence.
    """
    # Formatted first, then extended: the extra check contains braces of its own
    # and ``str.format`` would try to read them as fields.
    probe = _IMPORT_PROBE.format(
        forbidden=FORBIDDEN_IN_API_IMAGE, module="src.serving.app"
    ).replace(
        'print("ok")',
        "sidecar = sorted(\n"
        "    name\n"
        "    for name in ('src.serving.model_server', 'src.serving.sequence_retrieval')\n"
        "    if name in sys.modules\n"
        ")\n"
        "if sidecar:\n"
        '    raise SystemExit(f"the API entrypoint imported sidecar modules: {sidecar}")\n'
        'print("ok")',
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=_REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert result.returncode == 0, (
        "src.serving.app reached into the model sidecar. The sidecar carries torch, faiss and "
        "LightGBM; the API image installs none of them, so this edge breaks the container at "
        f"startup rather than here.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_the_sidecar_loads_sasrec_without_importing_torch_at_module_scope() -> None:
    """Installed is not imported.

    ``src.serving.model_server`` and the module that rebuilds a SASRec encoder
    must both import cleanly with torch, faiss and implicit blocked, which is
    what keeps an item-item deployment from paying a torch import to start and
    keeps every non-sidecar reader of a manifest — the release verifier, the
    reproducibility check — working in an environment that has none of the
    three. The heavy imports happen inside the SASRec loader, so they are paid
    by the bundles that need them and by nothing else.
    """
    for module in ("src.serving.model_server", "src.serving.sequence_retrieval"):
        result = _import_with_blocked(module, DEFERRED_IN_SIDECAR_IMAGE)
        assert result.returncode == 0, (
            f"{module} imports torch, faiss or implicit at module scope. Keep those imports "
            "inside the function that rebuilds an encoder.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def test_serving_contract_modules_stay_dependency_free() -> None:
    # These are the modules the API imports purely to agree on vocabulary with
    # the sidecar; each one is a tempting place to import a model constant.
    for module in (
        "src.serving.policy",
        "src.serving.audit",
        "src.serving.orchestration",
        "src.serving.models",
        "src.serving.request_id",
        "src.serving.recommendations",
    ):
        result = _import_under_slim_image(module)
        assert result.returncode == 0, (
            f"{module} pulled a package the API image does not install.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
