from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from padawan.atlas.adapters import (
    AdapterEvaluation,
    AdapterNotReadyError,
    AdapterReadinessContext,
    AdapterReadinessState,
    AdapterVerifierEvidence,
    AlgebraAdapter,
    AppellateAdapter,
    CodingAgenticAdapter,
    ContextMemoryAdapter,
    ContextTaskKind,
    LeanAdapter,
    MagellanAdapter,
    MultimodalGateAdapter,
    StaticQAAdapter,
    TemporalAdapter,
    UnavailableRegistrationAdapter,
    build_context_task_evidence,
    build_environment_readiness_evidence,
    build_static_qa_oracle,
)
from padawan.atlas.contracts import (
    AdapterKind,
    AuthorityKind,
    Modality,
    ModalityGateStatus,
    ModalityValidationEvidence,
    content_id,
)
from padawan.corpus.algebra import AlgebraCorpusGenerator, AlgebraFamily
from padawan.domains.contracts import VerifierDisposition, VerifierResult
from padawan.domains.lean_math import LeanVerifier
from padawan.domains.legal.appellate import (
    AppellateCorpusGenerator,
    AppellateScenarioFamily,
    SemanticAssessmentMethod,
    build_fourth_circuit_pack,
)
from padawan.domains.temporal_grounding import (
    TemporalGroundingCorpusGenerator,
    TemporalScenarioFamily,
    TemporalScenarioManifest,
)
from padawan.models.contracts import CorpusPool
from padawan.models.hashing import sha256_digest
from padawan.temporal import TemporalDecision
from tests.appellate_helpers import build_semantic_assessment, build_submission
from tests.helpers import public_derivation

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _gate(modality: Modality = Modality.TEXT) -> ModalityValidationEvidence:
    return ModalityValidationEvidence(
        modality=modality,
        status=ModalityGateStatus.PASSED,
        gate_id=f"gate-{modality.value}",
        gate_revision="1.0.0",
        evidence_digest=sha256_digest(f"validated-{modality.value}"),
        evidence_refs=(f"artifact-{modality.value}",),
        validated_at=NOW,
    )


def _text_context() -> AdapterReadinessContext:
    return AdapterReadinessContext(
        required_modalities=(Modality.TEXT,),
        modality_gates=(_gate(),),
    )


def test_configured_modality_metadata_cannot_replace_gate_evidence() -> None:
    context = AdapterReadinessContext(
        required_modalities=(Modality.TEXT,),
        modality_gates=(),
        configured_modalities=(Modality.TEXT,),
    )

    readiness = AlgebraAdapter().readiness(context)

    assert readiness.state == AdapterReadinessState.BLOCKED
    assert "missing validation gate for modality: text" in readiness.blocking_reasons
    with pytest.raises(AdapterNotReadyError, match="missing validation gate"):
        readiness.require_ready()


def test_unavailable_benchmark_stays_registration_only() -> None:
    adapter = UnavailableRegistrationAdapter(
        adapter_id="atlas.gpqa.registration",
        version="1.0.0",
        kind=AdapterKind.STATIC_QA,
        reason="dataset access and redistribution review are incomplete",
    )

    readiness = adapter.readiness(_text_context())

    assert readiness.state == AdapterReadinessState.REGISTRATION_ONLY
    with pytest.raises(AdapterNotReadyError, match="redistribution review"):
        adapter.evaluate()


def test_public_adapter_surface_covers_all_six_required_families() -> None:
    multimodal = MultimodalGateAdapter(
        grounding_verifier_id="media.grounding",
        grounding_verifier_version="1.0.0",
        supported_modalities=(Modality.IMAGE, Modality.TEXT),
    )

    kinds = {
        StaticQAAdapter.descriptor.kind,
        AlgebraAdapter.descriptor.kind,
        CodingAgenticAdapter.descriptor.kind,
        MagellanAdapter.descriptor.kind,
        ContextMemoryAdapter.descriptor.kind,
        multimodal.descriptor.kind,
    }

    assert kinds == set(AdapterKind)


