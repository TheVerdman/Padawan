from __future__ import annotations

from datetime import datetime
from typing import Any

from padawan.atlas.contracts import (
    AtlasCampaignManifest,
    AtlasSnapshot,
    AtlasSuiteManifest,
    AtlasTrialRequest,
    AtlasTrialResult,
    CampaignCondition,
    CampaignExecutionBinding,
    SuiteStatus,
    content_id,
)
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    BudgetDisposition,
    BudgetLimit,
    ContextPolicy,
    ContinuationPolicy,
    HarnessBudgets,
    HarnessProfile,
    IdentityEvidenceStatus,
    ResearchExecutionManifest,
    TaskCorpusIdentity,
    VersionedComponentIdentity,
)


def atlas_schema_identity() -> VersionedComponentIdentity:
    """Return the content identity for the public Atlas interchange surface."""

    schemas = {
        model.__name__: model.model_json_schema(mode="validation")
        for model in (
            AtlasCampaignManifest,
            AtlasSnapshot,
            AtlasSuiteManifest,
            AtlasTrialRequest,
            AtlasTrialResult,
        )
    }
    digest = sha256_digest(schemas)
    return VersionedComponentIdentity(
        component_id="padawan.capability_atlas",
        version=f"1.0.0+{digest[7:19]}",
        digest=digest,
        evidence_status=IdentityEvidenceStatus.PINNED,
        evidence=(
            "Digest binds the generated Atlas campaign, suite, trial, result, and snapshot "
            "contracts; it contains no model capability claim."
        ),
    )


def build_atlas_harness_profile(
    *,
    base: HarnessProfile,
    condition: CampaignCondition,
    source_revision: str,
    created_at: datetime,
) -> HarnessProfile:
    """Derive an Atlas profile while preserving explicit Inkling continuation safeguards."""

    factor_levels = condition.factor_levels
    retention = _factor_enabled(factor_levels, "retention")
    compaction = _factor_enabled(factor_levels, "compaction")
    tool_access = _tool_access_enabled(factor_levels)
    reasoning_effort = factor_levels.get("effort", "unspecified")
    continuation = ContinuationPolicy(
        continuation_mode="explicit_history" if retention or compaction else "none",
        response_storage_enabled=False,
        previous_response_id_enabled=False,
        reasoning_retention_enabled=retention,
        reasoning_retention_mode="public_reasoning_summary" if retention else "none",
        private_reasoning_capture_enabled=base.continuation.private_reasoning_capture_enabled,
        private_reasoning_used_as_context=False,
    )
    compactor = _compactor_identity(source_revision) if compaction else None
    context_limit = _condition_context_limit(base=base, condition=condition)
    context_seed = {
        "base_context": base.context.model_dump(mode="json"),
        "retention": retention,
        "compaction": compaction,
        "reasoning_effort": reasoning_effort,
    }
    context = ContextPolicy(
        policy_id="padawan.atlas.explicit_history",
        version=f"1.0.0+{sha256_digest(context_seed)[7:19]}",
        configured_context_window_tokens=base.context.configured_context_window_tokens,
        effective_input_limit_tokens=context_limit,
        context_limit_evidence=(
            f"Atlas condition {condition.condition_id} declares a {context_limit}-token input "
            f"cap within the base evidence: {base.context.context_limit_evidence}"
        ),
        token_counting_mode=base.context.token_counting_mode,
        history_selection=(
            "explicit_messages_with_public_reasoning_summary"
            if retention
            else "explicit_messages_without_reasoning_summary"
        ),
        truncation_enabled=False,
        truncation_strategy="none",
        compaction_enabled=compaction,
        compaction_strategy="deterministic_evidence_preserving_summary" if compaction else "none",
        compactor=compactor,
    )
    if tool_access:
        if all(_is_no_tool_identity(tool) for tool in base.tools):
            raise ValueError("tool-enabled Atlas conditions require a pinned executable tool")
        tools = base.tools
    else:
        tools = (_no_tool_identity(),)
    budgets = _condition_budgets(base=base, condition=condition)
    instrumentation = base.instrumentation.model_copy(
        update={"capability_atlas_schema": atlas_schema_identity()}
    )
    profile_seed = {
        "tier": condition.harness_tier,
        "purpose": "capability_boundary_mapping",
        "continuation": continuation,
        "context": context,
        "prompts": base.prompt_templates,
        "tools": tools,
        "budgets": budgets,
        "instrumentation": instrumentation,
        "factor_levels": factor_levels,
    }
    suffix = sha256_digest(profile_seed)[7:19]
    profile = HarnessProfile(
        profile_id=f"padawan.atlas.{condition.condition_id}",
        version=f"1.0.0+{suffix}",
        tier=condition.harness_tier,
        purpose="capability_boundary_mapping",
        continuation=continuation,
        context=context,
        prompt_templates=base.prompt_templates,
        tools=tools,
        budgets=budgets,
        instrumentation=instrumentation,
        created_at=created_at,
    )
    validate_profile_against_condition(profile=profile, condition=condition)
    return profile


