from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from padawan.atlas.campaigns import build_first_inkling_campaign_bundle
from padawan.atlas.contracts import (
    AccessClassification,
    AdapterKind,
    AtlasComparison,
    AtlasSnapshot,
    AtlasSuiteManifest,
    AtlasTrialRequest,
    BenchmarkClaim,
    ClaimSourceKind,
    EvaluationClass,
    ExploratoryFailureProposal,
    ExtractionProvenance,
    LocalObservation,
    MetricEstimate,
    SuiteStatus,
    TrialAllocation,
    content_id,
)
from padawan.atlas.registry import AtlasRegistry, AtlasRegistryError
from padawan.experiments.controls import ResearchControlRegistry
from padawan.models.contracts import (
    ArtifactRef,
)
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    ArtifactRow,
    AtlasRunManifestRow,
    ExternalCallRow,
    VerifierResultRow,
)
from tests.support.atlas_registry import (
    NOW,
    _artifact_store,
    _binding,
    _campaign,
    _gate,
    _governance,
    _item,
    _ontology,
    _register_executable_chain,
    _rehash_run_manifest,
    _request,
    _response_artifact,
    _run_manifest,
    _run_row,
    _seed_controls,
    _snapshot,
    _suite,
    _trial_result,
    _verifier_record,
)


async def test_registry_is_idempotent_and_rejects_claim_rewrite(database) -> None:
    extraction = ExtractionProvenance(
        method="official publication transcription",
        extracted_by="test-reviewer",
        extracted_at=NOW,
        source_excerpt_digest=sha256_digest("official excerpt"),
    )
    claim = BenchmarkClaim(
        claim_id="vendor-claim",
        source_kind=ClaimSourceKind.VENDOR,
        source_url="https://example.test/model-card",
        publication_title="Official model card",
        source_revision="rev-1",
        source_published_at=NOW,
        model_id="source-model",
        model_revision="bf16",
        benchmark_id="benchmark",
        benchmark_version="1",
        split="test",
        metric_id="accuracy",
        reported_value=0.8,
        reported_unit="fraction",
        harness_assumptions=("official harness",),
        tool_assumptions=("no tools",),
        context_assumptions=("official context",),
        budget_assumptions=("official budget",),
        contamination_caveats=("vendor reported",),
        extraction=extraction,
        created_at=NOW,
    )
    registry = AtlasRegistry()
    async with database.transaction() as session:
        first = await registry.register_claim(session, claim)
        replay = await registry.register_claim(session, claim)
        assert replay is first
        rewritten = claim.model_copy(update={"reported_value": 0.9})
        with pytest.raises(AtlasRegistryError, match="conflicts"):
            await registry.register_claim(session, rewritten)


async def test_complete_first_campaign_bundle_registers_without_live_execution(database) -> None:
    bundle = build_first_inkling_campaign_bundle()
    registry = AtlasRegistry()
    async with database.transaction() as session:
        for claim in bundle.claims:
            await registry.register_claim(session, claim)
        for governance in bundle.governance:
            await registry.register_dataset_governance(session, governance)
        await registry.register_ontology(session, bundle.ontology)
        for suite in bundle.suites:
            await registry.register_suite(session, suite)
        row = await registry.register_campaign(session, bundle.campaign)

        assert row.campaign_digest == bundle.campaign.manifest_digest
        assert bundle.verification.external_requests_made == 0


