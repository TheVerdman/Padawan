from __future__ import annotations

from padawan.domains.algebra.domain import AlgebraDomain
from padawan.domains.lean_math.domain import LeanMathDomain
from padawan.domains.legal.appellate.domain import AppellateBriefingDomain
from padawan.domains.magellan_improvement.domain import MagellanImprovementDomain
from padawan.domains.registry import DomainRegistry
from padawan.domains.temporal_grounding.domain import TemporalGroundingDomain


def build_builtin_domain_registry() -> DomainRegistry:
    registry = DomainRegistry()
    registry.register(AlgebraDomain())
    registry.register(LeanMathDomain())
    registry.register(AppellateBriefingDomain())
    registry.register(MagellanImprovementDomain())
    registry.register(TemporalGroundingDomain())
    return registry
