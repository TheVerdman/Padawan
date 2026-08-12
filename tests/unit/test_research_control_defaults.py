from __future__ import annotations

from types import SimpleNamespace

from padawan.adapters.inkling.contract import INKLING_SMALL_AMPERE
from padawan.config.settings import Settings
from padawan.domains.builtin import build_builtin_domain_registry
from padawan.experiments.controls import research_corpus_digest
from padawan.experiments.defaults import (
    build_live_worker_configuration,
    build_standardized_developmental_control,
)
from padawan.models.contracts import ItemStatus, ResearchRole
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import IdentityEvidenceStatus


def test_validated_inkling_profile_keeps_capture_continuation_and_compaction_separate() -> None:
    settings = Settings(
        code_revision="4bd45ef",
        openai_model="teacher-test-model",
        inkling_edge_image_digest=f"sha256:{'d' * 64}",
        inkling_edge_deployment_revision="inkling-edge-00004-test",
    )
    domain = build_builtin_domain_registry().get_workflow("math.algebra")
    control = build_standardized_developmental_control(
        settings=settings,
        domain=domain.spec,
        student_provider="inkling",
        student_model_id=INKLING_SMALL_AMPERE.served_model_name,
        student_runtime_id="inkling-vllm",
        student_runtime_version=INKLING_SMALL_AMPERE.runtime_revision,
        student_checkpoint_id=INKLING_SMALL_AMPERE.checkpoint_id,
        student_role=ResearchRole.TARGET,
        student_base_url="http://127.0.0.1:8000",
        teacher_provider="openai",
        teacher_model_id="teacher-test-model",
        corpus_digest=sha256_digest("test-corpus"),
    )

    assert control.profile.tier == "standardized"
    assert control.profile.continuation.private_reasoning_capture_enabled
    assert not control.profile.continuation.response_storage_enabled
    assert not control.profile.continuation.previous_response_id_enabled
    assert not control.profile.continuation.reasoning_retention_enabled
    assert not control.profile.continuation.private_reasoning_used_as_context
    assert not control.profile.context.compaction_enabled
    assert control.profile.context.compactor is None
    assert control.harness_parameters["teacher_mode"] == "diagnostic_critique"
    assert control.harness_parameters["student_sampling_seed_source"] == ("item_generation_seed")
    assert control.harness_parameters["external_transport_max_attempts"] == "3"
    assert control.harness_parameters["treatment_condition"] == ("frontier_teacher_critique")
    assert (
        control.profile.context.effective_input_limit_tokens
        == INKLING_SMALL_AMPERE.maximum_verified_target_input_tokens
    )
    assert control.student_model.checkpoint.component_id == INKLING_SMALL_AMPERE.checkpoint_id
    assert control.student_model.quantization.evidence_status == IdentityEvidenceStatus.PINNED
    assert control.student_model.serving_artifact.digest == (
        INKLING_SMALL_AMPERE.serving_image_digest
    )
    assert control.student_model.transport_artifact is not None
    assert control.student_model.transport_artifact.digest == f"sha256:{'d' * 64}"
    assert (
        control.student_model.transport_artifact.evidence_status == IdentityEvidenceStatus.DECLARED
    )
    assert control.student_model.runtime_parameters["tensor_parallel_size"] == "4"
    assert control.student_model.runtime_parameters["batch_size"] == "1"
    assert control.environment_parameters["padawan_code_revision"] == "4bd45ef"
    assert "dependency.pydantic" in control.environment_parameters
    assert control.profile.instrumentation.capability_atlas_schema is None
    assert control.profile.instrumentation.interactive_trajectory_schema is None
    assert control.profile.instrumentation.mechanistic_telemetry_schema is None
    assert control.profile.instrumentation.checkpoint_evaluation_schema is None


