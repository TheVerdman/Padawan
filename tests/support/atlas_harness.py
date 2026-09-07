"""Shared atlas harness fixture setup."""

from __future__ import annotations

from datetime import UTC, datetime

from padawan.adapters.inkling.contract import INKLING_SMALL_AMPERE
from padawan.atlas.contracts import (
    AdapterKind,
    AtlasItemManifest,
    AtlasSuiteManifest,
    CampaignCondition,
    EvaluationClass,
    Modality,
    ModalityGateStatus,
    ModalityValidationEvidence,
    SuiteStatus,
    content_id,
)
from padawan.config.settings import Settings
from padawan.domains.builtin import build_builtin_domain_registry
from padawan.experiments.defaults import build_standardized_developmental_control
from padawan.models.contracts import ResearchRole
from padawan.models.hashing import sha256_digest

NOW = datetime(2026, 8, 12, tzinfo=UTC)


def _base_control():
    settings = Settings(
        code_revision="c2e7a25",
        openai_model="teacher-test",
        inkling_edge_image_digest=f"sha256:{'d' * 64}",
    )
    domain = build_builtin_domain_registry().get_workflow("math.algebra")
    return build_standardized_developmental_control(
        settings=settings,
        domain=domain.spec,
        student_provider="inkling",
        student_model_id=INKLING_SMALL_AMPERE.served_model_name,
        student_runtime_id="inkling-vllm",
        student_runtime_version=INKLING_SMALL_AMPERE.runtime_revision,
        student_checkpoint_id=INKLING_SMALL_AMPERE.checkpoint_id,
        student_role=ResearchRole.TARGET,
        student_base_url="https://inkling.example",
        teacher_provider="openai",
        teacher_model_id="teacher-test",
        corpus_digest=sha256_digest("developmental-corpus"),
    )


def _condition(*, checkpoint: str = INKLING_SMALL_AMPERE.checkpoint_id) -> CampaignCondition:
    return CampaignCondition(
        condition_id="optimized-retained-compacted-max",
        title="Explicit history, retained public summaries, deterministic compaction",
        harness_tier="optimized",
        required_checkpoint_id=checkpoint,
        required_quantization_id=INKLING_SMALL_AMPERE.quantization,
        required_protocol="responses",
        factor_levels={
            "compaction": "enabled",
            "effort": "max",
            "retention": "retained",
            "tool_access": "disabled",
        },
        max_requests=30,
        max_input_tokens=300_000,
        max_output_tokens=150_000,
        max_actions=60,
        max_cost_usd=100.0,
        expected_runtime_minutes=120,
        externally_gated=True,
        blocking_reasons=("requires explicit GPU/live endpoint authorization",),
    )


def _suite(
    environment_fingerprint: str,
    *,
    task_manifest_digest: str,
    corpus_digest: str,
) -> AtlasSuiteManifest:
    prompt = "Solve the generated algebra boundary item."
    item_identity = {
        "family_id": "algebra.boundary",
        "difficulty": 0.9,
        "adapter_kind": AdapterKind.GENERATED_VERIFIER,
        "modalities": (Modality.TEXT,),
        "prompt_digest": sha256_digest(prompt),
        "verifier_id": "symbolic_algebra",
        "verifier_version": "padawan-sympy-v1",
        "verifier_payload": {"expected": "x=2"},
        "metadata": {},
        "pair_id": None,
        "variant_id": None,
    }
    item = AtlasItemManifest(
        item_id=content_id("atlas-item", item_identity),
        item_digest=sha256_digest(item_identity),
        family_id="algebra.boundary",
        difficulty=0.9,
        adapter_kind=AdapterKind.GENERATED_VERIFIER,
        modalities=(Modality.TEXT,),
        prompt=prompt,
        prompt_digest=sha256_digest(prompt),
        verifier_id="symbolic_algebra",
        verifier_version="padawan-sympy-v1",
        verifier_payload={"expected": "x=2"},
    )
    gate = ModalityValidationEvidence(
        modality=Modality.TEXT,
        status=ModalityGateStatus.PASSED,
        gate_id="text-serving-contract",
        gate_revision="1",
        evidence_digest=sha256_digest("text-serving-contract"),
        evidence_refs=("inkling-text-gate",),
        validated_at=NOW,
    )
    suite_identity = {
        "suite_id": "atlas-algebra-boundary",
        "version": "1.0.0",
        "benchmark_id": "padawan.algebra",
        "benchmark_version": "1.0.0",
        "split": "adaptive",
        "governance_id": "padawan-project-authored",
        "adapter_kind": AdapterKind.GENERATED_VERIFIER,
        "evaluation_class": EvaluationClass.ADAPTIVE_SEARCH,
        "item_digests": (item.item_digest,),
        "modality_gates": (gate,),
        "task_manifest_digests": (task_manifest_digest,),
        "corpus_digests": (corpus_digest,),
        "environment_fingerprints": (environment_fingerprint,),
        "evaluation_suite_manifest_digest": None,
    }
    return AtlasSuiteManifest(
        suite_id="atlas-algebra-boundary",
        version="1.0.0",
        title="Algebra boundary suite",
        benchmark_id="padawan.algebra",
        benchmark_version="1.0.0",
        split="adaptive",
        governance_id="padawan-project-authored",
        adapter_kind=AdapterKind.GENERATED_VERIFIER,
        status=SuiteStatus.READY,
        evaluation_class=EvaluationClass.ADAPTIVE_SEARCH,
        item_digests=(item.item_digest,),
        items=(item,),
        modality_gates=(gate,),
        task_manifest_digests=(task_manifest_digest,),
        corpus_digests=(corpus_digest,),
        environment_fingerprints=(environment_fingerprint,),
        content_digest=sha256_digest(suite_identity),
        created_at=NOW,
    )
