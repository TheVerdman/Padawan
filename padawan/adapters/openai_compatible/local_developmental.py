"""Explicit prompt-schema transport for non-paged local Metal development.

The server does not enforce the schema. The developmental workflow still validates
the public answer and grades malformed output normally. No response repair occurs.
"""

from __future__ import annotations

import json

from padawan.adapters.base import GenerationRequest, GenerationResult
from padawan.adapters.openai_compatible.client import OpenAICompatibleClient
from padawan.adapters.openai_compatible.local_metal import LocalMetalClient
from padawan.adapters.prepared import PreparedGeneration
from padawan.models.hashing import canonical_json_bytes, sha256_digest


class LocalDevelopmentalClient(LocalMetalClient):
    async def generate(
        self, request: GenerationRequest, *, stream: bool = False
    ) -> GenerationResult:
        # Native developmental calls use generate(), whereas Atlas dispatches
        # generate_prepared(). Both must send the same configured body bytes.
        if stream:
            raise ValueError("local development requires a single non-streaming response")
        return await self.generate_prepared(request, self.prepare_generation(request))

    def prepare_generation(self, request: GenerationRequest) -> PreparedGeneration:
        if request.store or request.previous_response_id or request.tools:
            raise ValueError("local development requires stateless, tool-free requests")
        prepared = OpenAICompatibleClient.prepare_generation(self, request)
        body = json.loads(prepared.body_json)
        body.pop("text", None)
        if request.json_schema:
            body["instructions"] = (
                request.instructions
                + "\nReturn one JSON object, without Markdown fences, matching this schema:\n"
                + json.dumps(request.json_schema, sort_keys=True)
            )
        body["chat_template_kwargs"] = {
            "enable_thinking": False,
            "truncate_history_thinking": True,
        }
        encoded = canonical_json_bytes(body)
        return prepared.model_copy(
            update={
                "body_json": encoded.decode(),
                "body_digest": sha256_digest(encoded),
                "configuration_digest": sha256_digest(
                    {
                        "adapter": "padawan.local-developmental.prompt-schema.v1",
                        "native": prepared.configuration_digest,
                        "schema_enforcement": "workflow-validation-only",
                        "enable_thinking": False,
                    }
                ),
            }
        )
