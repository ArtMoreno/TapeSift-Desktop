"""research/ is a sandbox. These keep it from quietly becoming a layer.

CONTRIBUTING.md calls the detector and scoring code frozen, and the docs
describe research/ as experiments. Both stop being true the moment the
shipped app cannot start without it - which is what had happened.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "tapesift" / "services"

#: services/snap_prediction_service derives PREDICTOR_VERSION from
#: run_pass_temporal_features at module scope, so it cannot be made lazy
#: without moving ~1,100 lines of frozen feature extraction down into
#: services. Named here so that widening the list is a deliberate edit and
#: not something that happens by accident. See docs/architecture.md.
KNOWN_SERVICE_IMPORTS_OF_RESEARCH = {"snap_prediction_service"}


def _research_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("tapesift.research"):
                found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("tapesift.research"):
                    found.add(alias.name)
    return found


class TestServicesDoNotDependOnResearch:
    def test_only_the_known_exception_imports_research(self):
        offenders = {
            path.stem: sorted(_research_imports(path))
            for path in sorted(SERVICES.glob("*.py"))
            if _research_imports(path)
        }
        unexpected = set(offenders) - KNOWN_SERVICE_IMPORTS_OF_RESEARCH
        assert not unexpected, (
            "a service reached up into research/: "
            f"{ {k: offenders[k] for k in unexpected} }. Shared logic belongs "
            "in services/ with research importing down into it - see "
            "services/segment_scoring.py.")

    def test_the_exception_list_has_no_stale_entries(self):
        # If someone does the outstanding move, this fails and the list -
        # and the paragraph in docs/architecture.md - get cleaned up.
        still_true = {
            path.stem for path in SERVICES.glob("*.py")
            if _research_imports(path)
        }
        assert KNOWN_SERVICE_IMPORTS_OF_RESEARCH <= still_true, (
            "KNOWN_SERVICE_IMPORTS_OF_RESEARCH names a service that no longer "
            "imports research/. Remove it, and update docs/architecture.md.")

    def test_scoring_maths_lives_in_services(self):
        from tapesift.services import segment_scoring

        # research re-exports these; the definitions are the service's.
        from tapesift.research import segmentation_benchmark

        assert (segmentation_benchmark.score_segments
                is segment_scoring.score_segments)


class TestTheResearchModesAreNotOnTheStartupPath:
    """Importing the bootstrap must not drag the review sessions in.

    A subprocess, because by the time this test file runs the modules are
    long since in sys.modules from other tests.
    """

    @pytest.mark.parametrize("forbidden", [
        "tapesift.research.run_pass_temporal_review",
        "tapesift.research.segmentation_benchmark",
    ])
    def test_importing_the_app_does_not_import(self, forbidden):
        code = (
            "import sys\n"
            "import tapesift.app_v2\n"
            f"print({forbidden!r} in sys.modules)\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, capture_output=True,
            text=True, timeout=300,
            env={"QT_QPA_PLATFORM": "offscreen", **_clean_env()})
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "False", (
            f"{forbidden} is imported when the app starts")


def _clean_env() -> dict:
    import os
    return {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
