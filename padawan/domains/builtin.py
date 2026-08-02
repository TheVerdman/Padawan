from __future__ import annotations

from padawan.domains.algebra.domain import AlgebraDomain
from padawan.domains.lean_math.domain import LeanMathDomain
from padawan.domains.registry import DomainRegistry


def build_builtin_domain_registry() -> DomainRegistry:
    registry = DomainRegistry()
    registry.register(AlgebraDomain())
    registry.register(LeanMathDomain())
    return registry
