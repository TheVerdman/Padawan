from __future__ import annotations

import pytest

from padawan.adapters.inkling import INKLING_SMALL_AMPERE
from padawan.config.composition import _validate_live_configuration
from padawan.config.settings import Settings


def _validate(settings: Settings, *, allow_legacy: bool = False) -> None:
    _validate_live_configuration(
        settings,
        student_provider="inkling",
        teacher_provider="openai",
        student_model=None,
        teacher_model=None,
        compatible_base_url=None,
        allow_legacy_student_fallback=allow_legacy,
    )


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "inkling_base_url": "https://inkling.example",
        "inkling_model": INKLING_SMALL_AMPERE.served_model_name,
        "inkling_edge_image_digest": f"sha256:{'d' * 64}",
        "inkling_edge_deployment_revision": "inkling-edge-00004-test",
        "INKLING_API_KEY": "edge-secret",
        "openai_model": "teacher-model",
        "OPENAI_API_KEY": "teacher-secret",
    }
    if "inkling_api_key" in updates:
        values["INKLING_API_KEY"] = updates.pop("inkling_api_key")
    values.update(updates)
    return Settings(**values)  # type: ignore[arg-type]


def test_validated_inkling_live_configuration_accepts_authenticated_edge() -> None:
    _validate(_settings())


def test_remote_inkling_edge_requires_bearer_credential() -> None:
    with pytest.raises(ValueError, match="requires INKLING_API_KEY"):
        _validate(_settings(inkling_api_key=None))


def test_inkling_edge_requires_versioned_artifact_identity() -> None:
    with pytest.raises(ValueError, match="EDGE_IMAGE_DIGEST"):
        _validate(
            _settings(
                inkling_edge_image_digest=None,
                inkling_edge_deployment_revision=None,
            )
        )


def test_inkling_live_configuration_rejects_unvalidated_identity_and_fallback() -> None:
    with pytest.raises(ValueError, match="requires model"):
        _validate(_settings(inkling_model="different-model"))
    with pytest.raises(ValueError, match="only for compatible"):
        _validate(_settings(), allow_legacy=True)
