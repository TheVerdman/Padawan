from __future__ import annotations

from padawan.adapters.inkling.contract import INKLING_SMALL_AMPERE
from padawan.config.settings import Settings
from padawan.domains.builtin import build_builtin_domain_registry
from padawan.experiments.defaults import build_standardized_developmental_control
from padawan.models.contracts import ResearchRole
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import IdentityEvidenceStatus


def test_validated_inkling_profile_keeps_capture_continuation_and_compaction_separate() -> None:
    settings = Settings(
        code_revision="4bd45ef",
        openai_model="teacher-test-model",
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
    assert control.student_model.runtime_parameters["tensor_parallel_size"] == "4"
    assert control.student_model.runtime_parameters["batch_size"] == "1"
    assert control.environment_parameters["padawan_code_revision"] == "4bd45ef"
    assert "dependency.pydantic" in control.environment_parameters
    assert control.profile.instrumentation.capability_atlas_schema is None
    assert control.profile.instrumentation.interactive_trajectory_schema is None
    assert control.profile.instrumentation.mechanistic_telemetry_schema is None
    assert control.profile.instrumentation.checkpoint_evaluation_schema is None
