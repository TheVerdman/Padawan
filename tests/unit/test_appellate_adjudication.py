from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from padawan.artifacts.store import LocalArtifactStore
from padawan.domains.legal.appellate import (
    AppellateCorpusGenerator,
    AppellateScenarioFamily,
    AppellateScenarioManifest,
    AppellateSemanticAssessmentDraft,
    build_fourth_circuit_pack,
)
from padawan.domains.legal.appellate.adjudication import (
    AppellateAdjudicationError,
    AppellateAdjudicationService,
)
from padawan.domains.legal.appellate.developmental import AppellateDevelopmentalAuthority
from padawan.models.contracts import CorpusPool, GradeOutcome, ResearchRole
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from padawan.orchestration.state_machine import RunStore
from tests.appellate_helpers import NOW, build_semantic_assessment, build_submission
from tests.helpers import CallbackGenerationClient


def _scenario():
    return AppellateCorpusGenerator().scenario(
        family=AppellateScenarioFamily.AMBIGUOUS_VIDEO,
        seed=31,
        split=CorpusPool.CURRICULUM.value,
        created_at=NOW,
    )


def _draft_json(*, unbound: bool = False) -> str:
    scenario = _scenario()
    submission = build_submission(scenario)
    assessment = build_semantic_assessment(scenario, submission)
    claim_assessments = assessment.claim_assessments
    if unbound:
        claim_assessments = tuple(
            item.model_copy(update={"authority_passage_ids": ("brown-grainy-video",)})
            if item.claim_id == "claim-counterargument"
            else item
            for item in claim_assessments
        )
    draft = AppellateSemanticAssessmentDraft(
        claim_assessments=claim_assessments,
        adverse_authority_assessments=assessment.adverse_authority_assessments,
        issue_coverage=assessment.issue_coverage,
        preservation_coverage=assessment.preservation_coverage,
        remedy_coverage=assessment.remedy_coverage,
    )
    return draft.model_dump_json()


async def test_adjudicator_uses_strict_evidence_bound_request(database, tmp_path) -> None:
    async with database.transaction() as session:
        await RunStore().create(session, run_id="run-adjudicator", payload={})
    client = CallbackGenerationClient(lambda _request: _draft_json(), "adjudicator-test")
    service = AppellateAdjudicationService(
        executor=IdempotentGenerationExecutor(
            database=database,
            artifacts=LocalArtifactStore(tmp_path / "artifacts"),
            client=client,
        ),
        provider="adjudicator-test",
    )
    scenario = _scenario()
    submission = build_submission(scenario)

    assessment = await service.adjudicate(
        run_id="run-adjudicator",
        phase="cold",
        pack=build_fourth_circuit_pack(),
        scenario=scenario,
        submission=submission,
    )

    assert assessment.research_role == ResearchRole.ADJUDICATOR
    assert assessment.model_id == "adjudicator-test-model"
    assert len(client.calls) == 1
    request = client.calls[0]
    assert request.store is False
    assert request.schema_name == "padawan_appellate_semantic_assessment"
    assert request.json_schema == AppellateSemanticAssessmentDraft.model_json_schema()
    assert request.metadata["research_role"] == ResearchRole.ADJUDICATOR.value
    payload = json.loads(str(request.input))
    assert payload["scope"]["currentness_available"] is False
    assert payload["scope"]["procedural_posture"] == scenario.procedural_posture
    assert payload["scope"]["court"] == build_fourth_circuit_pack().court
    counterargument = next(
        packet
        for packet in payload["claim_packets"]
        if packet["claim"]["claim_id"] == "claim-counterargument"
    )
    assert {item["passage_id"] for item in counterargument["admitted_authority_passages"]} == {
        "scott-blatant-contradiction"
    }
    assert counterargument["admitted_authority_passages"][0]["weight"] == "supreme_court"
    assert "good-law" in request.instructions.lower()


async def test_adjudicator_retries_and_rejects_unbound_semantic_evidence(
    database, tmp_path
) -> None:
    async with database.transaction() as session:
        await RunStore().create(session, run_id="run-invalid-adjudicator", payload={})
    client = CallbackGenerationClient(
        lambda _request: _draft_json(unbound=True), "adjudicator-test"
    )
    service = AppellateAdjudicationService(
        executor=IdempotentGenerationExecutor(
            database=database,
            artifacts=LocalArtifactStore(tmp_path / "artifacts"),
            client=client,
        ),
        provider="adjudicator-test",
        max_attempts=2,
    )

    with pytest.raises(AppellateAdjudicationError, match="unbound passage"):
        await service.adjudicate(
            run_id="run-invalid-adjudicator",
            phase="cold",
            pack=build_fourth_circuit_pack(),
            scenario=_scenario(),
            submission=build_submission(_scenario()),
        )

    assert len(client.calls) == 2
    second_payload = json.loads(str(client.calls[1].input))
    assert any(
        "unbound passage" in error
        for error in second_payload["correction_errors_from_prior_attempt"]
    )


class _AdjudicatorSpy:
    def __init__(self) -> None:
        self.calls = 0

    async def adjudicate(self, **_kwargs):
        self.calls += 1
        raise AssertionError("deterministic hard-gate failures must not reach adjudication")


async def test_appellate_hard_gate_failure_skips_model_adjudication() -> None:
    item = AppellateCorpusGenerator().generate(
        pool=CorpusPool.CURRICULUM,
        seed=31,
        groups_per_family=1,
        siblings_per_group=2,
        families=(AppellateScenarioFamily.AMBIGUOUS_VIDEO,),
        created_at=NOW,
    )[0]
    scenario = AppellateScenarioManifest.model_validate(
        item.verifier_spec.parameters["scenario"], strict=False
    )
    submission = build_submission(scenario)
    citations = tuple(
        citation.model_copy(update={"quoted_text": "a court may weigh the evidence"})
        if citation.citation_id == "cite-tolan"
        else citation
        for citation in submission.citations
    )
    invalid_submission = submission.model_copy(update={"citations": citations})
    spy = _AdjudicatorSpy()
    authority = AppellateDevelopmentalAuthority(adjudicator=spy)  # type: ignore[arg-type]

    grade = await authority.grade_attempt(
        run_id="run-hard-gate",
        phase="cold",
        item=item,
        attempt=SimpleNamespace(
            attempt_id="attempt-hard-gate",
            final_answer=invalid_submission.model_dump(mode="json"),
        ),
    )

    assert grade.outcome == GradeOutcome.INVALID_PROCESS
    assert grade.error_class == "appellate_integrity_gate_failure"
    assert spy.calls == 0
