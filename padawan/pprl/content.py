"""Structural process content admission inside the trusted broker.

These receipts do not attest semantic safety, authentic authorship, or a sandbox.
Registry classes are trusted configuration, never worker-provided Python code.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from typing import Any, Concatenate, Literal

from pydantic import Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.artifacts.information import InformationClass, ProcessArtifactRef
from padawan.models.contracts import NonEmpty, StrictRecord
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import (
    ArtifactInformationRow,
    ArtifactRow,
    ProcessContainerReceiptRow,
    ProcessContainerWorkloadRow,
    ProcessContentAdmissionRow,
    ProcessEventRow,
    ProcessResourceEventRow,
    ProcessResourceGrantRow,
    ProcessResourceReservationRow,
    ProcessRolloutRow,
    ProcessStateRow,
)
from padawan.pprl.content_contracts import (
    ProcessContentAdmission,
    ProcessContentPolicy,
    ProcessContentSchema,
    ProcessContentSurface,
)
from padawan.pprl.contracts import (
    ProcessEventKind,
    ProcessEventRecord,
    ProjectStatePayload,
    ProjectStateVersion,
)


class ProcessContentDeniedError(PermissionError):
    """Uniform boundary failure; privileged malformed records must not be echoed."""


class _CoreEvent(StrictRecord):
    summary: NonEmpty | None = None
    plan: tuple[NonEmpty, ...] = ()


class _CoreIntervention(StrictRecord):
    description: NonEmpty | None = None
    control_condition: NonEmpty | None = None
    treatment_condition: NonEmpty | None = None

    @model_validator(mode="after")
    def intervention_is_explicit(self) -> _CoreIntervention:
        if (self.control_condition is None) != (self.treatment_condition is None):
            raise ValueError("paired intervention requires both condition identities")
        if self.description is None and self.control_condition is None:
            raise ValueError("intervention requires a description or paired conditions")
        return self


class _ExtensionEnvelope(StrictRecord):
    schema_id: NonEmpty
    content: dict[str, Any]


class _ForkEvent(StrictRecord):
    conditions: tuple[NonEmpty, ...]
    fork_id: NonEmpty
    intervention: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class RegisteredProcessSchema:
    namespace: str
    version: str
    surface: ProcessContentSurface
    model: type[StrictRecord]


def _guard_content[**P, R](
    operation: Callable[Concatenate[ProcessContentBoundary, AsyncSession, P], Awaitable[R]],
) -> Callable[Concatenate[ProcessContentBoundary, AsyncSession, P], Awaitable[R]]:
    @wraps(operation)
    async def wrapped(
        self: ProcessContentBoundary, session: AsyncSession, /, *args: P.args, **kwargs: P.kwargs
    ) -> R:
        cancelled = False
        try:
            return await operation(self, session, *args, **kwargs)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            pass
        if cancelled:
            raise asyncio.CancelledError("process content validation cancelled")
        raise ProcessContentDeniedError("process content admission denied")

    return wrapped


class ProcessContentBoundary:
    def __init__(
        self,
        *,
        schemas: tuple[RegisteredProcessSchema, ...] = (),
        policy: ProcessContentPolicy | None = None,
    ) -> None:
        self._models: dict[tuple[ProcessContentSurface, str], type[StrictRecord]] = {}
        identities = []
        for entry in schemas:
            if not issubclass(entry.model, StrictRecord):
                raise ValueError("process extension models must be closed strict records")
            definition = entry.model.model_json_schema()
            _closed_schema(definition, definition, ())
            identity = ProcessContentSchema(
                namespace=entry.namespace,
                version=entry.version,
                surface=entry.surface,
                json_schema=definition,
                schema_digest=sha256_digest(definition),
            )
            key = (entry.surface, entry.namespace)
            if key in self._models:
                raise ValueError("process extension surface/namespace is already registered")
            self._models[key] = entry.model
            identities.append(identity)
        schemas_record = tuple(sorted(identities, key=lambda item: (item.surface, item.namespace)))
        core_digest = sha256_digest(
            {
                "validator": "padawan.pprl.closed-content.v1",
                "state": ProjectStatePayload.model_json_schema(),
                "event": _CoreEvent.model_json_schema(),
                "fork": _ForkEvent.model_json_schema(),
                "intervention": _CoreIntervention.model_json_schema(),
                "extension_envelope": _ExtensionEnvelope.model_json_schema(),
            }
        )
        configured = policy or ProcessContentPolicy(
            policy_id="padawan.pprl.content",
            version="1.0.0",
            core_schema_digest=core_digest,
            schemas=schemas_record,
        )
        self._policy = ProcessContentPolicy.model_validate_json(configured.model_dump_json())
        if self._policy.core_schema_digest != core_digest or self._policy.schemas != schemas_record:
            raise ValueError("process content policy differs from its composed schema registry")

    @property
    def policy(self) -> ProcessContentPolicy:
        return self._policy.model_copy(deep=True)

    @_guard_content
    async def check_state(self, session: AsyncSession, payload: ProjectStatePayload) -> None:
        strings = self._bounded_strings(payload, maximum_bytes=self._policy.maximum_state_bytes)
        # Reparse to catch mutated model_copy values without rewriting the input.
        parsed = ProjectStatePayload.model_validate_json(payload.model_dump_json())
        refs = parsed.artifact_refs
        if any(not isinstance(reference, ProcessArtifactRef) for reference in refs):
            raise ValueError("state contains a legacy storage reference")
        allowed = {
            reference.process_artifact_id
            for reference in refs
            if isinstance(reference, ProcessArtifactRef)
        }
        links = (
            *parsed.memory_refs,
            *(ref for claim in parsed.claims for ref in claim.evidence_refs),
            *(ref for hypothesis in parsed.hypotheses for ref in hypothesis.evidence_refs),
        )
        if not set(links).issubset(allowed):
            raise ValueError("process memory/evidence link lacks an explicit artifact declaration")
        for namespace, value in parsed.extension_state.items():
            self._extension(ProcessContentSurface.STATE_EXTENSION, namespace, value)
        await self._check_identifiers(
            session,
            strings,
            allowed=allowed,
            admitted_digests={
                ref.content_digest for ref in refs if isinstance(ref, ProcessArtifactRef)
            },
        )

    @_guard_content
    async def check_event(self, session: AsyncSession, record: ProcessEventRecord) -> None:
        strings = self._bounded_strings(
            record.payload, maximum_bytes=self._policy.maximum_event_bytes
        )
        if any(not isinstance(reference, ProcessArtifactRef) for reference in record.artifact_refs):
            raise ValueError("event contains a legacy storage reference")
        if record.kind == ProcessEventKind.ROLLOUT_FORKED:
            fork = _ForkEvent.model_validate_json(canonical_json_bytes(record.payload))
            self._intervention(fork.intervention)
        else:
            self._event_or_intervention(ProcessContentSurface.EVENT, record.payload)
        allowed = {
            reference.process_artifact_id
            for reference in record.artifact_refs
            if isinstance(reference, ProcessArtifactRef)
        }
        await self._check_identifiers(
            session,
            strings,
            allowed=allowed,
            admitted_digests={
                ref.content_digest
                for ref in record.artifact_refs
                if isinstance(ref, ProcessArtifactRef)
            },
        )

    @_guard_content
    async def check_intervention(self, session: AsyncSession, value: dict[str, Any]) -> None:
        strings = self._bounded_strings(value, maximum_bytes=self._policy.maximum_event_bytes)
        self._intervention(value)
        await self._check_identifiers(session, strings, allowed=set(), admitted_digests=set())

    def _intervention(self, value: dict[str, Any]) -> None:
        if value:
            self._event_or_intervention(ProcessContentSurface.FORK_INTERVENTION, value)

    def _event_or_intervention(self, surface: ProcessContentSurface, value: dict[str, Any]) -> None:
        if "schema_id" in value:
            envelope = _ExtensionEnvelope.model_validate_json(canonical_json_bytes(value))
            self._extension(surface, envelope.schema_id, envelope.content)
        elif surface == ProcessContentSurface.EVENT:
            _CoreEvent.model_validate_json(canonical_json_bytes(value))
        else:
            _CoreIntervention.model_validate_json(canonical_json_bytes(value))

    def _extension(self, surface: ProcessContentSurface, namespace: str, value: Any) -> None:
        model = self._models.get((surface, namespace))
        if model is None:
            raise ValueError("unknown process extension schema")
        encoded = canonical_json_bytes(value)
        parsed = model.model_validate_json(encoded)
        # Reject serializers/default insertion that could add an unvalidated channel.
        if canonical_json_bytes(parsed.model_dump(mode="json", exclude_unset=True)) != encoded:
            raise ValueError("process extension validation changed its content")

    @_guard_content
    async def admit_state(
        self,
        session: AsyncSession,
        state: ProjectStateVersion,
        *,
        execution_digest: str,
        now: datetime,
    ) -> None:
        async with session.begin_nested():
            await self.check_state(session, state.payload)
            await self._admit(
                session,
                kind="state",
                record_id=state.state_id,
                digest=state.state_digest,
                execution_digest=execution_digest,
                created_at=state.created_at,
                now=now,
                source_json=state.model_dump(mode="json"),
            )

    @_guard_content
    async def admit_event(
        self,
        session: AsyncSession,
        event: ProcessEventRecord,
        *,
        execution_digest: str,
        now: datetime,
    ) -> None:
        async with session.begin_nested():
            await self.check_event(session, event)
            await self._admit(
                session,
                kind="event",
                record_id=event.event_id,
                digest=event.event_digest,
                execution_digest=execution_digest,
                created_at=event.created_at,
                now=now,
                source_json=event.model_dump(mode="json"),
            )

    async def _admit(
        self,
        session: AsyncSession,
        *,
        kind: Literal["state", "event"],
        record_id: str,
        digest: str,
        execution_digest: str,
        created_at: datetime,
        now: datetime,
        source_json: dict[str, Any],
    ) -> None:
        if now.tzinfo is None or created_at != now:
            raise ValueError("content receipts are issued with the new record, not retroactively")
        await _verify_source(
            session,
            kind=kind,
            record_id=record_id,
            digest=digest,
            execution_digest=execution_digest,
            source_json=source_json,
        )
        receipt = ProcessContentAdmission(
            record_kind=kind,
            record_id=record_id,
            record_digest=digest,
            execution_digest=execution_digest,
            policy=self._policy,
            policy_digest=self._policy.digest,
            admitted_at=now,
        )
        existing = await session.get(ProcessContentAdmissionRow, (kind, record_id))
        if existing is not None:
            if _receipt(existing) != receipt:
                raise ValueError("process content receipt is immutable")
            return
        session.add(
            ProcessContentAdmissionRow(
                record_kind=kind,
                record_id=record_id,
                source_digest=digest,
                execution_digest=execution_digest,
                policy_digest=receipt.policy_digest,
                record_digest=receipt.digest,
                record_json=receipt.model_dump(mode="json"),
                created_at=now,
            )
        )
        await session.flush()

    @_guard_content
    async def verify_state(
        self,
        session: AsyncSession,
        state: ProjectStateVersion,
        *,
        execution_digest: str,
        now: datetime,
    ) -> None:
        await self.check_state(session, state.payload)
        await self._verify(
            session,
            kind="state",
            record_id=state.state_id,
            digest=state.state_digest,
            execution_digest=execution_digest,
            created_at=state.created_at,
            now=now,
            source_json=state.model_dump(mode="json"),
        )

    @_guard_content
    async def verify_event(
        self,
        session: AsyncSession,
        event: ProcessEventRecord,
        *,
        execution_digest: str,
        now: datetime,
    ) -> None:
        await self.check_event(session, event)
        await self._verify(
            session,
            kind="event",
            record_id=event.event_id,
            digest=event.event_digest,
            execution_digest=execution_digest,
            created_at=event.created_at,
            now=now,
            source_json=event.model_dump(mode="json"),
        )

    async def _verify(
        self,
        session: AsyncSession,
        *,
        kind: Literal["state", "event"],
        record_id: str,
        digest: str,
        execution_digest: str,
        created_at: datetime,
        now: datetime,
        source_json: dict[str, Any],
    ) -> None:
        await _verify_source(
            session,
            kind=kind,
            record_id=record_id,
            digest=digest,
            execution_digest=execution_digest,
            source_json=source_json,
        )
        row = await session.get(ProcessContentAdmissionRow, (kind, record_id))
        if row is None:
            raise ValueError("legacy process content has no admission receipt")
        receipt = _receipt(row)
        if (
            receipt.record_digest != digest
            or receipt.execution_digest != execution_digest
            or receipt.policy_digest != self._policy.digest
            or now.tzinfo is None
            or receipt.admitted_at != created_at
            or now < receipt.admitted_at
        ):
            raise ValueError("process record differs from its content admission")

    def _bounded_strings(self, value: Any, *, maximum_bytes: int) -> set[str]:
        strings: set[str] = set()
        active: set[int] = set()
        nodes = 0
        approximate_bytes = 0

        def visit(item: Any, depth: int) -> None:
            nonlocal nodes, approximate_bytes
            nodes += 1
            approximate_bytes += 1
            if depth > self._policy.maximum_depth or nodes > self._policy.maximum_nodes:
                raise ValueError("process content exceeds structural bounds")
            if isinstance(item, str):
                size = len(item.encode("utf-8"))
                if size > self._policy.maximum_string_bytes:
                    raise ValueError("process string exceeds its byte bound")
                approximate_bytes += size
                strings.add(item)
            elif item is None or type(item) in (int, bool):
                pass
            elif type(item) is float:
                if not math.isfinite(item):
                    raise ValueError("non-finite process number")
            else:
                identity = id(item)
                if identity in active:
                    raise ValueError("cyclic process content")
                active.add(identity)
                if isinstance(item, StrictRecord):
                    fields = type(item).model_fields
                    if set(item.__dict__) - set(fields):
                        raise ValueError("process record contains undeclared fields")
                    for key in fields:
                        visit(key, depth + 1)
                        visit(getattr(item, key), depth + 1)
                elif isinstance(item, dict):
                    for key, nested in item.items():
                        if not isinstance(key, str):
                            raise ValueError("process keys must be strings")
                        visit(key, depth + 1)
                        visit(nested, depth + 1)
                elif isinstance(item, (tuple, list)):
                    for nested in item:
                        visit(nested, depth + 1)
                else:
                    raise ValueError("non-JSON process content")
                active.remove(identity)
            if approximate_bytes > maximum_bytes:
                raise ValueError("process content exceeds its byte bound")

        visit(value, 0)
        if len(canonical_json_bytes(value)) > maximum_bytes:
            raise ValueError("serialized process content exceeds its byte bound")
        return strings

    async def _check_identifiers(
        self,
        session: AsyncSession,
        strings: set[str],
        *,
        allowed: set[str],
        admitted_digests: set[str],
    ) -> None:
        digests: set[str] = set()
        for value in strings:
            if re.search(r"(?:process-container-|padawan-cpu-)[0-9a-f]{32}", value):
                raise ValueError("process content references private container execution")
            links = set(re.findall(r"process-artifact-[0-9a-f]{32}", value))
            if not links.issubset(allowed):
                raise ValueError("undeclared process link in content")
            digests.update(
                "sha256:" + match.lower()
                for match in re.findall(r"(?<![0-9a-fA-F])([0-9a-fA-F]{64})(?![0-9a-fA-F])", value)
            )
            if len(digests) > self._policy.maximum_digest_candidates:
                raise ValueError("process content exceeds its digest lookup bound")
        # Index lookups on presented digests, never a scan/copy of the forensic corpus.
        ordered = sorted(digests)
        for offset in range(0, len(ordered), 128):
            batch = ordered[offset : offset + 128]
            raw = await session.scalar(
                select(ArtifactRow.artifact_id)
                .outerjoin(
                    ArtifactInformationRow,
                    ArtifactInformationRow.artifact_id == ArtifactRow.artifact_id,
                )
                .where(
                    ArtifactRow.digest.in_(batch),
                    or_(
                        ArtifactRow.digest.not_in(admitted_digests),
                        ArtifactRow.raw_data.is_(True),
                        ArtifactInformationRow.artifact_id.is_(None),
                        ArtifactInformationRow.information_class == InformationClass.FORENSIC.value,
                    ),
                )
                .limit(1)
            )
            classification = await session.scalar(
                select(ArtifactInformationRow.artifact_id)
                .where(
                    ArtifactInformationRow.record_digest.in_(batch),
                )
                .limit(1)
            )
            if raw is not None or classification is not None:
                raise ValueError("process content references forensic or unclassified storage")
            for table in (
                ProcessContainerWorkloadRow,
                ProcessContainerReceiptRow,
                ProcessResourceGrantRow,
                ProcessResourceReservationRow,
                ProcessResourceEventRow,
            ):
                if (
                    await session.scalar(
                        select(table.record_digest).where(table.record_digest.in_(batch)).limit(1)
                    )
                    is not None
                ):
                    raise ValueError("process content references private resource accounting")


async def _verify_source(
    session: AsyncSession,
    *,
    kind: Literal["state", "event"],
    record_id: str,
    digest: str,
    execution_digest: str,
    source_json: dict[str, Any],
) -> None:
    if kind == "state":
        state = await session.get(ProcessStateRow, record_id)
        if state is None:
            raise ValueError("content admission requires its stored state")
        parsed_state = ProjectStateVersion.model_validate(state.record_json, strict=False)
        rollout_id, stored_json, stored_digest = (
            state.rollout_id,
            state.record_json,
            state.state_digest,
        )
        if (
            parsed_state.state_digest != stored_digest
            or parsed_state.state_id != record_id
            or parsed_state.rollout_id != state.rollout_id
            or parsed_state.sequence != state.sequence
        ):
            raise ValueError("content source state is inconsistent")
    else:
        event = await session.get(ProcessEventRow, record_id)
        if event is None:
            raise ValueError("content admission requires its stored event")
        parsed_event = ProcessEventRecord.model_validate(event.record_json, strict=False)
        rollout_id, stored_json, stored_digest = (
            event.rollout_id,
            event.record_json,
            event.event_digest,
        )
        if (
            parsed_event.event_digest != stored_digest
            or parsed_event.event_id != record_id
            or parsed_event.rollout_id != event.rollout_id
            or parsed_event.sequence != event.sequence
        ):
            raise ValueError("content source event is inconsistent")
    rollout = await session.get(ProcessRolloutRow, rollout_id)
    if (
        stored_json != source_json
        or stored_digest != digest
        or rollout is None
        or rollout.execution_digest != execution_digest
    ):
        raise ValueError("content receipt differs from its stored execution or record")


def _receipt(row: ProcessContentAdmissionRow) -> ProcessContentAdmission:
    receipt = ProcessContentAdmission.model_validate(row.record_json, strict=False)
    timestamp = (
        row.created_at.replace(tzinfo=UTC) if row.created_at.tzinfo is None else row.created_at
    )
    if (
        sha256_digest(row.record_json) != row.record_digest
        or receipt.digest != row.record_digest
        or receipt.record_kind != row.record_kind
        or receipt.record_id != row.record_id
        or receipt.record_digest != row.source_digest
        or receipt.execution_digest != row.execution_digest
        or receipt.policy_digest != row.policy_digest
        or receipt.admitted_at != timestamp
    ):
        raise ValueError("corrupted process content receipt")
    return receipt


def _closed_schema(node: dict[str, Any], root: dict[str, Any], path: tuple[str, ...]) -> None:
    if "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/$defs/") or ref in path:
            raise ValueError("process schemas require acyclic local definitions")
        _closed_schema(root["$defs"][ref.removeprefix("#/$defs/")], root, (*path, ref))
        return
    if "anyOf" in node:
        for branch in node["anyOf"]:
            _closed_schema(branch, root, path)
        return
    kind = node.get("type")
    if kind == "object":
        if node.get("additionalProperties") is not False:
            raise ValueError("process extension objects must forbid arbitrary keys")
        fields = node.get("properties", {})
        if (
            {"artifact_id", "uri", "digest"}.issubset(fields)
            or {"process_artifact_id", "execution_digest", "content_digest"}.issubset(fields)
            or fields.get("domain", {}).get("const") == "forensic"
        ):
            raise ValueError("process extensions must link explicitly declared process artifacts")
        for child in fields.values():
            _closed_schema(child, root, path)
    elif kind == "array":
        children = node.get("prefixItems", [])
        if "items" in node and node["items"] is not False:
            children = [*children, node["items"]]
        if not children:
            raise ValueError("process extension arrays require typed items")
        for child in children:
            _closed_schema(child, root, path)
    elif kind not in {"string", "integer", "number", "boolean", "null"}:
        raise ValueError("process extensions require explicit JSON types")
