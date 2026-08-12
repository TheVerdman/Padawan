from __future__ import annotations

import platform
from datetime import UTC, datetime
from importlib import metadata
from typing import Any, Literal
from urllib.parse import urlsplit

from padawan.adapters.inkling.contract import INKLING_SMALL_AMPERE
from padawan.domains.contracts import DomainSpec
from padawan.experiments.controls import (
    ResearchControlConfiguration,
    ResearchWorkerConfiguration,
)
from padawan.models.contracts import ResearchRole, TeacherMode
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    BudgetDisposition,
    BudgetLimit,
    ContextPolicy,
    ContinuationPolicy,
    HarnessBudgets,
    HarnessProfile,
    IdentityEvidenceStatus,
    ModelServingIdentity,
    ResearchInstrumentationSeams,
    TaskCorpusIdentity,
    VersionedComponentIdentity,
)

_PROFILE_RELEASED_AT = datetime(2026, 8, 12, tzinfo=UTC)


def build_standardized_developmental_control(
    *,
    settings: Any,
    domain: DomainSpec,
    student_provider: Literal["inkling", "openai", "compatible"],
    student_model_id: str,
    student_runtime_id: str,
    student_runtime_version: str,
    student_checkpoint_id: str,
    student_role: ResearchRole,
    student_base_url: str | None,
    teacher_provider: Literal["openai", "anthropic"],
    teacher_model_id: str,
    corpus_digest: str,
    allow_legacy_student_fallback: bool = False,
    teacher_mode: TeacherMode = TeacherMode.DIAGNOSTIC_CRITIQUE,
    treatment_condition: str = "frontier_teacher_critique",
    control_condition: str = "no_intervention",
    worker_configuration: ResearchWorkerConfiguration | None = None,
) -> ResearchControlConfiguration:
    """Build the current live workflow profile without claiming deferred capabilities."""

    profile = _developmental_profile(
        settings=settings,
        domain=domain,
        student_provider=student_provider,
    )

    task = TaskCorpusIdentity(
        task_id=domain.domain_id,
        task_version=domain.version,
        task_manifest_digest=sha256_digest(
            {
                "domain": domain.model_dump(mode="json"),
                "split": "curriculum",
            }
        ),
        corpus_id=f"{domain.domain_id}.curriculum",
        corpus_version=domain.version,
        corpus_digest=corpus_digest,
        split="curriculum",
        evidence_status=IdentityEvidenceStatus.PINNED,
    )
    harness_parameters = _developmental_harness_parameters(
        domain_id=domain.domain_id,
        teacher_mode=teacher_mode,
        treatment_condition=treatment_condition,
        control_condition=control_condition,
        external_transport_max_attempts=settings.external_retry_attempts,
    )
    worker = worker_configuration or build_live_worker_configuration(
        settings=settings,
        domain=domain,
        student_provider=student_provider,
        student_model_id=student_model_id,
        student_runtime_id=student_runtime_id,
        student_runtime_version=student_runtime_version,
        student_checkpoint_id=student_checkpoint_id,
        student_role=student_role,
        student_base_url=student_base_url,
        teacher_provider=teacher_provider,
        teacher_model_id=teacher_model_id,
        allow_legacy_student_fallback=allow_legacy_student_fallback,
    )
    if (
        worker.profile != profile
        or worker.harness_parameters != harness_parameters
        or worker.domain_id != domain.domain_id
        or worker.workflow != harness_parameters["workflow"]
    ):
        raise ValueError("live worker harness differs from requested research controls")
    return ResearchControlConfiguration(
        profile=profile,
        student_model=worker.student_model,
        auxiliary_models=worker.auxiliary_models,
        task=task,
        harness_parameters=harness_parameters,
        environment=worker.environment,
        environment_parameters=worker.environment_parameters,
        environment_fingerprint=worker.environment_fingerprint,
    )


