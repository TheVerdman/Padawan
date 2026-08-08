from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from padawan.domains.builtin import build_builtin_domain_registry
from padawan.domains.contracts import VerifierDisposition
from padawan.domains.lean_math import (
    LeanMathCorpusGenerator,
    LeanMathDomain,
    LeanProofTask,
    LeanVerifier,
)
from padawan.domains.lean_math.verifier import LeanInputPolicyError
from padawan.models.contracts import CorpusPool


def _unconfigured_verifier(tmp_path: Path) -> LeanVerifier:
    return LeanVerifier(
        project_root=tmp_path / "lean",
        lake_executable=tmp_path / "elan" / "lake",
        elan_home=tmp_path / "elan",
        sandbox_mode="off",
    )


def test_lean_generator_is_deterministic_and_domain_validated() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    generator = LeanMathCorpusGenerator()
    first = generator.generate(
        pool=CorpusPool.CURRICULUM,
        seed=73,
        groups_per_family=1,
        siblings_per_group=2,
        created_at=timestamp,
    )
    second = generator.generate(
        pool=CorpusPool.CURRICULUM,
        seed=73,
        groups_per_family=1,
        siblings_per_group=2,
        created_at=timestamp,
    )

    assert first == second
    assert len(first) == 4
    assert len({item.item_id for item in first}) == 4
    assert all(item.expected_answer["proof"] == "by\n  omega" for item in first)
    domain = LeanMathDomain()
    for item in first:
        domain.validate_item(item)


def test_lean_domain_exposes_the_shared_developmental_workflow() -> None:
    registry = build_builtin_domain_registry()

    assert registry.get("math.lean").spec.deterministic_verifiers == ("lean4_kernel",)
    assert registry.get_workflow("math.lean").spec.domain_id == "math.lean"


def test_lean_source_wrapper_rejects_command_and_metaprogramming_injection(tmp_path: Path) -> None:
    verifier = _unconfigured_verifier(tmp_path)
    safe = verifier.render_source(
        LeanProofTask(task_id="safe", statement="∀ n : Nat, n ≤ 4 → n ≤ 5", proof="by\n  omega")
    )

    assert safe.startswith("import Mathlib\n")
    assert "theorem candidate_" in safe
    assert "  by\n    omega" in safe

    for proof in (
        "by\n  run_tac do pure ()",
        "by\n  omega\n#import os",
        "by\n  exact IO.ofExcept (Except.ok trivial)",
        "by\n  (run_tac do pure ())",
        "by\n  sorry",
        "by\n  native_decide",
    ):
        with pytest.raises(LeanInputPolicyError):
            verifier.render_source(LeanProofTask(task_id="unsafe", statement="True", proof=proof))

    with pytest.raises(LeanInputPolicyError):
        verifier.render_source(
            LeanProofTask(
                task_id="unsafe-statement",
                statement="by run_tac do pure ()",
                proof="by\n  trivial",
            )
        )


def test_lean_configuration_failure_is_not_mislabeled_as_proof_rejection(tmp_path: Path) -> None:
    result = _unconfigured_verifier(tmp_path).verify(
        LeanProofTask(task_id="missing", statement="True", proof="by\n  trivial")
    )

    assert result.disposition == VerifierDisposition.INFRASTRUCTURE_FAILURE
    assert result.evidence["kernel_executed"] is False
