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


def _import_under_slim_image(module: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            _IMPORT_PROBE.format(forbidden=FORBIDDEN_IN_API_IMAGE, module=module),
        ],
        cwd=_REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_api_entrypoint_imports_without_the_model_stack() -> None:
    result = _import_under_slim_image("src.serving.app")

    assert result.returncode == 0, (
        "src.serving.app pulled a package the API image does not install. "
        "Keep contract modules the API imports dependency-free rather than "
        "reaching into src.models or src.features.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip().endswith("ok")


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