def _developmental_profile(
    *,
    settings: Any,
    domain: DomainSpec,
    student_provider: Literal["inkling", "openai", "compatible"],
) -> HarnessProfile:
    source_status = (
        IdentityEvidenceStatus.PINNED
        if settings.code_revision != "unknown"
        else IdentityEvidenceStatus.UNKNOWN
    )
    prompt_templates = [
        _component(
            component_id=f"padawan.{domain.domain_id}.student_prompt",
            version=domain.version,
            evidence_status=source_status,
            evidence="Prompt implementation is bound to the Padawan source revision.",
            content={
                "code_revision": settings.code_revision,
                "domain_id": domain.domain_id,
                "domain_version": domain.version,
                "purpose": "student",
            },
        ),
        _component(
            component_id="padawan.teacher.evidence_citing_prompt",
            version="1.0.0",
            evidence_status=source_status,
            evidence="Teacher prompt implementation is bound to the Padawan source revision.",
            content={"code_revision": settings.code_revision, "purpose": "teacher"},
        ),
    ]
    if domain.domain_id == "legal.appellate.fourth_circuit":
        prompt_templates.append(
            _component(
                component_id="padawan.appellate.semantic_adjudication_prompt",
                version=domain.version,
                evidence_status=source_status,
                evidence=(
                    "Appellate adjudication prompt implementation is bound to the Padawan "
                    "source revision."
                ),
                content={
                    "code_revision": settings.code_revision,
                    "domain_id": domain.domain_id,
                    "purpose": "adjudicator",
                },
            )
        )
    tools = [
        _component(
            component_id=tool_id,
            version=domain.version,
            evidence_status=source_status,
            evidence="Deterministic verifier implementation is bound to the Padawan revision.",
            content={
                "code_revision": settings.code_revision,
                "domain_id": domain.domain_id,
                "tool_id": tool_id,
            },
        )
        for tool_id in domain.deterministic_verifiers
    ]
    if not tools:
        tools.append(
            _component(
                component_id="padawan.no_deterministic_tool_surface",
                version="1.0.0",
                evidence_status=IdentityEvidenceStatus.PINNED,
                evidence="The selected domain declares no deterministic tool surface.",
                content={"domain_id": domain.domain_id, "tools": []},
            )
        )
    context_window = (
        INKLING_SMALL_AMPERE.configured_max_model_len if student_provider == "inkling" else None
    )
    effective_limit = (
        INKLING_SMALL_AMPERE.maximum_verified_target_input_tokens
        if student_provider == "inkling"
        else None
    )
    context_seed = {
        "configured_context_window_tokens": context_window,
        "effective_input_limit_tokens": effective_limit,
        "history_selection": "immutable_state_and_retrieved_lessons_per_request",
        "truncation_enabled": False,
        "truncation_strategy": "none",
        "compaction_enabled": False,
        "compaction_strategy": "none",
        "token_counting_mode": "provider_reported_usage_without_token_ids",
    }
    context = ContextPolicy(
        policy_id="padawan.developmental.context",
        version=f"1.0.0+{sha256_digest(context_seed)[7:19]}",
        configured_context_window_tokens=context_window,
        effective_input_limit_tokens=effective_limit,
        context_limit_evidence=(
            "Validated Inkling transport ceiling; this does not claim long-horizon reasoning."
            if student_provider == "inkling"
            else "Provider context limit has not been pinned in this local harness."
        ),
        token_counting_mode="provider_reported_usage_without_token_ids",
        history_selection="immutable_state_and_retrieved_lessons_per_request",
        truncation_enabled=False,
        truncation_strategy="none",
        compaction_enabled=False,
        compaction_strategy="none",
        compactor=None,
    )
    continuation = ContinuationPolicy(
        continuation_mode="none",
        response_storage_enabled=False,
        previous_response_id_enabled=False,
        reasoning_retention_enabled=False,
        reasoning_retention_mode="none",
        private_reasoning_capture_enabled=student_provider == "inkling",
        private_reasoning_used_as_context=False,
    )
    budgets = HarnessBudgets(
        actions=_capped(256, scope="run", unit="actions"),
        input_tokens=_unbounded(scope="request", unit="tokens"),
        output_tokens=_capped(
            _domain_output_limit(domain.domain_id), scope="request", unit="tokens"
        ),
        latency=_capped(
            float(
                settings.inkling_timeout_seconds
                if student_provider == "inkling"
                else settings.external_timeout_seconds
            ),
            scope="request",
            unit="seconds",
        ),
        wall_time=_unbounded(scope="run", unit="seconds"),
        retries=_capped(settings.run_retry_budget, scope="run", unit="retries"),
        cost=_unbounded(scope="run", unit="usd"),
    )
    profile_seed = {
        "tier": "standardized",
        "purpose": "developmental_boundary_mapping",
        "continuation": continuation.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "prompt_templates": [
            item.model_dump(mode="json") for item in sorted(prompt_templates, key=_component_key)
        ],
        "tools": [item.model_dump(mode="json") for item in sorted(tools, key=_component_key)],
        "budgets": budgets.model_dump(mode="json"),
    }
    return HarnessProfile(
        profile_id=f"padawan.{domain.domain_id}.developmental",
        version=f"1.0.0+{sha256_digest(profile_seed)[7:19]}",
        tier="standardized",
        purpose="developmental_boundary_mapping",
        continuation=continuation,
        context=context,
        prompt_templates=tuple(sorted(prompt_templates, key=_component_key)),
        tools=tuple(sorted(tools, key=_component_key)),
        budgets=budgets,
        instrumentation=ResearchInstrumentationSeams(),
        created_at=_PROFILE_RELEASED_AT,
    )


