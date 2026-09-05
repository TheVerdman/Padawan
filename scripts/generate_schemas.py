#!/usr/bin/env python3
"""Generate deterministic JSON Schemas for Padawan's durable public records."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from padawan.adapters.prepared import PreparedGeneration
from padawan.artifacts.information import (
    ArtifactInformationRecord,
    ForensicArtifactRef,
    ProcessArtifactRef,
)
from padawan.atlas.contracts import (
    AdapterDescriptor,
    AtlasCampaignManifest,
    AtlasComparison,
    AtlasItemManifest,
    AtlasRunManifest,
    AtlasSnapshot,
    AtlasSuiteManifest,
    AtlasTrialRequest,
    AtlasTrialResult,
    BenchmarkClaim,
    CampaignExecutionBinding,
    ChallengeAdmissionDecision,
    DatasetGovernance,
    ExploratoryFailureProposal,
    ExploratoryReproduction,
    FailureCluster,
    MemoryInterventionEligibility,
    OfflineVerificationRecord,
    OntologyManifest,
    PhenomenonManifest,
    ProbeSetManifest,
    TrainingFailureEligibility,
    TrialAllocation,
)
from padawan.atlas.evidence_contracts import (
    AtlasEvidenceDisclosurePolicy,
    AtlasEvidenceOriginReview,
    AtlasEvidenceSourceScope,
    AtlasProcessEvidenceAdmissionRecord,
    AtlasTrialEvidenceSource,
)
from padawan.domains.contracts import (
    EnvironmentSnapshot,
    RewardPolicy,
    RewardRecord,
    TaskManifest,
    TrainingEligibilityDecision,
    VerifierResult,
)
from padawan.domains.legal.appellate.contracts import (
    AppellateClosedRecord,
    AppellateCourtPack,
    AppellateScenarioManifest,
    AppellateSemanticAssessment,
    AppellateSemanticAssessmentDraft,
    AppellateSubmission,
    AppellateSubmissionDraft,
    AppellateVerificationBundle,
    AuthorityCurrentnessAssessment,
)
from padawan.domains.magellan_improvement.contracts import (
    MagellanAgentTrace,
    MagellanEnvironmentAssessment,
    MagellanEnvironmentHandshake,
    MagellanMatchedWorldManifest,
    MagellanRepositorySnapshot,
    MagellanScenarioManifest,
    MagellanVerificationBundle,
    MagellanWorldSnapshot,
)
from padawan.domains.temporal_grounding.contracts import (
    TemporalScenarioManifest,
    TemporalScenarioOracle,
)
from padawan.governance.amber import (
    AmberActionRequest,
    AmberAdmissionDecision,
    AmberAuthorizationEnvelope,
    AmberAuthorizationEvent,
)
from padawan.interaction.contracts import (
    ExploratoryInteractionManifest,
    InteractionConsentEventRecord,
    InteractionFeedbackRecord,
    InteractionMessageRecord,
    InteractionSessionRecord,
    InteractionTraceRecord,
    StudentTargetDescriptor,
    TargetReadinessRecord,
)
from padawan.models.contracts import (
    AttemptRecord,
    CompactedStateRecord,
    CompetencyRecord,
    CorpusItemRecord,
    DevelopmentalEpisode,
    ExposureRecord,
    GradeRecord,
    LessonRecord,
    SourceRights,
    StateForkRecord,
    StudentStateRecord,
    TeacherInterventionRecord,
    TransferTrialRecord,
)
from padawan.models.research_contracts import (
    CheckpointComparisonRecord,
    CheckpointDecisionRecord,
    CheckpointEvaluationRecord,
    CheckpointGateEvidenceScope,
    CheckpointManifest,
    CheckpointPromotionPolicy,
    EvaluationOutcome,
    EvaluationSchedule,
    EvaluationSuiteManifest,
    HarnessProfile,
    ResearchComparabilityAssessment,
    ResearchExecutionManifest,
    StudyManifest,
    StudyResultRecord,
)
from padawan.pprl.container_contracts import (
    ContainerRuntimeIdentity,
    ProcessContainerProfile,
    ProcessContainerReceipt,
    ProcessContainerWorkload,
)
from padawan.pprl.content_contracts import (
    ProcessContentAdmission,
    ProcessContentPolicy,
    ProcessContentSchema,
)
from padawan.pprl.contracts import (
    ProcessDistributionManifest,
    ProcessEventRecord,
    ProcessExecutionManifest,
    ProcessForkRecord,
    ProcessOutcomeAssessment,
    ProcessProgram,
    ProcessRolloutRecord,
    ProcessTrainingEligibilityDecision,
    ProcessWorkerOutput,
    ProjectInstance,
    ProjectStateVersion,
)
from padawan.pprl.evidence_contracts import (
    EvidenceAdmissionPolicy,
    ProcessEvidenceAdmission,
    ProcessEvidenceAdmissionRecord,
)
from padawan.pprl.generation_contracts import ProcessGenerationPolicy, ProcessGenerationWorkload
from padawan.pprl.observation_contracts import (
    ProcessObservationDecisionBinding,
    ProcessObservationPolicy,
    ProcessObservationReceipt,
    ProcessWorkerObservation,
    ProcessWorkerState,
)
from padawan.pprl.recovered_evidence_contracts import (
    RecoveredEffectSource,
    RecoveredEvidenceOriginReview,
    RecoveredProcessEvidenceAdmissionRecord,
    RecoveryEvidenceDisclosurePolicy,
)
from padawan.pprl.recovery_contracts import (
    ProcessRecoveryEffect,
    ProcessRecoveryReceipt,
    ProcessRecoveryRequest,
    ProcessRecoverySource,
)
from padawan.pprl.resource_contracts import (
    ProcessModelRate,
    ProcessResourceEvent,
    ProcessResourceGrant,
    ProcessResourceReservation,
    ProcessResources,
)
from padawan.pprl.worker_contracts import (
    ProcessWorkerDecisionBinding,
    ProcessWorkerLeaseAssignment,
    ProcessWorkerRegistration,
    ProcessWorkerRevocation,
    ProcessWorkerScope,
)
from padawan.pprl.worker_protocol_contracts import (
    ProcessWorkerProposal,
    ProcessWorkerReply,
    ProcessWorkerRequest,
    ProcessWorkerRequestPayload,
    ProcessWorkerRequestReceipt,
)
from padawan.temporal.contracts import (
    DurationProfile,
    OperationSpanEventRecord,
    OperationSpanRecord,
    TemporalDecision,
    TemporalFrame,
)
from padawan.training.contracts import (
    AuthoredDemonstration,
    AuthoredSFTTrainingRow,
    CompilerInvocation,
    ContinuedPretrainingRow,
    EvidenceLedgerEntry,
    NormalizedEpisodeEntry,
    PPRLForkPreferenceTrainingRow,
    PPRLTrajectoryTrainingRow,
    PPRLVerifiableTrainingRow,
    PreferenceTrainingRow,
    ProcessTrainingRow,
    RLVRTrainingRow,
    SealedEvaluationRow,
    SFTTrainingRow,
    TrainingBundleManifest,
    TrainingBundleVerification,
    TrainingExclusionRecord,
    TrainingProductManifest,
    TrainingSourceDecision,
    TrainingSourceDocument,
)
from padawan.training.process_projection_contracts import (
    ProcessForkPreferencePayload,
    ProcessLearningOutcome,
    ProcessLearningOutcomeComponent,
    ProcessLearningStep,
    ProcessProjectionExclusion,
    ProcessProjectionProduct,
    ProcessProjectionRowLink,
    ProcessProjectionSource,
    ProcessTrainingProjectionPolicy,
    ProcessTrainingProjectionReceipt,
    ProcessTrainingTaskSchema,
    ProcessTrajectoryPayload,
    ProcessVerifiablePayload,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas"
SCHEMA_BASE = "https://schemas.padawan.local/v1"
PUBLIC_MODELS: tuple[tuple[str, type[BaseModel]], ...] = (
    ("amber-action-request", AmberActionRequest),
    ("amber-admission-decision", AmberAdmissionDecision),
    ("amber-authorization-envelope", AmberAuthorizationEnvelope),
    ("amber-authorization-event", AmberAuthorizationEvent),
    ("artifact-information", ArtifactInformationRecord),
    ("forensic-artifact-reference", ForensicArtifactRef),
    ("process-artifact-reference", ProcessArtifactRef),
    ("evidence-admission-policy", EvidenceAdmissionPolicy),
    ("process-evidence-admission", ProcessEvidenceAdmission),
    ("process-content-admission", ProcessContentAdmission),
    ("process-content-policy", ProcessContentPolicy),
    ("process-content-schema", ProcessContentSchema),
    ("process-evidence-admission-record", ProcessEvidenceAdmissionRecord),
    ("atlas-evidence-disclosure-policy", AtlasEvidenceDisclosurePolicy),
    ("atlas-evidence-origin-review", AtlasEvidenceOriginReview),
    ("atlas-evidence-source-scope", AtlasEvidenceSourceScope),
    ("atlas-process-evidence-admission-record", AtlasProcessEvidenceAdmissionRecord),
    ("atlas-trial-evidence-source", AtlasTrialEvidenceSource),
    ("process-worker-output", ProcessWorkerOutput),
    ("process-worker-state", ProcessWorkerState),
    ("process-worker-observation", ProcessWorkerObservation),
    ("process-observation-policy", ProcessObservationPolicy),
    ("process-observation-receipt", ProcessObservationReceipt),
    ("process-observation-decision-binding", ProcessObservationDecisionBinding),
    ("prepared-generation", PreparedGeneration),
    ("process-generation-policy", ProcessGenerationPolicy),
    ("process-generation-workload", ProcessGenerationWorkload),
    ("container-runtime-identity", ContainerRuntimeIdentity),
    ("process-container-profile", ProcessContainerProfile),
    ("process-container-workload", ProcessContainerWorkload),
    ("process-container-receipt", ProcessContainerReceipt),
    ("process-worker-scope", ProcessWorkerScope),
    ("process-recovery-request", ProcessRecoveryRequest),
    ("process-recovery-source", ProcessRecoverySource),
    ("process-recovery-effect", ProcessRecoveryEffect),
    ("process-recovery-receipt", ProcessRecoveryReceipt),
    ("recovery-evidence-disclosure-policy", RecoveryEvidenceDisclosurePolicy),
    ("recovered-effect-source", RecoveredEffectSource),
    ("recovered-evidence-origin-review", RecoveredEvidenceOriginReview),
    ("recovered-process-evidence-admission-record", RecoveredProcessEvidenceAdmissionRecord),
    ("process-worker-registration", ProcessWorkerRegistration),
    ("process-worker-revocation", ProcessWorkerRevocation),
    ("process-worker-lease-assignment", ProcessWorkerLeaseAssignment),
    ("process-worker-decision-binding", ProcessWorkerDecisionBinding),
    ("process-worker-proposal", ProcessWorkerProposal),
    ("process-worker-request-payload", ProcessWorkerRequestPayload),
    ("process-worker-request", ProcessWorkerRequest),
    ("process-worker-reply", ProcessWorkerReply),
    ("process-worker-request-receipt", ProcessWorkerRequestReceipt),
    ("process-resources", ProcessResources),
    ("process-model-rate", ProcessModelRate),
    ("process-resource-grant", ProcessResourceGrant),
    ("process-resource-reservation", ProcessResourceReservation),
    ("process-resource-event", ProcessResourceEvent),
    ("process-learning-outcome-component", ProcessLearningOutcomeComponent),
    ("process-learning-outcome", ProcessLearningOutcome),
    ("process-learning-step", ProcessLearningStep),
    ("process-trajectory-payload", ProcessTrajectoryPayload),
    ("process-verifiable-payload", ProcessVerifiablePayload),
    ("process-fork-preference-payload", ProcessForkPreferencePayload),
    ("process-training-task-schema", ProcessTrainingTaskSchema),
    ("process-training-projection-policy", ProcessTrainingProjectionPolicy),
    ("process-projection-source", ProcessProjectionSource),
    ("process-projection-row-link", ProcessProjectionRowLink),
    ("process-projection-product", ProcessProjectionProduct),
    ("process-projection-exclusion", ProcessProjectionExclusion),
    ("process-training-projection-receipt", ProcessTrainingProjectionReceipt),
    ("atlas-adapter-descriptor", AdapterDescriptor),
    ("atlas-campaign-manifest", AtlasCampaignManifest),
    ("atlas-campaign-execution-binding", CampaignExecutionBinding),
    ("atlas-challenge-admission", ChallengeAdmissionDecision),
    ("atlas-comparison", AtlasComparison),
    ("atlas-dataset-governance", DatasetGovernance),
    ("atlas-exploratory-failure-proposal", ExploratoryFailureProposal),
    ("atlas-exploratory-reproduction", ExploratoryReproduction),
    ("atlas-failure-cluster", FailureCluster),
    ("atlas-item-manifest", AtlasItemManifest),
    ("atlas-memory-intervention-eligibility", MemoryInterventionEligibility),
    ("atlas-offline-verification", OfflineVerificationRecord),
    ("atlas-ontology-manifest", OntologyManifest),
    ("atlas-phenomenon-manifest", PhenomenonManifest),
    ("atlas-probe-set-manifest", ProbeSetManifest),
    ("atlas-run-manifest", AtlasRunManifest),
    ("atlas-snapshot", AtlasSnapshot),
    ("atlas-source-claim", BenchmarkClaim),
    ("atlas-suite-manifest", AtlasSuiteManifest),
    ("atlas-training-failure-eligibility", TrainingFailureEligibility),
    ("atlas-trial-allocation", TrialAllocation),
    ("atlas-trial-request", AtlasTrialRequest),
    ("atlas-trial-result", AtlasTrialResult),
    ("appellate-closed-record", AppellateClosedRecord),
    ("appellate-court-pack", AppellateCourtPack),
    ("appellate-currentness-assessment", AuthorityCurrentnessAssessment),
    ("appellate-scenario-manifest", AppellateScenarioManifest),
    ("appellate-semantic-assessment", AppellateSemanticAssessment),
    ("appellate-semantic-assessment-draft", AppellateSemanticAssessmentDraft),
    ("appellate-submission", AppellateSubmission),
    ("appellate-submission-draft", AppellateSubmissionDraft),
    ("appellate-verification-bundle", AppellateVerificationBundle),
    ("attempt", AttemptRecord),
    ("authored-demonstration", AuthoredDemonstration),
    ("authored-sft-training-row", AuthoredSFTTrainingRow),
    ("checkpoint-comparison", CheckpointComparisonRecord),
    ("checkpoint-decision", CheckpointDecisionRecord),
    ("checkpoint-evaluation", CheckpointEvaluationRecord),
    ("checkpoint-gate-evidence-scope", CheckpointGateEvidenceScope),
    ("checkpoint-manifest", CheckpointManifest),
    ("checkpoint-promotion-policy", CheckpointPromotionPolicy),
    ("compiler-invocation", CompilerInvocation),
    ("compacted-state", CompactedStateRecord),
    ("competency", CompetencyRecord),
    ("corpus-item", CorpusItemRecord),
    ("continued-pretraining-row", ContinuedPretrainingRow),
    ("developmental-episode", DevelopmentalEpisode),
    ("duration-profile", DurationProfile),
    ("environment-snapshot", EnvironmentSnapshot),
    ("evidence-ledger-entry", EvidenceLedgerEntry),
    ("evaluation-outcome", EvaluationOutcome),
    ("evaluation-schedule", EvaluationSchedule),
    ("evaluation-suite-manifest", EvaluationSuiteManifest),
    ("exposure", ExposureRecord),
    ("exploratory-interaction-manifest", ExploratoryInteractionManifest),
    ("grade", GradeRecord),
    ("harness-profile", HarnessProfile),
    ("interaction-consent-event", InteractionConsentEventRecord),
    ("interaction-feedback", InteractionFeedbackRecord),
    ("interaction-message", InteractionMessageRecord),
    ("interaction-session", InteractionSessionRecord),
    ("interaction-trace", InteractionTraceRecord),
    ("lesson", LessonRecord),
    ("magellan-agent-trace", MagellanAgentTrace),
    ("magellan-environment-assessment", MagellanEnvironmentAssessment),
    ("magellan-environment-handshake", MagellanEnvironmentHandshake),
    ("magellan-matched-world-manifest", MagellanMatchedWorldManifest),
    ("magellan-repository-snapshot", MagellanRepositorySnapshot),
    ("magellan-scenario-manifest", MagellanScenarioManifest),
    ("magellan-verification-bundle", MagellanVerificationBundle),
    ("magellan-world-snapshot", MagellanWorldSnapshot),
    ("normalized-episode-entry", NormalizedEpisodeEntry),
    ("operation-span", OperationSpanRecord),
    ("operation-span-event", OperationSpanEventRecord),
    ("preference-training-row", PreferenceTrainingRow),
    ("pprl-fork-preference-training-row", PPRLForkPreferenceTrainingRow),
    ("pprl-trajectory-training-row", PPRLTrajectoryTrainingRow),
    ("pprl-verifiable-training-row", PPRLVerifiableTrainingRow),
    ("process-distribution-manifest", ProcessDistributionManifest),
    ("process-event", ProcessEventRecord),
    ("process-execution-manifest", ProcessExecutionManifest),
    ("process-fork", ProcessForkRecord),
    ("process-outcome-assessment", ProcessOutcomeAssessment),
    ("process-program", ProcessProgram),
    ("process-rollout", ProcessRolloutRecord),
    ("process-training-eligibility", ProcessTrainingEligibilityDecision),
    ("process-training-row", ProcessTrainingRow),
    ("project-instance", ProjectInstance),
    ("project-state", ProjectStateVersion),
    ("reward", RewardRecord),
    ("reward-policy", RewardPolicy),
    ("research-comparability-assessment", ResearchComparabilityAssessment),
    ("research-execution-manifest", ResearchExecutionManifest),
    ("rlvr-training-row", RLVRTrainingRow),
    ("sealed-evaluation-row", SealedEvaluationRow),
    ("sft-training-row", SFTTrainingRow),
    ("source-rights", SourceRights),
    ("state-fork", StateForkRecord),
    ("student-state", StudentStateRecord),
    ("student-target-descriptor", StudentTargetDescriptor),
    ("study-manifest", StudyManifest),
    ("study-result", StudyResultRecord),
    ("task-manifest", TaskManifest),
    ("target-readiness", TargetReadinessRecord),
    ("teacher-intervention", TeacherInterventionRecord),
    ("temporal-decision", TemporalDecision),
    ("temporal-frame", TemporalFrame),
    ("temporal-scenario-manifest", TemporalScenarioManifest),
    ("temporal-scenario-oracle", TemporalScenarioOracle),
    ("transfer-trial", TransferTrialRecord),
    ("training-eligibility", TrainingEligibilityDecision),
    ("training-bundle-manifest", TrainingBundleManifest),
    ("training-bundle-verification", TrainingBundleVerification),
    ("training-exclusion", TrainingExclusionRecord),
    ("training-product-manifest", TrainingProductManifest),
    ("training-source-decision", TrainingSourceDecision),
    ("training-source-document", TrainingSourceDocument),
    ("verifier-result", VerifierResult),
)


def _schema_bytes(name: str, model: type[BaseModel]) -> bytes:
    schema: dict[str, Any] = model.model_json_schema(mode="validation")
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{SCHEMA_BASE}/{name}.schema.json",
        **schema,
    }
    return (json.dumps(schema, indent=2, sort_keys=True) + "\n").encode()


def expected_files() -> dict[Path, bytes]:
    files = {
        SCHEMA_ROOT / f"{name}.schema.json": _schema_bytes(name, model)
        for name, model in PUBLIC_MODELS
    }
    entries = [
        {
            "name": name,
            "schema": f"{name}.schema.json",
            "sha256": hashlib.sha256(files[SCHEMA_ROOT / f"{name}.schema.json"]).hexdigest(),
        }
        for name, _model in PUBLIC_MODELS
    ]
    index = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "contract_version": "1.0.0",
        "generated_by": "scripts/generate_schemas.py",
        "schemas": entries,
    }
    files[SCHEMA_ROOT / "index.json"] = (
        json.dumps(index, indent=2, sort_keys=True) + "\n"
    ).encode()
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="fail if checked-in schemas differ from models"
    )
    args = parser.parse_args()
    expected = expected_files()
    if args.check:
        changed = [
            str(path.relative_to(ROOT))
            for path, content in expected.items()
            if not path.exists() or path.read_bytes() != content
        ]
        if changed:
            print("schema drift: " + ", ".join(changed))
            return 1
        print(f"{len(expected) - 1} schemas match their Pydantic contracts")
        return 0
    SCHEMA_ROOT.mkdir(parents=True, exist_ok=True)
    for path, content in expected.items():
        path.write_bytes(content)
    print(f"wrote {len(expected) - 1} schemas and schemas/index.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
