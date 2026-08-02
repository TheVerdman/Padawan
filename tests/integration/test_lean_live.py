from __future__ import annotations

import platform
from pathlib import Path

import pytest

from padawan.domains.contracts import VerifierDisposition
from padawan.domains.lean_math import LeanMathCorpusGenerator, LeanProofTask, LeanVerifier
from padawan.models.contracts import CorpusPool

pytestmark = pytest.mark.lean


def _verifier() -> LeanVerifier:
    repository = Path(__file__).resolve().parents[2]
    lake = repository / ".tools" / "elan" / "bin" / "lake"
    if not lake.is_file():
        pytest.skip("workspace-local Lean toolchain is not installed")
    return LeanVerifier(
        project_root=repository / "lean",
        lake_executable=lake,
        elan_home=repository / ".tools" / "elan",
        sandbox_mode="required" if platform.system() == "Darwin" else "best_effort",
        timeout_seconds=30,
    )


def test_real_lean_kernel_accepts_and_rejects_with_distinct_dispositions() -> None:
    verifier = _verifier()
    statement = "∀ x : Int, 3 * x + 2 = 17 → x = 5"
    generated = LeanMathCorpusGenerator().generate(
        pool=CorpusPool.CURRICULUM,
        seed=73,
        groups_per_family=1,
        siblings_per_group=2,
    )
    one_per_family = {item.competency_id: item for item in generated}.values()
    accepted = []
    for item in one_per_family:
        assert item.expected_answer is not None
        accepted.append(
            verifier.verify(
                LeanProofTask(
                    task_id=item.item_id,
                    statement=str(item.expected_answer["statement"]),
                    proof=str(item.expected_answer["proof"]),
                )
            )
        )
    rejected = verifier.verify(
        LeanProofTask(task_id="lean-rejected", statement=statement, proof="by\n  rfl")
    )

    assert all(result.disposition == VerifierDisposition.VERIFIED for result in accepted)
    assert all(result.evidence["kernel_executed"] is True for result in accepted)
    assert all(
        result.evidence["network_isolation_enforced"] is (platform.system() == "Darwin")
        for result in accepted
    )
    assert rejected.disposition == VerifierDisposition.REJECTED
    assert rejected.evidence["kernel_executed"] is True
    assert rejected.evidence["diagnostics"]
