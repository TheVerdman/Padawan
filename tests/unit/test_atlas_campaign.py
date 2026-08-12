from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from padawan.atlas.campaigns import (
    build_capability_ontology,
    build_first_inkling_campaign,
    build_first_inkling_campaign_bundle,
    offline_verification,
    prepare_first_inkling_campaign_stage,
)
from padawan.atlas.catalog import dataset_governance_records, source_claims
from padawan.atlas.contracts import (
    EvaluationClass,
    Modality,
    ModalityGateStatus,
    SuiteStatus,
)
from padawan.atlas.reporting import (
    build_first_inkling_machine_report,
    machine_report_json,
    render_first_inkling_report,
)


def test_preview_and_released_vendor_claims_are_distinct_priors() -> None:
    claims = source_claims()
    assert len(claims) == 14
    assert len({claim.claim_id for claim in claims}) == 14
    preview = {
        claim.benchmark_id: claim
        for claim in claims
        if claim.model_revision == "preview-2026-07-15-unreleased"
    }
    release = {
        claim.benchmark_id: claim
        for claim in claims
        if claim.model_revision
        == "released-2026-07-30-after-additional-post-training-and-coding-rl"
    }
    assert {benchmark: claim.reported_value for benchmark, claim in preview.items()} == {
        "aime-2026": 95.1,
        "gpqa-diamond": 88.3,
        "humanitys-last-exam": 46.6,
        "ifbench": 83.4,
        "mmau": 77.5,
        "mmmu-pro": 73.1,
        "swe-bench-verified": 77.4,
    }
    assert {benchmark: claim.reported_value for benchmark, claim in release.items()} == {
        "aime-2026": 95.5,
        "gpqa-diamond": 89.5,
        "humanitys-last-exam": 47.8,
        "ifbench": 82.2,
        "mmau": 77.0,
        "mmmu-pro": 74.0,
        "swe-bench-verified": 80.2,
    }
    assert all(claim.supersedes_claim_id is None for claim in claims)
    assert all(claim.effort == 0.99 for claim in claims)
    assert all(
        any("no Padawan" in caveat for caveat in claim.harness_assumptions) for claim in claims
    )


def test_external_dataset_registration_fails_closed() -> None:
    governance = dataset_governance_records()
    local = [record for record in governance if record.access.value == "local"]
    external = [record for record in governance if record.access.value != "local"]
    assert len(local) == 8
    assert len({(record.benchmark_id, record.evaluation_class) for record in local}) == 8
    assert all(record.executable for record in local)
    assert all(not record.executable for record in external)
    hle = next(record for record in governance if record.benchmark_id == "humanitys-last-exam")
    assert hle.redistribution.value == "prohibited"
    swe = next(record for record in governance if record.benchmark_id == "swe-bench-verified")
    assert swe.access.value == "legally_unclear"
    mmau = next(record for record in governance if record.benchmark_id == "mmau")
    assert mmau.access.value == "unavailable"


def test_campaign_builders_are_stable_and_freeze_local_content() -> None:
    first = build_first_inkling_campaign_bundle()
    second = build_first_inkling_campaign_bundle()
    assert first.campaign.manifest_digest == second.campaign.manifest_digest
    assert first.ontology.manifest_digest == second.ontology.manifest_digest
    assert first.verification.record_digest == second.verification.record_digest
    assert build_first_inkling_campaign().manifest_digest == first.campaign.manifest_digest
    assert build_capability_ontology().manifest_digest == first.ontology.manifest_digest
    assert offline_verification().record_digest == first.verification.record_digest

    ready = [suite for suite in first.suites if suite.status == SuiteStatus.READY]
    assert {suite.suite_id for suite in ready} == {
        "padawan-algebra-adaptive-v0",
        "padawan-algebra-training-candidates-v0",
        "padawan-temporal-adaptive-v0",
    }
    assert all(suite.items and suite.item_digests for suite in ready)
    assert all(suite.task_manifest_digests and suite.corpus_digests for suite in ready)
    assert all(
        tuple(sorted(item.item_digest for item in suite.items)) == suite.item_digests
        for suite in ready
    )


