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
from padawan.models.contracts import (
    AttemptRecord,
    CompactedStateRecord,
    CompetencyRecord,
    CorpusItemRecord,
    DevelopmentalEpisode,
    ExposureRecord,
    GradeRecord,
    LessonRecord,
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
    StudyManifest,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas"
SCHEMA_BASE = "https://schemas.padawan.local/v1"
PUBLIC_MODELS: tuple[tuple[str, type[BaseModel]], ...] = (
    ("attempt", AttemptRecord),
    ("checkpoint-comparison", CheckpointComparisonRecord),
    ("checkpoint-decision", CheckpointDecisionRecord),
    ("checkpoint-evaluation", CheckpointEvaluationRecord),
    ("checkpoint-manifest", CheckpointManifest),
    ("checkpoint-promotion-policy", CheckpointPromotionPolicy),
    ("compacted-state", CompactedStateRecord),
    ("competency", CompetencyRecord),
    ("corpus-item", CorpusItemRecord),
    ("developmental-episode", DevelopmentalEpisode),
    ("environment-snapshot", EnvironmentSnapshot),
    ("evaluation-outcome", EvaluationOutcome),
    ("evaluation-schedule", EvaluationSchedule),
    ("evaluation-suite-manifest", EvaluationSuiteManifest),
    ("exposure", ExposureRecord),
    ("grade", GradeRecord),
    ("lesson", LessonRecord),
    ("reward", RewardRecord),
    ("reward-policy", RewardPolicy),
    ("state-fork", StateForkRecord),
    ("student-state", StudentStateRecord),
    ("study-manifest", StudyManifest),
    ("task-manifest", TaskManifest),
    ("teacher-intervention", TeacherInterventionRecord),
    ("transfer-trial", TransferTrialRecord),
    ("training-eligibility", TrainingEligibilityDecision),
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
