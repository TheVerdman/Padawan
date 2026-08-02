from __future__ import annotations

from typing import Protocol, runtime_checkable

from padawan.domains.contracts import DomainSpec
from padawan.domains.runtime import DomainRuntimeContext
from padawan.models.contracts import (
    CompetencyRecord,
    CorpusItemRecord,
    CorpusPool,
    VerifierSpec,
)
from padawan.orchestration.supervisor import WorkHandler


@runtime_checkable
class DomainPackage(Protocol):
    @property
    def spec(self) -> DomainSpec: ...

    def competencies(self) -> tuple[CompetencyRecord, ...]: ...

    def validate_verifier_spec(self, verifier: VerifierSpec) -> None: ...

    def validate_item(self, item: CorpusItemRecord) -> None: ...

    def generate_curriculum(
        self,
        *,
        pool: CorpusPool,
        seed: int,
        groups_per_family: int,
        siblings_per_group: int,
    ) -> tuple[CorpusItemRecord, ...]: ...


@runtime_checkable
class WorkflowDomainPackage(DomainPackage, Protocol):
    """A domain with a complete autonomous developmental-episode workflow."""

    def build_workflow(self, context: DomainRuntimeContext) -> WorkHandler: ...


class DomainRegistry:
    """Version-aware authority for installed domain packages."""

    def __init__(self) -> None:
        self._packages: dict[tuple[str, str], DomainPackage] = {}
        self._active_versions: dict[str, str] = {}
        self._competency_domains: dict[str, str] = {}

    def register(self, package: DomainPackage, *, active: bool = True) -> None:
        key = (package.spec.domain_id, package.spec.version)
        if key in self._packages:
            raise ValueError(f"domain package already registered: {key[0]}@{key[1]}")
        competencies = package.competencies()
        if not competencies:
            raise ValueError("domain package must declare at least one competency")
        competency_ids = [competency.competency_id for competency in competencies]
        if len(competency_ids) != len(set(competency_ids)):
            raise ValueError("domain package declares duplicate competency IDs")
        conflicts = [
            competency_id
            for competency_id in competency_ids
            if self._competency_domains.get(competency_id, package.spec.domain_id)
            != package.spec.domain_id
        ]
        if conflicts:
            raise ValueError(f"competency ID belongs to another domain: {sorted(conflicts)}")
        if active:
            existing = self._active_versions.get(package.spec.domain_id)
            if existing is not None:
                raise ValueError(
                    f"active domain version already registered: {package.spec.domain_id}@{existing}"
                )
        self._packages[key] = package
        for competency_id in competency_ids:
            self._competency_domains[competency_id] = package.spec.domain_id
        if active:
            self._active_versions[package.spec.domain_id] = package.spec.version

    def get(self, domain_id: str, *, version: str | None = None) -> DomainPackage:
        selected_version = version or self._active_versions.get(domain_id)
        if selected_version is None:
            raise KeyError(f"no active domain registered: {domain_id}")
        try:
            return self._packages[(domain_id, selected_version)]
        except KeyError as exc:
            raise KeyError(
                f"domain package not registered: {domain_id}@{selected_version}"
            ) from exc

    def get_workflow(self, domain_id: str, *, version: str | None = None) -> WorkflowDomainPackage:
        package = self.get(domain_id, version=version)
        if not isinstance(package, WorkflowDomainPackage):
            raise ValueError(
                f"domain is installed for corpus/verifier use but has no autonomous workflow: "
                f"{package.spec.domain_id}@{package.spec.version}"
            )
        return package

    def validate_item(self, item: CorpusItemRecord) -> None:
        try:
            domain_id = self._competency_domains[item.competency_id]
        except KeyError as exc:
            raise KeyError(
                f"competency does not identify an installed domain: {item.competency_id}"
            ) from exc
        self.get(domain_id).validate_item(item)

    def installed(self) -> tuple[DomainSpec, ...]:
        return tuple(
            package.spec
            for _, package in sorted(self._packages.items(), key=lambda entry: entry[0])
        )