def test_external_suite_content_and_media_capability_are_not_inferred() -> None:
    bundle = build_first_inkling_campaign_bundle()
    external = [suite for suite in bundle.suites if suite.suite_id.endswith("registration-v0")]
    assert len(external) == 9
    assert all(suite.status == SuiteStatus.BLOCKED for suite in external)
    assert all(not suite.items and not suite.item_digests for suite in external)
    assert all(not suite.task_manifest_digests and not suite.corpus_digests for suite in external)
    media = [
        suite
        for suite in external
        if any(gate.modality != Modality.TEXT for gate in suite.modality_gates)
    ]
    assert media
    assert all(
        any(
            gate.status == ModalityGateStatus.UNVALIDATED
            for gate in suite.modality_gates
            if gate.modality != Modality.TEXT
        )
        for suite in media
    )


def test_promotion_adaptive_and_training_lanes_are_auditable_and_disjoint() -> None:
    bundle = build_first_inkling_campaign_bundle()
    campaign = bundle.campaign
    assert not (
        set(campaign.promotion_suite_digests) & set(campaign.adaptive_suite_digests)
        or set(campaign.promotion_suite_digests) & set(campaign.training_candidate_suite_digests)
        or set(campaign.adaptive_suite_digests) & set(campaign.training_candidate_suite_digests)
    )
    promotion = next(
        suite
        for suite in bundle.suites
        if suite.evaluation_class == EvaluationClass.SEALED_PROMOTION
        and suite.suite_id == "inkling-n-plus-one-promotion-v0"
    )
    assert promotion.status == SuiteStatus.BLOCKED
    assert promotion.evaluation_suite_manifest_digest is None
    assert not promotion.items
    assert any("EvaluationSuiteManifest" in reason for reason in promotion.blocking_reasons)

    by_digest = {suite.content_digest: suite for suite in bundle.suites}
    adaptive_items = {
        item.item_digest
        for digest in campaign.adaptive_suite_digests
        for item in by_digest[digest].items
    }
    training_items = {
        item.item_digest
        for digest in campaign.training_candidate_suite_digests
        for item in by_digest[digest].items
    }
    assert adaptive_items.isdisjoint(training_items)


def test_first_campaign_predeclares_boundary_and_harness_ablations() -> None:
    bundle = build_first_inkling_campaign_bundle()
    campaign = bundle.campaign
    assert {factor.factor_id for factor in campaign.factors} == {
        "artifact",
        "budget",
        "compaction",
        "context",
        "effort",
        "retention",
        "sampling",
        "tool_access",
    }
    factorial = {
        (
            condition.factor_levels["retention"],
            condition.factor_levels["compaction"],
        )
        for condition in campaign.conditions
        if condition.condition_id.startswith("factorial-")
    }
    assert factorial == {
        ("disabled", "disabled"),
        ("disabled", "enabled"),
        ("enabled", "disabled"),
        ("enabled", "enabled"),
    }
    assert all(rule.fixed_before_results for rule in campaign.stop_rules)
    assert {template.reasoning_effort for template in bundle.harness_templates} >= {
        "none",
        "mapping-required(vendor-label=0.5)",
        "mapping-required(vendor-label=0.99)",
    }
    assert all(not template.response_storage for template in bundle.harness_templates)
    assert all(not template.previous_response_id for template in bundle.harness_templates)
    assert all(not template.private_reasoning_reused for template in bundle.harness_templates)
    assert all(
        any("Only reasoning={'effort':'none'}" in reason for reason in condition.blocking_reasons)
        for condition in campaign.conditions
    )
    non_none = [
        condition for condition in campaign.conditions if condition.factor_levels["effort"] != "e0"
    ]
    assert all(
        any(
            "no registered numeric-to-Responses adapter mapping" in reason
            for reason in condition.blocking_reasons
        )
        for condition in non_none
    )


