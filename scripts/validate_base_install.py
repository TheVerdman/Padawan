"""Check an installed base package in an isolated interpreter, without research workloads.

Run with a fresh base-only virtual environment's Python and -I, outside the checkout.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import padawan
from padawan.artifacts.factory import build_artifact_backend
from padawan.atlas.dependencies import coding_dependency
from padawan.config.composition import build_live_application
from padawan.config.settings import Settings
from padawan.interaction.composition import build_interaction_application
from padawan.pprl.composition import build_pprl_application


def main() -> None:
    assert padawan.__file__ is not None
    assert Path(padawan.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    for module in ("google_crc32c", "yaml", "pyarrow", "transformers"):
        assert importlib.util.find_spec(module) is None, f"base environment includes {module}"
    assert all(
        callable(root)
        for root in (build_live_application, build_interaction_application, build_pprl_application)
    )
    for dependency in ("yaml", "pyarrow.parquet", "transformers"):
        try:
            coding_dependency(dependency)
        except RuntimeError as error:
            assert "pip install" in str(error) and "coding" in str(error)
        else:
            raise AssertionError("missing coding feature did not identify its required extra")
    with TemporaryDirectory(prefix="padawan-base-smoke-") as directory:
        settings = Settings(_env_file=None, artifact_root=Path(directory))
        assert build_artifact_backend(settings).backend_name == "local"
        settings = settings.model_copy(update={"artifact_backend": "gcs", "gcs_bucket": "fixture"})
        try:
            build_artifact_backend(settings)
        except RuntimeError as error:
            assert "gcs" in str(error) and "optional dependency" in str(error)
        else:
            raise AssertionError("missing GCS feature did not identify its required extra")
        subprocess.run(
            [sys.executable, "-I", "-c", "from padawan.cli.app import main; main()", "--help"],
            cwd=directory,
            check=True,
            stdout=subprocess.DEVNULL,
        )
    print(json.dumps({"installed_base": True, "optional_dependencies_absent": True, "cli": True}))


if __name__ == "__main__":
    main()