async def test_registration_only_suite_can_plan_but_cannot_allocate(database) -> None:
    registry = AtlasRegistry()
    governance = _governance(access=AccessClassification.REGISTRATION_ONLY)
    ontology = _ontology()
    empty_identity = {
        "suite_id": "unavailable-suite",
        "version": "1.0.0",
        "benchmark_id": governance.benchmark_id,
        "benchmark_version": governance.benchmark_version,
        "split": "test",
        "governance_id": governance.governance_id,
        "adapter_kind": AdapterKind.STATIC_QA,
        "evaluation_class": EvaluationClass.DEVELOPMENT,
        "item_digests": (),
        "modality_gates": (_gate(),),
        "task_manifest_digests": (),
        "corpus_digests": (),
        "environment_fingerprints": (),
        "evaluation_suite_manifest_digest": None,
    }
    suite = AtlasSuiteManifest(
        suite_id="unavailable-suite",
        version="1.0.0",
        title="Unavailable benchmark registration",
        benchmark_id=governance.benchmark_id,
        benchmark_version=governance.benchmark_version,
        split="test",
        governance_id=governance.governance_id,
        adapter_kind=AdapterKind.STATIC_QA,
        status=SuiteStatus.BLOCKED,
        evaluation_class=EvaluationClass.DEVELOPMENT,
        item_digests=(),
        modality_gates=(_gate(),),
        environment_fingerprints=(),
        content_digest=sha256_digest(empty_identity),
        blocking_reasons=("dataset unavailable locally",),
        created_at=NOW,
    )
    campaign = _campaign(
        suite.content_digest,
        ontology.manifest_digest,
        execution_digest=None,
        planned_item_count=10,
    )
    async with database.transaction() as session:
        await registry.register_dataset_governance(session, governance)
        await registry.register_suite(session, suite)
        await registry.register_ontology(session, ontology)
        await registry.register_campaign(session, campaign)
        allocation = TrialAllocation(
            allocation_id="blocked-allocation",
            campaign_digest=campaign.manifest_digest,
            condition_id="standardized",
            suite_digest=suite.content_digest,
            item_digest=sha256_digest("invented item"),
            trial_index=0,
            decision_sequence=0,
            selection_probability=1,
            allocation_policy_id="fixed",
            allocation_policy_version="1",
            decision_evidence_digest=sha256_digest("invented allocation"),
            created_at=NOW,
        )
        with pytest.raises(AtlasRegistryError, match="executable suite"):
            await registry.record_allocation(session, allocation)


async def test_run_manifest_recomputes_trial_design_and_aggregate_ceilings(database) -> None:
    async with database.transaction() as session:
        (
            registry,
            execution,
            campaign,
            _suite_record,
            _item_record,
            _request_record,
            run_manifest,
        ) = await _register_executable_chain(session)
        second = _rehash_run_manifest(run_manifest, run_id="atlas-run-second")
        session.add(_run_row(second.run_id, execution))
        await session.flush()
        with pytest.raises(AtlasRegistryError, match="aggregate Atlas runs"):
            await registry.register_run_manifest(session, second)
        stored_runs = (await session.scalars(select(AtlasRunManifestRow))).all()
        assert len(stored_runs) == 1


async def test_run_manifest_cannot_understate_fixed_trial_design(database) -> None:
    async with database.transaction() as session:
        registry = AtlasRegistry()
        profile, execution = await _seed_controls(session)
        governance = _governance()
        item = _item()
        suite = _suite(governance, item)
        ontology = _ontology()
        await registry.register_dataset_governance(session, governance)
        await registry.register_suite(session, suite)
        await registry.register_ontology(session, ontology)
        campaign = _campaign(
            suite.content_digest,
            ontology.manifest_digest,
            execution_digest=sha256_digest(execution),
            trials_per_item=2,
        )
        await registry.register_campaign(session, campaign)
        binding = _binding(campaign, suite, profile, execution)
        await registry.register_execution_binding(session, binding)
        request = _request(campaign, suite, item, execution)
        understated = _run_manifest(campaign, suite, binding, profile, execution, request)
        session.add(_run_row(understated.run_id, execution))
        await session.flush()
        with pytest.raises(AtlasRegistryError, match="trial design"):
            await registry.register_run_manifest(session, understated)