def test_offline_verification_and_live_cost_plan_are_exact() -> None:
    bundle = build_first_inkling_campaign_bundle()
    verification = bundle.verification
    assert verification.generated_items == 100
    assert verification.structurally_validated_matched_neighborhoods == 45
    assert verification.verified_oracles == 70
    assert verification.external_requests_made == 0
    assert verification.external_cost_usd == 0.0
    assert all(verification.checks.values())

    plan = bundle.live_plan
    assert plan.authorization_required
    assert plan.total_max_requests == 7_238
    assert plan.total_max_input_tokens == 248_508_928
    assert plan.total_max_output_tokens == 130_326_528
    assert plan.total_max_actions == 34_590
    assert plan.total_max_cost_usd == 14_839.0
    assert plan.total_expected_runtime_minutes == 106_360
    assert sum(stage.max_requests for stage in plan.stages) == plan.total_max_requests
    assert {stage.stage_id for stage in plan.stages} == {
        "source-bf16-reference",
        "w8a16-blocked-expansion",
        "w8a16-content-ready",
    }
    assert (
        sum(allocation.planned_trials for stage in plan.stages for allocation in stage.allocations)
        == plan.total_max_requests
    )
    allocated_by_condition = {
        condition.condition_id: sum(
            binding.planned_item_count * binding.trials_per_item
            for binding in bundle.campaign.suite_bindings
            if condition.condition_id in binding.condition_ids
        )
        for condition in bundle.campaign.conditions
    }
    assert all(
        condition.max_requests == allocated_by_condition[condition.condition_id]
        for condition in bundle.campaign.conditions
    )
    assert (
        sum(condition.max_requests for condition in bundle.campaign.conditions)
        == plan.total_max_requests
    )
    assert all(
        stage.command.startswith("padawan atlas campaign prepare --preparation-only")
        for stage in plan.stages
    )
    assert all(
        "--authorization-ref REPLACE_WITH_AUTHORIZATION_REFERENCE" in stage.command
        for stage in plan.stages
    )
    assert all("--max-runtime-minutes" in stage.command for stage in plan.stages)
    assert all(
        shlex.split(stage.command)[:4] == ["padawan", "atlas", "campaign", "prepare"]
        for stage in plan.stages
    )


def test_campaign_preparation_is_exact_content_addressed_and_non_executable() -> None:
    bundle = build_first_inkling_campaign_bundle()
    stage = bundle.live_plan.stages[0]
    arguments = {
        "campaign_digest": bundle.campaign.manifest_digest,
        "allocation_set": stage.stage_id,
        "authorization_ref": "approval://atlas-CAP-2049",
        "max_requests": stage.max_requests,
        "max_input_tokens": stage.max_input_tokens,
        "max_output_tokens": stage.max_output_tokens,
        "max_actions": stage.max_actions,
        "max_cost_usd": stage.max_cost_usd,
        "max_runtime_minutes": stage.expected_runtime_minutes,
        "preparation_only": True,
    }
    prepared = prepare_first_inkling_campaign_stage(**arguments)
    replay = prepare_first_inkling_campaign_stage(**arguments)
    assert prepared == replay
    assert prepared.mode == "preparation_only"
    assert prepared.execution_gateway_status == "unavailable_fail_closed"
    assert not prepared.execution_permitted
    assert not prepared.authorization_verified
    assert prepared.network_calls_made == 0
    assert prepared.database_writes == 0
    assert prepared.artifact_writes == 0
    assert prepared.external_requests_made == 0
    assert prepared.external_cost_usd == 0.0
    assert prepared.gpu_actions == 0
    assert prepared.preparation_digest.startswith("sha256:")
    assert prepared.evidence_artifact_plan == stage.evidence_artifacts

    with pytest.raises(ValueError, match="placeholder"):
        prepare_first_inkling_campaign_stage(
            **{**arguments, "authorization_ref": "REPLACE_WITH_AUTHORIZATION_REFERENCE"}
        )
    with pytest.raises(ValueError, match="placeholder"):
        prepare_first_inkling_campaign_stage(
            **{**arguments, "authorization_ref": "approval://REQUIRED-CAP-2049"}
        )
    with pytest.raises(ValueError, match="exact predeclared"):
        prepare_first_inkling_campaign_stage(
            **{**arguments, "max_requests": stage.max_requests - 1}
        )
    with pytest.raises(ValueError, match="preparation-only"):
        prepare_first_inkling_campaign_stage(**{**arguments, "preparation_only": False})
    with pytest.raises(ValueError, match="campaign digest"):
        prepare_first_inkling_campaign_stage(
            **{**arguments, "campaign_digest": "sha256:" + "0" * 64}
        )


