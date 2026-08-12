# ruff: noqa: E501
"""Machine-readable and human Capability Atlas v0 reports."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from padawan.atlas.campaigns import (
    FirstInklingCampaignBundle,
    build_first_inkling_campaign_bundle,
)
from padawan.atlas.contracts import Modality, ModalityGateStatus, SuiteStatus, content_id
from padawan.models.hashing import sha256_digest


def build_first_inkling_machine_report(
    bundle: FirstInklingCampaignBundle | None = None,
) -> dict[str, Any]:
    """Render the v0 evidence lanes without converting priors into observations."""

    resolved = bundle or build_first_inkling_campaign_bundle()
    upstream = [
        {
            "claim_id": claim.claim_id,
            "source_url": claim.source_url,
            "publication_title": claim.publication_title,
            "source_revision": claim.source_revision,
            "source_published_at": claim.source_published_at.isoformat(),
            "model_id": claim.model_id,
            "model_revision": claim.model_revision,
            "benchmark_id": claim.benchmark_id,
            "benchmark_version": claim.benchmark_version,
            "split": claim.split,
            "metric_id": claim.metric_id,
            "reported_value": claim.reported_value,
            "reported_unit": claim.reported_unit,
            "effort": claim.effort,
            "harness_assumptions": list(claim.harness_assumptions),
            "tool_assumptions": list(claim.tool_assumptions),
            "context_assumptions": list(claim.context_assumptions),
            "budget_assumptions": list(claim.budget_assumptions),
            "contamination_caveats": list(claim.contamination_caveats),
            "evidence_lane": "reported_upstream_claim",
            "is_padawan_observation": False,
        }
        for claim in resolved.claims
    ]
    suites = [
        {
            "suite_id": suite.suite_id,
            "content_digest": suite.content_digest,
            "status": suite.status.value,
            "evaluation_class": suite.evaluation_class.value,
            "benchmark_id": suite.benchmark_id,
            "split": suite.split,
            "adapter_kind": suite.adapter_kind.value,
            "item_count": len(suite.items),
            "task_manifest_digests": list(suite.task_manifest_digests),
            "corpus_digests": list(suite.corpus_digests),
            "evaluation_suite_manifest_digest": suite.evaluation_suite_manifest_digest,
            "modalities": [gate.modality.value for gate in suite.modality_gates],
            "modality_gate_status": {
                gate.modality.value: gate.status.value for gate in suite.modality_gates
            },
            "blocking_reasons": list(suite.blocking_reasons),
        }
        for suite in resolved.suites
    ]
    governance = [
        {
            "governance_id": record.governance_id,
            "benchmark_id": record.benchmark_id,
            "benchmark_version": record.benchmark_version,
            "dataset_revision": record.dataset_revision,
            "source_url": record.source_url,
            "access": record.access.value,
            "redistribution": record.redistribution.value,
            "contamination": record.contamination.value,
            "rights_review_status": record.rights.review_status.value,
            "rights_id": record.rights.rights_id,
            "license_expression": record.license_expression,
            "executable": record.executable,
            "access_requirements": list(record.access_requirements),
        }
        for record in resolved.governance
    ]
    conditions = [condition.model_dump(mode="json") for condition in resolved.campaign.conditions]
    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "report_title": "Inkling-Small-Ampere W8A16 Capability Atlas v0",
        "generated_at": resolved.verification.created_at.isoformat(),
        "campaign": resolved.campaign.model_dump(mode="json"),
        "ontology": resolved.ontology.model_dump(mode="json"),
        "evidence_lanes": {
            "reported_upstream_claims": upstream,
            "locally_reproduced_observations": [],
            "extrapolations": [],
            "unknowns": list(resolved.verification.unknowns),
        },
        "dataset_governance": governance,
        "suites": suites,
        "conditions": conditions,
        "harness_templates": [asdict(template) for template in resolved.harness_templates],
        "offline_verification": resolved.verification.model_dump(mode="json"),
        "live_campaign_plan": asdict(resolved.live_plan),
        "execution_gateway": {
            "status": "unavailable_fail_closed",
            "execution_permitted": False,
            "preparation_command": "padawan atlas campaign prepare --preparation-only",
            "preparation_side_effects": {
                "network_calls": 0,
                "database_writes": 0,
                "artifact_writes": 0,
                "provider_requests": 0,
                "gpu_actions": 0,
            },
        },
        "planned_analyses": [
            "fixed-denominator accuracy, missingness, timeout, infrastructure-failure, and contamination accounting",
            "pass@k and repeated-trial stability without treating repeated attempts as independent items",
            "difficulty-indexed capability curves with confidence intervals and adaptive boundary allocation",
            "calibration, selective accuracy, consistency, self-correction, and repeated-action pathology",
            "standardized-versus-optimized harness uplift and retention-by-compaction interaction",
            "source-BF16-versus-W8A16 quantization delta only under matched non-quantization controls",
            "verified-success efficiency, token/action use, wall time, and cost per verified success",
            "failure-cluster reproducibility, stability, severity, exemplars, changed axes, and probe-set joins",
            "checkpoint N-versus-N+1 held-out improvement and regression matrix on a genuinely sealed suite",
        ],
        "mechanistic_interchange_assumptions": list(resolved.interchange_assumptions),
        "interaction_lab_ingestion_seam": list(resolved.interaction_lab_seam),
        "unsupported_conclusions": list(resolved.unsupported_conclusions),
    }
    identity = {"contract": "padawan.atlas.machine-report.v0", "payload": payload}
    payload["report_id"] = content_id("atlas-report", identity)
    payload["report_digest"] = sha256_digest(identity)
    return payload


def machine_report_json(
    bundle: FirstInklingCampaignBundle | None = None,
    *,
    indent: int = 2,
) -> str:
    """Serialize the stable report using canonical key ordering."""

    return json.dumps(
        build_first_inkling_machine_report(bundle),
        indent=indent,
        sort_keys=True,
        ensure_ascii=False,
    )


def render_first_inkling_report(bundle: FirstInklingCampaignBundle | None = None) -> str:
    """Render a compact human report from the same machine-readable bundle."""

    resolved = bundle or build_first_inkling_campaign_bundle()
    report = build_first_inkling_machine_report(resolved)
    preview = {
        claim.benchmark_id: claim.reported_value
        for claim in resolved.claims
        if claim.model_revision == "preview-2026-07-15-unreleased"
    }
    release = {
        claim.benchmark_id: claim.reported_value
        for claim in resolved.claims
        if claim.model_revision
        == "released-2026-07-30-after-additional-post-training-and-coding-rl"
    }
    claim_rows = []
    for benchmark_id in sorted(preview):
        claim_rows.append(
            f"| {benchmark_id} | {preview[benchmark_id]:.1f}% | {release[benchmark_id]:.1f}% | upstream prior only |"
        )
    ready = [suite for suite in resolved.suites if suite.status == SuiteStatus.READY]
    blocked = [suite for suite in resolved.suites if suite.status == SuiteStatus.BLOCKED]
    suite_rows = [
        f"| {suite.suite_id} | {suite.status.value} | {suite.evaluation_class.value} | {len(suite.items)} | {suite.content_digest} |"
        for suite in resolved.suites
    ]
    media_unknown = sorted(
        {
            gate.modality.value
            for suite in resolved.suites
            for gate in suite.modality_gates
            if gate.modality != Modality.TEXT and gate.status == ModalityGateStatus.UNVALIDATED
        }
    )
    stage_sections: list[str] = []
    for stage in resolved.live_plan.stages:
        stage_sections.extend(
            (
                f"### {stage.title}",
                "",
                f"- Preparation-only command template (replace the authorization placeholder): `{stage.command}`",
                f"- Ceiling: {stage.max_requests:,} requests; {stage.max_input_tokens:,} input tokens; {stage.max_output_tokens:,} output tokens; {stage.max_actions:,} actions; ${stage.max_cost_usd:,.2f}; {stage.expected_runtime_minutes:,} minutes.",
                f"- Gates: {'; '.join(stage.gates)}",
                "- Result: a content-addressed activation envelope and evidence-artifact plan; no request, database write, artifact write, provider call, GPU action, or spend.",
                "",
            )
        )
    lines = [
        "# Inkling-Small-Ampere W8A16 Capability Atlas v0",
        "",
        f"Campaign digest: `{resolved.campaign.manifest_digest}`  ",
        f"Offline verification digest: `{resolved.verification.record_digest}`  ",
        f"Machine report digest: `{report['report_digest']}`",
        "",
        "## Outcome",
        "",
        f"Atlas v0 froze {resolved.verification.generated_items} project-authored items across {len(resolved.suites)} suite manifests, structurally checked {resolved.verification.structurally_validated_matched_neighborhoods} matched neighborhoods and executed {resolved.verification.verified_oracles} deterministic oracle payloads, and made exactly 0 external requests at $0.00. Structural matching is not claimed as an independently verified metamorphic relation. Three suites are locally content-ready ({', '.join(suite.suite_id for suite in ready)}); the remaining suites are deliberately blocked rather than approximated.",
        "",
        "There are no locally reproduced Inkling capability scores, no extrapolated scores, and no promotion-eligible snapshot. The current output is a campaign design plus immutable offline evidence, not a model benchmark result.",
        "",
        "## Evidence lanes",
        "",
        "- Reported upstream claims: 14 primary-source records, split between the July 15 preview and the distinct July 30 released model.",
        "- Locally reproduced observations: none.",
        "- Extrapolations: none.",
        f"- Unknowns: {'; '.join(resolved.verification.unknowns)}",
        "",
        "## Official Inkling priors",
        "",
        "| Benchmark | July 15 preview | July 30 release | Evidence class |",
        "| --- | ---: | ---: | --- |",
        *claim_rows,
        "",
        "Sources: [official preview publication](https://thinkingmachines.ai/news/introducing-inkling/), [official release publication](https://thinkingmachines.ai/news/inkling-small/), and [official released model card](https://huggingface.co/thinkingmachines/Inkling-Small). The preview scores in the mission brief belong to the unreleased preview, not the released checkpoint. The release states effort=0.99 and temperature=1; the table does not fully bind item, evaluator, trial, token, retry, or tool manifests. Vendor scores remain priors and are never represented as Padawan observations.",
        "",
        "## Frozen suites",
        "",
        "| Suite | Status | Class | Frozen items | Content digest |",
        "| --- | --- | --- | ---: | --- |",
        *suite_rows,
        "",
        f"Blocked suites: {len(blocked)}. Public benchmark registration suites contain no copied questions, answers, or media. Generated executable suites bind both task-manifest and corpus digests. The planned promotion suite is `blocked`, not `sealed`, because it has no exposed items and no core `EvaluationSuiteManifest` bridge.",
        "",
        "The 240k ladder is represented only as blocked transport/exact-retrieval registration. It does not establish long-horizon reasoning or usable working memory. Temporal and Magellan probe families are the declared state/strategy lane; Magellan remains blocked until its isolated environment fingerprint is bound.",
        "",
        "## Boundary and comparison design",
        "",
        "The predeclared design includes effort 0.0/0.5/0.99, standardized versus optimized harnesses, tools off versus Bash-only, deterministic versus repeated stochastic sampling, 32k/128k/240k context controls, a retention × compaction factorial, adaptive allocation near 50% success, fixed confidence-width stop rules, calibration/selective-answering analysis, and source-BF16 versus W8A16 comparison under a quantization-only allowed-axis contract.",
        "",
        "Observed reports will use fixed denominators and distinguish pass@k from item-level accuracy; include missingness, timeouts, infrastructure failures, and contamination; estimate capability curves and uncertainty; report verified-success efficiency/cost; and produce harness, quantization, and checkpoint regression matrices. None of these outputs is populated before attributable trials exist.",
        "",
        "Retention and compaction preserve the Inkling continuation guard: explicit message history only, response storage disabled, `previous_response_id` disabled, and private reasoning never reused. The adapter can carry `SamplingConfiguration.reasoning_effort` to Responses `reasoning.effort`, but the sibling validation exercised only `none`; vendor numeric effort labels are not assumed to be wire values, and every non-`none` mapping plus tool structure needs registered edge preflight and behavioral validation.",
        "",
        "## Media status",
        "",
        f"Configured or registered media labels do not establish capability. Unvalidated modalities are: {', '.join(media_unknown) if media_unknown else 'none'}. Every media suite is blocked until the sibling Inkling complete gate supplies immutable evidence.",
        "",
        "## Exact externally gated campaign",
        "",
        f"Combined ceiling: {resolved.live_plan.total_max_requests:,} requests; {resolved.live_plan.total_max_input_tokens:,} input tokens; {resolved.live_plan.total_max_output_tokens:,} output tokens; {resolved.live_plan.total_max_actions:,} actions; ${resolved.live_plan.total_max_cost_usd:,.2f}; {resolved.live_plan.total_expected_runtime_minutes:,} serial minutes. These are ceilings, not spend already incurred.",
        "",
        "Every command below is preparation-only. It requires a real, non-placeholder authorization reference so the proposed scope can be content-addressed, but it does not verify that reference and cannot activate work. The governed authorization/execution gateway does not exist yet, so actual execution fails closed.",
        "",
        *stage_sections,
        "## Interaction Lab ingestion seam",
        "",
        *[f"- {item}" for item in resolved.interaction_lab_seam],
        "",
        "## Mechanistic-interpretability interchange",
        "",
        *[f"- {item}" for item in resolved.interchange_assumptions],
        "",
        "## Training and promotion boundary",
        "",
        "Only independently reproduced, stable model failures with harness effects ruled out, authoritative verifier evidence, training-permitted rights, cleared contamination, and exclusion from adaptive/challenge/promotion suites can become training candidates. Even then, Atlas does not feed a failure directly to the compiler: it requires governed corpus materialization. Promotion items, adaptive-search items, and training candidates remain digest-disjoint.",
        "",
        "## Unsupported conclusions",
        "",
        *[f"- {item}" for item in resolved.unsupported_conclusions],
        "",
        "## Offline checks",
        "",
        *[
            f"- {'PASS' if passed else 'FAIL'} — `{check}`"
            for check, passed in resolved.verification.checks.items()
        ],
        "",
    ]
    return "\n".join(lines)