def validate_profile_against_condition(
    *, profile: HarnessProfile, condition: CampaignCondition
) -> None:
    """Recompute materialized condition factors from one exact harness profile."""

    if profile.tier != condition.harness_tier:
        raise ValueError("Atlas profile tier differs from the campaign condition")
    factors = condition.factor_levels
    retention = _factor_enabled(factors, "retention")
    compaction = _factor_enabled(factors, "compaction")
    if profile.continuation.reasoning_retention_enabled != retention:
        raise ValueError("Atlas profile retention differs from the campaign factor")
    if profile.context.compaction_enabled != compaction:
        raise ValueError("Atlas profile compaction differs from the campaign factor")
    if (retention or compaction) and (
        profile.continuation.continuation_mode != "explicit_history"
        or profile.continuation.response_storage_enabled
        or profile.continuation.previous_response_id_enabled
        or profile.continuation.private_reasoning_used_as_context
    ):
        raise ValueError("Atlas continuation violates the explicit-history guard")
    if "context" in factors:
        expected_context = _context_tokens(factors["context"])
        if profile.context.effective_input_limit_tokens != expected_context:
            raise ValueError("Atlas profile context limit differs from the campaign factor")
    tool_enabled = _tool_access_enabled(factors)
    observed_tool_enabled = any(not _is_no_tool_identity(tool) for tool in profile.tools)
    if observed_tool_enabled != tool_enabled:
        raise ValueError("Atlas profile tools differ from the campaign factor")
    if "budget" in factors:
        expected = _budget_values(condition)
        actual = (
            profile.budgets.input_tokens.value,
            profile.budgets.output_tokens.value,
            profile.budgets.actions.value,
            profile.budgets.cost.value,
        )
        if actual != expected:
            raise ValueError("Atlas profile budgets differ from the campaign factors")
    if {"artifact", "budget", "context", "sampling"}.issubset(
        factors
    ) and profile.instrumentation.capability_atlas_schema != atlas_schema_identity():
        raise ValueError("Atlas profile uses another instrumentation contract")


def build_atlas_execution_manifest(
    *,
    base: ResearchExecutionManifest,
    profile: HarnessProfile,
    campaign_digest: str,
    condition: CampaignCondition,
    suite: AtlasSuiteManifest,
    seed: int,
    created_at: datetime,
) -> ResearchExecutionManifest:
    """Bind an Atlas condition to one exact existing serving and parent-state identity."""

    if suite.status not in {SuiteStatus.READY, SuiteStatus.SEALED}:
        raise ValueError("Atlas execution requires an executable frozen suite")
    if suite.environment_fingerprints and (
        base.environment_fingerprint not in suite.environment_fingerprints
    ):
        raise ValueError("Atlas suite does not admit the execution environment")
    student = base.student_model
    if (
        condition.required_checkpoint_id is not None
        and student.checkpoint.component_id != condition.required_checkpoint_id
    ):
        raise ValueError("Atlas condition checkpoint differs from the serving identity")
    if (
        condition.required_quantization_id is not None
        and student.quantization.component_id != condition.required_quantization_id
    ):
        raise ValueError("Atlas condition quantization differs from the serving identity")
    if student.protocol != condition.required_protocol:
        raise ValueError("Atlas condition protocol differs from the serving identity")
    validate_profile_against_condition(profile=profile, condition=condition)
    profile_digest = sha256_digest(profile.model_dump(mode="json"))
    if profile.instrumentation.capability_atlas_schema != atlas_schema_identity():
        raise ValueError("Atlas execution requires the pinned Atlas instrumentation schema")
    parameters = dict(
        sorted(
            {
                "adapter_kind": suite.adapter_kind.value,
                "atlas_campaign_digest": campaign_digest,
                "atlas_condition_id": condition.condition_id,
                "atlas_suite_digest": suite.content_digest,
                "base_execution_digest": sha256_digest(base.model_dump(mode="json")),
                "compaction": condition.factor_levels.get("compaction", "disabled"),
                "reasoning_effort": condition.factor_levels.get("effort", "unspecified"),
                "retention": condition.factor_levels.get("retention", "disabled"),
                "tool_access": condition.factor_levels.get("tool_access", "disabled"),
                "workflow": "padawan.capability_atlas",
            }.items()
        )
    )
    task_manifest_digest = _select_suite_identity(
        suite.task_manifest_digests,
        preferred=base.task.task_manifest_digest,
        label="task manifest",
    )
    corpus_digest = _select_suite_identity(
        suite.corpus_digests,
        preferred=base.task.corpus_digest,
        label="corpus",
    )
    task = TaskCorpusIdentity(
        task_id=suite.suite_id,
        task_version=suite.version,
        task_manifest_digest=task_manifest_digest,
        corpus_id=f"{suite.benchmark_id}.{suite.split}",
        corpus_version=suite.benchmark_version,
        corpus_digest=corpus_digest,
        split=suite.split,
        evidence_status=IdentityEvidenceStatus.PINNED,
    )
    identity: dict[str, Any] = {
        "campaign_digest": campaign_digest,
        "condition_id": condition.condition_id,
        "suite_digest": suite.content_digest,
        "profile_digest": profile_digest,
        "student_model": student,
        "parent_state": base.parent_state,
        "task": task,
        "parameters": parameters,
        "environment": base.environment_fingerprint,
        "seed": seed,
    }
    return ResearchExecutionManifest(
        execution_id=content_id("atlas-execution", identity),
        harness_profile_id=profile.profile_id,
        harness_profile_version=profile.version,
        harness_profile_digest=profile_digest,
        student_model=student,
        auxiliary_models=base.auxiliary_models,
        task=task,
        parent_state=base.parent_state,
        harness_parameters=parameters,
        environment=base.environment,
        environment_parameters=base.environment_parameters,
        environment_fingerprint=base.environment_fingerprint,
        seed=seed,
        created_at=created_at,
    )


