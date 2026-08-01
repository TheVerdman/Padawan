from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from padawan.artifacts.store import LocalArtifactStore
from padawan.models.contracts import (
    AttemptRecord,
    Capability,
    CapabilityAvailability,
    ChannelSpan,
    SamplingConfiguration,
)
from tests.helpers import runtime_capabilities


def _attempt(tmp_path, **updates: object) -> AttemptRecord:
    artifacts = LocalArtifactStore(tmp_path)
    payload: dict[str, object] = {
        "attempt_id": "attempt",
        "episode_id": "episode",
        "item_id": "item",
        "state_before_id": "state",
        "rendered_messages": [],
        "rendered_prompt": "prompt",
        "raw_generation_ref": artifacts.put_bytes(
            b"raw-response", media_type="application/json", restricted=True, raw_data=True
        ).model_dump(mode="json"),
        "output_token_ids": [1, 2, 3, 4],
        "token_logprobs": [-0.1, -0.2, -0.3, -0.4],
        "channel_spans": [
            {
                "channel": "public_derivation",
                "start_token": 0,
                "end_token": 3,
                "source": "runtime",
            },
            {
                "channel": "final_answer",
                "start_token": 3,
                "end_token": 4,
                "source": "runtime",
            },
        ],
        "public_derivation": {"steps": [], "final_answer": {}},
        "final_answer": "answer",
        "timing_ms": {"total": 1.0},
        "stop_reason": "completed",
        "request_id": "request",
        "model_id": "model",
        "checkpoint_id": "checkpoint",
        "runtime_id": "runtime",
        "runtime_version": "1",
        "sampling": SamplingConfiguration(
            temperature=0.0,
            top_p=1.0,
            max_output_tokens=100,
            seed=1,
            top_logprobs=1,
            stop=(),
        ).model_dump(mode="json"),
        "capabilities": runtime_capabilities().model_dump(mode="json"),
        "created_at": datetime.now(UTC).isoformat(),
    }
    payload.update(updates)
    return AttemptRecord.model_validate(payload, strict=False)


def test_token_ids_logprobs_and_public_channels_align(tmp_path) -> None:
    attempt = _attempt(tmp_path)
    assert attempt.output_token_ids == (1, 2, 3, 4)
    assert attempt.token_logprobs == (-0.1, -0.2, -0.3, -0.4)
    assert attempt.capabilities.private_reasoning.availability == (
        CapabilityAvailability.UNAVAILABLE
    )
    assert attempt.private_reasoning_ref is None


@pytest.mark.parametrize(
    "updates",
    [
        {"token_logprobs": [-0.1]},
        {
            "channel_spans": [
                {
                    "channel": "public_derivation",
                    "start_token": 0,
                    "end_token": 5,
                    "source": "runtime",
                }
            ]
        },
        {
            "channel_spans": [
                {
                    "channel": "private_reasoning",
                    "start_token": 0,
                    "end_token": 3,
                    "source": "provider",
                },
                {
                    "channel": "public_derivation",
                    "start_token": 2,
                    "end_token": 4,
                    "source": "provider",
                },
            ]
        },
    ],
)
def test_misaligned_or_conflated_channels_are_rejected(tmp_path, updates) -> None:
    with pytest.raises(ValidationError):
        _attempt(tmp_path, **updates)


def test_private_trace_requires_available_capability_and_separate_artifact(tmp_path) -> None:
    artifacts = LocalArtifactStore(tmp_path / "private")
    private_ref = artifacts.put_text("private", restricted=True, raw_data=True)
    with pytest.raises(ValidationError, match="contradicts runtime capability"):
        _attempt(tmp_path, private_reasoning_ref=private_ref.model_dump(mode="json"))
    available = runtime_capabilities().model_copy(
        update={
            "private_reasoning": Capability(
                availability=CapabilityAvailability.AVAILABLE,
                reason="runtime exposed a separately bounded private channel",
            )
        }
    )
    attempt = _attempt(
        tmp_path,
        private_reasoning_ref=private_ref.model_dump(mode="json"),
        capabilities=available.model_dump(mode="json"),
        channel_spans=[
            ChannelSpan(
                channel="private_reasoning",
                start_token=0,
                end_token=2,
                source="runtime",
            ).model_dump(mode="json"),
            ChannelSpan(
                channel="public_derivation",
                start_token=2,
                end_token=4,
                source="runtime",
            ).model_dump(mode="json"),
        ],
    )
    assert attempt.private_reasoning_ref == private_ref


def test_sampling_configuration_requires_complete_output_budget() -> None:
    with pytest.raises(ValidationError):
        SamplingConfiguration.model_validate({"temperature": 0.0})
