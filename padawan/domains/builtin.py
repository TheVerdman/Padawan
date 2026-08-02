from __future__ import annotations

from padawan.domains.algebra.domain import AlgebraDomain
from padawan.domains.lean_math.domain import LeanMathDomain
from padawan.domains.magellan_improvement.domain import MagellanImprovementDomain
from padawan.domains.registry import DomainRegistry


def build_builtin_domain_registry() -> DomainRegistry:
    registry = DomainRegistry()
    registry.register(AlgebraDomain())
    registry.register(LeanMathDomain())
    registry.register(MagellanImprovementDomain())
    return registry
