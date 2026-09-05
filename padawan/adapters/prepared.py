"""Exact, privileged transport inputs; configuration identity is not attestation."""

from __future__ import annotations

import json
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import model_validator

from padawan.adapters.base import GenerationRequest, GenerationResult
from padawan.models.contracts import NonEmpty, Sha256, StrictRecord
from padawan.models.hashing import canonical_json_bytes, sha256_digest


class PreparedGeneration(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    request_id: NonEmpty
    request_digest: Sha256
    provider: NonEmpty
    model_id: NonEmpty
    protocol: NonEmpty
    transport: Literal["http", "in_process"]
    destination: NonEmpty | None
    configuration_digest: Sha256
    body_json: NonEmpty
    body_digest: Sha256

    @model_validator(mode="after")
    def body_and_destination_are_exact(self) -> PreparedGeneration:
        body = self.body_json.encode("utf-8")
        parsed = json.loads(body)
        if (
            not isinstance(parsed, dict)
            or canonical_json_bytes(parsed) != body
            or sha256_digest(body) != self.body_digest
        ):
            raise ValueError("prepared generation requires exact canonical JSON object bytes")
        if self.transport == "http":
            destination = urlsplit(self.destination or "")
            if (
                destination.scheme not in {"http", "https"}
                or not destination.hostname
                or destination.username is not None
                or destination.password is not None
                or destination.query
                or destination.fragment
            ):
                raise ValueError("prepared HTTP destination must be explicit and credential-free")
        elif self.destination is not None:
            raise ValueError("in-process transport cannot declare a network destination")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class PreparedGenerationClient(Protocol):
    """Trusted adapter contract. Implementations must not retry or change prepared effects."""

    async def generate(self, request: GenerationRequest) -> GenerationResult: ...

    def prepare_generation(self, request: GenerationRequest) -> PreparedGeneration: ...

    async def generate_prepared(
        self, request: GenerationRequest, prepared: PreparedGeneration
    ) -> GenerationResult: ...