def build_live_worker_configuration(
    *,
    settings: Any,
    domain: DomainSpec,
    student_provider: Literal["inkling", "openai", "compatible"],
    student_model_id: str,
    student_runtime_id: str,
    student_runtime_version: str,
    student_checkpoint_id: str,
    student_role: ResearchRole,
    student_base_url: str | None,
    teacher_provider: Literal["openai", "anthropic"],
    teacher_model_id: str,
    corpus_competency_ids: tuple[str, ...] = (),
    allow_legacy_student_fallback: bool = False,
    inkling_edge_image_digest: str | None = None,
    inkling_edge_deployment_revision: str | None = None,
    inkling_edge_identity_verified: bool = False,
) -> ResearchWorkerConfiguration:
    """Describe the exact non-secret composition allowed to execute controlled runs."""

    student = build_student_serving_identity(
        provider=student_provider,
        model_id=student_model_id,
        runtime_id=student_runtime_id,
        runtime_version=student_runtime_version,
        checkpoint_id=student_checkpoint_id,
        role=student_role,
        base_url=student_base_url,
        allow_legacy_fallback=allow_legacy_student_fallback,
        timeout_seconds=(
            settings.inkling_timeout_seconds
            if student_provider == "inkling"
            else settings.external_timeout_seconds
        ),
        retry_attempts=(1 if student_provider == "inkling" else settings.external_retry_attempts),
        edge_image_digest=(
            inkling_edge_image_digest or getattr(settings, "inkling_edge_image_digest", None)
            if student_provider == "inkling"
            else None
        ),
        edge_deployment_revision=(
            inkling_edge_deployment_revision
            or getattr(settings, "inkling_edge_deployment_revision", None)
            if student_provider == "inkling"
            else None
        ),
        edge_identity_verified=inkling_edge_identity_verified,
    )
    teacher_base_url = (
        settings.openai_base_url if teacher_provider == "openai" else settings.anthropic_base_url
    )
    auxiliary = [
        _managed_provider_identity(
            provider=teacher_provider,
            model_id=teacher_model_id,
            purpose="teacher",
            role=ResearchRole.TEACHER,
            base_url=teacher_base_url,
            timeout_seconds=settings.external_timeout_seconds,
            retry_attempts=settings.external_retry_attempts,
        )
    ]
    if domain.domain_id == "legal.appellate.fourth_circuit":
        auxiliary.append(
            _managed_provider_identity(
                provider=teacher_provider,
                model_id=teacher_model_id,
                purpose="adjudicator",
                role=ResearchRole.ADJUDICATOR,
                base_url=teacher_base_url,
                timeout_seconds=settings.external_timeout_seconds,
                retry_attempts=settings.external_retry_attempts,
            )
        )
    environment, environment_parameters, environment_fingerprint = _environment_identity(
        settings=settings,
        domain=domain,
    )
    return ResearchWorkerConfiguration(
        profile=_developmental_profile(
            settings=settings,
            domain=domain,
            student_provider=student_provider,
        ),
        task=TaskCorpusIdentity(
            task_id=domain.domain_id,
            task_version=domain.version,
            task_manifest_digest=sha256_digest(
                {"domain": domain.model_dump(mode="json"), "split": "curriculum"}
            ),
            corpus_id=f"{domain.domain_id}.curriculum",
            corpus_version=domain.version,
            corpus_digest=sha256_digest([]),
            split="curriculum",
            evidence_status=IdentityEvidenceStatus.PINNED,
        ),
        corpus_competency_ids=tuple(sorted(corpus_competency_ids)),
        harness_parameters=_developmental_harness_parameters(
            domain_id=domain.domain_id,
            teacher_mode=TeacherMode.DIAGNOSTIC_CRITIQUE,
            treatment_condition="frontier_teacher_critique",
            control_condition="no_intervention",
            external_transport_max_attempts=settings.external_retry_attempts,
        ),
        student_model=student,
        auxiliary_models=tuple(
            sorted(
                auxiliary,
                key=lambda item: (item.purpose, item.research_role.value, item.model_id),
            )
        ),
        environment=environment,
        environment_parameters=environment_parameters,
        environment_fingerprint=environment_fingerprint,
        domain_id=domain.domain_id,
        workflow="padawan.developmental_episode",
    )


