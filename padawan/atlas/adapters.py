"""Model-neutral, evidence-first adapters for Capability Atlas.

Atlas adapters do not invoke a model.  They translate an already captured response or
environment trace into the authoritative verifier contracts that Padawan already owns.
Readiness is deliberately fail closed: configured capability metadata is never accepted as
modality-validation or environment evidence.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Protocol, runtime_checkable

from pydantic import Field, ValidationError, model_validator

from padawan.atlas.contracts import (
    AdapterDescriptor,
    AdapterKind,
    AuthorityKind,
    Modality,
    ModalityGateStatus,
    ModalityValidationEvidence,
    Probability,
    content_id,
)
from padawan.domains.contracts import VerifierDisposition, VerifierResult
from padawan.domains.lean_math.verifier import (
    LeanProofTask,
    LeanVerifier,
    LeanVerifierConfigurationError,
)
from padawan.domains.legal.appellate.contracts import (
    AppellateCourtPack,
    AppellateScenarioManifest,
    AppellateSemanticAssessment,
    AppellateSubmission,
    AuthorityCurrentnessAssessment,
    SemanticAssessmentMethod,
)
from padawan.domains.legal.appellate.verifier import AppellateBriefVerifier
from padawan.domains.magellan_improvement.contracts import (
    MagellanAgentTrace,
    MagellanEnvironmentAssessment,
    MagellanScenarioManifest,
    MagellanWorldSnapshot,
)
from padawan.domains.magellan_improvement.verifier import MagellanScenarioVerifier
from padawan.domains.temporal_grounding.contracts import TemporalScenarioManifest
from padawan.domains.temporal_grounding.verifier import TemporalPolicyVerifier
from padawan.grading.algebra import AlgebraGrader
from padawan.models.contracts import (
    CorpusItemRecord,
    GradeOutcome,
    NonEmpty,
    Sha256,
    StrictRecord,
)
from padawan.models.hashing import sha256_digest
from padawan.temporal.contracts import TemporalDecision


class AdapterReadinessState(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"
    REGISTRATION_ONLY = "registration_only"


class AdapterNotReadyError(RuntimeError):
    """The requested adapter cannot honestly execute with the supplied evidence."""


class AdapterReadinessContext(StrictRecord):
    """Evidence presented when deciding whether an adapter may execute.

    ``configured_modalities`` is retained only so reports can explain the difference between
    configuration and validation.  It never satisfies a modality gate.
    """

    required_modalities: Annotated[tuple[Modality, ...], Field(min_length=1)]
    modality_gates: tuple[ModalityValidationEvidence, ...]
    configured_modalities: tuple[Modality, ...] = ()

    @model_validator(mode="after")
    def values_are_canonical(self) -> AdapterReadinessContext:
        for values, label in (
            (self.required_modalities, "required modalities"),
            (self.configured_modalities, "configured modalities"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")
            if tuple(sorted(values, key=lambda value: value.value)) != values:
                raise ValueError(f"{label} must use canonical lexical order")
        gate_modalities = tuple(gate.modality for gate in self.modality_gates)
        if len(gate_modalities) != len(set(gate_modalities)):
            raise ValueError("modality readiness gates must be unique")
        if tuple(sorted(gate_modalities, key=lambda value: value.value)) != gate_modalities:
            raise ValueError("modality readiness gates must use canonical lexical order")
        return self


class AdapterReadiness(StrictRecord):
    adapter_id: NonEmpty
    adapter_version: NonEmpty
    state: AdapterReadinessState
    blocking_reasons: tuple[NonEmpty, ...]
    context_digest: Sha256
    evidence_digest: Sha256

    @model_validator(mode="after")
    def state_matches_reasons(self) -> AdapterReadiness:
        if self.state == AdapterReadinessState.READY and self.blocking_reasons:
            raise ValueError("a ready adapter cannot retain blockers")
        if self.state != AdapterReadinessState.READY and not self.blocking_reasons:
            raise ValueError("a non-ready adapter requires blockers")
        identity = self.model_dump(mode="json", exclude={"evidence_digest"})
        if self.evidence_digest != sha256_digest(identity):
            raise ValueError("adapter readiness digest disagrees with its evidence")
        return self

    @property
    def ready(self) -> bool:
        return self.state == AdapterReadinessState.READY

    def require_ready(self) -> None:
        if not self.ready:
            raise AdapterNotReadyError("; ".join(self.blocking_reasons))


class AdapterVerifierEvidence(StrictRecord):
    authority: AuthorityKind
    result: VerifierResult


class AdapterEvaluation(StrictRecord):
    """Verifier output before orchestration binds request, response, and artifact records."""

    evaluation_id: NonEmpty
    adapter_id: NonEmpty
    adapter_version: NonEmpty
    disposition: VerifierDisposition
    score: Probability | None
    success: bool | None
    primary_authority: AuthorityKind | None
    evidence: tuple[AdapterVerifierEvidence, ...]
    failure_codes: tuple[NonEmpty, ...] = ()
    evidence_digest: Sha256

    @model_validator(mode="after")
    def outcome_is_authoritative(self) -> AdapterEvaluation:
        observed = self.disposition in {
            VerifierDisposition.VERIFIED,
            VerifierDisposition.REJECTED,
        }
        if observed != (self.score is not None and self.success is not None):
            raise ValueError("only verified or rejected evaluations receive a model score")
        if self.disposition == VerifierDisposition.VERIFIED and self.success is not True:
            raise ValueError("verified adapter evaluation requires success=true")
        if self.disposition == VerifierDisposition.REJECTED and self.success is not False:
            raise ValueError("rejected adapter evaluation requires success=false")
        if observed != (self.primary_authority is not None):
            raise ValueError("observed adapter outcomes require exactly one primary authority")
        authorities = {item.authority for item in self.evidence}
        result_ids = [item.result.result_id for item in self.evidence]
        if len(result_ids) != len(set(result_ids)):
            raise ValueError("adapter verifier-result IDs must be unique")
        if self.primary_authority is not None and self.primary_authority not in authorities:
            raise ValueError("primary authority is absent from adapter evidence")
        non_model = {
            AuthorityKind.DETERMINISTIC,
            AuthorityKind.KERNEL,
            AuthorityKind.ENVIRONMENT,
        }
        if self.primary_authority == AuthorityKind.MODEL and authorities & non_model:
            raise ValueError("model grading cannot outrank deterministic authority")
        identity = self.model_dump(mode="json", exclude={"evaluation_id", "evidence_digest"})
        if self.evidence_digest != sha256_digest(identity):
            raise ValueError("adapter evaluation digest disagrees with its evidence")
        if self.evaluation_id != content_id("atlas-evaluation", identity):
            raise ValueError("adapter evaluation ID disagrees with its evidence")
        return self


@runtime_checkable
class AtlasAdapter(Protocol):
    """Common readiness surface for executable and registration-only adapters."""

    descriptor: AdapterDescriptor

    def readiness(self, context: AdapterReadinessContext) -> AdapterReadiness: ...


def _descriptor(
    *,
    adapter_id: str,
    version: str,
    kind: AdapterKind,
    authority_order: tuple[AuthorityKind, ...],
    supports_tools: bool = False,
    supports_interaction: bool = False,
    supports_repeated_trials: bool = True,
    supported_modalities: tuple[Modality, ...] = (Modality.TEXT,),
) -> AdapterDescriptor:
    identity = {
        "adapter_id": adapter_id,
        "version": version,
        "kind": kind,
        "authority_order": authority_order,
        "supports_tools": supports_tools,
        "supports_interaction": supports_interaction,
        "supports_repeated_trials": supports_repeated_trials,
        "supported_modalities": supported_modalities,
        "contract": "padawan.atlas.adapters.v1",
    }
    return AdapterDescriptor(
        adapter_id=adapter_id,
        version=version,
        kind=kind,
        supported_modalities=supported_modalities,
        authority_order=authority_order,
        deterministic_authority_required=bool(
            {AuthorityKind.DETERMINISTIC, AuthorityKind.KERNEL, AuthorityKind.ENVIRONMENT}
            & set(authority_order)
        ),
        supports_tools=supports_tools,
        supports_interaction=supports_interaction,
        supports_repeated_trials=supports_repeated_trials,
        implementation_digest=sha256_digest(identity),
    )


def _readiness(
    descriptor: AdapterDescriptor,
    context: AdapterReadinessContext,
    *,
    extra_blockers: tuple[str, ...] = (),
    registration_only: bool = False,
) -> AdapterReadiness:
    blockers = list(extra_blockers)
    unsupported = sorted(set(context.required_modalities) - set(descriptor.supported_modalities))
    blockers.extend(f"unsupported modality: {modality.value}" for modality in unsupported)
    gates = {gate.modality: gate for gate in context.modality_gates}
    for modality in context.required_modalities:
        gate = gates.get(modality)
        if gate is None:
            blockers.append(f"missing validation gate for modality: {modality.value}")
        elif gate.status != ModalityGateStatus.PASSED:
            blockers.append(f"modality gate is not passed: {modality.value}={gate.status.value}")
    canonical_blockers = tuple(dict.fromkeys(blockers))
    if registration_only:
        state = AdapterReadinessState.REGISTRATION_ONLY
    else:
        state = AdapterReadinessState.BLOCKED if canonical_blockers else AdapterReadinessState.READY
    context_digest = sha256_digest(context)
    identity = {
        "adapter_id": descriptor.adapter_id,
        "adapter_version": descriptor.version,
        "state": state,
        "blocking_reasons": canonical_blockers,
        "context_digest": context_digest,
    }
    return AdapterReadiness(
        adapter_id=descriptor.adapter_id,
        adapter_version=descriptor.version,
        state=state,
        blocking_reasons=canonical_blockers,
        context_digest=context_digest,
        evidence_digest=sha256_digest(identity),
    )


def _evaluation(
    *,
    descriptor: AdapterDescriptor,
    disposition: VerifierDisposition,
    score: float | None,
    success: bool | None,
    primary_authority: AuthorityKind | None,
    evidence: tuple[AdapterVerifierEvidence, ...],
    failure_codes: tuple[str, ...] = (),
) -> AdapterEvaluation:
    identity = {
        "adapter_id": descriptor.adapter_id,
        "adapter_version": descriptor.version,
        "disposition": disposition,
        "score": score,
        "success": success,
        "primary_authority": primary_authority,
        "evidence": evidence,
        "failure_codes": failure_codes,
    }
    digest = sha256_digest(identity)
    return AdapterEvaluation(
        evaluation_id=content_id("atlas-evaluation", identity),
        adapter_id=descriptor.adapter_id,
        adapter_version=descriptor.version,
        disposition=disposition,
        score=score,
        success=success,
        primary_authority=primary_authority,
        evidence=evidence,
        failure_codes=failure_codes,
        evidence_digest=digest,
    )


def _verifier_result(
    *,
    verifier_id: str,
    verifier_version: str,
    scope: str,
    disposition: VerifierDisposition,
    summary: str,
    evidence: dict[str, Any],
    created_at: datetime,
) -> VerifierResult:
    identity = {
        "verifier_id": verifier_id,
        "verifier_version": verifier_version,
        "scope": scope,
        "disposition": disposition,
        "summary": summary,
        "evidence": evidence,
    }
    return VerifierResult(
        result_id=content_id("atlas-verifier", identity),
        verifier_id=verifier_id,
        verifier_version=verifier_version,
        scope=scope,
        disposition=disposition,
        deterministic=True,
        summary=summary,
        evidence=evidence,
        created_at=created_at,
    )


def _evaluation_from_result(
    *,
    descriptor: AdapterDescriptor,
    result: VerifierResult,
    authority: AuthorityKind,
    failure_code: str,
) -> AdapterEvaluation:
    observed = result.disposition in {
        VerifierDisposition.VERIFIED,
        VerifierDisposition.REJECTED,
    }
    verified = result.disposition == VerifierDisposition.VERIFIED
    return _evaluation(
        descriptor=descriptor,
        disposition=result.disposition,
        score=(1.0 if verified else 0.0) if observed else None,
        success=verified if observed else None,
        primary_authority=authority if observed else None,
        evidence=(AdapterVerifierEvidence(authority=authority, result=result),),
        failure_codes=() if verified else (failure_code,),
    )


class StaticQANormalization(StrEnum):
    EXACT = "exact"
    TRIM = "trim"
    TRIM_CASEFOLD = "trim_casefold"
    COLLAPSE_WHITESPACE_CASEFOLD = "collapse_whitespace_casefold"


class StaticQAOracle(StrictRecord):
    item_id: NonEmpty
    accepted_answers: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    normalization: StaticQANormalization
    oracle_digest: Sha256

    @model_validator(mode="after")
    def answers_are_bound(self) -> StaticQAOracle:
        normalized = tuple(
            _normalize_static_answer(answer, self.normalization) for answer in self.accepted_answers
        )
        if len(normalized) != len(set(normalized)):
            raise ValueError("static-QA accepted answers must remain unique after normalization")
        identity = self.model_dump(mode="json", exclude={"oracle_digest"})
        if self.oracle_digest != sha256_digest(identity):
            raise ValueError("static-QA oracle digest disagrees with its accepted answers")
        return self


def build_static_qa_oracle(
    *,
    item_id: str,
    accepted_answers: tuple[str, ...],
    normalization: StaticQANormalization = StaticQANormalization.TRIM_CASEFOLD,
) -> StaticQAOracle:
    identity = {
        "item_id": item_id,
        "accepted_answers": accepted_answers,
        "normalization": normalization,
    }
    return StaticQAOracle(
        item_id=item_id,
        accepted_answers=accepted_answers,
        normalization=normalization,
        oracle_digest=sha256_digest(identity),
    )


def _normalize_static_answer(value: str, normalization: StaticQANormalization) -> str:
    if normalization == StaticQANormalization.EXACT:
        return value
    if normalization == StaticQANormalization.TRIM:
        return value.strip()
    if normalization == StaticQANormalization.TRIM_CASEFOLD:
        return value.strip().casefold()
    return " ".join(value.split()).casefold()


class StaticQAAdapter:
    """Deterministically evaluate already-captured static-QA responses."""

    descriptor = _descriptor(
        adapter_id="atlas.static_qa",
        version="1.0.0",
        kind=AdapterKind.STATIC_QA,
        authority_order=(AuthorityKind.DETERMINISTIC,),
    )

    def readiness(self, context: AdapterReadinessContext) -> AdapterReadiness:
        return _readiness(self.descriptor, context)

    def evaluate(
        self,
        *,
        oracle: StaticQAOracle,
        response: str,
        context: AdapterReadinessContext,
        created_at: datetime | None = None,
    ) -> AdapterEvaluation:
        self.readiness(context).require_ready()
        normalized_response = _normalize_static_answer(response, oracle.normalization)
        normalized_answers = tuple(
            _normalize_static_answer(answer, oracle.normalization)
            for answer in oracle.accepted_answers
        )
        accepted = normalized_response in normalized_answers
        result = _verifier_result(
            verifier_id="atlas.static_qa.exact_match",
            verifier_version="1.0.0",
            scope=oracle.item_id,
            disposition=(
                VerifierDisposition.VERIFIED if accepted else VerifierDisposition.REJECTED
            ),
            summary=(
                "static-QA response matched the bound answer oracle"
                if accepted
                else "static-QA response did not match the bound answer oracle"
            ),
            evidence={
                "oracle_digest": oracle.oracle_digest,
                "response_digest": sha256_digest(response),
                "normalized_response_digest": sha256_digest(normalized_response),
                "normalization": oracle.normalization.value,
            },
            created_at=created_at or datetime.now(UTC),
        )
        return _evaluation_from_result(
            descriptor=self.descriptor,
            result=result,
            authority=AuthorityKind.DETERMINISTIC,
            failure_code="static_qa_answer_mismatch",
        )


class ContextTaskKind(StrEnum):
    EXACT_RETRIEVAL = "exact_retrieval"
    STATE_USE = "state_use"
    STRATEGY = "strategy"


class ContextTaskReadinessEvidence(StrictRecord):
    task_id: NonEmpty
    task_kind: ContextTaskKind
    oracle_digest: Sha256 | None = None
    environment_fingerprint: Sha256 | None = None
    verifier_id: NonEmpty | None = None
    verifier_version: NonEmpty | None = None
    evidence_digest: Sha256

    @model_validator(mode="after")
    def authority_matches_task_kind(self) -> ContextTaskReadinessEvidence:
        if self.task_kind == ContextTaskKind.EXACT_RETRIEVAL:
            if self.oracle_digest is None:
                raise ValueError("exact retrieval requires a bound deterministic oracle")
            if any(
                value is not None
                for value in (
                    self.environment_fingerprint,
                    self.verifier_id,
                    self.verifier_version,
                )
            ):
                raise ValueError("exact retrieval cannot masquerade as state-use evidence")
        else:
            if self.oracle_digest is not None:
                raise ValueError("state-use and strategy tasks cannot use retrieval-only oracles")
            if any(
                value is None
                for value in (
                    self.environment_fingerprint,
                    self.verifier_id,
                    self.verifier_version,
                )
            ):
                raise ValueError("state-use and strategy tasks require environment authority")
        identity = self.model_dump(mode="json", exclude={"evidence_digest"})
        if self.evidence_digest != sha256_digest(identity):
            raise ValueError("context-task readiness digest disagrees with its authority")
        return self


def build_context_task_evidence(
    *,
    task_id: str,
    task_kind: ContextTaskKind,
    oracle_digest: str | None = None,
    environment_fingerprint: str | None = None,
    verifier_id: str | None = None,
    verifier_version: str | None = None,
) -> ContextTaskReadinessEvidence:
    identity = {
        "task_id": task_id,
        "task_kind": task_kind,
        "oracle_digest": oracle_digest,
        "environment_fingerprint": environment_fingerprint,
        "verifier_id": verifier_id,
        "verifier_version": verifier_version,
    }
    return ContextTaskReadinessEvidence(
        task_id=task_id,
        task_kind=task_kind,
        oracle_digest=oracle_digest,
        environment_fingerprint=environment_fingerprint,
        verifier_id=verifier_id,
        verifier_version=verifier_version,
        evidence_digest=sha256_digest(identity),
    )


class ContextMemoryAdapter:
    """Keep exact retrieval separate from long-horizon state use and strategy."""

    descriptor = _descriptor(
        adapter_id="atlas.context_memory",
        version="1.0.0",
        kind=AdapterKind.CONTEXT_MEMORY,
        authority_order=(AuthorityKind.ENVIRONMENT, AuthorityKind.DETERMINISTIC),
        supports_interaction=True,
    )

    def readiness(
        self,
        context: AdapterReadinessContext,
        *,
        task_evidence: ContextTaskReadinessEvidence | None = None,
    ) -> AdapterReadiness:
        blockers = (
            ("context task has no bound oracle or environment-authority evidence",)
            if task_evidence is None
            else ()
        )
        return _readiness(self.descriptor, context, extra_blockers=blockers)

    def evaluate_exact_retrieval(
        self,
        *,
        task_evidence: ContextTaskReadinessEvidence,
        oracle: StaticQAOracle,
        response: str,
        context: AdapterReadinessContext,
        created_at: datetime | None = None,
    ) -> AdapterEvaluation:
        self.readiness(context, task_evidence=task_evidence).require_ready()
        if task_evidence.task_kind != ContextTaskKind.EXACT_RETRIEVAL:
            raise AdapterNotReadyError("state-use tasks cannot be scored as exact retrieval")
        if (
            task_evidence.task_id != oracle.item_id
            or task_evidence.oracle_digest != oracle.oracle_digest
        ):
            raise AdapterNotReadyError("context retrieval oracle differs from readiness evidence")
        normalized_response = _normalize_static_answer(response, oracle.normalization)
        accepted = normalized_response in {
            _normalize_static_answer(answer, oracle.normalization)
            for answer in oracle.accepted_answers
        }
        result = _verifier_result(
            verifier_id="atlas.context.exact_retrieval",
            verifier_version="1.0.0",
            scope=task_evidence.task_id,
            disposition=(
                VerifierDisposition.VERIFIED if accepted else VerifierDisposition.REJECTED
            ),
            summary=(
                "context item was exactly retrieved"
                if accepted
                else "context item was not exactly retrieved"
            ),
            evidence={
                "task_kind": task_evidence.task_kind.value,
                "task_evidence_digest": task_evidence.evidence_digest,
                "oracle_digest": oracle.oracle_digest,
                "response_digest": sha256_digest(response),
            },
            created_at=created_at or datetime.now(UTC),
        )
        return _evaluation_from_result(
            descriptor=self.descriptor,
            result=result,
            authority=AuthorityKind.DETERMINISTIC,
            failure_code="exact_retrieval_failure",
        )

    def evaluate_state_use(
        self,
        *,
        task_evidence: ContextTaskReadinessEvidence,
        verifier_result: VerifierResult,
        context: AdapterReadinessContext,
    ) -> AdapterEvaluation:
        self.readiness(context, task_evidence=task_evidence).require_ready()
        if task_evidence.task_kind == ContextTaskKind.EXACT_RETRIEVAL:
            raise AdapterNotReadyError("exact retrieval cannot stand in for state-use evidence")
        if verifier_result.scope != task_evidence.task_id:
            raise AdapterNotReadyError("context verifier result belongs to another task")
        if (
            verifier_result.verifier_id != task_evidence.verifier_id
            or verifier_result.verifier_version != task_evidence.verifier_version
        ):
            raise AdapterNotReadyError("context verifier differs from readiness evidence")
        return _evaluation_from_result(
            descriptor=self.descriptor,
            result=verifier_result,
            authority=AuthorityKind.ENVIRONMENT,
            failure_code=f"context_{task_evidence.task_kind.value}_failure",
        )


class EnvironmentReadinessEvidence(StrictRecord):
    environment_fingerprint: Sha256
    verifier_id: NonEmpty
    verifier_version: NonEmpty
    evidence_digest: Sha256

    @model_validator(mode="after")
    def content_is_bound(self) -> EnvironmentReadinessEvidence:
        identity = self.model_dump(mode="json", exclude={"evidence_digest"})
        if self.evidence_digest != sha256_digest(identity):
            raise ValueError("environment readiness digest disagrees with its evidence")
        return self


def build_environment_readiness_evidence(
    *, environment_fingerprint: str, verifier_id: str, verifier_version: str
) -> EnvironmentReadinessEvidence:
    identity = {
        "environment_fingerprint": environment_fingerprint,
        "verifier_id": verifier_id,
        "verifier_version": verifier_version,
    }
    return EnvironmentReadinessEvidence(**identity, evidence_digest=sha256_digest(identity))


class CodingAgenticAdapter:
    """Verify captured coding/agentic outcomes without driving an external environment."""

    descriptor = _descriptor(
        adapter_id="atlas.coding_agentic",
        version="1.0.0",
        kind=AdapterKind.CODING_AGENTIC,
        authority_order=(AuthorityKind.ENVIRONMENT, AuthorityKind.KERNEL),
        supports_tools=True,
        supports_interaction=True,
    )

    def readiness(
        self,
        context: AdapterReadinessContext,
        *,
        environment_evidence: EnvironmentReadinessEvidence | None = None,
    ) -> AdapterReadiness:
        blockers = (
            ("coding/agentic environment evidence is missing",)
            if environment_evidence is None
            else ()
        )
        return _readiness(self.descriptor, context, extra_blockers=blockers)

    def evaluate(
        self,
        *,
        verifier_result: VerifierResult,
        environment_evidence: EnvironmentReadinessEvidence,
        context: AdapterReadinessContext,
    ) -> AdapterEvaluation:
        self.readiness(context, environment_evidence=environment_evidence).require_ready()
        if (
            verifier_result.verifier_id != environment_evidence.verifier_id
            or verifier_result.verifier_version != environment_evidence.verifier_version
        ):
            raise AdapterNotReadyError("coding verifier differs from environment evidence")
        return _evaluation_from_result(
            descriptor=self.descriptor,
            result=verifier_result,
            authority=AuthorityKind.ENVIRONMENT,
            failure_code="coding_agentic_verification_failure",
        )


class MultimodalGateAdapter:
    """Admit captured grounding results only after every media gate has passed."""

    def __init__(
        self,
        *,
        grounding_verifier_id: str,
        grounding_verifier_version: str,
        supported_modalities: tuple[Modality, ...],
    ) -> None:
        if not ({Modality.IMAGE, Modality.AUDIO, Modality.VIDEO} & set(supported_modalities)):
            raise ValueError("multimodal adapters require at least one non-text modality")
        if (
            tuple(sorted(supported_modalities, key=lambda value: value.value))
            != supported_modalities
        ):
            raise ValueError("multimodal adapter modalities must use canonical lexical order")
        self.grounding_verifier_id = grounding_verifier_id
        self.grounding_verifier_version = grounding_verifier_version
        self.descriptor = _descriptor(
            adapter_id="atlas.multimodal_gate",
            version="1.0.0",
            kind=AdapterKind.MULTIMODAL,
            authority_order=(
                AuthorityKind.ENVIRONMENT,
                AuthorityKind.DETERMINISTIC,
                AuthorityKind.HUMAN,
            ),
            supported_modalities=supported_modalities,
        )

    def readiness(self, context: AdapterReadinessContext) -> AdapterReadiness:
        return _readiness(self.descriptor, context)

    def evaluate(
        self,
        *,
        verifier_result: VerifierResult,
        authority: AuthorityKind,
        context: AdapterReadinessContext,
    ) -> AdapterEvaluation:
        self.readiness(context).require_ready()
        if authority not in {
            AuthorityKind.ENVIRONMENT,
            AuthorityKind.DETERMINISTIC,
            AuthorityKind.HUMAN,
        }:
            raise AdapterNotReadyError("model-only grading cannot establish media grounding")
        if (
            verifier_result.verifier_id != self.grounding_verifier_id
            or verifier_result.verifier_version != self.grounding_verifier_version
        ):
            raise AdapterNotReadyError("grounding verifier differs from adapter registration")
        return _evaluation_from_result(
            descriptor=self.descriptor,
            result=verifier_result,
            authority=authority,
            failure_code="multimodal_grounding_failure",
        )


class AlgebraAdapter:
    descriptor = _descriptor(
        adapter_id="atlas.algebra",
        version="1.0.0",
        kind=AdapterKind.GENERATED_VERIFIER,
        authority_order=(AuthorityKind.DETERMINISTIC,),
    )

    def __init__(self, grader: AlgebraGrader | None = None) -> None:
        self.grader = grader or AlgebraGrader()

    def readiness(self, context: AdapterReadinessContext) -> AdapterReadiness:
        return _readiness(self.descriptor, context)

    def evaluate(
        self,
        *,
        item: CorpusItemRecord,
        attempt_id: str,
        response: str | bytes | dict[str, Any],
        context: AdapterReadinessContext,
        created_at: datetime | None = None,
    ) -> AdapterEvaluation:
        self.readiness(context).require_ready()
        if item.expected_answer is None:
            raise AdapterNotReadyError("algebra item has no authoritative expected answer")
        if item.verifier_spec.verifier_type != self.grader.grader_type:
            raise AdapterNotReadyError("algebra item is bound to a different verifier")
        if item.verifier_spec.verifier_version != self.grader.grader_version:
            raise AdapterNotReadyError("algebra item is bound to a different verifier version")
        grade = self.grader.grade(
            attempt_id=attempt_id,
            response=response,
            expected_answer=item.expected_answer,
        )
        normalized_grade = grade.model_dump(mode="json", exclude={"grade_id", "created_at"})
        disposition = {
            GradeOutcome.CORRECT: VerifierDisposition.VERIFIED,
            GradeOutcome.INCORRECT: VerifierDisposition.REJECTED,
            GradeOutcome.PARTIAL: VerifierDisposition.REJECTED,
            GradeOutcome.INVALID_PROCESS: VerifierDisposition.REJECTED,
            GradeOutcome.MALFORMED: VerifierDisposition.REJECTED,
            GradeOutcome.INFRASTRUCTURE_FAILURE: VerifierDisposition.INFRASTRUCTURE_FAILURE,
            GradeOutcome.GRADER_FAILURE: VerifierDisposition.UNKNOWN,
        }[grade.outcome]
        observed = disposition in {
            VerifierDisposition.VERIFIED,
            VerifierDisposition.REJECTED,
        }
        result = _verifier_result(
            verifier_id=grade.grader_type,
            verifier_version=grade.grader_version,
            scope=item.item_id,
            disposition=disposition,
            summary=f"authoritative algebra grade: {grade.outcome.value}",
            evidence={
                "item_id": item.item_id,
                "expected_answer_digest": sha256_digest(item.expected_answer),
                "grade": normalized_grade,
            },
            created_at=created_at or datetime.now(UTC),
        )
        failure_codes = (
            (grade.error_class or grade.outcome.value,)
            if disposition != VerifierDisposition.VERIFIED
            else ()
        )
        return _evaluation(
            descriptor=self.descriptor,
            disposition=disposition,
            score=grade.score if observed else None,
            success=(grade.outcome == GradeOutcome.CORRECT) if observed else None,
            primary_authority=AuthorityKind.DETERMINISTIC if observed else None,
            evidence=(
                AdapterVerifierEvidence(
                    authority=AuthorityKind.DETERMINISTIC,
                    result=result,
                ),
            ),
            failure_codes=failure_codes,
        )


class TemporalAdapter:
    descriptor = _descriptor(
        adapter_id="atlas.temporal",
        version="1.0.0",
        kind=AdapterKind.CONTEXT_MEMORY,
        authority_order=(AuthorityKind.DETERMINISTIC,),
        supports_interaction=True,
    )

    def __init__(self, verifier: TemporalPolicyVerifier | None = None) -> None:
        self.verifier = verifier or TemporalPolicyVerifier()

    def readiness(self, context: AdapterReadinessContext) -> AdapterReadiness:
        return _readiness(self.descriptor, context)

    def evaluate(
        self,
        *,
        scenario: TemporalScenarioManifest,
        response: TemporalDecision | str | bytes | dict[str, Any],
        context: AdapterReadinessContext,
        created_at: datetime | None = None,
    ) -> AdapterEvaluation:
        self.readiness(context).require_ready()
        timestamp = created_at or datetime.now(UTC)
        try:
            if isinstance(response, TemporalDecision):
                decision = response
            elif isinstance(response, (str, bytes)):
                decision = TemporalDecision.model_validate_json(response)
            else:
                decision = TemporalDecision.model_validate(response, strict=False)
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
            result = _verifier_result(
                verifier_id=self.verifier.verifier_id,
                verifier_version=self.verifier.verifier_version,
                scope=scenario.scenario_id,
                disposition=VerifierDisposition.REJECTED,
                summary="temporal decision did not satisfy the required schema",
                evidence={"stage": "parse", "error": str(exc)},
                created_at=timestamp,
            )
            return _evaluation(
                descriptor=self.descriptor,
                disposition=VerifierDisposition.REJECTED,
                score=0.0,
                success=False,
                primary_authority=AuthorityKind.DETERMINISTIC,
                evidence=(
                    AdapterVerifierEvidence(
                        authority=AuthorityKind.DETERMINISTIC,
                        result=result,
                    ),
                ),
                failure_codes=("malformed_temporal_decision",),
            )
        result = self.verifier.verify(
            scenario=scenario,
            decision=decision,
            created_at=timestamp,
        )
        verified = result.disposition == VerifierDisposition.VERIFIED
        observed = result.disposition in {
            VerifierDisposition.VERIFIED,
            VerifierDisposition.REJECTED,
        }
        score_value = result.evidence.get("score")
        score = float(score_value) if observed and isinstance(score_value, (int, float)) else None
        return _evaluation(
            descriptor=self.descriptor,
            disposition=result.disposition,
            score=score,
            success=verified if observed else None,
            primary_authority=AuthorityKind.DETERMINISTIC if observed else None,
            evidence=(
                AdapterVerifierEvidence(
                    authority=AuthorityKind.DETERMINISTIC,
                    result=result,
                ),
            ),
            failure_codes=() if verified else ("temporal_policy_failure",),
        )


class AppellateAdapter:
    descriptor = _descriptor(
        adapter_id="atlas.appellate",
        version="1.0.0",
        kind=AdapterKind.GENERATED_VERIFIER,
        authority_order=(
            AuthorityKind.DETERMINISTIC,
            AuthorityKind.HUMAN,
            AuthorityKind.MODEL,
        ),
    )

    def __init__(self, verifier: AppellateBriefVerifier | None = None) -> None:
        self.verifier = verifier or AppellateBriefVerifier()

    def readiness(self, context: AdapterReadinessContext) -> AdapterReadiness:
        return _readiness(self.descriptor, context)

    def evaluate(
        self,
        *,
        pack: AppellateCourtPack,
        scenario: AppellateScenarioManifest,
        submission: AppellateSubmission,
        context: AdapterReadinessContext,
        semantic_assessment: AppellateSemanticAssessment | None = None,
        currentness_assessments: tuple[AuthorityCurrentnessAssessment, ...] = (),
        created_at: datetime | None = None,
    ) -> AdapterEvaluation:
        self.readiness(context).require_ready()
        bundle = self.verifier.verify(
            pack=pack,
            scenario=scenario,
            submission=submission,
            semantic_assessment=semantic_assessment,
            currentness_assessments=currentness_assessments,
            created_at=created_at,
        )
        semantic_authority = AuthorityKind.HUMAN
        if (
            semantic_assessment is not None
            and semantic_assessment.method != SemanticAssessmentMethod.HUMAN
        ):
            semantic_authority = AuthorityKind.MODEL
        evidence = tuple(
            AdapterVerifierEvidence(
                authority=(
                    AuthorityKind.DETERMINISTIC if result.deterministic else semantic_authority
                ),
                result=result,
            )
            for result in bundle.verifier_results
        )
        hard_failure = any(not gate.passed for gate in bundle.hard_gates)
        if hard_failure:
            return _evaluation(
                descriptor=self.descriptor,
                disposition=VerifierDisposition.REJECTED,
                score=0.0,
                success=False,
                primary_authority=AuthorityKind.DETERMINISTIC,
                evidence=evidence,
                failure_codes=("appellate_hard_gate_failure",),
            )
        semantic_results = tuple(item for item in evidence if not item.result.deterministic)
        if semantic_assessment is None or any(
            item.result.disposition == VerifierDisposition.UNKNOWN for item in semantic_results
        ):
            return _evaluation(
                descriptor=self.descriptor,
                disposition=VerifierDisposition.UNKNOWN,
                score=None,
                success=None,
                primary_authority=None,
                evidence=evidence,
                failure_codes=("appellate_semantic_authority_missing",),
            )
        if semantic_authority == AuthorityKind.MODEL:
            return _evaluation(
                descriptor=self.descriptor,
                disposition=VerifierDisposition.UNKNOWN,
                score=None,
                success=None,
                primary_authority=None,
                evidence=evidence,
                failure_codes=("model_semantic_assessment_is_supplemental",),
            )
        return _evaluation(
            descriptor=self.descriptor,
            disposition=(
                VerifierDisposition.VERIFIED
                if bundle.task_verified
                else VerifierDisposition.REJECTED
            ),
            score=1.0 if bundle.task_verified else 0.0,
            success=bundle.task_verified,
            primary_authority=AuthorityKind.HUMAN,
            evidence=evidence,
            failure_codes=() if bundle.task_verified else ("appellate_semantic_failure",),
        )


class MagellanAdapter:
    descriptor = _descriptor(
        adapter_id="atlas.magellan",
        version="1.0.0",
        kind=AdapterKind.INTERACTIVE_ENVIRONMENT,
        authority_order=(AuthorityKind.ENVIRONMENT, AuthorityKind.DETERMINISTIC),
        supports_tools=True,
        supports_interaction=True,
    )

    def __init__(self, verifier: MagellanScenarioVerifier | None = None) -> None:
        self.verifier = verifier or MagellanScenarioVerifier()

    def readiness(
        self,
        context: AdapterReadinessContext,
        *,
        environment: MagellanEnvironmentAssessment | None = None,
    ) -> AdapterReadiness:
        blockers: tuple[str, ...] = ()
        if environment is None:
            blockers = ("Magellan environment assessment is missing",)
        elif not environment.ready:
            blockers = environment.blockers or ("Magellan environment is not ready",)
        return _readiness(self.descriptor, context, extra_blockers=blockers)

    def evaluate(
        self,
        *,
        scenario: MagellanScenarioManifest,
        environment: MagellanEnvironmentAssessment,
        before: MagellanWorldSnapshot,
        after: MagellanWorldSnapshot,
        trace: MagellanAgentTrace,
        context: AdapterReadinessContext,
        created_at: datetime | None = None,
    ) -> AdapterEvaluation:
        self.readiness(context, environment=environment).require_ready()
        bundle = self.verifier.verify(
            scenario=scenario,
            environment=environment,
            before=before,
            after=after,
            trace=trace,
            created_at=created_at,
        )
        evidence = tuple(
            AdapterVerifierEvidence(authority=AuthorityKind.ENVIRONMENT, result=result)
            for result in bundle.verifier_results
        )
        infrastructure = any(
            result.disposition == VerifierDisposition.INFRASTRUCTURE_FAILURE
            for result in bundle.verifier_results
        )
        if infrastructure:
            return _evaluation(
                descriptor=self.descriptor,
                disposition=VerifierDisposition.INFRASTRUCTURE_FAILURE,
                score=None,
                success=None,
                primary_authority=None,
                evidence=evidence,
                failure_codes=("magellan_infrastructure_failure",),
            )
        return _evaluation(
            descriptor=self.descriptor,
            disposition=(
                VerifierDisposition.VERIFIED
                if bundle.task_verified and all(gate.passed for gate in bundle.hard_gates)
                else VerifierDisposition.REJECTED
            ),
            score=1.0
            if bundle.task_verified and all(gate.passed for gate in bundle.hard_gates)
            else 0.0,
            success=bundle.task_verified and all(gate.passed for gate in bundle.hard_gates),
            primary_authority=AuthorityKind.ENVIRONMENT,
            evidence=evidence,
            failure_codes=() if bundle.task_verified else ("magellan_task_failure",),
        )


class LeanAdapter:
    descriptor = _descriptor(
        adapter_id="atlas.lean",
        version="1.0.0",
        kind=AdapterKind.GENERATED_VERIFIER,
        authority_order=(AuthorityKind.KERNEL, AuthorityKind.DETERMINISTIC),
    )

    def __init__(self, verifier: LeanVerifier) -> None:
        self.verifier = verifier

    def readiness(self, context: AdapterReadinessContext) -> AdapterReadiness:
        blockers: tuple[str, ...] = ()
        try:
            self.verifier.environment_snapshot()
        except (OSError, subprocess.SubprocessError, LeanVerifierConfigurationError) as exc:
            blockers = (f"Lean verifier environment unavailable: {exc}",)
        return _readiness(self.descriptor, context, extra_blockers=blockers)

    def evaluate(
        self,
        *,
        item: CorpusItemRecord,
        proof: str,
        context: AdapterReadinessContext,
    ) -> AdapterEvaluation:
        self.readiness(context).require_ready()
        statement = item.verifier_spec.parameters.get("statement")
        if not isinstance(statement, str) or not statement:
            raise AdapterNotReadyError("Lean item has no bound theorem statement")
        result = self.verifier.verify(
            LeanProofTask(task_id=item.item_id, statement=statement, proof=proof)
        )
        kernel_executed = result.evidence.get("kernel_executed") is True
        authority = AuthorityKind.KERNEL if kernel_executed else AuthorityKind.DETERMINISTIC
        observed = result.disposition in {
            VerifierDisposition.VERIFIED,
            VerifierDisposition.REJECTED,
        }
        return _evaluation(
            descriptor=self.descriptor,
            disposition=result.disposition,
            score=(1.0 if result.disposition == VerifierDisposition.VERIFIED else 0.0)
            if observed
            else None,
            success=(result.disposition == VerifierDisposition.VERIFIED) if observed else None,
            primary_authority=authority if observed else None,
            evidence=(AdapterVerifierEvidence(authority=authority, result=result),),
            failure_codes=()
            if result.disposition == VerifierDisposition.VERIFIED
            else ("lean_verification_failure",),
        )


class UnavailableRegistrationAdapter:
    """Honest placeholder for a benchmark whose content or execution is unavailable."""

    def __init__(
        self,
        *,
        adapter_id: str,
        version: str,
        kind: AdapterKind,
        reason: str,
        supported_modalities: tuple[Modality, ...] = (Modality.TEXT,),
    ) -> None:
        if not reason:
            raise ValueError("registration-only adapters require an unavailable reason")
        self.reason = reason
        self.descriptor = _descriptor(
            adapter_id=adapter_id,
            version=version,
            kind=kind,
            authority_order=(AuthorityKind.DETERMINISTIC,),
            supports_repeated_trials=False,
            supported_modalities=supported_modalities,
        )

    def readiness(self, context: AdapterReadinessContext) -> AdapterReadiness:
        return _readiness(
            self.descriptor,
            context,
            extra_blockers=(self.reason,),
            registration_only=True,
        )

    def evaluate(self, *_args: object, **_kwargs: object) -> AdapterEvaluation:
        raise AdapterNotReadyError(self.reason)


def builtin_adapter_descriptors() -> tuple[AdapterDescriptor, ...]:
    """Return the code-shipped, content-addressed adapter implementations Atlas may execute."""

    return tuple(
        sorted(
            (
                StaticQAAdapter.descriptor,
                ContextMemoryAdapter.descriptor,
                CodingAgenticAdapter.descriptor,
                AlgebraAdapter.descriptor,
                TemporalAdapter.descriptor,
                AppellateAdapter.descriptor,
                MagellanAdapter.descriptor,
                LeanAdapter.descriptor,
            ),
            key=lambda descriptor: (descriptor.adapter_id, descriptor.version),
        )
    )
