"""Inspect an explicitly supplied, pinned local Metal runtime without launching it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def runtime_inventory(config: dict[str, Any]) -> dict[str, Any]:
    lab = Path(config["lab_path"])
    upstream = lab / ".upstreams/vllm-metal"
    python = upstream / ".venv-vllm-metal/bin/python"
    head = subprocess.check_output(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(upstream), "status", "--porcelain", "--untracked-files=no"], text=True
    ).strip()
    if head != config["vllm_metal_revision"] or dirty:
        raise ValueError("local serving source is not the pinned clean runtime")
    expression = (
        "import importlib.metadata as m,json; "
        "names=" + repr(list(config["runtime_packages"])) + "; "
        "print(json.dumps({'packages':{n:m.version(n) for n in names},"
        "'mlx_lm_origin':json.loads(m.distribution('mlx-lm').read_text('direct_url.json'))}))"
    )
    inventory = json.loads(subprocess.check_output([str(python), "-c", expression], text=True))
    if inventory["packages"] != config["runtime_packages"]:
        raise ValueError("installed runtime packages differ from the frozen local condition")
    if inventory["mlx_lm_origin"].get("vcs_info", {}).get("commit_id") != config["mlx_lm_revision"]:
        raise ValueError("MLX-LM source revision differs")
    return {"python": str(python), "upstream": str(upstream), "head": head, **inventory}
