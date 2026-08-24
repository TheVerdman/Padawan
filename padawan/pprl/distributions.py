from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    ProcessDistributionRow,
    ProcessProgramRow,
    ProjectInstanceRow,
)
from padawan.pprl.contracts import (
    ProcessDistributionManifest,
    ProcessProgram,
    ProjectInstance,
    ProjectSplit,
)


@dataclass(frozen=True)
class GeneratedProject:
    task: dict[str, Any]
    difficulty: float
    difficulty_stratum: str
    environment_fingerprint: str
    metadata: dict[str, Any]


class ProjectGenerator(Protocol):
    generator_id: str
    generator_version: str
    generator_digest: str

    def generate(
        self,
        *,
        manifest: ProcessDistributionManifest,
        split: ProjectSplit,
        seed: int,
    ) -> GeneratedProject: ...


@dataclass(frozen=True)
class PlannedReplication:
    instance_id: str
    replication_index: int
    rollout_seed: int


class ProcessDistributionRegistry:
    async def register_distribution(
        self,
        session: AsyncSession,
        manifest: ProcessDistributionManifest,
    ) -> str:
        digest = sha256_digest(manifest)
        existing = await session.get(ProcessDistributionRow, digest)
        if existing is not None:
            if existing.record_json != manifest.model_dump(mode="json"):
                raise ValueError("distribution digest collision")
            return digest
        version_row = await session.scalar(
            select(ProcessDistributionRow).where(
                ProcessDistributionRow.distribution_id == manifest.distribution_id,
                ProcessDistributionRow.version == manifest.version,
            )
        )
        if version_row is not None:
            raise ValueError("distribution version already has different content")
        session.add(
            ProcessDistributionRow(
                distribution_digest=digest,
                distribution_id=manifest.distribution_id,
                version=manifest.version,
                generator_digest=manifest.generator.digest,
                record_json=manifest.model_dump(mode="json"),
                created_at=manifest.created_at,
            )
        )
        await session.flush()
        return digest

    async def get_distribution(
        self, session: AsyncSession, *, distribution_digest: str
    ) -> ProcessDistributionManifest:
        row = await session.get(ProcessDistributionRow, distribution_digest)
        if row is None:
            raise KeyError(distribution_digest)
        if sha256_digest(row.record_json) != row.distribution_digest:
            raise ValueError("stored process distribution digest is invalid")
        return ProcessDistributionManifest.model_validate(row.record_json, strict=False)

    async def register_program(
        self,
        session: AsyncSession,
        program: ProcessProgram,
    ) -> str:
        await self.get_distribution(session, distribution_digest=program.distribution_digest)
        digest = sha256_digest(program)
        existing = await session.get(ProcessProgramRow, digest)
        if existing is not None:
            if existing.record_json != program.model_dump(mode="json"):
                raise ValueError("process program digest collision")
            return digest
        version_row = await session.scalar(
            select(ProcessProgramRow).where(
                ProcessProgramRow.program_id == program.program_id,
                ProcessProgramRow.version == program.version,
            )
        )
        if version_row is not None:
            raise ValueError("process program version already has different content")
        session.add(
            ProcessProgramRow(
                program_digest=digest,
                program_id=program.program_id,
                version=program.version,
                distribution_digest=program.distribution_digest,
                persistence_mode=program.persistence_mode.value,
                reward_authority_kind=program.reward_authority.kind.value,
                record_json=program.model_dump(mode="json"),
                created_at=program.created_at,
            )
        )
        await session.flush()
        return digest

    async def get_program(self, session: AsyncSession, *, program_digest: str) -> ProcessProgram:
        row = await session.get(ProcessProgramRow, program_digest)
        if row is None:
            raise KeyError(program_digest)
        if sha256_digest(row.record_json) != row.program_digest:
            raise ValueError("stored process program digest is invalid")
        return ProcessProgram.model_validate(row.record_json, strict=False)

    async def sample(
        self,
        session: AsyncSession,
        *,
        distribution_digest: str,
        split: ProjectSplit,
        seed: int,
        generator: ProjectGenerator,
    ) -> ProjectInstance:
        manifest = await self.get_distribution(session, distribution_digest=distribution_digest)
        _validate_generator(manifest, generator)
        if split not in {partition.split for partition in manifest.partitions}:
            raise ValueError("requested split is absent from the distribution")
        identity_digest = sha256_digest(
            {
                "distribution_digest": distribution_digest,
                "split": split,
                "seed": seed,
            }
        )
        instance_id = f"project-instance-{identity_digest[7:31]}"
        existing = await session.get(ProjectInstanceRow, instance_id)
        if existing is not None:
            return _instance_from_row(existing)
        generated = generator.generate(manifest=manifest, split=split, seed=seed)
        if generated.difficulty_stratum not in manifest.difficulty_strata:
            raise ValueError("generator returned an undeclared difficulty stratum")
        instance = ProjectInstance(
            instance_id=instance_id,
            distribution_digest=distribution_digest,
            split=split,
            seed=seed,
            difficulty=generated.difficulty,
            difficulty_stratum=generated.difficulty_stratum,
            task_digest=sha256_digest(generated.task),
            environment_fingerprint=generated.environment_fingerprint,
            task=generated.task,
            metadata={key: generated.metadata[key] for key in sorted(generated.metadata)},
            created_at=manifest.created_at,
        )
        instance_digest = sha256_digest(instance)
        session.add(
            ProjectInstanceRow(
                instance_id=instance.instance_id,
                instance_digest=instance_digest,
                distribution_digest=instance.distribution_digest,
                split=instance.split.value,
                seed=instance.seed,
                difficulty=float(instance.difficulty),
                environment_fingerprint=instance.environment_fingerprint,
                record_json=instance.model_dump(mode="json"),
                created_at=instance.created_at,
            )
        )
        await session.flush()
        return instance

    async def plan_replications(
        self,
        session: AsyncSession,
        *,
        distribution_digest: str,
        instance_ids: tuple[str, ...],
    ) -> tuple[PlannedReplication, ...]:
        manifest = await self.get_distribution(session, distribution_digest=distribution_digest)
        unique_ids = tuple(sorted(set(instance_ids)))
        if len(unique_ids) != len(instance_ids):
            raise ValueError("replication plan instance IDs must be unique")
        if len(unique_ids) < manifest.replication.minimum_unique_instances:
            raise ValueError("replication plan has too few unique project instances")
        rows: list[ProjectInstanceRow | None] = []
        for instance_id in unique_ids:
            rows.append(await session.get(ProjectInstanceRow, instance_id))
        if any(row is None for row in rows):
            raise ValueError("replication plan refers to an unknown project instance")
        if any(row is not None and row.distribution_digest != distribution_digest for row in rows):
            raise ValueError("replication plan crosses project distributions")
        result: list[PlannedReplication] = []
        for instance_id in unique_ids:
            for replication_index in range(manifest.replication.minimum_rollouts_per_instance):
                result.append(
                    PlannedReplication(
                        instance_id=instance_id,
                        replication_index=replication_index,
                        rollout_seed=_derived_seed(
                            manifest.seed_namespace,
                            instance_id,
                            replication_index,
                        ),
                    )
                )
        return tuple(result)


def _validate_generator(manifest: ProcessDistributionManifest, generator: ProjectGenerator) -> None:
    identity = manifest.generator
    if (
        generator.generator_id != identity.component_id
        or generator.generator_version != identity.version
        or generator.generator_digest != identity.digest
    ):
        raise ValueError("project generator identity differs from the distribution manifest")


def _instance_from_row(row: ProjectInstanceRow) -> ProjectInstance:
    if sha256_digest(row.record_json) != row.instance_digest:
        raise ValueError("stored project instance digest is invalid")
    return ProjectInstance.model_validate(row.record_json, strict=False)


def _derived_seed(namespace: str, instance_id: str, replication_index: int) -> int:
    digest = sha256_digest(
        {
            "namespace": namespace,
            "instance_id": instance_id,
            "replication_index": replication_index,
        }
    )
    return int(digest[7:23], 16) % (2**63)
