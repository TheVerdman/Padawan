from __future__ import annotations

from dataclasses import dataclass

from padawan.adapters.base import rendered_messages, rendered_prompt
from padawan.domains.contracts import VerifierDisposition, VerifierResult
from padawan.domains.temporal_grounding.developmental import (
    build_temporal_student_request,
    temporal_scenario,
)
from padawan.domains.temporal_grounding.verifier import TemporalPolicyVerifier
from padawan.models.contracts import CorpusItemRecord
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.temporal.contracts import TemporalDecision
from padawan.training.contracts import (
    AuthoredDemonstration,
    AuthoredMessage,
    AuthoredTargetEvent,
)

TEMPORAL_DEMONSTRATION_VERSION = "padawan-temporal-authored-sft-v1"


@dataclass(frozen=True)
class TemporalAuthoredDemonstrationBuild:
    demonstration: AuthoredDemonstration
    verifier_result: VerifierResult


class TemporalAuthoredDemonstrationFactory:
    """Build deterministic, verifier-backed SFT bootstrap examples from temporal items."""

    def __init__(self, verifier: TemporalPolicyVerifier | None = None) -> None:
        self.verifier = verifier or TemporalPolicyVerifier()

    def build(self, item: CorpusItemRecord) -> TemporalAuthoredDemonstrationBuild:
        scenario = temporal_scenario(item)
        expected = item.expected_answer
        if expected is None or expected.get("kind") != "temporal_decision":
            raise ValueError("temporal authored demonstration requires a decision answer")
        decision = TemporalDecision.model_validate(expected.get("decision"), strict=False)
        verifier_result = self.verifier.verify(
            scenario=scenario,
            decision=decision,
            created_at=item.created_at,
        )
        if verifier_result.disposition != VerifierDisposition.VERIFIED:
            raise ValueError("temporal authored answer does not satisfy its verifier")

        request = build_temporal_student_request(
            request_id=f"authored:{item.item_id}",
            item=item,
            state_reference=f"authored-bootstrap:{scenario.scenario_id}",
            intervention=None,
            retrieved_lessons={},
        )
        messages = tuple(
            AuthoredMessage.model_validate(message, strict=False)
            for message in rendered_messages(request)
        )
        final_answer = decision.model_dump(mode="json")
        final_content = canonical_json_bytes(final_answer).decode("utf-8")
        rights_digest = sha256_digest(item.rights.model_dump(mode="json"))
        identity = sha256_digest(
            {
                "version": TEMPORAL_DEMONSTRATION_VERSION,
                "item_id": item.item_id,
                "scenario_id": scenario.scenario_id,
                "messages": [message.model_dump(mode="json") for message in messages],
                "final_answer": final_answer,
                "verifier_result_id": verifier_result.result_id,
            }
        )
        demonstration = AuthoredDemonstration(
            demonstration_id=f"authored-temporal-{identity[7:39]}",
            domain_id="temporal.grounding",
            competency_id=item.competency_id,
            source_item_id=item.item_id,
            verification_scope=scenario.scenario_id,
            messages=messages,
            prompt=rendered_prompt(request),
            target_events=(AuthoredTargetEvent(content=final_content),),
            final_answer=final_answer,
            verifier_result_id=verifier_result.result_id,
            verifier_result_digest=sha256_digest(verifier_result.model_dump(mode="json")),
            rights=item.rights,
            rights_digest=rights_digest,
            quality_evidence_refs=(verifier_result.result_id,),
            authored_by=TEMPORAL_DEMONSTRATION_VERSION,
            reviewed_by=item.rights.reviewed_by or "padawan.generated-source-policy",
            created_at=item.created_at,
        )
        return TemporalAuthoredDemonstrationBuild(
            demonstration=demonstration,
            verifier_result=verifier_result,
        )
