"""Research identity for the bounded local Nemotron/authored-feedback pilot."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from padawan.domains.graduate_algebra import (
    COMPETENCY_ID,
    STUDENT_CONTEXT_TOKENS,
    STUDENT_OUTPUT_TOKENS,
    STUDENT_TIMEOUT_SECONDS,
    domain_spec,
)
from padawan.experiments.controls import ResearchControlConfiguration, ResearchWorkerConfiguration
from padawan.experiments.defaults import (
    _developmental_harness_parameters,
    _developmental_profile,
    _environment_identity,
)
from padawan.models.contracts import ResearchRole, TeacherMode
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    BudgetDisposition,
    BudgetLimit,
    IdentityEvidenceStatus,
    ModelServingIdentity,
    TaskCorpusIdentity,
    VersionedComponentIdentity,
)
from padawan.teaching.authored import AuthoredTeacherClient


def local_developmental_control(
    inputs: dict[str, Any], *, corpus_digest: str
) -> tuple[ResearchControlConfiguration, ResearchWorkerConfiguration]:
    config = inputs["config"]
    code_digest = sha256_digest(inputs["sources"])
    settings = SimpleNamespace(
        code_revision=code_digest,
        environment="local-developmental-pilot",
        external_timeout_seconds=float(STUDENT_TIMEOUT_SECONDS),
        run_retry_budget=0,
    )
    domain = domain_spec()

    def component(
        name: str, version: str, content: Any, *, declared: bool = False
    ) -> VersionedComponentIdentity:
        return VersionedComponentIdentity(
            component_id=name,
            version=version,
            digest=sha256_digest(content),
            evidence_status=(
                IdentityEvidenceStatus.DECLARED if declared else IdentityEvidenceStatus.PINNED
            ),
            evidence="Bound to the retained local pilot inputs; no weight-training claim.",
        )

    profile = _developmental_profile(
        settings=settings, domain=domain, student_provider="compatible"
    )
    context = profile.context.model_copy(
        update={
            "configured_context_window_tokens": STUDENT_CONTEXT_TOKENS,
            "effective_input_limit_tokens": None,
            "context_limit_evidence": f"Server enforces a {STUDENT_CONTEXT_TOKENS}-token context.",
        }
    )
    continuation = profile.continuation.model_copy(
        update={"private_reasoning_capture_enabled": True}
    )
    prompt = component("padawan.local_developmental.prompt_schema", "1", inputs["sources"])
    profile = profile.model_copy(
        update={
            "profile_id": "padawan.math.graduate_algebra.local_authored_pilot",
            "version": f"1.0.0+{code_digest[7:19]}",
            "tier": "exploratory",
            "purpose": "memory_mediated_teaching_diagnostic",
            "context": context,
            "continuation": continuation,
            "budgets": profile.budgets.model_copy(
                update={
                    "output_tokens": BudgetLimit(
                        disposition=BudgetDisposition.CAPPED,
                        scope="student_request",
                        unit="tokens",
                        value=STUDENT_OUTPUT_TOKENS,
                    ),
                    "latency": BudgetLimit(
                        disposition=BudgetDisposition.CAPPED,
                        scope="model_or_review_request",
                        unit="seconds",
                        value=STUDENT_TIMEOUT_SECONDS,
                    ),
                    "wall_time": BudgetLimit(
                        disposition=BudgetDisposition.CAPPED,
                        scope="pilot",
                        unit="seconds",
                        value=config["duration_seconds"],
                    ),
                    "cost": BudgetLimit(
                        disposition=BudgetDisposition.CAPPED,
                        scope="external_inference_services",
                        unit="usd",
                        value=0,
                    ),
                }
            ),
            "prompt_templates": tuple(
                sorted(
                    (*profile.prompt_templates, prompt), key=lambda p: (p.component_id, p.version)
                )
            ),
        }
    )
    runtime_parameters = dict(
        sorted(
            {
                "enable_thinking": "false",
                "endpoint_origin": f"http://127.0.0.1:{config['port']}",
                "grammar_decoding": "false",
                "max_model_len": str(STUDENT_CONTEXT_TOKENS),
                "max_num_seqs": "1",
                "protocol": "responses",
                "response_storage": "false",
                "retry_attempts": "1",
                "schema_enforcement": "public_proof_text",
                "timeout_seconds": str(float(STUDENT_TIMEOUT_SECONDS)),
            }.items()
        )
    )
    model = ModelServingIdentity(
        purpose="student",
        research_role=ResearchRole.TARGET,
        model_id=config["model_id"],
        checkpoint=component(config["model_id"], "cached-capsule", inputs["model_inventory"]),
        quantization=component("mlx.affine.4bit.group64", "1", inputs["model_inventory"]),
        runtime=component("vllm-metal-local", inputs["runtime"]["head"], inputs["runtime"]),
        serving_artifact=component("local-metal-server", "1", config["runtime_args"]),
        transport_artifact=component("local-developmental-client", "1", inputs["sources"]),
        protocol="responses",
        runtime_parameters=runtime_parameters,
        runtime_parameters_digest=sha256_digest(runtime_parameters),
    )
    authored = component(
        "codex.session.authored_feedback",
        "1",
        {"source": "current Codex session", "public_evidence_only": True},
        declared=True,
    )
    teacher_parameters = {"input": "public_grade_and_attempt", "transport": "authored_file"}
    teacher = ModelServingIdentity(
        purpose="teacher",
        research_role=ResearchRole.TEACHER,
        model_id=AuthoredTeacherClient.model_id,
        checkpoint=authored,
        quantization=component("not_applicable.authored_input", "1", {}),
        runtime=authored,
        serving_artifact=authored,
        protocol="authored_file",
        runtime_parameters=teacher_parameters,
        runtime_parameters_digest=sha256_digest(teacher_parameters),
    )
    parameters = _developmental_harness_parameters(
        domain_id=domain.domain_id,
        teacher_mode=TeacherMode.DIAGNOSTIC_CRITIQUE,
        treatment_condition="authored_evidence_critique",
        control_condition="no_intervention",
        external_transport_max_attempts=1,
    )
    parameters.update(
        teacher_max_attempts="2",
        teacher_sampling_policy="authored_public_evidence",
        student_sampling_temperature="0.7",
        student_sampling_top_p="0.95",
        student_sampling_top_logprobs="none",
        student_max_output_tokens=str(STUDENT_OUTPUT_TOKENS),
        proof_grading="authored_fixed_rubric_not_independent_not_kernel_verified",
    )
    parameters = dict(sorted(parameters.items()))
    environment, environment_parameters, fingerprint = _environment_identity(
        settings=settings, domain=domain
    )
    task = TaskCorpusIdentity(
        task_id=domain.domain_id,
        task_version=domain.version,
        task_manifest_digest=config["suite_digest"],
        corpus_id="nemotron-local-graduate-algebra-pilot",
        corpus_version="1",
        corpus_digest=corpus_digest,
        split="curriculum",
        evidence_status=IdentityEvidenceStatus.PINNED,
    )
    control = ResearchControlConfiguration(
        profile=profile,
        student_model=model,
        auxiliary_models=(teacher,),
        task=task,
        harness_parameters=parameters,
        environment=environment,
        environment_parameters=environment_parameters,
        environment_fingerprint=fingerprint,
    )
    worker = ResearchWorkerConfiguration(
        profile=profile,
        student_model=model,
        auxiliary_models=(teacher,),
        task=task,
        harness_parameters=parameters,
        environment=environment,
        environment_parameters=environment_parameters,
        environment_fingerprint=fingerprint,
        corpus_competency_ids=(COMPETENCY_ID,),
        domain_id=domain.domain_id,
        workflow="padawan.developmental_episode",
    )
    return control, worker
