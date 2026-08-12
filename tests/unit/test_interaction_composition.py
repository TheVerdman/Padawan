from __future__ import annotations

import pytest

from padawan.adapters.inkling import INKLING_SMALL_AMPERE
from padawan.config.settings import Settings
from padawan.interaction.composition import (
    _endpoint_origin,
    _endpoint_route,
    _inkling_target,
)


def test_model_neutral_serving_path_preserves_a_configured_route_prefix() -> None:
    base_url = "https://students.example/research-edge/"

    assert _endpoint_origin(base_url) == "https://students.example"
    assert _endpoint_route(base_url, "/v1/responses") == ("/research-edge/v1/responses")


def test_serving_path_rejects_url_credentials() -> None:
    with pytest.raises(ValueError, match="credentials"):
        _endpoint_origin("https://operator:secret@students.example")


def test_interaction_composition_reuses_fail_closed_inkling_admission() -> None:
    with pytest.raises(ValueError, match="EDGE_IMAGE_DIGEST"):
        _inkling_target(Settings(inkling_model=INKLING_SMALL_AMPERE.served_model_name))

    with pytest.raises(ValueError, match="authenticated Inkling Responses edge"):
        _inkling_target(
            Settings(
                inkling_base_url="https://inkling.example",
                inkling_model=INKLING_SMALL_AMPERE.served_model_name,
                inkling_edge_deployment_revision="edge-revision",
            )
        )
