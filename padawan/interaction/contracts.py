from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from padawan.models.contracts import (
    SCHEMA_VERSION,
    ArtifactRef,
    NonEmpty,
    SamplingConfiguration,
    Sha256,
    StrictRecord,
)
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    HarnessProfile,
    ModelServingIdentity,
    VersionedComponentIdentity,
)


class InteractionRetentionClass(StrEnum):
    PERSONAL_DELETABLE = "personal_deletable"
    CONSENTED_RESEARCH_EVIDENCE = "consented_research_evidence"


class InteractionMode(StrEnum):
    MESSAGE = "message"
    BRANCH = "branch"
    RETRY = "retry"
    REPLAY = "replay"


class InteractionAxis(StrEnum):
    MEMORY_SNAPSHOT = "memory_snapshot"
    RENDERED_CONTEXT = "rendered_context"
    SAMPLING = "sampling"
    STUDENT_TARGET = "student_target"
    SYSTEM_PROMPT = "system_prompt"
    TOOLS = "tools"


class InteractionFeedbackKind(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    CORRECTION = "correction"
    NOTE = "note"


class InteractionConsentSnapshot(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    consent_event_id: NonEmpty
    sequence: Annotated[int, Field(ge=1)]
    retention_classification: InteractionRetentionClass
    research_trace_consent: bool
    memory_mode: Literal["disabled"] = "disabled"
    training_candidate_mode: Literal["not_admitted"] = "not_admitted"
    recorded_at: datetime

    @model_validator(mode="after")
    def retention_matches_consent(self) -> InteractionConsentSnapshot:
        expected = (
            InteractionRetentionClass.CONSENTED_RESEARCH_EVIDENCE
            if self.research_trace_consent
            else InteractionRetentionClass.PERSONAL_DELETABLE
        )
        if self.retention_classification != expected:
            raise ValueError("retention classification disagrees with trace consent")
        return self


class InteractionConsentEventRecord(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    session_id: NonEmpty
    consent: InteractionConsentSnapshot


class InteractionSessionRecord(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    session_id: NonEmpty
    title: NonEmpty
    current_consent: InteractionConsentSnapshot
    created_at: datetime
    last_activity_at: datetime


class InteractionMessageRecord(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    message_id: NonEmpty
    session_id: NonEmpty
    parent_message_id: NonEmpty | None = None
    turn_id: NonEmpty
    role: Literal["user", "assistant"]
    content: NonEmpty
    content_digest: Sha256
    created_at: datetime


class SelectedHistoryMessage(StrictRecord):
    message_id: NonEmpty
    parent_message_id: NonEmpty | None = None
    role: Literal["user", "assistant"]
    content_digest: Sha256


class ServingPathIdentity(StrictRecord):
    target_id: NonEmpty
    endpoint_origin: Annotated[str, Field(pattern=r"^https?://[^\s]+$")]
    route: Annotated[str, Field(pattern=r"^/[^\s]*$")]
    protocol: NonEmpty
    server_side_authentication: Literal[True] = True
    response_storage_enabled: Literal[False] = False
    previous_response_id_enabled: Literal[False] = False
    components: Annotated[tuple[VersionedComponentIdentity, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def components_are_canonical(self) -> ServingPathIdentity:
        keys = [(item.component_id, item.version) for item in self.components]
        if tuple(sorted(keys)) != tuple(keys) or len(keys) != len(set(keys)):
            raise ValueError("serving path components must be unique and canonical")
        return self


class StudentTargetDescriptor(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    target_id: NonEmpty
    display_name: NonEmpty
    provider: NonEmpty
    model: ModelServingIdentity
    serving_path: ServingPathIdentity
    configured_context_window_tokens: Annotated[int, Field(gt=0)] | None = None
    effective_input_limit_tokens: Annotated[int, Field(gt=0)] | None = None
    private_reasoning_capture_enabled: bool

    @model_validator(mode="after")
    def target_is_consistent(self) -> StudentTargetDescriptor:
        if self.target_id != self.serving_path.target_id:
            raise ValueError("target and serving-path IDs differ")
        if (
            self.configured_context_window_tokens is not None
            and self.effective_input_limit_tokens is not None
            and self.effective_input_limit_tokens > self.configured_context_window_tokens
        ):
            raise ValueError("effective target input limit exceeds configured context")
        return self


class ExploratoryInteractionManifest(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    trace_id: NonEmpty
    evidence_class: Literal["exploratory_not_controlled_benchmark"] = (
        "exploratory_not_controlled_benchmark"
    )
    controlled_benchmark_eligible: Literal[False] = False
    session_id: NonEmpty
    turn_id: NonEmpty
    mode: InteractionMode
    parent_turn_id: NonEmpty | None = None
    parent_manifest_digest: Sha256 | None = None
    comparison_source_trace_id: NonEmpty | None = None
    comparison_source_manifest_digest: Sha256 | None = None
    target: StudentTargetDescriptor
    harness_profile: HarnessProfile
    selected_history: tuple[SelectedHistoryMessage, ...]
    selected_history_digest: Sha256
    input_message_digest: Sha256
    rendered_context_digest: Sha256
    memory_snapshot_digest: Sha256 | None = None
    system_prompt: VersionedComponentIdentity
    tools: Annotated[tuple[VersionedComponentIdentity, ...], Field(min_length=1)]
    sampling: SamplingConfiguration
    declared_axis_changes: tuple[InteractionAxis, ...] = ()
    actual_axis_changes: tuple[InteractionAxis, ...] = ()
    consent: InteractionConsentSnapshot
    retention_classification: InteractionRetentionClass
    created_at: datetime

    @model_validator(mode="after")
    def exploratory_controls_are_consistent(self) -> ExploratoryInteractionManifest:
        if self.retention_classification != self.consent.retention_classification:
            raise ValueError("manifest retention differs from its consent snapshot")
        for label, axes in (
            ("declared", self.declared_axis_changes),
            ("actual", self.actual_axis_changes),
        ):
            if (
                len(axes) != len(set(axes))
                or tuple(sorted(axes, key=lambda axis: axis.value)) != axes
            ):
                raise ValueError(f"{label} interaction axes must be unique and canonical")
        has_comparison = self.comparison_source_trace_id is not None
        if has_comparison != (self.comparison_source_manifest_digest is not None):
            raise ValueError("comparison trace and manifest digest must be provided together")
        if has_comparison and self.declared_axis_changes != self.actual_axis_changes:
            raise ValueError("declared replay axes differ from the observed changes")
        if not has_comparison and (self.declared_axis_changes or self.actual_axis_changes):
            raise ValueError("axis changes require a comparison source trace")
        tool_keys = [(item.component_id, item.version) for item in self.tools]
        if tuple(sorted(tool_keys)) != tuple(tool_keys) or len(tool_keys) != len(set(tool_keys)):
            raise ValueError("manifest tools must be unique and canonical")
        return self


class InteractionTraceTiming(StrictRecord):
    requested_at: datetime
    first_event_at: datetime | None = None
    first_public_token_at: datetime | None = None
    completed_at: datetime
    total_latency_ms: Annotated[float, Field(ge=0)]
    time_to_first_event_ms: Annotated[float, Field(ge=0)] | None = None
    time_to_first_public_token_ms: Annotated[float, Field(ge=0)] | None = None


class InteractionTraceArtifacts(StrictRecord):
    selected_history: ArtifactRef
    generation_request: ArtifactRef
    wire_request: ArtifactRef
    raw_events: ArtifactRef
    public_response: ArtifactRef
    generation_result: ArtifactRef
    private_reasoning: ArtifactRef | None = None


class InteractionTraceRecord(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    trace_id: NonEmpty
    manifest_digest: Sha256
    manifest: ExploratoryInteractionManifest
    request_id: NonEmpty
    response_id: NonEmpty | None = None
    provider: NonEmpty
    model_id: NonEmpty
    protocol: NonEmpty
    finish_reason: str | None = None
    usage: dict[NonEmpty, Annotated[int, Field(ge=0)]]
    timing: InteractionTraceTiming
    artifacts: InteractionTraceArtifacts
    capabilities: dict[str, Any]
    serving_telemetry: dict[str, Any] | None = None
    raw_event_summary: tuple[dict[str, Any], ...]
    retention_classification: InteractionRetentionClass
    controlled_benchmark_eligible: Literal[False] = False
    memory_synthesis_status: Literal["not_implemented"] = "not_implemented"
    training_candidate_status: Literal["not_admitted"] = "not_admitted"
    sealed_at: datetime

    @model_validator(mode="after")
    def trace_matches_manifest(self) -> InteractionTraceRecord:
        if self.trace_id != self.manifest.trace_id:
            raise ValueError("trace and manifest IDs differ")
        if self.manifest_digest != sha256_digest(self.manifest.model_dump(mode="json")):
            raise ValueError("trace manifest digest is invalid")
        if self.retention_classification != self.manifest.retention_classification:
            raise ValueError("trace retention differs from manifest")
        return self


class InteractionFeedbackRecord(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    feedback_id: NonEmpty
    session_id: NonEmpty
    turn_id: NonEmpty
    kind: InteractionFeedbackKind
    body: str | None = None
    consent: InteractionConsentSnapshot
    retention_classification: InteractionRetentionClass
    created_at: datetime

    @model_validator(mode="after")
    def correction_has_content(self) -> InteractionFeedbackRecord:
        if self.kind == InteractionFeedbackKind.CORRECTION and not self.body:
            raise ValueError("correction feedback requires replacement guidance")
        if self.retention_classification != self.consent.retention_classification:
            raise ValueError("feedback retention differs from its consent snapshot")
        return self


class TargetReadinessRecord(StrictRecord):
    target_id: NonEmpty
    status: Literal["configured", "checking", "ready", "busy", "unavailable"]
    checked_at: datetime | None = None
    detail: NonEmpty
    serving_path: ServingPathIdentity | None = None