async def test_trial_result_cannot_be_cherry_picked_or_replaced(database) -> None:
    async with database.transaction() as session:
        (
            registry,
            execution,
            campaign,
            suite,
            _item_record,
            request,
            _run_manifest_record,
        ) = await _register_executable_chain(session)
        await registry.record_trial_request(session, request)
        artifact = _response_artifact()
        assert (
            _artifact_store(session).put_text(
                "4", media_type="text/plain", restricted=True, raw_data=True
            )
            == artifact
        )
        session.add(
            ArtifactRow(
                artifact_id=artifact.artifact_id,
                digest=artifact.digest,
                uri=artifact.uri,
                media_type=artifact.media_type,
                size_bytes=artifact.size_bytes,
                restricted=artifact.restricted,
                raw_data=artifact.raw_data,
                storage_backend="local",
                metadata_json={},
                created_at=NOW,
            )
        )
        verifier_record = _verifier_record()
        verifier_payload = verifier_record.model_dump(mode="json")
        verifier_row = VerifierResultRow(
            result_id=verifier_record.result_id,
            verifier_id=verifier_record.verifier_id,
            verifier_version=verifier_record.verifier_version,
            scope=verifier_record.scope,
            disposition=verifier_record.disposition.value,
            deterministic=verifier_record.deterministic,
            record_digest=sha256_digest(verifier_payload),
            record_json=verifier_payload,
            created_at=verifier_record.created_at,
        )
        session.add(verifier_row)
        await session.flush()
        mismatched_retry = _trial_result(
            execution, request, result_id_seed="retry-inflation", retry_count=1
        )
        with pytest.raises(AtlasRegistryError, match="retry count"):
            await registry.record_trial_result(session, mismatched_retry)
        result = _trial_result(execution, request)
        envelope = result.external_call_artifact
        assert envelope is not None
        assert (
            _artifact_store(session).put_text(
                "registry envelope",
                media_type="application/vnd.padawan.generation-result+json",
                restricted=True,
                raw_data=True,
            )
            == envelope
        )
        session.add(
            ArtifactRow(
                artifact_id=envelope.artifact_id,
                digest=envelope.digest,
                uri=envelope.uri,
                media_type=envelope.media_type,
                size_bytes=envelope.size_bytes,
                restricted=envelope.restricted,
                raw_data=envelope.raw_data,
                storage_backend="local",
                metadata_json={},
                created_at=NOW,
            )
        )
        session.add(
            ExternalCallRow(
                request_id=request.request_id,
                run_id=request.run_id,
                purpose="capability_atlas",
                provider="test-provider",
                request_hash=request.wire_request_digest,
                request_artifact_id=artifact.artifact_id,
                response_artifact_id=envelope.artifact_id,
                provider_response_id="response-test",
                status="completed",
                error=None,
                result_envelope_digest=envelope.digest,
                result_model_id=execution.student_model.model_id,
                result_protocol=execution.student_model.protocol,
                result_raw_request_digest=sha256_digest("registry raw request"),
                result_raw_response_digest=artifact.digest,
                result_output_text_digest=sha256_digest("registry output"),
                result_usage={"input_tokens": 4, "output_tokens": 1, "total_tokens": 5},
                result_capabilities_digest=sha256_digest("registry capabilities"),
                result_latency_ms=1,
                created_at=request.created_at,
                completed_at=result.completed_at,
            )
        )
        await session.flush()
        verifier_row.record_json = {"tampered": True}
        with pytest.raises(AtlasRegistryError, match="trial verifier evidence"):
            await registry.record_trial_result(session, result)
        verifier_row.record_json = verifier_payload
        await session.flush()
        await registry.record_trial_result(session, result)
        retry_provisional = request.model_copy(
            update={
                "request_id": "request-retry",
                "attempt_index": 1,
                "parent_request_id": request.request_id,
                "request_digest": sha256_digest("pending"),
                "created_at": NOW + timedelta(seconds=4),
            }
        )
        retry_identity = retry_provisional.model_dump(
            mode="json", exclude={"request_digest", "created_at"}
        )
        retry = AtlasTrialRequest(
            **{
                **retry_provisional.model_dump(mode="python"),
                "request_digest": sha256_digest(retry_identity),
            }
        )
        with pytest.raises(AtlasRegistryError, match="retry allowance"):
            await registry.record_trial_request(session, retry)
        alternate = _trial_result(execution, request, result_id_seed="alternate", score=0.5)
        with pytest.raises(AtlasRegistryError, match="alternate result"):
            await registry.record_trial_result(session, alternate)

        omitted = MetricEstimate(
            metric_id="success_rate",
            value=None,
            lower=None,
            upper=None,
            confidence_level=None,
            planned_trials=0,
            observed_trials=0,
            missing_trials=0,
            infrastructure_failures=0,
            contaminated_trials=0,
            evidence_result_digests=(),
            missing_reason="omitted unfavorable evidence",
        )
        provisional = AtlasSnapshot.model_construct(
            snapshot_id="pending",
            campaign_digest=campaign.manifest_digest,
            research_execution_digest=sha256_digest(execution),
            harness_profile_digest=execution.harness_profile_digest,
            ontology_digest=campaign.ontology_digest,
            upstream_claim_ids=(),
            local_observations=(
                LocalObservation(
                    metric=omitted,
                    condition_id="standardized",
                    suite_digest=suite.content_digest,
                    research_execution_digest=sha256_digest(execution),
                ),
            ),
            curves=(),
            failure_cluster_digests=(),
            unknowns=("intentionally incomplete",),
            complete=False,
            promotion_eligible=False,
            snapshot_digest=sha256_digest("pending"),
            created_at=NOW + timedelta(seconds=4),
        )
        identity = provisional.model_dump(
            mode="json", exclude={"snapshot_id", "snapshot_digest", "created_at"}
        )
        snapshot = AtlasSnapshot(
            **{
                **provisional.model_dump(mode="python"),
                "snapshot_id": content_id("atlas-snapshot", identity),
                "snapshot_digest": sha256_digest(identity),
            }
        )
        with pytest.raises(AtlasRegistryError, match="omits"):
            await registry.register_snapshot(session, snapshot)


