"""Explicitly authored teaching input; this does not simulate model inference."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from padawan.adapters.base import GenerationRequest, GenerationResult
from padawan.models.contracts import Capability, CapabilityAvailability, RuntimeCapabilities
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.teaching.contracts import TeacherOutput


class AuthoredTeacherClient:
    provider = "local-authored-feedback"
    model_id = "codex-session-authored-teacher-v1"

    def __init__(self, root: Path, *, timeout_seconds: float = 300) -> None:
        self.root = root
        self.timeout_seconds = timeout_seconds
        root.mkdir(parents=True, exist_ok=True, mode=0o700)

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        if request.schema_name != "padawan_teacher_intervention":
            raise ValueError("authored teacher accepts only teacher interventions")
        context = json.loads(str(request.input))
        reasoning = context.get("private_reasoning")
        if reasoning is not None and not (
            isinstance(reasoning, dict)
            and reasoning.get("availability") == "unavailable"
            and set(reasoning) <= {"availability", "reason"}
        ):
            raise ValueError("authored teacher accepts public evidence only")
        request_digest = sha256_digest(request)
        stem = sha256_digest(request.request_id)[7:]
        prompt_path = self.root / f"{stem}.request.json"
        response_path = self.root / f"{stem}.response.json"
        prompt_bytes = canonical_json_bytes(
            {
                "request_digest": request_digest,
                "request_id": request.request_id,
                "context": context,
                "output_schema": request.json_schema,
            }
        )
        if prompt_path.exists() and prompt_path.read_bytes() != prompt_bytes:
            raise ValueError("authored teacher request ID changed content")
        if not prompt_path.exists():
            prompt_path.write_bytes(prompt_bytes)
        started = time.monotonic()
        while not response_path.exists():
            if time.monotonic() - started >= self.timeout_seconds:
                raise TimeoutError("authored feedback was not supplied within its wait budget")
            await asyncio.sleep(0.2)
        raw_response = response_path.read_bytes()
        response = json.loads(raw_response)
        if response.get("request_digest") != request_digest:
            raise ValueError("authored feedback does not bind the exact teacher request")
        if not response.get("author") or not response.get("evidence_review_note"):
            raise ValueError("authored feedback requires author and public evidence review")
        output = TeacherOutput.model_validate(response["output"])
        unavailable = Capability(
            availability=CapabilityAvailability.UNAVAILABLE,
            reason="Authored file input; no inference server or model telemetry.",
        )
        return GenerationResult(
            request_id=request.request_id,
            response_id=sha256_digest(raw_response),
            provider=self.provider,
            model_id=self.model_id,
            protocol="authored_file",
            output_text=output.model_dump_json(),
            raw_request=request.model_dump_json().encode(),
            raw_response=raw_response,
            usage={},
            token_ids=None,
            token_logprobs=None,
            private_reasoning=None,
            reasoning_summary=None,
            finish_reason="completed",
            latency_ms=(time.monotonic() - started) * 1000,
            capabilities=RuntimeCapabilities(
                responses_api=unavailable,
                streaming=unavailable,
                cancellation=unavailable,
                logprobs=unavailable,
                token_ids=unavailable,
                private_reasoning=unavailable,
                reasoning_boundaries=unavailable,
                gpu_telemetry=unavailable,
                router_telemetry=unavailable,
            ),
            provider_metadata={
                "authored_feedback": True,
                "author": response["author"],
                "evidence_review_note": response["evidence_review_note"],
                "request_digest": request_digest,
                "response_digest": sha256_digest(raw_response),
            },
        )