def test_reports_keep_four_evidence_lanes_and_closed_loop_seams_explicit() -> None:
    bundle = build_first_inkling_campaign_bundle()
    report = build_first_inkling_machine_report(bundle)
    lanes = report["evidence_lanes"]
    assert len(lanes["reported_upstream_claims"]) == 14
    assert lanes["locally_reproduced_observations"] == []
    assert lanes["extrapolations"] == []
    assert lanes["unknowns"]
    assert all(not claim["is_padawan_observation"] for claim in lanes["reported_upstream_claims"])
    assert report["mechanistic_interchange_assumptions"]
    assert report["interaction_lab_ingestion_seam"]
    assert report["unsupported_conclusions"]
    gateway = report["execution_gateway"]
    assert gateway["status"] == "unavailable_fail_closed"
    assert gateway["execution_permitted"] is False
    assert gateway["preparation_command"] == "padawan atlas campaign prepare --preparation-only"
    assert set(gateway["preparation_side_effects"].values()) == {0}
    assert json.loads(machine_report_json(bundle))["report_digest"] == report["report_digest"]

    human = render_first_inkling_report(bundle)
    assert "There are no locally reproduced Inkling capability scores" in human
    assert "240k ladder" in human
    assert "Raw chats" in human
    assert "activation/router telemetry" in human
    assert "EvaluationSuiteManifest" in human
    assert "Every command below is preparation-only" in human
    assert "actual execution fails closed" in human


def test_tracked_campaign_and_verification_summaries_match_builders() -> None:
    root = Path(__file__).resolve().parents[2]
    bundle = build_first_inkling_campaign_bundle()
    campaign_summary = json.loads(
        (root / "padawan/atlas/data/first-inkling-campaign-v0.json").read_text()
    )
    verification_summary = json.loads(
        (root / "reports/verification/2026-08-12-capability-atlas-v0.json").read_text()
    )
    assert campaign_summary["campaign_digest"] == bundle.campaign.manifest_digest
    assert campaign_summary["ontology_digest"] == bundle.ontology.manifest_digest
    assert campaign_summary["offline_verification_digest"] == bundle.verification.record_digest
    assert verification_summary["campaign_digest"] == bundle.campaign.manifest_digest
    assert verification_summary["record_digest"] == bundle.verification.record_digest
    assert campaign_summary["live_authorization_plan"]["command_mode"] == "preparation_only"
    assert (
        campaign_summary["live_authorization_plan"]["execution_gateway"]
        == "unavailable_fail_closed"
    )
    assert verification_summary["live_plan"]["command_mode"] == "preparation_only"
    assert verification_summary["live_plan"]["execution_gateway"] == "unavailable_fail_closed"
    assert verification_summary["live_plan"]["total"] == {
        "actions": bundle.live_plan.total_max_actions,
        "cost_usd": bundle.live_plan.total_max_cost_usd,
        "input_tokens": bundle.live_plan.total_max_input_tokens,
        "output_tokens": bundle.live_plan.total_max_output_tokens,
        "requests": bundle.live_plan.total_max_requests,
        "serial_runtime_minutes": bundle.live_plan.total_expected_runtime_minutes,
    }