def build_student_serving_identity(
    *,
    provider: str,
    model_id: str,
    runtime_id: str,
    runtime_version: str,
    checkpoint_id: str,
    role: ResearchRole,
    base_url: str | None,
    allow_legacy_fallback: bool,
    timeout_seconds: float,
    retry_attempts: int,
    edge_image_digest: str | None,
    edge_deployment_revision: str | None,
    edge_identity_verified: bool,
) -> ModelServingIdentity:
    """Build the foundation's canonical non-secret student serving identity."""
    parameters: dict[str, str]
    transport_artifact: VersionedComponentIdentity | None = None
    if provider == "inkling":
        checkpoint = VersionedComponentIdentity(
            component_id=checkpoint_id,
            version=INKLING_SMALL_AMPERE.validation_record,
            digest=sha256_digest(
                {
                    "checkpoint_id": checkpoint_id,
                    "conversion_manifest_sha256": (INKLING_SMALL_AMPERE.conversion_manifest_sha256),
                }
            ),
            evidence_status=IdentityEvidenceStatus.PINNED,
            evidence=(
                "Digest binds the validated conversion manifest identity, not an asserted "
                "full weight hash."
            ),
        )
        quantization = VersionedComponentIdentity(
            component_id=INKLING_SMALL_AMPERE.quantization,
            version=INKLING_SMALL_AMPERE.profile_id,
            digest=f"sha256:{INKLING_SMALL_AMPERE.conversion_manifest_sha256}",
            evidence_status=IdentityEvidenceStatus.PINNED,
            evidence="Pinned validated quantization conversion manifest.",
        )
        runtime = VersionedComponentIdentity(
            component_id=runtime_id,
            version=runtime_version,
            digest=sha256_digest(runtime_version),
            evidence_status=IdentityEvidenceStatus.PINNED,
            evidence="Pinned validated Inkling runtime source revision.",
        )
        serving = VersionedComponentIdentity(
            component_id=INKLING_SMALL_AMPERE.service,
            version=INKLING_SMALL_AMPERE.profile_id,
            digest=INKLING_SMALL_AMPERE.serving_image_digest,
            evidence_status=IdentityEvidenceStatus.PINNED,
            evidence="Pinned validated serving image digest and profile identity.",
        )
        edge_identity = edge_image_digest or edge_deployment_revision
        if edge_identity_verified and edge_identity is None:
            raise ValueError("verified Inkling edge identity cannot be empty")
        transport_artifact = VersionedComponentIdentity(
            component_id="inkling.responses_edge",
            version=edge_deployment_revision or "deployment_revision_unreported",
            digest=(
                edge_image_digest
                if edge_image_digest is not None
                else sha256_digest(edge_deployment_revision or "unknown")
            ),
            evidence_status=(
                IdentityEvidenceStatus.VERIFIED
                if edge_identity_verified
                else IdentityEvidenceStatus.DECLARED
                if edge_identity is not None
                else IdentityEvidenceStatus.UNKNOWN
            ),
            evidence=(
                "Responses edge identity observed from capability preflight and matched to the "
                "configured deployment identity; endpoint origin is recorded separately without "
                "credentials."
                if edge_identity_verified
                else "Operator-declared Responses edge image/deployment identity; endpoint origin "
                "is recorded separately without credentials."
            ),
        )
        parameters = {
            "batch_size": "1",
            "configured_max_model_len": str(INKLING_SMALL_AMPERE.configured_max_model_len),
            "continuation": "false",
            "conversion_manifest_sha256": (
                f"sha256:{INKLING_SMALL_AMPERE.conversion_manifest_sha256}"
            ),
            "maximum_verified_actual_input_tokens": str(
                INKLING_SMALL_AMPERE.maximum_verified_actual_input_tokens
            ),
            "maximum_verified_target_input_tokens": str(
                INKLING_SMALL_AMPERE.maximum_verified_target_input_tokens
            ),
            "endpoint_origin": _origin(base_url),
            "edge_deployment_revision": edge_deployment_revision or "unreported",
            "edge_image_digest": edge_image_digest or "unreported",
            "legacy_fallback": _boolean_text(allow_legacy_fallback),
            "response_storage": "false",
            "retry_attempts": str(retry_attempts),
            "serving_profile_sha256": f"sha256:{INKLING_SMALL_AMPERE.profile_sha256}",
            "tensor_parallel_size": str(INKLING_SMALL_AMPERE.tensor_parallel_size),
            "timeout_seconds": str(float(timeout_seconds)),
            "validation_repository_revision": (INKLING_SMALL_AMPERE.validation_repository_revision),
        }
    else:
        declared = IdentityEvidenceStatus.DECLARED
        checkpoint = _component(
            component_id=checkpoint_id,
            version="provider_model_alias",
            evidence_status=declared,
            evidence="Provider model alias selected by the operator; weight digest is unavailable.",
            content={"provider": provider, "model_id": model_id},
        )
        quantization = _component(
            component_id=f"{provider}.provider_managed_quantization",
            version=model_id,
            evidence_status=(
                IdentityEvidenceStatus.UNKNOWN
                if provider == "compatible"
                else IdentityEvidenceStatus.DECLARED
            ),
            evidence="Quantization is provider-managed and is not exposed to Padawan.",
            content={"provider": provider, "model_id": model_id, "quantization": "unreported"},
        )
        runtime = _component(
            component_id=runtime_id,
            version=runtime_version,
            evidence_status=declared,
            evidence="Runtime client identity declared by the Padawan composition root.",
            content={"runtime_id": runtime_id, "runtime_version": runtime_version},
        )
        serving = _component(
            component_id=f"{provider}.responses_service",
            version="v1",
            evidence_status=declared,
            evidence="Serving endpoint origin is digest-bound without retaining credentials.",
            content={"provider": provider, "origin": _origin(base_url)},
        )
        parameters = {
            "continuation": "false",
            "endpoint_origin": _origin(base_url),
            "legacy_fallback": _boolean_text(allow_legacy_fallback),
            "protocol": "responses",
            "response_storage": "false",
            "retry_attempts": str(retry_attempts),
            "timeout_seconds": str(float(timeout_seconds)),
        }
    parameters = dict(sorted(parameters.items()))
    return ModelServingIdentity(
        purpose="student",
        research_role=role,
        model_id=model_id,
        checkpoint=checkpoint,
        quantization=quantization,
        runtime=runtime,
        serving_artifact=serving,
        transport_artifact=transport_artifact,
        protocol="responses",
        runtime_parameters=parameters,
        runtime_parameters_digest=sha256_digest(parameters),
    )