def build_campaign_execution_binding(
    *,
    campaign: AtlasCampaignManifest,
    condition: CampaignCondition,
    suite: AtlasSuiteManifest,
    execution: ResearchExecutionManifest,
    bound_by: str,
    created_at: datetime,
    external_authorization_ref: str | None = None,
) -> CampaignExecutionBinding:
    """Create the append-only activation record after external authorization, if needed."""

    if condition not in campaign.conditions:
        raise ValueError("campaign execution binding cites an unregistered condition")
    suite_binding = next(
        (item for item in campaign.suite_bindings if item.suite_digest == suite.content_digest),
        None,
    )
    if suite_binding is None or condition.condition_id not in suite_binding.condition_ids:
        raise ValueError("campaign condition is not bound to the selected suite")
    execution_digest = sha256_digest(execution.model_dump(mode="json"))
    if (
        condition.required_execution_digest is not None
        and execution_digest != condition.required_execution_digest
    ):
        raise ValueError("campaign condition requires another research execution")
    if condition.externally_gated and external_authorization_ref is None:
        raise ValueError("externally gated Atlas execution requires authorization evidence")
    if execution.task.task_manifest_digest not in suite.task_manifest_digests:
        raise ValueError("Atlas execution task differs from the selected suite")
    if execution.task.corpus_digest not in suite.corpus_digests:
        raise ValueError("Atlas execution corpus differs from the selected suite")
    if (
        suite.environment_fingerprints
        and execution.environment_fingerprint not in suite.environment_fingerprints
    ):
        raise ValueError("Atlas execution environment differs from the selected suite")
    profile_digest = execution.harness_profile_digest
    provisional = CampaignExecutionBinding.model_construct(
        binding_id="pending",
        campaign_digest=campaign.manifest_digest,
        condition_id=condition.condition_id,
        suite_digest=suite.content_digest,
        research_execution_digest=execution_digest,
        harness_profile_digest=profile_digest,
        factor_levels=condition.factor_levels,
        external_authorization_ref=external_authorization_ref,
        bound_by=bound_by,
        binding_digest=sha256_digest("pending"),
        created_at=created_at,
    )
    identity = provisional.model_dump(
        mode="json", exclude={"binding_id", "binding_digest", "created_at"}
    )
    return CampaignExecutionBinding(
        **{
            **provisional.model_dump(mode="python"),
            "binding_id": content_id("atlas-binding", identity),
            "binding_digest": sha256_digest(identity),
        }
    )


def _factor_enabled(
    factors: dict[str, str],
    name: str,
    *,
    enabled_values: set[str] | None = None,
) -> bool:
    values = enabled_values or {"enabled", "retained", "on"}
    return factors.get(name, "disabled").casefold() in values