def test_static_qa_and_context_memory_keep_retrieval_separate_from_state_use() -> None:
    oracle = build_static_qa_oracle(
        item_id="context-item",
        accepted_answers=("The retained value",),
    )
    exact_evidence = build_context_task_evidence(
        task_id="context-item",
        task_kind=ContextTaskKind.EXACT_RETRIEVAL,
        oracle_digest=oracle.oracle_digest,
    )
    static = StaticQAAdapter().evaluate(
        oracle=oracle,
        response=" the retained VALUE ",
        context=_text_context(),
        created_at=NOW,
    )
    retrieval = ContextMemoryAdapter().evaluate_exact_retrieval(
        task_evidence=exact_evidence,
        oracle=oracle,
        response="the retained value",
        context=_text_context(),
        created_at=NOW,
    )
    assert static.success is True
    assert retrieval.success is True
    assert retrieval.primary_authority == AuthorityKind.DETERMINISTIC

    state_evidence = build_context_task_evidence(
        task_id="state-task",
        task_kind=ContextTaskKind.STATE_USE,
        environment_fingerprint=sha256_digest("state-environment"),
        verifier_id="state.environment",
        verifier_version="1.0.0",
    )
    state_result = VerifierResult(
        result_id="state-result",
        verifier_id="state.environment",
        verifier_version="1.0.0",
        scope="state-task",
        disposition=VerifierDisposition.VERIFIED,
        deterministic=True,
        summary="strategy used retained state successfully",
        evidence={"final_state_digest": sha256_digest("final-state")},
        created_at=NOW,
    )
    state = ContextMemoryAdapter().evaluate_state_use(
        task_evidence=state_evidence,
        verifier_result=state_result,
        context=_text_context(),
    )
    assert state.success is True
    assert state.primary_authority == AuthorityKind.ENVIRONMENT
    with pytest.raises(AdapterNotReadyError, match="cannot stand in"):
        ContextMemoryAdapter().evaluate_state_use(
            task_evidence=exact_evidence,
            verifier_result=state_result,
            context=_text_context(),
        )


def test_coding_and_multimodal_surfaces_require_bound_environment_and_gate_evidence() -> None:
    coding = CodingAgenticAdapter()
    assert not coding.readiness(_text_context()).ready
    environment = build_environment_readiness_evidence(
        environment_fingerprint=sha256_digest("coding-environment"),
        verifier_id="coding.environment",
        verifier_version="1.0.0",
    )
    coding_result = VerifierResult(
        result_id="coding-result",
        verifier_id="coding.environment",
        verifier_version="1.0.0",
        scope="coding-task",
        disposition=VerifierDisposition.REJECTED,
        deterministic=True,
        summary="repository acceptance tests failed",
        evidence={"test_report_digest": sha256_digest("failed-tests")},
        created_at=NOW,
    )
    coding_evaluation = coding.evaluate(
        verifier_result=coding_result,
        environment_evidence=environment,
        context=_text_context(),
    )
    assert coding_evaluation.success is False
    assert coding_evaluation.primary_authority == AuthorityKind.ENVIRONMENT

    multimodal = MultimodalGateAdapter(
        grounding_verifier_id="media.grounding",
        grounding_verifier_version="1.0.0",
        supported_modalities=(Modality.IMAGE, Modality.TEXT),
    )
    configured_only = AdapterReadinessContext(
        required_modalities=(Modality.IMAGE, Modality.TEXT),
        modality_gates=(_gate(Modality.TEXT),),
        configured_modalities=(Modality.IMAGE, Modality.TEXT),
    )
    assert not multimodal.readiness(configured_only).ready
    assert (
        "missing validation gate for modality: image"
        in multimodal.readiness(configured_only).blocking_reasons
    )

    gated = AdapterReadinessContext(
        required_modalities=(Modality.IMAGE, Modality.TEXT),
        modality_gates=(_gate(Modality.IMAGE), _gate(Modality.TEXT)),
        configured_modalities=(Modality.IMAGE, Modality.TEXT),
    )
    grounding_result = VerifierResult(
        result_id="grounding-result",
        verifier_id="media.grounding",
        verifier_version="1.0.0",
        scope="media-item",
        disposition=VerifierDisposition.VERIFIED,
        deterministic=True,
        summary="grounding oracle verified the response",
        evidence={"media_digest": sha256_digest("image")},
        created_at=NOW,
    )
    assert (
        multimodal.evaluate(
            verifier_result=grounding_result,
            authority=AuthorityKind.ENVIRONMENT,
            context=gated,
        ).success
        is True
    )
    with pytest.raises(AdapterNotReadyError, match="model-only"):
        multimodal.evaluate(
            verifier_result=grounding_result,
            authority=AuthorityKind.MODEL,
            context=gated,
        )


def test_algebra_adapter_reuses_sympy_authority_without_calling_a_model() -> None:
    item = AlgebraCorpusGenerator().generate(
        pool=CorpusPool.CURRICULUM,
        seed=17,
        groups_per_family=1,
        siblings_per_group=2,
        families=(AlgebraFamily.DISTRIBUTION_SIGN,),
        created_at=NOW,
    )[0]
    expected = item.expected_answer
    assert expected is not None

    evaluation = AlgebraAdapter().evaluate(
        item=item,
        attempt_id="atlas-attempt-1",
        response=public_derivation(expected, correct=True),
        context=_text_context(),
        created_at=NOW,
    )

    assert evaluation.disposition == VerifierDisposition.VERIFIED
    assert evaluation.success is True
    assert evaluation.primary_authority == AuthorityKind.DETERMINISTIC
    assert evaluation.evidence[0].result.verifier_id == "symbolic_algebra"