def _managed_provider_identity(
    *,
    provider: str,
    model_id: str,
    purpose: str,
    role: ResearchRole,
    base_url: str,
    timeout_seconds: float,
    retry_attempts: int,
) -> ModelServingIdentity:
    protocol = "responses" if provider == "openai" else "messages"
    parameters = {
        "protocol": protocol,
        "provider": provider,
        "response_storage": "false",
        "retry_attempts": str(retry_attempts),
        "timeout_seconds": str(float(timeout_seconds)),
    }
    return ModelServingIdentity(
        purpose=purpose,
        research_role=role,
        model_id=model_id,
        checkpoint=_component(
            component_id=model_id,
            version="provider_model_alias",
            evidence_status=IdentityEvidenceStatus.DECLARED,
            evidence="Provider model alias selected by the operator; weight digest is unavailable.",
            content={"provider": provider, "model_id": model_id},
        ),
        quantization=_component(
            component_id=f"{provider}.provider_managed_quantization",
            version=model_id,
            evidence_status=IdentityEvidenceStatus.DECLARED,
            evidence="Provider-managed quantization is not exposed to Padawan.",
            content={"provider": provider, "model_id": model_id, "quantization": "unreported"},
        ),
        runtime=_component(
            component_id=f"{provider}.{protocol}_client",
            version="v1",
            evidence_status=IdentityEvidenceStatus.DECLARED,
            evidence="Protocol client family is declared by the Padawan composition root.",
            content={"provider": provider, "protocol": protocol},
        ),
        serving_artifact=_component(
            component_id=f"{provider}.managed_service",
            version="v1",
            evidence_status=IdentityEvidenceStatus.DECLARED,
            evidence="Provider-managed endpoint origin is digest-bound.",
            content={"provider": provider, "origin": _origin(base_url)},
        ),
        protocol=protocol,
        runtime_parameters=parameters,
        runtime_parameters_digest=sha256_digest(parameters),
    )


