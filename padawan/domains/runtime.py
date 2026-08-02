from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from padawan.artifacts.store import ArtifactBackend
from padawan.corpus.registry import CorpusRegistry
from padawan.episodes.store import EpisodeStore
from padawan.experiments.engine import ExperimentEngine
from padawan.memory.lessons import LessonMemory
from padawan.models.contracts import ResearchRole
from padawan.models.database import Database
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from padawan.provenance.ledger import ProvenanceLedger
from padawan.state.store import StateStore
from padawan.updates.backends import ConsolidationBackend


@dataclass(frozen=True)
class DomainRuntimeContext:
    database: Database
    artifacts: ArtifactBackend
    corpus_registry: CorpusRegistry
    states: StateStore
    episodes: EpisodeStore
    experiments: ExperimentEngine
    provenance: ProvenanceLedger
    student_calls: IdempotentGenerationExecutor
    teacher_calls: IdempotentGenerationExecutor
    teacher_provider: str
    memory: LessonMemory
    memory_backend: ConsolidationBackend
    student_runtime_id: str
    student_runtime_version: str
    student_checkpoint_id: str
    student_role: ResearchRole
    lease_for: timedelta