def test_temporal_adapter_uses_event_time_oracle() -> None:
    item = TemporalGroundingCorpusGenerator().generate(
        pool=CorpusPool.CURRICULUM,
        seed=31,
        groups_per_family=1,
        siblings_per_group=2,
        families=(TemporalScenarioFamily.GAP_CONTINUITY,),
        created_at=NOW,
    )[0]
    expected = item.expected_answer
    assert expected is not None
    scenario = TemporalScenarioManifest.model_validate(
        item.verifier_spec.parameters["scenario"], strict=False
    )
    decision = TemporalDecision.model_validate(expected["decision"], strict=False)

    evaluation = TemporalAdapter().evaluate(
        scenario=scenario,
        response=decision,
        context=_text_context(),
        created_at=NOW,
    )

    assert evaluation.disposition == VerifierDisposition.VERIFIED
    assert evaluation.evidence[0].result.evidence["operational_time_used_as_scenario_time"] is False


def test_appellate_model_adjudication_remains_supplemental_to_hard_authority() -> None:
    scenario = AppellateCorpusGenerator().scenario(
        family=AppellateScenarioFamily.AMBIGUOUS_VIDEO,
        seed=31,
        split=CorpusPool.CURRICULUM.value,
        created_at=NOW,
    )
    submission = build_submission(scenario)
    assessment = build_semantic_assessment(scenario, submission).model_copy(
        update={
            "method": SemanticAssessmentMethod.GOVERNED_MODEL,
            "model_id": "supplemental-adjudicator",
        }
    )

    evaluation = AppellateAdapter().evaluate(
        pack=build_fourth_circuit_pack(),
        scenario=scenario,
        submission=submission,
        semantic_assessment=assessment,
        context=_text_context(),
        created_at=NOW,
    )

    assert evaluation.disposition == VerifierDisposition.UNKNOWN
    assert evaluation.primary_authority is None
    assert evaluation.failure_codes == ("model_semantic_assessment_is_supplemental",)
    assert {item.authority for item in evaluation.evidence} >= {
        AuthorityKind.DETERMINISTIC,
        AuthorityKind.MODEL,
    }


def test_adapter_evaluation_rejects_model_authority_over_deterministic_evidence() -> None:
    deterministic = VerifierResult(
        result_id="deterministic-result",
        verifier_id="deterministic.oracle",
        verifier_version="1.0.0",
        scope="item",
        disposition=VerifierDisposition.REJECTED,
        deterministic=True,
        summary="oracle rejected the answer",
        evidence={"accepted": False},
        created_at=NOW,
    )
    model = deterministic.model_copy(
        update={
            "result_id": "model-result",
            "verifier_id": "model.grader",
            "deterministic": False,
            "disposition": VerifierDisposition.VERIFIED,
            "summary": "model preferred the answer",
        }
    )
    evidence = (
        AdapterVerifierEvidence(authority=AuthorityKind.DETERMINISTIC, result=deterministic),
        AdapterVerifierEvidence(authority=AuthorityKind.MODEL, result=model),
    )
    identity = {
        "adapter_id": "atlas.test",
        "adapter_version": "1.0.0",
        "disposition": VerifierDisposition.VERIFIED,
        "score": 1.0,
        "success": True,
        "primary_authority": AuthorityKind.MODEL,
        "evidence": evidence,
        "failure_codes": (),
    }

    with pytest.raises(ValidationError, match="cannot outrank"):
        AdapterEvaluation(
            evaluation_id=content_id("atlas-evaluation", identity),
            adapter_id="atlas.test",
            adapter_version="1.0.0",
            disposition=VerifierDisposition.VERIFIED,
            score=1.0,
            success=True,
            primary_authority=AuthorityKind.MODEL,
            evidence=evidence,
            evidence_digest=sha256_digest(identity),
        )


def test_lean_and_magellan_readiness_fail_closed_without_environment_evidence(
    tmp_path: Path,
) -> None:
    verifier = LeanVerifier(
        project_root=tmp_path / "lean",
        lake_executable=tmp_path / "elan" / "lake",
        elan_home=tmp_path / "elan",
        sandbox_mode="off",
    )

    lean = LeanAdapter(verifier).readiness(_text_context())
    magellan = MagellanAdapter().readiness(_text_context())

    assert not lean.ready
    assert "Lean verifier environment unavailable" in lean.blocking_reasons[0]
    assert not magellan.ready
    assert magellan.blocking_reasons == ("Magellan environment assessment is missing",)