def test_effective_transport_flags_and_inkling_endpoint_change_worker_identity() -> None:
    settings = Settings(
        code_revision="4bd45ef",
        openai_model="teacher-test-model",
        inkling_edge_image_digest=f"sha256:{'d' * 64}",
        inkling_edge_deployment_revision="inkling-edge-00004-test",
    )
    domain = build_builtin_domain_registry().get_workflow("math.algebra")
    compatible_common = {
        "settings": settings,
        "domain": domain.spec,
        "student_provider": "compatible",
        "student_model_id": "compatible-model",
        "student_runtime_id": "openai-compatible-responses",
        "student_runtime_version": "v1",
        "student_checkpoint_id": "compatible-model",
        "student_role": ResearchRole.TARGET,
        "student_base_url": "https://compatible.example",
        "teacher_provider": "openai",
        "teacher_model_id": "teacher-test-model",
    }
    responses_only = build_live_worker_configuration(
        **compatible_common,
        allow_legacy_student_fallback=False,
    )
    legacy_enabled = build_live_worker_configuration(
        **compatible_common,
        allow_legacy_student_fallback=True,
    )
    assert responses_only.student_model.runtime_parameters["legacy_fallback"] == "false"
    assert legacy_enabled.student_model.runtime_parameters["legacy_fallback"] == "true"
    assert responses_only.student_model != legacy_enabled.student_model

    inkling_common = {
        "settings": settings,
        "domain": domain.spec,
        "student_provider": "inkling",
        "student_model_id": INKLING_SMALL_AMPERE.served_model_name,
        "student_runtime_id": "inkling-vllm",
        "student_runtime_version": INKLING_SMALL_AMPERE.runtime_revision,
        "student_checkpoint_id": INKLING_SMALL_AMPERE.checkpoint_id,
        "student_role": ResearchRole.TARGET,
        "teacher_provider": "openai",
        "teacher_model_id": "teacher-test-model",
    }
    local_edge = build_live_worker_configuration(
        **inkling_common,
        student_base_url="http://127.0.0.1:8000",
    )
    remote_edge = build_live_worker_configuration(
        **inkling_common,
        student_base_url="https://inkling-edge.example",
    )
    assert local_edge.student_model.runtime_parameters["endpoint_origin"] == (
        "http://127.0.0.1:8000"
    )
    assert remote_edge.student_model.runtime_parameters["endpoint_origin"] == (
        "https://inkling-edge.example"
    )
    assert local_edge.student_model != remote_edge.student_model

    verified_edge = build_live_worker_configuration(
        **inkling_common,
        student_base_url="https://inkling-edge.example",
        inkling_edge_image_digest=f"sha256:{'d' * 64}",
        inkling_edge_deployment_revision="inkling-edge-00004-test",
        inkling_edge_identity_verified=True,
    )
    assert verified_edge.student_model.transport_artifact is not None
    assert (
        verified_edge.student_model.transport_artifact.evidence_status
        == IdentityEvidenceStatus.VERIFIED
    )
    assert verified_edge.student_model != remote_edge.student_model

    rotated_edge = build_live_worker_configuration(
        **{
            **inkling_common,
            "settings": settings.model_copy(
                update={
                    "inkling_edge_image_digest": f"sha256:{'e' * 64}",
                    "inkling_edge_deployment_revision": "inkling-edge-00005-test",
                }
            ),
        },
        student_base_url="https://inkling-edge.example",
    )
    assert remote_edge.student_model.transport_artifact is not None
    assert rotated_edge.student_model.transport_artifact is not None
    assert remote_edge.student_model.transport_artifact != (
        rotated_edge.student_model.transport_artifact
    )


def test_endpoint_identity_never_persists_url_credentials() -> None:
    settings = Settings(code_revision="4bd45ef", openai_model="teacher-test-model")
    domain = build_builtin_domain_registry().get_workflow("math.algebra")
    worker = build_live_worker_configuration(
        settings=settings,
        domain=domain.spec,
        student_provider="compatible",
        student_model_id="compatible-model",
        student_runtime_id="openai-compatible-responses",
        student_runtime_version="v1",
        student_checkpoint_id="compatible-model",
        student_role=ResearchRole.TARGET,
        student_base_url="https://user:secret@example.com/v1",
        teacher_provider="openai",
        teacher_model_id="teacher-test-model",
    )
    serialized = worker.student_model.model_dump_json()
    assert worker.student_model.runtime_parameters["endpoint_origin"] == "https://example.com"
    assert "user" not in serialized
    assert "secret" not in serialized


def test_corpus_identity_excludes_retired_and_quarantined_items() -> None:
    fields = {
        "item_id": "corpus-item-1",
        "competency_id": "algebra.linear",
        "template_family_id": "linear",
        "instance_group_id": "linear-group-1",
        "generation_seed": 17,
        "generator_version": "1",
        "difficulty": 0.5,
        "prompt": "redacted-test-prompt",
        "expected_answer": "redacted-test-answer",
        "verifier_spec": {"kind": "exact"},
        "pool": "curriculum",
        "source": "generated",
        "rights_digest": sha256_digest("rights"),
        "contamination_scope": "family",
    }
    active = SimpleNamespace(**fields, status=ItemStatus.ACTIVE.value)
    leased = SimpleNamespace(**fields, status=ItemStatus.LEASED.value)
    retired = SimpleNamespace(**fields, status=ItemStatus.RETIRED.value)
    quarantined = SimpleNamespace(**fields, status=ItemStatus.QUARANTINED.value)

    active_digest = research_corpus_digest([active])  # type: ignore[list-item]
    assert active_digest == research_corpus_digest([leased])  # type: ignore[list-item]
    assert active_digest != research_corpus_digest([retired])  # type: ignore[list-item]
    assert active_digest != research_corpus_digest([quarantined])  # type: ignore[list-item]