def _component(
    *,
    component_id: str,
    version: str,
    evidence_status: IdentityEvidenceStatus,
    evidence: str,
    content: Any,
) -> VersionedComponentIdentity:
    return VersionedComponentIdentity(
        component_id=component_id,
        version=version,
        digest=sha256_digest(content),
        evidence_status=evidence_status,
        evidence=evidence,
    )


def _component_key(item: VersionedComponentIdentity) -> tuple[str, str]:
    return item.component_id, item.version


def _capped(value: float, *, scope: str, unit: str) -> BudgetLimit:
    return BudgetLimit(
        disposition=BudgetDisposition.CAPPED,
        scope=scope,
        unit=unit,
        value=value,
    )


def _unbounded(*, scope: str, unit: str) -> BudgetLimit:
    return BudgetLimit(
        disposition=BudgetDisposition.UNBOUNDED,
        scope=scope,
        unit=unit,
        value=None,
    )


def _domain_output_limit(domain_id: str) -> int:
    return {
        "math.algebra": 1_600,
        "math.lean": 2_000,
        "legal.appellate.fourth_circuit": 12_000,
        "temporal.grounding": 2_000,
    }.get(domain_id, 12_000)


def _developmental_harness_parameters(
    *,
    domain_id: str,
    teacher_mode: TeacherMode,
    treatment_condition: str,
    control_condition: str,
    external_transport_max_attempts: int,
) -> dict[str, str]:
    parameters = {
        "control_condition": control_condition,
        "domain_id": domain_id,
        "external_transport_max_attempts": str(external_transport_max_attempts),
        "pool": "curriculum",
        "student_request_storage": "false",
        "student_sampling_seed_source": "item_generation_seed",
        "student_sampling_temperature": "0.0",
        "student_sampling_top_logprobs": "5" if domain_id == "math.algebra" else "none",
        "student_sampling_top_p": "none",
        "teacher_guidance_max_output_tokens": ("800" if domain_id == "math.algebra" else "1200"),
        "teacher_max_attempts": "3",
        "teacher_mode": teacher_mode.value,
        "teacher_sampling_policy": "provider_model_default",
        "treatment_condition": treatment_condition,
        "workflow": "padawan.developmental_episode",
    }
    if domain_id == "legal.appellate.fourth_circuit":
        parameters.update(
            {
                "adjudicator_max_attempts": "3",
                "adjudicator_max_output_tokens": "8000",
                "adjudicator_sampling_seed_source": "scenario_seed",
                "adjudicator_sampling_temperature": "0.0",
            }
        )
    return dict(sorted(parameters.items()))


def _environment_identity(
    *,
    settings: Any,
    domain: DomainSpec,
) -> tuple[VersionedComponentIdentity, dict[str, str], str]:
    parameters = {
        "padawan_code_revision": settings.code_revision,
        "environment": settings.environment,
        "domain_id": domain.domain_id,
        "domain_version": domain.version,
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
    }
    parameters.update(
        {f"dependency.{name}": version for name, version in _dependency_versions().items()}
    )
    canonical = dict(sorted(parameters.items()))
    fingerprint = sha256_digest(canonical)
    source_status = (
        IdentityEvidenceStatus.PINNED
        if settings.code_revision != "unknown"
        else IdentityEvidenceStatus.UNKNOWN
    )
    identity = VersionedComponentIdentity(
        component_id=f"padawan.environment.{settings.environment}",
        version=settings.code_revision,
        digest=fingerprint,
        evidence_status=source_status,
        evidence="Fingerprint of the non-secret Padawan execution environment identity.",
    )
    return identity, canonical, fingerprint


def _origin(value: str | None) -> str:
    if not value:
        return "unconfigured"
    parsed = urlsplit(value)
    if not parsed.scheme or parsed.hostname is None:
        return "invalid-endpoint"
    hostname = parsed.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    try:
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return "invalid-endpoint"
    return f"{parsed.scheme.lower()}://{hostname}{port}"


def _boolean_text(value: bool) -> str:
    return "true" if value else "false"


def _dependency_versions() -> dict[str, str]:
    distributions = ("anthropic", "google-auth", "httpx", "openai", "pydantic", "sqlalchemy")
    versions: dict[str, str] = {}
    for distribution in distributions:
        try:
            versions[distribution] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            versions[distribution] = "not-installed"
    return versions
