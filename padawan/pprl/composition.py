from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import timedelta

from padawan.artifacts.factory import build_artifact_backend
from padawan.artifacts.store import ArtifactBackend, ArtifactCatalog
from padawan.config.settings import Settings
from padawan.governance.amber_store import AmberStore
from padawan.models.database import Database
from padawan.pprl.coordinator import ProcessCoordinator, ProcessWorkHandler
from padawan.pprl.distributions import ProcessDistributionRegistry
from padawan.pprl.store import ProcessStore
from padawan.training.compiler import TrainingCompiler


@dataclass
class PPRLApplication:
    """Composition root for governed project-scale learning.

    This root deliberately shares Padawan's database, artifact, provenance, and
    training infrastructure without routing project rollouts through the
    developmental episode or private Interaction Lab state machines.
    """

    database: Database
    artifacts: ArtifactBackend
    catalog: ArtifactCatalog
    distributions: ProcessDistributionRegistry
    amber: AmberStore
    processes: ProcessStore
    training: TrainingCompiler
    default_worker_id: str
    default_lease_for: timedelta

    def coordinator(
        self,
        *,
        handler: ProcessWorkHandler,
        worker_id: str | None = None,
        lease_for: timedelta | None = None,
    ) -> ProcessCoordinator:
        return ProcessCoordinator(
            database=self.database,
            store=self.processes,
            amber=self.amber,
            handler=handler,
            worker_id=worker_id or self.default_worker_id,
            lease_for=lease_for or self.default_lease_for,
        )

    async def close(self) -> None:
        close_artifacts = getattr(self.artifacts, "close", None)
        if close_artifacts is not None:
            result = close_artifacts()
            if inspect.isawaitable(result):
                await result
        await self.database.close()


def build_pprl_application(settings: Settings) -> PPRLApplication:
    """Build the non-provider PPRL control plane from ordinary Padawan settings."""

    database = Database(settings.database_url)
    artifacts = build_artifact_backend(settings)
    catalog = ArtifactCatalog(artifacts)
    amber = AmberStore()
    processes = ProcessStore(amber)
    return PPRLApplication(
        database=database,
        artifacts=artifacts,
        catalog=catalog,
        distributions=ProcessDistributionRegistry(),
        amber=amber,
        processes=processes,
        training=TrainingCompiler(artifacts, catalog),
        default_worker_id=settings.worker_id,
        default_lease_for=timedelta(seconds=settings.lease_seconds),
    )
