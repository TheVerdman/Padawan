#!/usr/bin/env python3
"""Generate deterministic JSON Schemas for Padawan's durable public records."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

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
    CheckpointManifest,
    CheckpointPromotionPolicy,
    EvaluationOutcome,
    EvaluationSchedule,
    EvaluationSuiteManifest,
    HarnessProfile,
    ResearchComparabilityAssessment,
    ResearchExecutionManifest,
    StudyManifest,
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

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas"
SCHEMA_BASE = "https://schemas.padawan.local/v1"
PUBLIC_MODELS: tuple[tuple[str, type[BaseModel]], ...] = (
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
    ("grade", GradeRecord),
    ("harness-profile", HarnessProfile),
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
    ("process-training-row", ProcessTrainingRow),
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
    ("study-manifest", StudyManifest),
    ("task-manifest", TaskManifest),
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
