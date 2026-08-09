from __future__ import annotations

import json

from padawan.models.hashing import canonical_json_bytes
from padawan.temporal.contracts import TemporalFrame

TEMPORAL_CONTEXT_BEGIN = '<padawan_temporal_context version="1.0.0">'
TEMPORAL_CONTEXT_END = "</padawan_temporal_context>"


def render_temporal_frame(frame: TemporalFrame) -> str:
    payload = canonical_json_bytes(frame.model_dump(mode="json")).decode("utf-8")
    if TEMPORAL_CONTEXT_BEGIN in payload or TEMPORAL_CONTEXT_END in payload:
        raise ValueError("temporal context payload contains a reserved sentinel")
    return f"{TEMPORAL_CONTEXT_BEGIN}\n{payload}\n{TEMPORAL_CONTEXT_END}"


def parse_temporal_frame(value: str) -> TemporalFrame:
    if not value.startswith(f"{TEMPORAL_CONTEXT_BEGIN}\n") or not value.endswith(
        f"\n{TEMPORAL_CONTEXT_END}"
    ):
        raise ValueError("temporal context frame has invalid sentinels")
    payload = value[len(TEMPORAL_CONTEXT_BEGIN) + 1 : -(len(TEMPORAL_CONTEXT_END) + 1)]
    if TEMPORAL_CONTEXT_BEGIN in payload or TEMPORAL_CONTEXT_END in payload:
        raise ValueError("temporal context payload contains a reserved sentinel")
    parsed = json.loads(payload)
    return TemporalFrame.model_validate(parsed, strict=False)