def _tool_access_enabled(factors: dict[str, str]) -> bool:
    return factors.get("tool_access", "disabled").casefold() not in {
        "disabled",
        "none",
        "off",
    }


def _context_tokens(level: str) -> int:
    try:
        return {"32k": 32_768, "128k": 131_072, "240k": 240_000}[level]
    except KeyError as error:
        raise ValueError(f"unsupported Atlas context factor: {level}") from error


def _condition_context_limit(*, base: HarnessProfile, condition: CampaignCondition) -> int:
    declared = condition.factor_levels.get("context")
    if declared is None:
        effective = base.context.effective_input_limit_tokens
        if effective is None:
            raise ValueError("Atlas execution requires an evidence-backed context limit")
        return effective
    selected = _context_tokens(declared)
    configured = base.context.configured_context_window_tokens
    effective = base.context.effective_input_limit_tokens
    if configured is None or effective is None or selected > min(configured, effective):
        raise ValueError("Atlas context factor exceeds the base serving evidence")
    return selected


def _budget_values(condition: CampaignCondition) -> tuple[float, float, float, float]:
    input_tokens = float(_context_tokens(condition.factor_levels["context"]))
    output_tokens = float(
        {"constrained": 4_096, "standard": 16_384, "extended": 32_768}[
            condition.factor_levels["budget"]
        ]
    )
    stateful = _factor_enabled(condition.factor_levels, "retention") or _factor_enabled(
        condition.factor_levels, "compaction"
    )
    actions = float(20 if _tool_access_enabled(condition.factor_levels) else (8 if stateful else 1))
    cost = condition.max_cost_usd / condition.max_requests
    expected_totals = (
        input_tokens * condition.max_requests,
        output_tokens * condition.max_requests,
        actions * condition.max_requests,
    )
    declared_totals = (
        condition.max_input_tokens,
        condition.max_output_tokens,
        condition.max_actions,
    )
    if expected_totals != declared_totals:
        raise ValueError("campaign condition ceilings disagree with its factor-level budgets")
    return input_tokens, output_tokens, actions, cost


def _condition_budgets(*, base: HarnessProfile, condition: CampaignCondition) -> HarnessBudgets:
    if "budget" not in condition.factor_levels or "context" not in condition.factor_levels:
        return base.budgets
    input_tokens, output_tokens, actions, cost = _budget_values(condition)

    def capped(unit: str, value: float) -> BudgetLimit:
        return BudgetLimit(
            disposition=BudgetDisposition.CAPPED,
            scope="request",
            unit=unit,
            value=value,
        )

    return HarnessBudgets(
        actions=capped("actions", actions),
        input_tokens=capped("tokens", input_tokens),
        output_tokens=capped("tokens", output_tokens),
        latency=base.budgets.latency,
        wall_time=capped(
            "seconds", condition.expected_runtime_minutes * 60 / condition.max_requests
        ),
        retries=capped("retries", 0),
        cost=capped("usd", cost),
    )


def _is_no_tool_identity(identity: VersionedComponentIdentity) -> bool:
    normalized = identity.component_id.casefold().replace("-", "_")
    return "no_tool" in normalized or normalized in {"none", "tool.none"}


def _select_suite_identity(values: tuple[str, ...], *, preferred: str, label: str) -> str:
    if preferred in values:
        return preferred
    if len(values) == 1:
        return values[0]
    raise ValueError(f"Atlas suite requires an explicit {label} selection")


def _compactor_identity(source_revision: str) -> VersionedComponentIdentity:
    status = (
        IdentityEvidenceStatus.PINNED
        if source_revision.casefold() != "unknown"
        else IdentityEvidenceStatus.UNKNOWN
    )
    return VersionedComponentIdentity(
        component_id="padawan.state_compactor",
        version="1.0.0",
        digest=sha256_digest(
            {
                "implementation": "padawan.atlas.orchestration._rendered_input",
                "contract": "padawan.atlas.explicit_history_compaction.v1",
                "source": source_revision,
            }
        ),
        evidence_status=status,
        evidence=(
            "Deterministic evidence-preserving compactor bound to the Padawan source revision."
        ),
    )


def _no_tool_identity() -> VersionedComponentIdentity:
    payload = {"tool_surface": [], "purpose": "capability_atlas_control"}
    return VersionedComponentIdentity(
        component_id="padawan.atlas.no_tool_surface",
        version="1.0.0",
        digest=sha256_digest(payload),
        evidence_status=IdentityEvidenceStatus.PINNED,
        evidence="Control condition exposes no callable tool surface.",
    )
