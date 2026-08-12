# ruff: noqa: E501
"""Deterministic builders for the first Capability Atlas campaign.

The builders perform no network, provider, GPU, or database activity.  They freeze local
project-authored suites, register unavailable public suites honestly, and predeclare the exact
external budget that would be consumed only after explicit authorization.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from padawan.adapters.inkling.contract import INKLING_SMALL_AMPERE
from padawan.atlas.catalog import dataset_governance_records, source_claims
from padawan.atlas.contracts import (
    AdapterKind,
    AtlasCampaignManifest,
    AtlasItemManifest,
    AtlasSuiteManifest,
    BenchmarkClaim,
    CampaignCondition,
    CampaignStatus,
    CampaignSuiteBinding,
    DatasetGovernance,
    EvaluationClass,
    Factor,
    FactorLevel,
    FailureOrigin,
    Modality,
    ModalityGateStatus,
    ModalityValidationEvidence,
    OfflineVerificationRecord,
    OntologyManifest,
    OntologyNode,
    StopRule,
    SuiteStatus,
    content_id,
)
from padawan.corpus.algebra import AlgebraCorpusGenerator, AlgebraFamily
from padawan.domains.lean_math.corpus import (
    LEAN_TOOLCHAIN,
    MATHLIB_REVISION,
    LeanMathCorpusGenerator,
)
from padawan.domains.legal.appellate.corpus import AppellateCorpusGenerator
from padawan.domains.magellan_improvement.corpus import (
    UNBOUND_ENVIRONMENT_FINGERPRINT,
    MagellanScenarioGenerator,
)
from padawan.domains.temporal_grounding.contracts import TemporalScenarioManifest
from padawan.domains.temporal_grounding.corpus import TemporalGroundingCorpusGenerator
from padawan.domains.temporal_grounding.verifier import TemporalPolicyVerifier
from padawan.grading.algebra import AlgebraGrader
from padawan.models.contracts import CorpusItemRecord, CorpusPool, GradeOutcome
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.research_contracts import ResearchAxis
from padawan.temporal.contracts import TemporalDecision

CAMPAIGN_TIME = datetime(2026, 8, 12, tzinfo=UTC)
CAMPAIGN_SEED = 2_026_081_200
CAMPAIGN_ID = "inkling-small-ampere-w8a16-capability-atlas-v0"
CAMPAIGN_VERSION = "1.0.0"


@dataclass(frozen=True)
class HarnessProfileTemplate:
    """A condition-level template to materialize through the authoritative harness builder."""

    condition_id: str
    tier: str
    reasoning_effort: str
    temperature: str
    context_tokens: int
    budget_level: str
    tool_names: tuple[str, ...]
    retention: bool
    compaction: bool
    response_storage: bool
    previous_response_id: bool
    private_reasoning_reused: bool
    notes: tuple[str, ...]


@dataclass(frozen=True)
class LiveCampaignStage:
    stage_id: str
    title: str
    command: str
    condition_ids: tuple[str, ...]
    suite_digests: tuple[str, ...]
    allocations: tuple[LiveCampaignAllocation, ...]
    planned_trials: int
    max_requests: int
    max_input_tokens: int
    max_output_tokens: int
    max_actions: int
    max_cost_usd: float
    expected_runtime_minutes: int
    gates: tuple[str, ...]
    evidence_artifacts: tuple[str, ...]


@dataclass(frozen=True)
class LiveCampaignAllocation:
    condition_id: str
    suite_digest: str
    suite_status: str
    planned_trials: int


@dataclass(frozen=True)
class LiveCampaignPlan:
    authorization_required: bool
    total_max_requests: int
    total_max_input_tokens: int
    total_max_output_tokens: int
    total_max_actions: int
    total_max_cost_usd: float
    total_expected_runtime_minutes: int
    stages: tuple[LiveCampaignStage, ...]
    stop_before_command: str


@dataclass(frozen=True)
class CampaignPreparationCeilings:
    max_requests: int
    max_input_tokens: int
    max_output_tokens: int
    max_actions: int
    max_cost_usd: float
    max_runtime_minutes: int


@dataclass(frozen=True)
class CampaignPreparationEnvelope:
    """A content-addressed plan that cannot authorize or execute model work."""

    schema_version: str
    contract: str
    mode: str
    campaign_id: str
    campaign_version: str
    campaign_digest: str
    offline_verification_digest: str
    allocation_set: str
    authorization_ref: str
    authorization_ref_digest: str
    ceilings: CampaignPreparationCeilings
    condition_ids: tuple[str, ...]
    suite_digests: tuple[str, ...]
    allocations: tuple[LiveCampaignAllocation, ...]
    gates: tuple[str, ...]
    evidence_artifact_plan: tuple[str, ...]
    execution_gateway_status: str
    execution_permitted: bool
    authorization_verified: bool
    network_calls_made: int
    database_writes: int
    artifact_writes: int
    external_requests_made: int
    external_cost_usd: float
    gpu_actions: int
    preparation_digest: str


@dataclass(frozen=True)
class FirstInklingCampaignBundle:
    claims: tuple[BenchmarkClaim, ...]
    governance: tuple[DatasetGovernance, ...]
    ontology: OntologyManifest
    suites: tuple[AtlasSuiteManifest, ...]
    campaign: AtlasCampaignManifest
    harness_templates: tuple[HarnessProfileTemplate, ...]
    live_plan: LiveCampaignPlan
    verification: OfflineVerificationRecord
    interchange_assumptions: tuple[str, ...]
    interaction_lab_seam: tuple[str, ...]
    unsupported_conclusions: tuple[str, ...]


def _ontology_node(
    node_id: str,
    title: str,
    description: str,
    *,
    origin: FailureOrigin = FailureOrigin.MODEL,
    parents: tuple[str, ...] = (),
    axes: tuple[ResearchAxis, ...] = (),
    examples: tuple[str, ...] = (),
) -> OntologyNode:
    return OntologyNode(
        node_id=node_id,
        title=title,
        description=description,
        origin=origin,
        parent_ids=parents,
        changed_axes=axes,
        examples=examples,
    )


def build_capability_ontology() -> OntologyManifest:
    """Build the versioned, overlapping failure ontology used by Atlas v0."""

    nodes = (
        _ontology_node(
            "capability.coding",
            "Coding and software-engineering agency",
            "Program synthesis, repository navigation, debugging, testing, and bounded software agency.",
            axes=(ResearchAxis.TOOLS, ResearchAxis.ENVIRONMENT, ResearchAxis.BUDGET),
        ),
        _ontology_node(
            "capability.context",
            "Context and memory",
            "Retrieval, state tracking, strategy maintenance, and revision across long contexts or horizons.",
            axes=(ResearchAxis.CONTEXT_POLICY, ResearchAxis.CONTINUATION),
        ),
        _ontology_node(
            "capability.context.exact_retrieval",
            "Exact long-context retrieval",
            "Literal recovery of content at a declared token distance; it is transport/retrieval evidence only.",
            parents=("capability.context",),
            axes=(ResearchAxis.CONTEXT_POLICY,),
            examples=("Needle retrieval at 240k target input without a strategy task.",),
        ),
        _ontology_node(
            "capability.context.state_use",
            "Long-horizon state use",
            "Correctly uses evolving state, latent dependencies, and prior actions to choose later actions.",
            parents=("capability.context", "capability.planning"),
            axes=(ResearchAxis.CONTEXT_POLICY, ResearchAxis.CONTINUATION, ResearchAxis.ENVIRONMENT),
        ),
        _ontology_node(
            "capability.epistemics",
            "Epistemics, confidence, and abstention",
            "Calibrated confidence, selective answering, uncertainty recognition, and honest abstention.",
            axes=(ResearchAxis.HARNESS, ResearchAxis.PROMPTS),
        ),
        _ontology_node(
            "capability.instruction",
            "Instruction following and structured output",
            "Constraint satisfaction, schema fidelity, priority handling, and format stability.",
            axes=(ResearchAxis.PROMPTS, ResearchAxis.HARNESS),
        ),
        _ontology_node(
            "capability.knowledge",
            "Knowledge and factuality",
            "Recall, synthesis, attribution, and factual grounding under declared freshness constraints.",
            axes=(ResearchAxis.TASK, ResearchAxis.TOOLS),
        ),
        _ontology_node(
            "capability.math_science",
            "Mathematical and scientific reasoning",
            "Symbolic, quantitative, proof-oriented, and scientific reasoning with checkable outcomes.",
            axes=(ResearchAxis.HARNESS, ResearchAxis.BUDGET, ResearchAxis.TOOLS),
        ),
        _ontology_node(
            "capability.multimodal",
            "Vision, audio, and mixed-media grounding",
            "Grounds responses in supplied non-text evidence only after the independent runtime modality gate passes.",
            axes=(ResearchAxis.SERVING, ResearchAxis.TASK, ResearchAxis.ENVIRONMENT),
        ),
        _ontology_node(
            "capability.planning",
            "Planning, tools, recovery, and environment modeling",
            "Plans and executes bounded actions, models effects, recovers from errors, and respects approvals.",
            axes=(ResearchAxis.TOOLS, ResearchAxis.ENVIRONMENT, ResearchAxis.BUDGET),
        ),
        _ontology_node(
            "capability.robustness",
            "Safety and robustness",
            "Legitimate robustness, authorization, isolation, refusal, and adversarial-behavior phenomena.",
            axes=(ResearchAxis.PROMPTS, ResearchAxis.TOOLS, ResearchAxis.ENVIRONMENT),
        ),
        _ontology_node(
            "capability.temporal",
            "Temporal reasoning and freshness",
            "Elapsed-time reasoning, observation freshness, duration calibration, and time-indexed claims.",
            axes=(ResearchAxis.TASK, ResearchAxis.CONTINUATION, ResearchAxis.TOOLS),
        ),
        _ontology_node(
            "failure.contamination",
            "Contamination failure",
            "Evidence is invalidated by training, adaptive-search, answer, or benchmark-content exposure.",
            origin=FailureOrigin.CONTAMINATION,
            axes=(ResearchAxis.TASK, ResearchAxis.PARENT_STATE),
        ),
        _ontology_node(
            "failure.harness",
            "Harness-induced failure",
            "Failure changes under prompt, effort, sampling, continuation, context, tool, or budget controls.",
            origin=FailureOrigin.HARNESS,
            axes=(
                ResearchAxis.HARNESS,
                ResearchAxis.PROMPTS,
                ResearchAxis.CONTINUATION,
                ResearchAxis.CONTEXT_POLICY,
                ResearchAxis.TOOLS,
                ResearchAxis.BUDGET,
            ),
        ),
        _ontology_node(
            "failure.harness.compaction",
            "Compaction-sensitive failure",
            "Behavior changes when declared explicit-history compaction is enabled.",
            origin=FailureOrigin.HARNESS,
            parents=("failure.harness",),
            axes=(ResearchAxis.CONTEXT_POLICY, ResearchAxis.CONTINUATION),
        ),
        _ontology_node(
            "failure.harness.effort",
            "Effort-sensitive failure",
            "Behavior changes across predeclared reasoning-effort levels.",
            origin=FailureOrigin.HARNESS,
            parents=("failure.harness",),
            axes=(ResearchAxis.HARNESS, ResearchAxis.BUDGET),
        ),
        _ontology_node(
            "failure.infrastructure",
            "Infrastructure failure",
            "Transport, runtime, worker, timeout, environment, or cost-limit failure without a model-capability attribution.",
            origin=FailureOrigin.INFRASTRUCTURE,
            axes=(ResearchAxis.SERVING, ResearchAxis.ENVIRONMENT, ResearchAxis.INSTRUMENTATION),
        ),
        _ontology_node(
            "failure.model.calibration",
            "Calibration-limited",
            "Confidence or abstention behavior is misaligned with authoritative correctness.",
            parents=("capability.epistemics",),
            axes=(ResearchAxis.HARNESS, ResearchAxis.SEED),
        ),
        _ontology_node(
            "failure.model.context",
            "Context-limited",
            "Fails retrieval or state use as distance, distractors, or horizon increase under stable serving.",
            parents=("capability.context",),
            axes=(ResearchAxis.CONTEXT_POLICY, ResearchAxis.CONTINUATION),
        ),
        _ontology_node(
            "failure.model.inconsistent",
            "Stochastic or consistency failure",
            "Authoritative outcomes vary materially across repeated matched trials or equivalent transformations.",
            axes=(ResearchAxis.SEED, ResearchAxis.HARNESS),
        ),
        _ontology_node(
            "failure.model.knowledge",
            "Knowledge-limited",
            "The needed fact or concept is absent or incorrect after ruling out retrieval and harness effects.",
            parents=("capability.knowledge",),
            axes=(ResearchAxis.TASK, ResearchAxis.TOOLS),
        ),
        _ontology_node(
            "failure.model.modality_grounding",
            "Modality-grounding failure",
            "After a passed media gate, the response is not grounded in authoritative visual or audio evidence.",
            parents=("capability.multimodal",),
            axes=(ResearchAxis.SERVING, ResearchAxis.TASK),
        ),
        _ontology_node(
            "failure.model.planning",
            "Planning-limited",
            "Fails decomposition, sequencing, state transition modeling, recovery, or termination.",
            parents=("capability.planning",),
            axes=(ResearchAxis.BUDGET, ResearchAxis.TOOLS, ResearchAxis.ENVIRONMENT),
        ),
        _ontology_node(
            "failure.model.quantization_sensitive",
            "Quantization-sensitive",
            "A controlled source-BF16 versus W8A16 comparison changes authoritative outcomes.",
            axes=(ResearchAxis.QUANTIZATION,),
        ),
        _ontology_node(
            "failure.model.repeated_action",
            "Repeated-action pathology",
            "Repeats, loops, or replays effects without justified state change or idempotency handling.",
            parents=("capability.planning",),
            axes=(ResearchAxis.TOOLS, ResearchAxis.ENVIRONMENT, ResearchAxis.BUDGET),
        ),
        _ontology_node(
            "failure.model.self_correction",
            "Self-correction failure",
            "Does not repair a verifiable error after an admissible critique, or degrades a correct answer.",
            axes=(ResearchAxis.HARNESS, ResearchAxis.CONTINUATION),
        ),
        _ontology_node(
            "failure.model.tool",
            "Tool-limited",
            "Fails tool selection, argument construction, evidence use, recovery, or effect interpretation.",
            parents=("capability.planning",),
            axes=(ResearchAxis.TOOLS, ResearchAxis.ENVIRONMENT),
        ),
        _ontology_node(
            "failure.parser",
            "Parser failure",
            "A response cannot be parsed under the frozen response schema; kept separate from semantic correctness.",
            origin=FailureOrigin.PARSER,
            axes=(ResearchAxis.PROMPTS, ResearchAxis.HARNESS),
        ),
        _ontology_node(
            "failure.verifier",
            "Verifier failure",
            "The deterministic, kernel, environment, or human authority is unavailable, inconsistent, or invalid.",
            origin=FailureOrigin.VERIFIER,
            axes=(ResearchAxis.ENVIRONMENT, ResearchAxis.INSTRUMENTATION),
        ),
    )
    canonical = tuple(sorted(nodes, key=lambda node: node.node_id))
    provisional = OntologyManifest.model_construct(
        ontology_id="padawan.capability-atlas.failure-ontology",
        version="1.0.0",
        nodes=canonical,
        manifest_digest=sha256_digest("pending"),
        created_at=CAMPAIGN_TIME,
    )
    identity = provisional.model_dump(mode="json", exclude={"manifest_digest", "created_at"})
    return OntologyManifest(
        **{
            **provisional.model_dump(mode="python"),
            "manifest_digest": sha256_digest(identity),
        }
    )


def _text_gate() -> ModalityValidationEvidence:
    evidence = {
        "gate": "padawan-text-responses-contract",
        "validation_record": INKLING_SMALL_AMPERE.validation_record,
        "revision": INKLING_SMALL_AMPERE.validation_repository_revision,
        "scope": "text transport and exact retrieval only; no reasoning-quality conclusion",
    }
    return ModalityValidationEvidence(
        modality=Modality.TEXT,
        status=ModalityGateStatus.PASSED,
        gate_id="padawan.inkling.text-responses",
        gate_revision=INKLING_SMALL_AMPERE.validation_repository_revision,
        evidence_digest=sha256_digest(evidence),
        evidence_refs=(
            INKLING_SMALL_AMPERE.validation_record,
            "padawan.adapters.inkling.contract:INKLING_SMALL_AMPERE",
        ),
        validated_at=CAMPAIGN_TIME,
    )


def _unvalidated_media_gate(modality: Modality) -> ModalityValidationEvidence:
    return ModalityValidationEvidence(
        modality=modality,
        status=ModalityGateStatus.UNVALIDATED,
        gate_id=f"inkling-small-ampere-{modality.value}-complete-gate",
        gate_revision="unregistered-at-atlas-v0",
        evidence_refs=(
            "Sibling Inkling multimodal validation must register a complete-gate digest before Atlas execution.",
        ),
    )


def _gates(modalities: tuple[Modality, ...]) -> tuple[ModalityValidationEvidence, ...]:
    gates = [
        _text_gate() if modality == Modality.TEXT else _unvalidated_media_gate(modality)
        for modality in modalities
    ]
    return tuple(sorted(gates, key=lambda gate: gate.modality.value))


def _atlas_item(record: CorpusItemRecord, *, adapter_kind: AdapterKind) -> AtlasItemManifest:
    metadata: dict[str, Any] = {
        "contamination_scope": record.contamination_scope,
        "generation_seed": record.generation_seed,
        "generator_version": record.generator_version,
        "original_item_id": record.item_id,
        "pool": record.pool.value,
        "rights_id": record.rights.rights_id,
        "source": record.source,
        "template_family_id": record.template_family_id,
    }
    verifier_payload: dict[str, Any] = {
        "expected_answer": record.expected_answer,
        "parameters": record.verifier_spec.parameters,
    }
    prompt_digest = sha256_digest(record.prompt)
    pair_id = record.instance_group_id
    variant_id = f"seed-{record.generation_seed}"
    identity = {
        "family_id": record.competency_id,
        "difficulty": record.difficulty,
        "adapter_kind": adapter_kind,
        "modalities": (Modality.TEXT,),
        "prompt_digest": prompt_digest,
        "verifier_id": record.verifier_spec.verifier_type,
        "verifier_version": record.verifier_spec.verifier_version,
        "verifier_payload": verifier_payload,
        "metadata": metadata,
        "pair_id": pair_id,
        "variant_id": variant_id,
    }
    return AtlasItemManifest(
        item_id=content_id("atlas-item", identity),
        item_digest=sha256_digest(identity),
        family_id=record.competency_id,
        difficulty=record.difficulty,
        adapter_kind=adapter_kind,
        modalities=(Modality.TEXT,),
        prompt=record.prompt,
        prompt_digest=prompt_digest,
        verifier_id=record.verifier_spec.verifier_type,
        verifier_version=record.verifier_spec.verifier_version,
        verifier_payload=verifier_payload,
        metadata=metadata,
        pair_id=pair_id,
        variant_id=variant_id,
    )


def _suite(
    *,
    suite_id: str,
    title: str,
    benchmark_id: str,
    benchmark_version: str,
    split: str,
    governance_id: str,
    adapter_kind: AdapterKind,
    status: SuiteStatus,
    evaluation_class: EvaluationClass,
    items: tuple[AtlasItemManifest, ...] = (),
    modalities: tuple[Modality, ...] = (Modality.TEXT,),
    environment_fingerprints: tuple[str, ...] = (),
    blocking_reasons: tuple[str, ...] = (),
) -> AtlasSuiteManifest:
    canonical_items = tuple(sorted(items, key=lambda item: item.item_digest))
    item_digests = tuple(item.item_digest for item in canonical_items)
    modality_gates = _gates(tuple(sorted(modalities, key=lambda value: value.value)))
    task_manifest_digests = (
        (
            sha256_digest(
                {
                    "contract": "padawan.atlas.generated-task-manifest.v0",
                    "suite_id": suite_id,
                    "benchmark_id": benchmark_id,
                    "adapter_kind": adapter_kind,
                    "verifiers": tuple(
                        sorted(
                            {(item.verifier_id, item.verifier_version) for item in canonical_items}
                        )
                    ),
                }
            ),
        )
        if canonical_items
        else ()
    )
    corpus_digests = (
        (
            sha256_digest(
                {
                    "contract": "padawan.atlas.generated-corpus.v0",
                    "benchmark_id": benchmark_id,
                    "benchmark_version": benchmark_version,
                    "split": split,
                    "item_digests": item_digests,
                }
            ),
        )
        if canonical_items
        else ()
    )
    identity = {
        "suite_id": suite_id,
        "version": "1.0.0",
        "benchmark_id": benchmark_id,
        "benchmark_version": benchmark_version,
        "split": split,
        "governance_id": governance_id,
        "adapter_kind": adapter_kind,
        "evaluation_class": evaluation_class,
        "item_digests": item_digests,
        "modality_gates": modality_gates,
        "task_manifest_digests": task_manifest_digests,
        "corpus_digests": corpus_digests,
        "environment_fingerprints": environment_fingerprints,
        "evaluation_suite_manifest_digest": None,
    }
    return AtlasSuiteManifest(
        suite_id=suite_id,
        version="1.0.0",
        title=title,
        benchmark_id=benchmark_id,
        benchmark_version=benchmark_version,
        split=split,
        governance_id=governance_id,
        adapter_kind=adapter_kind,
        status=status,
        evaluation_class=evaluation_class,
        item_digests=item_digests,
        items=canonical_items,
        modality_gates=modality_gates,
        task_manifest_digests=task_manifest_digests,
        corpus_digests=corpus_digests,
        environment_fingerprints=environment_fingerprints,
        evaluation_suite_manifest_digest=None,
        content_digest=sha256_digest(identity),
        blocking_reasons=blocking_reasons,
        created_at=CAMPAIGN_TIME,
    )


GovernanceKey = tuple[str, EvaluationClass]


def _local_suites(
    governance: dict[GovernanceKey, DatasetGovernance],
) -> tuple[AtlasSuiteManifest, ...]:
    algebra_records = AlgebraCorpusGenerator().generate(
        pool=CorpusPool.ROTATING_SHADOW,
        seed=CAMPAIGN_SEED + 101,
        groups_per_family=2,
        siblings_per_group=2,
        created_at=CAMPAIGN_TIME,
    )
    temporal_records = TemporalGroundingCorpusGenerator().generate(
        pool=CorpusPool.ROTATING_SHADOW,
        seed=CAMPAIGN_SEED + 202,
        groups_per_family=2,
        siblings_per_group=3,
        created_at=CAMPAIGN_TIME,
    )
    lean_records = LeanMathCorpusGenerator().generate(
        pool=CorpusPool.ROTATING_SHADOW,
        seed=CAMPAIGN_SEED + 303,
        groups_per_family=2,
        siblings_per_group=2,
        created_at=CAMPAIGN_TIME,
    )
    appellate_records = AppellateCorpusGenerator().generate(
        pool=CorpusPool.ROTATING_SHADOW,
        seed=CAMPAIGN_SEED + 404,
        groups_per_family=1,
        siblings_per_group=2,
        created_at=CAMPAIGN_TIME,
    )
    magellan_records = MagellanScenarioGenerator().generate(
        pool=CorpusPool.ROTATING_SHADOW,
        seed=CAMPAIGN_SEED + 505,
        groups_per_family=1,
        siblings_per_group=2,
        created_at=CAMPAIGN_TIME,
    )
    training_records = AlgebraCorpusGenerator().generate(
        pool=CorpusPool.CURRICULUM,
        seed=CAMPAIGN_SEED + 909,
        groups_per_family=2,
        siblings_per_group=2,
        families=(AlgebraFamily.INVALID_CANCELLATION, AlgebraFamily.EXTRANEOUS_ROOTS),
        created_at=CAMPAIGN_TIME,
    )
    return (
        _suite(
            suite_id="padawan-algebra-adaptive-v0",
            title="Project-authored algebra matched boundary neighborhood",
            benchmark_id="padawan-algebra",
            benchmark_version="padawan-algebra-v1",
            split="rotating-shadow-adaptive-v0",
            governance_id=governance[
                ("padawan-algebra", EvaluationClass.ADAPTIVE_SEARCH)
            ].governance_id,
            adapter_kind=AdapterKind.GENERATED_VERIFIER,
            status=SuiteStatus.READY,
            evaluation_class=EvaluationClass.ADAPTIVE_SEARCH,
            items=tuple(
                _atlas_item(record, adapter_kind=AdapterKind.GENERATED_VERIFIER)
                for record in algebra_records
            ),
        ),
        _suite(
            suite_id="padawan-temporal-adaptive-v0",
            title="Project-authored temporal grounding and state-use neighborhoods",
            benchmark_id="padawan-temporal-grounding",
            benchmark_version="padawan-temporal-grounding-v1",
            split="rotating-shadow-adaptive-v0",
            governance_id=governance[
                ("padawan-temporal-grounding", EvaluationClass.ADAPTIVE_SEARCH)
            ].governance_id,
            adapter_kind=AdapterKind.CONTEXT_MEMORY,
            status=SuiteStatus.READY,
            evaluation_class=EvaluationClass.ADAPTIVE_SEARCH,
            items=tuple(
                _atlas_item(record, adapter_kind=AdapterKind.CONTEXT_MEMORY)
                for record in temporal_records
            ),
        ),
        _suite(
            suite_id="padawan-lean-challenge-v0",
            title="Pinned Lean kernel mathematical challenge set",
            benchmark_id="padawan-lean-math",
            benchmark_version="padawan-lean-math-v1",
            split="rotating-shadow-challenge-v0",
            governance_id=governance[
                ("padawan-lean-math", EvaluationClass.CHALLENGE)
            ].governance_id,
            adapter_kind=AdapterKind.GENERATED_VERIFIER,
            status=SuiteStatus.BLOCKED,
            evaluation_class=EvaluationClass.CHALLENGE,
            items=tuple(
                _atlas_item(record, adapter_kind=AdapterKind.GENERATED_VERIFIER)
                for record in lean_records
            ),
            environment_fingerprints=(
                sha256_digest({"lean_toolchain": LEAN_TOOLCHAIN, "mathlib": MATHLIB_REVISION}),
            ),
            blocking_reasons=(
                "The pinned Lean/Mathlib kernel environment has not been activated and verified in this offline campaign run.",
            ),
        ),
        _suite(
            suite_id="padawan-appellate-challenge-v0",
            title="Synthetic closed-record appellate agency challenge set",
            benchmark_id="padawan-appellate",
            benchmark_version="padawan-appellate-synthetic-v1",
            split="rotating-shadow-challenge-v0",
            governance_id=governance[
                ("padawan-appellate", EvaluationClass.CHALLENGE)
            ].governance_id,
            adapter_kind=AdapterKind.STATIC_QA,
            status=SuiteStatus.BLOCKED,
            evaluation_class=EvaluationClass.CHALLENGE,
            items=tuple(
                _atlas_item(record, adapter_kind=AdapterKind.STATIC_QA)
                for record in appellate_records
            ),
            blocking_reasons=(
                "Deterministic record/citation gates are frozen, but human semantic adjudication and legal-currentness review remain unregistered.",
            ),
        ),
        _suite(
            suite_id="padawan-magellan-adaptive-v0",
            title="Project-authored long-horizon Magellan strategy and recovery neighborhoods",
            benchmark_id="padawan-magellan",
            benchmark_version="padawan-magellan-scenarios-v1",
            split="rotating-shadow-adaptive-v0",
            governance_id=governance[
                ("padawan-magellan", EvaluationClass.ADAPTIVE_SEARCH)
            ].governance_id,
            adapter_kind=AdapterKind.INTERACTIVE_ENVIRONMENT,
            status=SuiteStatus.BLOCKED,
            evaluation_class=EvaluationClass.ADAPTIVE_SEARCH,
            items=tuple(
                _atlas_item(record, adapter_kind=AdapterKind.INTERACTIVE_ENVIRONMENT)
                for record in magellan_records
            ),
            environment_fingerprints=(UNBOUND_ENVIRONMENT_FINGERPRINT,),
            blocking_reasons=(
                "The scenario content is frozen, but the isolated Magellan environment fingerprint is deliberately unbound.",
            ),
        ),
        _suite(
            suite_id="padawan-algebra-training-candidates-v0",
            title="Disjoint project-authored algebra training-candidate lane",
            benchmark_id="padawan-algebra",
            benchmark_version="padawan-algebra-v1",
            split="curriculum-training-candidates-v0",
            governance_id=governance[
                ("padawan-algebra", EvaluationClass.DEVELOPMENT)
            ].governance_id,
            adapter_kind=AdapterKind.GENERATED_VERIFIER,
            status=SuiteStatus.READY,
            evaluation_class=EvaluationClass.DEVELOPMENT,
            items=tuple(
                _atlas_item(record, adapter_kind=AdapterKind.GENERATED_VERIFIER)
                for record in training_records
            ),
        ),
        _suite(
            suite_id="inkling-n-plus-one-promotion-v0",
            title="Planned sealed checkpoint N versus N+1 promotion suite",
            benchmark_id="padawan-algebra",
            benchmark_version="padawan-algebra-v1",
            split="sealed-promotion-unmaterialized-v0",
            governance_id=governance[
                ("padawan-algebra", EvaluationClass.SEALED_PROMOTION)
            ].governance_id,
            adapter_kind=AdapterKind.GENERATED_VERIFIER,
            status=SuiteStatus.BLOCKED,
            evaluation_class=EvaluationClass.SEALED_PROMOTION,
            blocking_reasons=(
                "Promotion items have not been materialized or exposed.",
                "A sealed Atlas suite requires a registered core EvaluationSuiteManifest digest; no bridge is registered for v0.",
            ),
        ),
        _suite(
            suite_id="inkling-240k-exact-retrieval-plan-v0",
            title="240k exact-retrieval transport registration",
            benchmark_id="padawan-temporal-grounding",
            benchmark_version="padawan-temporal-grounding-v1",
            split="transport-evidence-registration-only",
            governance_id=governance[
                ("padawan-temporal-grounding", EvaluationClass.CHALLENGE)
            ].governance_id,
            adapter_kind=AdapterKind.CONTEXT_MEMORY,
            status=SuiteStatus.BLOCKED,
            evaluation_class=EvaluationClass.CHALLENGE,
            blocking_reasons=(
                "The existing production record establishes batch-one transport and exact retrieval, not a frozen Atlas item suite.",
                "No long-horizon reasoning or usable-working-memory claim may be inferred from the 240k transport ladder.",
            ),
        ),
    )


def _external_suites(
    governance: dict[GovernanceKey, DatasetGovernance],
) -> tuple[AtlasSuiteManifest, ...]:
    specs = (
        (
            "gpqa-diamond-registration-v0",
            "GPQA Diamond registration",
            "gpqa-diamond",
            AdapterKind.STATIC_QA,
            (Modality.TEXT,),
        ),
        (
            "aime-2026-registration-v0",
            "AIME 2026 / MathArena registration",
            "aime-2026",
            AdapterKind.GENERATED_VERIFIER,
            (Modality.TEXT,),
        ),
        (
            "swe-bench-verified-registration-v0",
            "SWE-bench Verified environment registration",
            "swe-bench-verified",
            AdapterKind.CODING_AGENTIC,
            (Modality.TEXT,),
        ),
        (
            "hle-registration-v0",
            "Humanity's Last Exam gated registration",
            "humanitys-last-exam",
            AdapterKind.MULTIMODAL,
            (Modality.IMAGE, Modality.TEXT),
        ),
        (
            "ifbench-registration-v0",
            "IFBench registration",
            "ifbench",
            AdapterKind.STATIC_QA,
            (Modality.TEXT,),
        ),
        (
            "mmmu-pro-registration-v0",
            "MMMU-Pro Standard-10 media registration",
            "mmmu-pro",
            AdapterKind.MULTIMODAL,
            (Modality.IMAGE, Modality.TEXT),
        ),
        (
            "mmau-registration-v0",
            "MMAU private-evaluator media registration",
            "mmau",
            AdapterKind.MULTIMODAL,
            (Modality.AUDIO, Modality.IMAGE, Modality.TEXT, Modality.VIDEO),
        ),
        (
            "arc-agi-2-registration-v0",
            "ARC-AGI-2 open task registration",
            "arc-agi-2",
            AdapterKind.INTERACTIVE_ENVIRONMENT,
            (Modality.IMAGE, Modality.TEXT),
        ),
        (
            "arc-agi-3-registration-v0",
            "ARC-AGI-3 private interactive evaluation registration",
            "arc-agi-3",
            AdapterKind.INTERACTIVE_ENVIRONMENT,
            (Modality.IMAGE, Modality.TEXT),
        ),
    )
    suites: list[AtlasSuiteManifest] = []
    for suite_id, title, benchmark_id, adapter_kind, modalities in specs:
        candidates = tuple(
            record
            for (candidate_id, _), record in governance.items()
            if candidate_id == benchmark_id
        )
        if len(candidates) != 1:
            raise ValueError(
                f"external benchmark requires exactly one governance lane: {benchmark_id}"
            )
        record = candidates[0]
        blockers = list(record.access_requirements)
        blockers.append(
            "No benchmark questions, answers, media, or evaluator artifacts were copied into Atlas v0."
        )
        if any(modality != Modality.TEXT for modality in modalities):
            blockers.append(
                "Inkling media capability remains unvalidated until the sibling complete gate registers immutable evidence."
            )
        suites.append(
            _suite(
                suite_id=suite_id,
                title=title,
                benchmark_id=benchmark_id,
                benchmark_version=record.benchmark_version,
                split="registration-only-unmaterialized",
                governance_id=record.governance_id,
                adapter_kind=adapter_kind,
                status=SuiteStatus.BLOCKED,
                evaluation_class=record.evaluation_class,
                modalities=modalities,
                blocking_reasons=tuple(blockers),
            )
        )
    return tuple(suites)


def _factor_level(level_id: str, **parameters: str) -> FactorLevel:
    return FactorLevel(level_id=level_id, parameters=dict(sorted(parameters.items())))


def _factors() -> tuple[Factor, ...]:
    return (
        Factor(
            factor_id="artifact",
            axis=ResearchAxis.QUANTIZATION,
            levels=(
                _factor_level("w8a16", quantization="w8a16-balanced-v1"),
                _factor_level("source-bf16", quantization="source-bf16-reference"),
            ),
        ),
        Factor(
            factor_id="compaction",
            axis=ResearchAxis.CONTEXT_POLICY,
            levels=(
                _factor_level("disabled", compaction="disabled"),
                _factor_level("enabled", compaction="deterministic-explicit-history"),
            ),
        ),
        Factor(
            factor_id="budget",
            axis=ResearchAxis.BUDGET,
            levels=(
                _factor_level("constrained", max_output_tokens_per_trial="4096"),
                _factor_level("standard", max_output_tokens_per_trial="16384"),
                _factor_level("extended", max_output_tokens_per_trial="32768"),
            ),
        ),
        Factor(
            factor_id="context",
            axis=ResearchAxis.CONTEXT_POLICY,
            levels=(
                _factor_level("32k", maximum_input_tokens="32768"),
                _factor_level("128k", maximum_input_tokens="131072"),
                _factor_level("240k", maximum_input_tokens="240000"),
            ),
        ),
        Factor(
            factor_id="effort",
            axis=ResearchAxis.HARNESS,
            levels=(
                _factor_level("e0", responses_reasoning_effort="none", vendor_effort_label="0.0"),
                _factor_level(
                    "e50",
                    responses_reasoning_effort="mapping-required",
                    vendor_effort_label="0.5",
                ),
                _factor_level(
                    "e99",
                    responses_reasoning_effort="mapping-required",
                    vendor_effort_label="0.99",
                ),
            ),
        ),
        Factor(
            factor_id="retention",
            axis=ResearchAxis.CONTINUATION,
            levels=(
                _factor_level("disabled", retained_public_reasoning="disabled"),
                _factor_level("enabled", retained_public_reasoning="enabled"),
            ),
        ),
        Factor(
            factor_id="sampling",
            axis=ResearchAxis.SEED,
            levels=(
                _factor_level("deterministic", temperature="0"),
                _factor_level("stochastic", temperature="1"),
            ),
        ),
        Factor(
            factor_id="tool_access",
            axis=ResearchAxis.TOOLS,
            levels=(
                _factor_level("disabled", tools="none"),
                _factor_level("bash", tools="bash-only-sandbox"),
            ),
        ),
    )


def _condition(
    condition_id: str,
    title: str,
    *,
    tier: str,
    effort: str = "e99",
    context: str = "32k",
    tools: str = "disabled",
    sampling: str = "deterministic",
    retention: str = "disabled",
    compaction: str = "disabled",
    artifact: str = "w8a16",
    budget: str = "standard",
    requests: int,
    input_tokens: int,
    output_tokens: int,
    actions: int,
    cost: float,
    runtime: int,
    extra_blockers: tuple[str, ...] = (),
) -> CampaignCondition:
    effort_blocker = (
        "Only reasoning={'effort':'none'} was exercised by the sibling serving validation; "
        "every effort level still requires behavioral validation."
    )
    mapping_blockers = (
        (
            (
                "The vendor effort label has no registered numeric-to-Responses adapter mapping; "
                "edge preflight must bind a supported reasoning.effort value before execution."
            ),
        )
        if effort != "e0"
        else ()
    )
    tool_blockers = (
        (
            (
                "GenerationRequest tool structure exists, but the Inkling edge/tool loop has not "
                "passed behavioral validation for this condition."
            ),
        )
        if tools != "disabled"
        else ()
    )
    blockers = (
        "Explicit authorization, a live authenticated Responses edge, and a matching ResearchExecutionManifest are required.",
        effort_blocker,
        *mapping_blockers,
        *tool_blockers,
        *extra_blockers,
    )
    bf16 = artifact == "source-bf16"
    return CampaignCondition(
        condition_id=condition_id,
        title=title,
        harness_tier=tier,
        required_checkpoint_id=(
            "thinkingmachines/Inkling-Small@released-2026-07-30-unbound"
            if bf16
            else INKLING_SMALL_AMPERE.checkpoint_id
        ),
        required_quantization_id=(
            "source-bf16-reference-unbound" if bf16 else INKLING_SMALL_AMPERE.quantization
        ),
        required_protocol="responses",
        factor_levels=dict(
            sorted(
                {
                    "artifact": artifact,
                    "budget": budget,
                    "compaction": compaction,
                    "context": context,
                    "effort": effort,
                    "retention": retention,
                    "sampling": sampling,
                    "tool_access": tools,
                }.items()
            )
        ),
        max_requests=requests,
        max_input_tokens=input_tokens,
        max_output_tokens=output_tokens,
        max_actions=actions,
        max_cost_usd=cost,
        expected_runtime_minutes=runtime,
        externally_gated=True,
        blocking_reasons=blockers,
    )


def _conditions() -> tuple[CampaignCondition, ...]:
    return (
        _condition(
            "std-e0-w8a16",
            "Standardized W8A16, effort 0.0",
            tier="standardized",
            effort="e0",
            requests=72,
            input_tokens=1_500_000,
            output_tokens=750_000,
            actions=72,
            cost=60.0,
            runtime=240,
        ),
        _condition(
            "budget-constrained-e99-w8a16",
            "Standardized W8A16 constrained output budget, effort 0.99",
            tier="standardized",
            budget="constrained",
            requests=72,
            input_tokens=1_500_000,
            output_tokens=300_000,
            actions=72,
            cost=45.0,
            runtime=180,
        ),
        _condition(
            "std-e50-w8a16",
            "Standardized W8A16, effort 0.5",
            tier="standardized",
            effort="e50",
            requests=72,
            input_tokens=1_500_000,
            output_tokens=750_000,
            actions=72,
            cost=60.0,
            runtime=240,
        ),
        _condition(
            "std-e99-w8a16",
            "Standardized W8A16, effort 0.99",
            tier="standardized",
            requests=72,
            input_tokens=1_500_000,
            output_tokens=750_000,
            actions=72,
            cost=60.0,
            runtime=240,
        ),
        _condition(
            "optimized-bash-e99-w8a16",
            "Optimized W8A16 with sandboxed Bash, effort 0.99",
            tier="optimized",
            tools="bash",
            budget="extended",
            requests=72,
            input_tokens=1_500_000,
            output_tokens=1_000_000,
            actions=720,
            cost=90.0,
            runtime=360,
        ),
        _condition(
            "stochastic-e99-w8a16",
            "Repeated stochastic W8A16 trials, effort 0.99",
            tier="standardized",
            sampling="stochastic",
            requests=96,
            input_tokens=2_000_000,
            output_tokens=1_500_000,
            actions=96,
            cost=90.0,
            runtime=480,
        ),
        _condition(
            "context-128k-w8a16",
            "W8A16 state-use challenge at 128k context",
            tier="standardized",
            context="128k",
            requests=24,
            input_tokens=3_072_000,
            output_tokens=384_000,
            actions=24,
            cost=60.0,
            runtime=240,
        ),
        _condition(
            "transport-240k-w8a16",
            "W8A16 240k exact-retrieval transport control",
            tier="standardized",
            context="240k",
            requests=12,
            input_tokens=2_880_000,
            output_tokens=96_000,
            actions=12,
            cost=75.0,
            runtime=180,
            extra_blockers=(
                "This condition is a transport/exact-retrieval control and must not be reported as long-horizon reasoning.",
            ),
        ),
        _condition(
            "factorial-r0-c0-w8a16",
            "Retention off × compaction off",
            tier="optimized",
            requests=36,
            input_tokens=1_000_000,
            output_tokens=512_000,
            actions=72,
            cost=45.0,
            runtime=240,
        ),
        _condition(
            "factorial-r0-c1-w8a16",
            "Retention off × deterministic compaction on",
            tier="optimized",
            compaction="enabled",
            requests=36,
            input_tokens=1_000_000,
            output_tokens=512_000,
            actions=72,
            cost=45.0,
            runtime=240,
        ),
        _condition(
            "factorial-r1-c0-w8a16",
            "Public-reasoning retention on × compaction off",
            tier="optimized",
            retention="enabled",
            requests=36,
            input_tokens=1_000_000,
            output_tokens=512_000,
            actions=72,
            cost=45.0,
            runtime=240,
        ),
        _condition(
            "factorial-r1-c1-w8a16",
            "Public-reasoning retention on × deterministic compaction on",
            tier="optimized",
            retention="enabled",
            compaction="enabled",
            requests=36,
            input_tokens=1_000_000,
            output_tokens=512_000,
            actions=72,
            cost=45.0,
            runtime=240,
        ),
        _condition(
            "std-e99-source-bf16",
            "Standardized source-BF16 reference, effort 0.99",
            tier="standardized",
            artifact="source-bf16",
            requests=144,
            input_tokens=3_000_000,
            output_tokens=1_500_000,
            actions=144,
            cost=180.0,
            runtime=720,
            extra_blockers=(
                "No source-BF16 reference endpoint, runtime identity, or execution manifest is registered.",
                "Comparison is permitted only when every non-quantization control is matched.",
            ),
        ),
    )


def _harness_templates(
    conditions: tuple[CampaignCondition, ...],
) -> tuple[HarnessProfileTemplate, ...]:
    context_tokens = {"32k": 32_768, "128k": 131_072, "240k": 240_000}
    effort = {
        "e0": "none",
        "e50": "mapping-required(vendor-label=0.5)",
        "e99": "mapping-required(vendor-label=0.99)",
    }
    templates = []
    for condition in conditions:
        levels = condition.factor_levels
        retained = levels["retention"] == "enabled"
        compacted = levels["compaction"] == "enabled"
        templates.append(
            HarnessProfileTemplate(
                condition_id=condition.condition_id,
                tier=condition.harness_tier,
                reasoning_effort=effort[levels["effort"]],
                temperature="1" if levels["sampling"] == "stochastic" else "0",
                context_tokens=context_tokens[levels["context"]],
                budget_level=levels["budget"],
                tool_names=("bash-only-sandbox",) if levels["tool_access"] == "bash" else (),
                retention=retained,
                compaction=compacted,
                response_storage=False,
                previous_response_id=False,
                private_reasoning_reused=False,
                notes=(
                    "SamplingConfiguration.reasoning_effort maps to Responses reasoning.effort only after a registered edge-supported mapping; numeric vendor labels are not assumed to be wire values.",
                    "Sibling validation exercised only reasoning.effort='none', which is transport evidence rather than behavioral support for the campaign effort sweep.",
                    "Tools are materialized through GenerationRequest.tools and remain disabled unless declared.",
                    "Continuation uses explicit history only; response storage, previous_response_id, and private-reasoning reuse remain forbidden.",
                ),
            )
        )
    return tuple(templates)


def _binding(
    suite: AtlasSuiteManifest,
    *,
    count: int,
    trials: int,
    conditions: tuple[str, ...],
    adaptive: bool = False,
) -> CampaignSuiteBinding:
    return CampaignSuiteBinding(
        suite_digest=suite.content_digest,
        evaluation_class=suite.evaluation_class,
        planned_item_count=count,
        trials_per_item=trials,
        condition_ids=conditions,
        adaptive=adaptive,
    )


def _condition_unit_budget(condition: CampaignCondition) -> tuple[int, int, int, float, int]:
    """Return a conservative, declared per-trial ceiling for one condition."""

    context = condition.factor_levels["context"]
    input_tokens = {"32k": 32_768, "128k": 131_072, "240k": 240_000}[context]
    tool_enabled = condition.factor_levels["tool_access"] == "bash"
    stateful = (
        condition.factor_levels["retention"] == "enabled"
        or condition.factor_levels["compaction"] == "enabled"
    )
    output_tokens = {
        "constrained": 4_096,
        "standard": 16_384,
        "extended": 32_768,
    }[condition.factor_levels["budget"]]
    actions = 20 if tool_enabled else (8 if stateful else 1)
    cost_usd = 4.0 if tool_enabled else (2.5 if context != "32k" else 1.5)
    if condition.factor_levels["artifact"] == "source-bf16":
        cost_usd = max(cost_usd, 2.5)
    runtime_minutes = 30 if tool_enabled else (20 if context != "32k" or stateful else 10)
    return input_tokens, output_tokens, actions, cost_usd, runtime_minutes


def _reconcile_condition_budgets(
    conditions: tuple[CampaignCondition, ...],
    bindings: tuple[CampaignSuiteBinding, ...],
) -> tuple[CampaignCondition, ...]:
    allocated = Counter[str]()
    for binding in bindings:
        trials = binding.planned_item_count * binding.trials_per_item
        for condition_id in binding.condition_ids:
            allocated[condition_id] += trials
    reconciled: list[CampaignCondition] = []
    for condition in conditions:
        trials = allocated[condition.condition_id]
        if trials <= 0:
            raise ValueError(
                f"campaign condition has no predeclared trials: {condition.condition_id}"
            )
        input_per_trial, output_per_trial, actions_per_trial, cost_per_trial, runtime_per_trial = (
            _condition_unit_budget(condition)
        )
        reconciled.append(
            CampaignCondition.model_validate(
                {
                    **condition.model_dump(mode="python"),
                    "max_requests": trials,
                    "max_input_tokens": trials * input_per_trial,
                    "max_output_tokens": trials * output_per_trial,
                    "max_actions": trials * actions_per_trial,
                    "max_cost_usd": trials * cost_per_trial,
                    "expected_runtime_minutes": trials * runtime_per_trial,
                }
            )
        )
    return tuple(reconciled)


def _campaign(
    *,
    claims: tuple[BenchmarkClaim, ...],
    ontology: OntologyManifest,
    suites: tuple[AtlasSuiteManifest, ...],
    conditions: tuple[CampaignCondition, ...],
) -> AtlasCampaignManifest:
    by_id = {suite.suite_id: suite for suite in suites}
    effort_conditions = ("std-e0-w8a16", "std-e50-w8a16", "std-e99-w8a16")
    budget_conditions = ("budget-constrained-e99-w8a16", "std-e99-w8a16")
    standardized_pair = ("std-e99-w8a16", "std-e99-source-bf16")
    retention_factorial = (
        "factorial-r0-c0-w8a16",
        "factorial-r0-c1-w8a16",
        "factorial-r1-c0-w8a16",
        "factorial-r1-c1-w8a16",
    )
    bindings = (
        _binding(
            by_id["padawan-algebra-adaptive-v0"],
            count=32,
            trials=3,
            conditions=effort_conditions
            + budget_conditions[:1]
            + ("stochastic-e99-w8a16", "std-e99-source-bf16"),
            adaptive=True,
        ),
        _binding(
            by_id["padawan-temporal-adaptive-v0"],
            count=30,
            trials=3,
            conditions=effort_conditions
            + budget_conditions[:1]
            + ("context-128k-w8a16",)
            + retention_factorial
            + ("std-e99-source-bf16",),
            adaptive=True,
        ),
        _binding(
            by_id["padawan-lean-challenge-v0"],
            count=8,
            trials=3,
            conditions=standardized_pair,
        ),
        _binding(
            by_id["padawan-appellate-challenge-v0"],
            count=6,
            trials=3,
            conditions=("std-e99-w8a16", "optimized-bash-e99-w8a16"),
        ),
        _binding(
            by_id["padawan-magellan-adaptive-v0"],
            count=16,
            trials=3,
            conditions=("optimized-bash-e99-w8a16",) + retention_factorial,
            adaptive=True,
        ),
        _binding(
            by_id["padawan-algebra-training-candidates-v0"],
            count=8,
            trials=2,
            conditions=("std-e99-w8a16",),
        ),
        _binding(
            by_id["inkling-n-plus-one-promotion-v0"],
            count=120,
            trials=3,
            conditions=standardized_pair,
        ),
        _binding(
            by_id["inkling-240k-exact-retrieval-plan-v0"],
            count=12,
            trials=1,
            conditions=("transport-240k-w8a16",),
        ),
        _binding(
            by_id["gpqa-diamond-registration-v0"], count=60, trials=3, conditions=standardized_pair
        ),
        _binding(
            by_id["aime-2026-registration-v0"], count=30, trials=3, conditions=standardized_pair
        ),
        _binding(
            by_id["swe-bench-verified-registration-v0"],
            count=100,
            trials=1,
            conditions=("optimized-bash-e99-w8a16",),
        ),
        _binding(
            by_id["hle-registration-v0"],
            count=100,
            trials=3,
            conditions=("optimized-bash-e99-w8a16",),
        ),
        _binding(
            by_id["ifbench-registration-v0"], count=100, trials=3, conditions=standardized_pair
        ),
        _binding(
            by_id["mmmu-pro-registration-v0"], count=100, trials=3, conditions=standardized_pair
        ),
        _binding(by_id["mmau-registration-v0"], count=100, trials=3, conditions=standardized_pair),
        _binding(
            by_id["arc-agi-2-registration-v0"],
            count=80,
            trials=3,
            conditions=("optimized-bash-e99-w8a16",) + retention_factorial,
        ),
        _binding(
            by_id["arc-agi-3-registration-v0"],
            count=50,
            trials=3,
            conditions=("optimized-bash-e99-w8a16",) + retention_factorial,
        ),
    )
    conditions = _reconcile_condition_budgets(conditions, bindings)
    adaptive = tuple(
        sorted(
            (
                by_id["padawan-algebra-adaptive-v0"].content_digest,
                by_id["padawan-temporal-adaptive-v0"].content_digest,
                by_id["padawan-magellan-adaptive-v0"].content_digest,
            )
        )
    )
    promotion = (by_id["inkling-n-plus-one-promotion-v0"].content_digest,)
    training = (by_id["padawan-algebra-training-candidates-v0"].content_digest,)
    provisional = AtlasCampaignManifest.model_construct(
        campaign_id=CAMPAIGN_ID,
        version=CAMPAIGN_VERSION,
        title="Inkling-Small-Ampere W8A16 Capability Atlas v0",
        description=(
            "Predeclared behavioral boundary campaign spanning deterministic local probes, "
            "effort/context/tool/sampling/budget controls, retention × compaction, blocked public "
            "benchmark registrations, and a controlled source-BF16 comparison plan."
        ),
        status=CampaignStatus.EXTERNALLY_GATED,
        ontology_digest=ontology.manifest_digest,
        source_claim_ids=tuple(sorted(claim.claim_id for claim in claims)),
        suite_bindings=bindings,
        conditions=conditions,
        factors=_factors(),
        stop_rules=(
            StopRule(
                rule_id="adaptive-boundary-precision-v0",
                minimum_trials=12,
                maximum_trials=96,
                target_interval_width=0.15,
                confidence_level=0.95,
                boundary_probability=0.5,
                maximum_infrastructure_failure_rate=0.05,
            ),
            StopRule(
                rule_id="promotion-regression-precision-v0",
                minimum_trials=30,
                maximum_trials=360,
                target_interval_width=0.1,
                confidence_level=0.95,
                boundary_probability=0.5,
                maximum_infrastructure_failure_rate=0.02,
            ),
            StopRule(
                rule_id="stochastic-stability-v0",
                minimum_trials=3,
                maximum_trials=12,
                target_interval_width=0.2,
                confidence_level=0.95,
                boundary_probability=0.5,
                maximum_infrastructure_failure_rate=0.05,
            ),
        ),
        randomization_seed=CAMPAIGN_SEED,
        analysis_policy_id="padawan.atlas.boundary-calibration-regression-v0",
        analysis_policy_version="1.0.0",
        promotion_suite_digests=promotion,
        adaptive_suite_digests=adaptive,
        training_candidate_suite_digests=training,
        manifest_digest=sha256_digest("pending"),
        created_at=CAMPAIGN_TIME,
    )
    identity = provisional.model_dump(
        mode="json", exclude={"manifest_digest", "status", "created_at"}
    )
    return AtlasCampaignManifest(
        **{
            **provisional.model_dump(mode="python"),
            "manifest_digest": sha256_digest(identity),
        }
    )


def _live_plan(
    campaign: AtlasCampaignManifest,
    conditions: tuple[CampaignCondition, ...],
    suites: tuple[AtlasSuiteManifest, ...],
) -> LiveCampaignPlan:
    condition_by_id = {condition.condition_id: condition for condition in conditions}
    suite_by_digest = {suite.content_digest: suite for suite in suites}
    all_allocations = tuple(
        LiveCampaignAllocation(
            condition_id=condition_id,
            suite_digest=binding.suite_digest,
            suite_status=suite_by_digest[binding.suite_digest].status.value,
            planned_trials=binding.planned_item_count * binding.trials_per_item,
        )
        for binding in campaign.suite_bindings
        for condition_id in binding.condition_ids
    )

    def artifact(allocation: LiveCampaignAllocation) -> str:
        return condition_by_id[allocation.condition_id].factor_levels["artifact"]

    w8_ready = tuple(
        allocation
        for allocation in all_allocations
        if artifact(allocation) == "w8a16" and allocation.suite_status == SuiteStatus.READY.value
    )
    w8_blocked = tuple(
        allocation
        for allocation in all_allocations
        if artifact(allocation) == "w8a16" and allocation.suite_status != SuiteStatus.READY.value
    )
    bf16 = tuple(
        allocation for allocation in all_allocations if artifact(allocation) == "source-bf16"
    )

    def stage(
        stage_id: str,
        title: str,
        allocations: tuple[LiveCampaignAllocation, ...],
        gates: tuple[str, ...],
    ) -> LiveCampaignStage:
        requests = sum(item.planned_trials for item in allocations)
        inputs = 0
        outputs = 0
        actions = 0
        cost = 0.0
        runtime = 0
        for allocation in allocations:
            condition = condition_by_id[allocation.condition_id]
            input_unit, output_unit, action_unit, cost_unit, runtime_unit = _condition_unit_budget(
                condition
            )
            inputs += allocation.planned_trials * input_unit
            outputs += allocation.planned_trials * output_unit
            actions += allocation.planned_trials * action_unit
            cost += allocation.planned_trials * cost_unit
            runtime += allocation.planned_trials * runtime_unit
        command = (
            "padawan atlas campaign prepare --preparation-only "
            f"--campaign-digest {campaign.manifest_digest} "
            f"--allocation-set {stage_id} "
            "--authorization-ref REPLACE_WITH_AUTHORIZATION_REFERENCE "
            f"--max-requests {requests} --max-input-tokens {inputs} "
            f"--max-output-tokens {outputs} --max-actions {actions} "
            f"--max-cost-usd {cost:.2f} --max-runtime-minutes {runtime}"
        )
        return LiveCampaignStage(
            stage_id=stage_id,
            title=title,
            command=command,
            condition_ids=tuple(sorted({item.condition_id for item in allocations})),
            suite_digests=tuple(sorted({item.suite_digest for item in allocations})),
            allocations=allocations,
            planned_trials=requests,
            max_requests=requests,
            max_input_tokens=inputs,
            max_output_tokens=outputs,
            max_actions=actions,
            max_cost_usd=cost,
            expected_runtime_minutes=runtime,
            gates=gates,
            evidence_artifacts=(
                "ResearchExecutionManifest and CampaignExecutionBinding for every activated condition/suite",
                "immutable request, response, grader/verifier, timing, token, tool, retry, cost, and failure records",
                "item-level AtlasTrialResult digests plus fixed-denominator aggregate uncertainty",
                "CapabilityAtlas snapshot, capability curves, failure clusters, and regression matrix",
            ),
        )

    stages = (
        stage(
            "w8a16-content-ready",
            "Content-ready Inkling-Small-Ampere W8A16 local campaign",
            w8_ready,
            (
                "User authorization naming the cost ceiling",
                "Authenticated Responses edge passes the current identity/capability admission check",
                "Effort mapping/tool conditions pass edge preflight before their allocations activate",
                "No GPU is deployed or awakened by this offline builder",
            ),
        ),
        stage(
            "w8a16-blocked-expansion",
            "Blocked W8A16 public, environment, transport, and promotion expansion",
            w8_blocked,
            (
                "Every included suite first becomes content-ready under its recorded governance and authority gates",
                "Media suites additionally register the sibling complete-gate evidence",
                "Promotion content additionally binds a core EvaluationSuiteManifest without exposure",
                "A new explicit authorization names this expansion's exact cost ceiling",
            ),
        ),
        stage(
            "source-bf16-reference",
            "Matched source-BF16 reference expansion",
            bf16,
            (
                "A source-BF16 endpoint/path and immutable runtime identity are registered",
                "Every control except the declared quantization axis matches W8A16",
                "Separate user authorization covers any provider or cloud spend",
            ),
        ),
    )
    return LiveCampaignPlan(
        authorization_required=True,
        total_max_requests=sum(item.max_requests for item in stages),
        total_max_input_tokens=sum(item.max_input_tokens for item in stages),
        total_max_output_tokens=sum(item.max_output_tokens for item in stages),
        total_max_actions=sum(item.max_actions for item in stages),
        total_max_cost_usd=sum(item.max_cost_usd for item in stages),
        total_expected_runtime_minutes=sum(item.expected_runtime_minutes for item in stages),
        stages=stages,
        stop_before_command=(
            "The advertised commands only prepare non-executable envelopes. Actual execution "
            "fails closed until a governed authorization gateway, exact endpoint identity, "
            "allocation-set materialization, and campaign execution bindings exist."
        ),
    )


def prepare_first_inkling_campaign_stage(
    *,
    campaign_digest: str,
    allocation_set: str,
    authorization_ref: str,
    max_requests: int,
    max_input_tokens: int,
    max_output_tokens: int,
    max_actions: int,
    max_cost_usd: float,
    max_runtime_minutes: int,
    preparation_only: bool,
) -> CampaignPreparationEnvelope:
    """Validate an exact stage and return a plan with no execution authority or side effects."""

    if not preparation_only:
        raise ValueError("campaign preparation requires --preparation-only acknowledgement")
    bundle = build_first_inkling_campaign_bundle()
    _validate_live_campaign_plan(bundle)
    campaign = bundle.campaign
    if campaign_digest != campaign.manifest_digest:
        raise ValueError(
            "campaign digest differs from the rebuilt first campaign: "
            f"expected {campaign.manifest_digest}"
        )
    authorization = _validated_authorization_reference(authorization_ref)
    stage = next(
        (
            candidate
            for candidate in bundle.live_plan.stages
            if candidate.stage_id == allocation_set
        ),
        None,
    )
    if stage is None:
        known = ", ".join(candidate.stage_id for candidate in bundle.live_plan.stages)
        raise ValueError(f"unknown allocation set {allocation_set!r}; expected one of: {known}")
    ceilings = CampaignPreparationCeilings(
        max_requests=max_requests,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        max_actions=max_actions,
        max_cost_usd=max_cost_usd,
        max_runtime_minutes=max_runtime_minutes,
    )
    expected = _stage_ceilings(stage)
    if ceilings != expected:
        raise ValueError(
            "requested ceilings differ from the exact predeclared allocation set: "
            f"expected {_ceiling_summary(expected)}"
        )
    failed_checks = tuple(name for name, passed in bundle.verification.checks.items() if not passed)
    if failed_checks:
        raise ValueError(
            "campaign preparation failed deterministic verification: " + ", ".join(failed_checks)
        )
    identity: dict[str, Any] = {
        "schema_version": "1.0.0",
        "contract": "padawan.atlas.campaign-preparation.v1",
        "mode": "preparation_only",
        "campaign_id": campaign.campaign_id,
        "campaign_version": campaign.version,
        "campaign_digest": campaign.manifest_digest,
        "offline_verification_digest": bundle.verification.record_digest,
        "allocation_set": stage.stage_id,
        "authorization_ref": authorization,
        "authorization_ref_digest": sha256_digest(authorization),
        "ceilings": asdict(ceilings),
        "condition_ids": stage.condition_ids,
        "suite_digests": stage.suite_digests,
        "allocations": tuple(asdict(allocation) for allocation in stage.allocations),
        "gates": stage.gates,
        "evidence_artifact_plan": stage.evidence_artifacts,
        "execution_gateway_status": "unavailable_fail_closed",
        "execution_permitted": False,
        "authorization_verified": False,
        "network_calls_made": 0,
        "database_writes": 0,
        "artifact_writes": 0,
        "external_requests_made": 0,
        "external_cost_usd": 0.0,
        "gpu_actions": 0,
    }
    return CampaignPreparationEnvelope(
        schema_version="1.0.0",
        contract="padawan.atlas.campaign-preparation.v1",
        mode="preparation_only",
        campaign_id=campaign.campaign_id,
        campaign_version=campaign.version,
        campaign_digest=campaign.manifest_digest,
        offline_verification_digest=bundle.verification.record_digest,
        allocation_set=stage.stage_id,
        authorization_ref=authorization,
        authorization_ref_digest=sha256_digest(authorization),
        ceilings=ceilings,
        condition_ids=stage.condition_ids,
        suite_digests=stage.suite_digests,
        allocations=stage.allocations,
        gates=stage.gates,
        evidence_artifact_plan=stage.evidence_artifacts,
        execution_gateway_status="unavailable_fail_closed",
        execution_permitted=False,
        authorization_verified=False,
        network_calls_made=0,
        database_writes=0,
        artifact_writes=0,
        external_requests_made=0,
        external_cost_usd=0.0,
        gpu_actions=0,
        preparation_digest=sha256_digest(identity),
    )


def _validate_live_campaign_plan(bundle: FirstInklingCampaignBundle) -> None:
    """Recompute the full allocation partition and all per-stage/global ceilings."""

    campaign = bundle.campaign
    plan = bundle.live_plan
    if not plan.authorization_required:
        raise ValueError("live campaign plan must require external authorization")
    stage_ids = tuple(stage.stage_id for stage in plan.stages)
    if len(set(stage_ids)) != len(stage_ids):
        raise ValueError("live campaign allocation-set identifiers must be unique")
    conditions = {condition.condition_id: condition for condition in campaign.conditions}
    suites = {suite.content_digest: suite for suite in bundle.suites}
    bindings = {
        (condition_id, binding.suite_digest): binding
        for binding in campaign.suite_bindings
        for condition_id in binding.condition_ids
    }
    if len(bindings) != sum(len(binding.condition_ids) for binding in campaign.suite_bindings):
        raise ValueError("campaign contains duplicate condition/suite allocations")
    observed: dict[tuple[str, str], LiveCampaignAllocation] = {}
    for stage in plan.stages:
        if not stage.allocations:
            raise ValueError(f"live campaign stage has no allocations: {stage.stage_id}")
        expected_stage = CampaignPreparationCeilings(0, 0, 0, 0, 0.0, 0)
        for allocation in stage.allocations:
            coordinate = (allocation.condition_id, allocation.suite_digest)
            binding = bindings.get(coordinate)
            condition = conditions.get(allocation.condition_id)
            suite = suites.get(allocation.suite_digest)
            if binding is None or condition is None or suite is None:
                raise ValueError("live campaign stage substitutes an unknown allocation")
            if coordinate in observed:
                raise ValueError("live campaign allocation appears in more than one stage")
            expected_trials = binding.planned_item_count * binding.trials_per_item
            if (
                allocation.planned_trials != expected_trials
                or allocation.suite_status != suite.status.value
            ):
                raise ValueError("live campaign allocation differs from its frozen binding")
            observed[coordinate] = allocation
            units = _condition_unit_budget(condition)
            expected_stage = CampaignPreparationCeilings(
                max_requests=expected_stage.max_requests + expected_trials,
                max_input_tokens=expected_stage.max_input_tokens + expected_trials * units[0],
                max_output_tokens=expected_stage.max_output_tokens + expected_trials * units[1],
                max_actions=expected_stage.max_actions + expected_trials * units[2],
                max_cost_usd=expected_stage.max_cost_usd + expected_trials * units[3],
                max_runtime_minutes=(
                    expected_stage.max_runtime_minutes + expected_trials * units[4]
                ),
            )
        if _stage_ceilings(stage) != expected_stage:
            raise ValueError(f"live campaign stage ceilings drifted: {stage.stage_id}")
        if stage.planned_trials != expected_stage.max_requests:
            raise ValueError(f"live campaign planned trials drifted: {stage.stage_id}")
        if stage.condition_ids != tuple(
            sorted({allocation.condition_id for allocation in stage.allocations})
        ) or stage.suite_digests != tuple(
            sorted({allocation.suite_digest for allocation in stage.allocations})
        ):
            raise ValueError(f"live campaign stage indices drifted: {stage.stage_id}")
    if set(observed) != set(bindings):
        raise ValueError("live campaign stages omit or substitute frozen allocations")
    totals = CampaignPreparationCeilings(
        max_requests=sum(stage.max_requests for stage in plan.stages),
        max_input_tokens=sum(stage.max_input_tokens for stage in plan.stages),
        max_output_tokens=sum(stage.max_output_tokens for stage in plan.stages),
        max_actions=sum(stage.max_actions for stage in plan.stages),
        max_cost_usd=sum(stage.max_cost_usd for stage in plan.stages),
        max_runtime_minutes=sum(stage.expected_runtime_minutes for stage in plan.stages),
    )
    declared = CampaignPreparationCeilings(
        max_requests=plan.total_max_requests,
        max_input_tokens=plan.total_max_input_tokens,
        max_output_tokens=plan.total_max_output_tokens,
        max_actions=plan.total_max_actions,
        max_cost_usd=plan.total_max_cost_usd,
        max_runtime_minutes=plan.total_expected_runtime_minutes,
    )
    if totals != declared:
        raise ValueError("live campaign global ceilings differ from its stage partition")
    allocated_by_condition = Counter[str]()
    for allocation in observed.values():
        allocated_by_condition[allocation.condition_id] += allocation.planned_trials
    for condition in campaign.conditions:
        trials = allocated_by_condition[condition.condition_id]
        units = _condition_unit_budget(condition)
        declared_condition = CampaignPreparationCeilings(
            max_requests=condition.max_requests,
            max_input_tokens=condition.max_input_tokens,
            max_output_tokens=condition.max_output_tokens,
            max_actions=condition.max_actions,
            max_cost_usd=condition.max_cost_usd,
            max_runtime_minutes=condition.expected_runtime_minutes,
        )
        expected_condition = CampaignPreparationCeilings(
            max_requests=trials,
            max_input_tokens=trials * units[0],
            max_output_tokens=trials * units[1],
            max_actions=trials * units[2],
            max_cost_usd=trials * units[3],
            max_runtime_minutes=trials * units[4],
        )
        if declared_condition != expected_condition:
            raise ValueError(f"campaign condition ceilings drifted: {condition.condition_id}")


def _stage_ceilings(stage: LiveCampaignStage) -> CampaignPreparationCeilings:
    return CampaignPreparationCeilings(
        max_requests=stage.max_requests,
        max_input_tokens=stage.max_input_tokens,
        max_output_tokens=stage.max_output_tokens,
        max_actions=stage.max_actions,
        max_cost_usd=stage.max_cost_usd,
        max_runtime_minutes=stage.expected_runtime_minutes,
    )


def _validated_authorization_reference(value: str) -> str:
    reference = value.strip()
    normalized = reference.casefold().replace("-", "_")
    placeholders = {
        "required",
        "authorization_reference",
        "authorization_reference_required",
        "replace_me",
        "replace_with_authorization_reference",
        "placeholder",
        "todo",
        "tbd",
        "none",
        "null",
        "n/a",
    }
    placeholder_tokens = {"required", "replace", "placeholder", "todo", "tbd"}
    reference_tokens = {
        token for token in normalized.replace(":", "_").replace("/", "_").split("_") if token
    }
    if (
        reference != value
        or len(reference) < 8
        or len(reference) > 512
        or normalized in placeholders
        or reference_tokens & placeholder_tokens
        or "placeholder" in normalized
        or normalized.startswith("replace_")
        or any(character.isspace() for character in reference)
    ):
        raise ValueError("authorization reference is missing, malformed, or a placeholder")
    return reference


def _ceiling_summary(ceilings: CampaignPreparationCeilings) -> str:
    return (
        f"requests={ceilings.max_requests}, input_tokens={ceilings.max_input_tokens}, "
        f"output_tokens={ceilings.max_output_tokens}, actions={ceilings.max_actions}, "
        f"cost_usd={ceilings.max_cost_usd:.2f}, "
        f"runtime_minutes={ceilings.max_runtime_minutes}"
    )


def _verify_matched_neighborhoods(
    suites: tuple[AtlasSuiteManifest, ...],
) -> tuple[str, ...]:
    evidence: list[str] = []
    for suite in suites:
        groups: dict[str, list[AtlasItemManifest]] = {}
        for item in suite.items:
            if item.pair_id is not None:
                groups.setdefault(item.pair_id, []).append(item)
        for pair_id, items in groups.items():
            if len(items) < 2:
                continue
            if len({item.item_digest for item in items}) != len(items):
                raise ValueError("matched neighborhood reuses item content")
            if len({item.variant_id for item in items}) != len(items):
                raise ValueError("matched neighborhood contains duplicate variants")
            if len({sha256_digest(item.verifier_payload) for item in items}) != len(items):
                raise ValueError("matched neighborhood contains duplicate oracle instances")
            fixed_axes = {
                (
                    item.family_id,
                    item.adapter_kind,
                    item.verifier_id,
                    item.verifier_version,
                    item.metadata.get("template_family_id"),
                )
                for item in items
            }
            if len(fixed_axes) != 1:
                raise ValueError("matched neighborhood changes an undeclared authority axis")
            evidence.append(
                sha256_digest(
                    {
                        "contract": "padawan.atlas.matched-neighborhood-verification.v1",
                        "suite_digest": suite.content_digest,
                        "pair_id": pair_id,
                        "item_digests": tuple(sorted(item.item_digest for item in items)),
                        "prompt_digests": tuple(sorted(item.prompt_digest for item in items)),
                        "oracle_payload_digests": tuple(
                            sorted(sha256_digest(item.verifier_payload) for item in items)
                        ),
                        "variant_ids": tuple(sorted(item.variant_id for item in items)),
                        "fixed_axes": tuple(fixed_axes),
                    }
                )
            )
    return tuple(sorted(evidence))


def _execute_offline_oracles(
    suites: tuple[AtlasSuiteManifest, ...],
) -> tuple[str, ...]:
    algebra = AlgebraGrader()
    temporal = TemporalPolicyVerifier()
    evidence: list[str] = []
    executable_ids = {
        "padawan-algebra-adaptive-v0",
        "padawan-temporal-adaptive-v0",
        "padawan-algebra-training-candidates-v0",
    }
    for suite in suites:
        if suite.suite_id not in executable_ids:
            continue
        for item in suite.items:
            expected = item.verifier_payload.get("expected_answer")
            parameters = item.verifier_payload.get("parameters")
            if suite.adapter_kind == AdapterKind.GENERATED_VERIFIER:
                if not isinstance(expected, dict):
                    raise ValueError("algebra oracle payload is missing")
                expression = expected.get("expression") or "x"
                oracle_response = {
                    "steps": (
                        {
                            "step_id": "s1",
                            "before": expression,
                            "operation": "identity replay of the frozen oracle",
                            "after": expression,
                            "assumptions": tuple(expected.get("exclusions", ())),
                        },
                    ),
                    "final_answer": expected,
                }
                grade = algebra.grade(
                    attempt_id=f"offline-oracle-{item.item_id}",
                    response=oracle_response,
                    expected_answer=expected,
                )
                if grade.outcome != GradeOutcome.CORRECT:
                    raise ValueError(f"frozen algebra oracle failed replay: {item.item_id}")
                normalized = grade.model_dump(mode="json", exclude={"grade_id", "created_at"})
                evidence.append(
                    sha256_digest(
                        {
                            "contract": "padawan.atlas.offline-oracle-replay.v1",
                            "item_digest": item.item_digest,
                            "verifier_id": item.verifier_id,
                            "verifier_version": item.verifier_version,
                            "oracle_response_digest": sha256_digest(oracle_response),
                            "grade": normalized,
                        }
                    )
                )
            elif suite.adapter_kind == AdapterKind.CONTEXT_MEMORY:
                if not isinstance(expected, dict) or not isinstance(parameters, dict):
                    raise ValueError("temporal oracle payload is missing")
                scenario_payload = parameters.get("scenario")
                decision_payload = expected.get("decision")
                scenario = TemporalScenarioManifest.model_validate_json(
                    canonical_json_bytes(scenario_payload)
                )
                decision = TemporalDecision.model_validate_json(
                    canonical_json_bytes(decision_payload)
                )
                result = temporal.verify(
                    scenario=scenario,
                    decision=decision,
                    created_at=CAMPAIGN_TIME,
                )
                if result.disposition.value != "verified":
                    raise ValueError(f"frozen temporal oracle failed replay: {item.item_id}")
                evidence.append(
                    sha256_digest(
                        {
                            "contract": "padawan.atlas.offline-oracle-replay.v1",
                            "item_digest": item.item_digest,
                            "verifier_id": result.verifier_id,
                            "verifier_version": result.verifier_version,
                            "result": result.model_dump(mode="json", exclude={"created_at"}),
                        }
                    )
                )
    return tuple(sorted(evidence))


def _offline_record(
    campaign: AtlasCampaignManifest,
    ontology: OntologyManifest,
    suites: tuple[AtlasSuiteManifest, ...],
) -> OfflineVerificationRecord:
    generated_items = sum(len(suite.items) for suite in suites)
    by_id = {suite.suite_id: suite for suite in suites}
    oracle_evidence = _execute_offline_oracles(suites)
    neighborhood_evidence = _verify_matched_neighborhoods(suites)
    media_suites = tuple(
        suite
        for suite in suites
        if any(gate.modality != Modality.TEXT for gate in suite.modality_gates)
    )
    external_suites = tuple(suite for suite in suites if suite.suite_id.endswith("registration-v0"))
    promotion = by_id["inkling-n-plus-one-promotion-v0"]
    allocated_trials = Counter[str]()
    for binding in campaign.suite_bindings:
        for condition_id in binding.condition_ids:
            allocated_trials[condition_id] += binding.planned_item_count * binding.trials_per_item
    checks = dict(
        sorted(
            {
                "campaign_partitions_are_pairwise_disjoint": not (
                    set(campaign.promotion_suite_digests) & set(campaign.adaptive_suite_digests)
                    or set(campaign.promotion_suite_digests)
                    & set(campaign.training_candidate_suite_digests)
                    or set(campaign.adaptive_suite_digests)
                    & set(campaign.training_candidate_suite_digests)
                ),
                "condition_request_ceilings_cover_every_bound_trial": all(
                    condition.max_requests == allocated_trials[condition.condition_id]
                    for condition in campaign.conditions
                ),
                "external_benchmark_content_is_absent": all(
                    not suite.items for suite in external_suites
                ),
                "local_item_and_suite_digests_validate": all(
                    suite.content_digest.startswith("sha256:")
                    and all(item.item_digest.startswith("sha256:") for item in suite.items)
                    for suite in suites
                ),
                "matched_neighborhood_structure_is_validated": len(neighborhood_evidence) >= 45,
                "authoritative_local_oracles_execute": len(oracle_evidence) == 70,
                "media_capability_fails_closed": all(
                    suite.status == SuiteStatus.BLOCKED
                    and any(
                        gate.status == ModalityGateStatus.UNVALIDATED
                        for gate in suite.modality_gates
                        if gate.modality != Modality.TEXT
                    )
                    for suite in media_suites
                ),
                "no_external_requests_or_cost": True,
                "promotion_suite_is_not_falsely_sealed": (
                    promotion.status == SuiteStatus.BLOCKED
                    and promotion.evaluation_suite_manifest_digest is None
                    and not promotion.items
                ),
                "source_claims_are_not_local_observations": True,
                "transport_240k_is_not_reasoning_evidence": (
                    by_id["inkling-240k-exact-retrieval-plan-v0"].status == SuiteStatus.BLOCKED
                ),
            }.items()
        )
    )
    provisional = OfflineVerificationRecord.model_construct(
        campaign_digest=campaign.manifest_digest,
        ontology_digest=ontology.manifest_digest,
        suite_digests=tuple(sorted(suite.content_digest for suite in suites)),
        generated_items=generated_items,
        structurally_validated_matched_neighborhoods=len(neighborhood_evidence),
        matched_neighborhood_evidence_digests=neighborhood_evidence,
        verified_oracles=len(oracle_evidence),
        oracle_evidence_digests=oracle_evidence,
        external_requests_made=0,
        external_cost_usd=0.0,
        checks=checks,
        unknowns=(
            "No W8A16 model trial has been executed by Capability Atlas v0.",
            "No source-BF16 reference endpoint or local comparison result is registered.",
            "The 45 matched neighborhoods have structural content/axis checks, but no "
            "family-specific metamorphic relation has yet been independently verified.",
            "Sibling serving evidence exercised only reasoning.effort='none'; numeric vendor effort labels and every tool structure require an adapter mapping, edge preflight, and behavioral validation.",
            "Image, audio, video, and mixed-media capability remain unvalidated.",
            "The promotion suite remains unmaterialized and lacks a core EvaluationSuiteManifest bridge.",
            "Blocked public benchmark registrations carry no local score.",
        ),
        record_digest=sha256_digest("pending"),
        created_at=CAMPAIGN_TIME,
    )
    identity = provisional.model_dump(mode="json", exclude={"record_digest", "created_at"})
    return OfflineVerificationRecord(
        **{
            **provisional.model_dump(mode="python"),
            "record_digest": sha256_digest(identity),
        }
    )


def _components() -> tuple[
    tuple[BenchmarkClaim, ...],
    tuple[DatasetGovernance, ...],
    OntologyManifest,
    tuple[AtlasSuiteManifest, ...],
    tuple[CampaignCondition, ...],
    AtlasCampaignManifest,
]:
    claims = source_claims()
    governance_records = dataset_governance_records()
    governance = {
        (record.benchmark_id, record.evaluation_class): record for record in governance_records
    }
    ontology = build_capability_ontology()
    suites = tuple(
        sorted(
            _local_suites(governance) + _external_suites(governance),
            key=lambda suite: suite.suite_id,
        )
    )
    conditions = _conditions()
    campaign = _campaign(
        claims=claims,
        ontology=ontology,
        suites=suites,
        conditions=conditions,
    )
    conditions = campaign.conditions
    return claims, governance_records, ontology, suites, conditions, campaign


def build_first_inkling_campaign_bundle() -> FirstInklingCampaignBundle:
    """Build the complete deterministic v0 catalog/campaign/reporting input bundle."""

    claims, governance, ontology, suites, conditions, campaign = _components()
    verification = _offline_record(campaign, ontology, suites)
    return FirstInklingCampaignBundle(
        claims=claims,
        governance=governance,
        ontology=ontology,
        suites=suites,
        campaign=campaign,
        harness_templates=_harness_templates(conditions),
        live_plan=_live_plan(campaign, conditions, suites),
        verification=verification,
        interchange_assumptions=(
            "Padawan owns behavioral PhenomenonManifest, ProbeSetManifest, item/result digests, and authoritative outcome evidence.",
            "The Inkling interpretability repository owns activation/router telemetry and interventions; it joins only on content-addressed phenomenon_id, phenomenon_digest, probe_set_id, probe-set manifest digest, item digests, and outcome digest.",
            "Interpretability telemetry cannot change an Atlas outcome, and Atlas labels cannot imply a mechanism without intervention evidence.",
            "No repository path, checkpoint alias, or mutable benchmark name is a cross-repository join key.",
        ),
        interaction_lab_seam=(
            "Only a consented, redacted Interaction Lab trace may create an ExploratoryFailureProposal.",
            "Raw chats remain exploratory and raw_chat_promoted=false; they are never benchmark evidence.",
            "A proposal is deduplicated, reproduced on independently generated Atlas items under a ResearchExecutionManifest, and reviewed before any challenge admission.",
            "Challenge admission records a new item/suite digest; adaptive, training-candidate, and sealed-promotion partitions remain disjoint.",
        ),
        unsupported_conclusions=(
            "The W8A16 artifact reproduces any vendor-reported source score.",
            "The W8A16 artifact has image, audio, video, or mixed-media capability.",
            "The 240k transport ladder demonstrates long-horizon reasoning or usable working memory.",
            "Any blocked public benchmark has been locally executed or approximately scored.",
            "Checkpoint N+1 improved until a genuinely sealed, disjoint promotion suite is executed under matched controls.",
        ),
    )


def build_first_inkling_campaign() -> AtlasCampaignManifest:
    """Return the stable first campaign manifest without performing external work."""

    return _components()[-1]


def offline_verification() -> OfflineVerificationRecord:
    """Regenerate and verify all locally achievable v0 campaign assets."""

    _, _, ontology, suites, _, campaign = _components()
    return _offline_record(campaign, ontology, suites)