async def test_raw_trace_proposal_requires_restricted_catalog_evidence(database) -> None:
    trace_digest = sha256_digest("consented trace")
    trace = ArtifactRef(
        artifact_id="trace-artifact",
        uri=f"artifact://sha256/{trace_digest[7:]}",
        digest=trace_digest,
        media_type="application/json",
        size_bytes=16,
        restricted=False,
        raw_data=True,
    )
    proposal = ExploratoryFailureProposal(
        proposal_id="proposal-1",
        consent_evidence_digest=sha256_digest("consent"),
        consent_lane="research reproduction",
        source_trace_digest=trace.digest,
        source_trace_artifact=trace,
        redacted_excerpt_digest=sha256_digest("redacted"),
        proposed_phenomenon="context state loss",
        proposed_failure_node_ids=("model.reasoning",),
        deduplication_key=sha256_digest("proposal dedupe"),
        created_at=NOW,
    )
    registry = AtlasRegistry()
    async with database.transaction() as session:
        session.add(
            ArtifactRow(
                artifact_id=trace.artifact_id,
                digest=trace.digest,
                uri=trace.uri,
                media_type=trace.media_type,
                size_bytes=trace.size_bytes,
                restricted=trace.restricted,
                raw_data=trace.raw_data,
                storage_backend="test",
                metadata_json={},
                created_at=NOW,
            )
        )
        await session.flush()
        with pytest.raises(AtlasRegistryError, match="restricted"):
            await registry.register_exploratory_proposal(session, proposal)


async def test_comparison_is_content_addressed_and_recomputes_controls(database) -> None:
    async with database.transaction() as session:
        (
            registry,
            execution,
            campaign,
            _suite_record,
            _item_record,
            _request_record,
            _run_manifest_record,
        ) = await _register_executable_chain(session)
        left = _snapshot(campaign, execution, unknown="left run not executed")
        right = _snapshot(campaign, execution, unknown="right run not executed")
        await registry.register_snapshot(session, left)
        await registry.register_snapshot(session, right)
        assessment = await ResearchControlRegistry().compare(
            session,
            left_execution_digest=left.research_execution_digest,
            right_execution_digest=right.research_execution_digest,
        )
        provisional = AtlasComparison.model_construct(
            comparison_id="pending",
            left_snapshot_digest=left.snapshot_digest,
            right_snapshot_digest=right.snapshot_digest,
            allowed_axes=(),
            observed_axes=assessment.differing_axes,
            comparability_evidence_digest=sha256_digest(assessment),
            metric_deltas=(),
            regressions=(),
            improvements=(),
            unknowns=("snapshots are incomplete",),
            causal_claim_permitted=False,
            comparison_digest=sha256_digest("pending"),
            created_at=NOW + timedelta(seconds=5),
        )
        identity = provisional.model_dump(
            mode="json",
            exclude={"comparison_id", "comparison_digest", "created_at"},
        )
        comparison = AtlasComparison(
            **{
                **provisional.model_dump(mode="python"),
                "comparison_id": content_id("atlas-comparison", identity),
                "comparison_digest": sha256_digest(identity),
            }
        )
        first = await registry.register_comparison(session, comparison)
        replay = await registry.register_comparison(session, comparison)
        assert first is replay
