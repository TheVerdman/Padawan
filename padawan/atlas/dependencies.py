"""Load coding extras only when an Atlas coding feature is selected."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Literal


def coding_dependency(name: Literal["yaml", "pyarrow.parquet", "transformers"]) -> ModuleType:
    try:
        return import_module(name)
    except ImportError as error:
        raise RuntimeError(
            f"Atlas coding requires the 'coding' optional dependency ({name}); "
            "install with: python -m pip install 'padawan-research[coding]' "
            "(from a checkout: python -m pip install '.[coding]')"
        ) from error
