from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class InklingServingContract:
    """Exact Inkling-Small-Ampere serving identity accepted by Padawan."""

    service: str
    served_model_name: str
    checkpoint_id: str
    quantization: str
    conversion_manifest_sha256: str
    artifact_uri: str
    profile_id: str
    profile_sha256: str
    runtime_revision: str
    serving_image_digest: str
    tensor_parallel_size: int
    configured_max_model_len: int
    maximum_verified_target_input_tokens: int
    maximum_verified_actual_input_tokens: int
    request_timeout_seconds: int
    validation_record: str
    validation_repository_revision: str

    def quantization_manifest(self) -> dict[str, Any]:
        return {
            "availability": "validated",
            "profile_id": self.profile_id,
            "profile_sha256": self.profile_sha256,
            "quantization": self.quantization,
            "conversion_manifest_sha256": self.conversion_manifest_sha256,
            "artifact_uri": self.artifact_uri,
            "serving_image_digest": self.serving_image_digest,
            "configured_max_model_len": self.configured_max_model_len,
            "maximum_verified_target_input_tokens": (self.maximum_verified_target_input_tokens),
            "maximum_verified_actual_input_tokens": (self.maximum_verified_actual_input_tokens),
            "validation_record": self.validation_record,
            "validation_repository_revision": self.validation_repository_revision,
        }


INKLING_SMALL_AMPERE = InklingServingContract(
    service="inkling-small-ampere",
    served_model_name="w8a16-balanced-v1",
    checkpoint_id="conversion-e747e8121d5cd12c54c9",
    quantization="w8a16-balanced-v1",
    conversion_manifest_sha256=("210b62035668a17ba89ed08dc9eb224db2d6be48424a89cf655e341c23f38e71"),
    artifact_uri=(
        "gs://project-49b1b523-d248-434f-bd4-vecl-qb-artifacts/"
        "inkling-small-ampere/conversions/conversion-e747e8121d5cd12c54c9"
    ),
    profile_id="responses-256k-candidate-v1",
    profile_sha256="a8775671b01904501ce20cb167009e83400eaa15cbe007aa12e30510122ce718",
    runtime_revision="aa2e7dd0f8f5fd1be0e4449f802ae5b72ffc534a",
    serving_image_digest=(
        "sha256:5cd713ab404a051892e98f624858f2550e50781489fa916d47f971974c310575"
    ),
    tensor_parallel_size=4,
    configured_max_model_len=262_144,
    maximum_verified_target_input_tokens=240_000,
    maximum_verified_actual_input_tokens=239_997,
    request_timeout_seconds=3_600,
    validation_record="gate-e-production-context-validation-20260810",
    validation_repository_revision="236d50e3f7f388e37fd6f37389b7ce50b9bb491c",
)


def validate_responses_edge_url(base_url: str) -> None:
    """Reject URLs that cannot be used as an ordinary Responses base URL."""

    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Inkling base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Inkling base URL must not contain credentials, a query, or a fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("Inkling base URL must be an origin without a /v1 route suffix")
    if parsed.hostname.endswith(".prediction.vertexai.goog"):
        raise ValueError(
            "PADAWAN_INKLING_BASE_URL must select the authenticated Responses edge, "
            "not the Vertex dedicated-Endpoint Invoke DNS"
        )
    loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    if parsed.scheme != "https" and not loopback:
        raise ValueError("a non-loopback Inkling Responses edge must use HTTPS")
