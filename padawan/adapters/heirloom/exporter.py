from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from padawan.governance.policy import AccessContext, ExportPolicy
from padawan.models.contracts import AttemptRecord, DevelopmentalEpisode, GradeRecord
from padawan.models.database import Database
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    AttemptRow,
    CorpusItemRow,
    EpisodeRow,
    GradeRow,
    TeacherInterventionRow,
)


@dataclass(frozen=True)
class HeirloomExportResult:
    episode_id: str
    episode_jsonl: Path
    artifact_root: Path
    bundle_path: Path
    artifact_count: int
    semantic_verification_claimed: bool = False


class HeirloomAuditExporter:
    """Restricted v0 export for Heirloom's structural Padawan validator.

    The export deliberately marks every training eligibility flag false. Passing
    Heirloom validation proves bundle shape and hashes, not algebra semantics.
    """

    def __init__(
        self,
        *,
        database: Database,
        policy: ExportPolicy,
        audit_hmac_key: bytes,
    ) -> None:
        if len(audit_hmac_key) < 32:
            raise ValueError("audit HMAC key must contain at least 32 bytes")
        self.database = database
        self.policy = policy
        self.audit_hmac_key = audit_hmac_key

    async def export(self, *, episode_id: str, output_root: Path) -> HeirloomExportResult:
        source = await self._load(episode_id)
        episode = source["episode"]
        attempt = source["attempt"]
        grade = source["grade"]
        item = source["item"]
        intervention = source["intervention"]
        if not isinstance(episode, DevelopmentalEpisode):
            raise TypeError("invalid episode record")
        if not isinstance(attempt, AttemptRecord):
            raise TypeError("invalid attempt record")
        if not isinstance(grade, GradeRecord):
            raise TypeError("invalid grade record")

        context = AccessContext(
            principal="padawan.heirloom_exporter",
            roles=frozenset(),
            purpose="audit",
        )
        raw_decision = self.policy.decide(
            attempt.raw_generation_ref,
            context=context,
            contains_private_reasoning=False,
        )
        # Raw provider bytes remain in Padawan CAS. The compatible export uses
        # normalized public evidence unless restricted export was explicitly enabled.
        include_raw = raw_decision.allowed

        root = output_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        hid = _heirloom_id(episode_id)
        public_derivation = attempt.public_derivation or {
            "unavailable": True,
            "reason": "student output did not satisfy public derivation schema",
        }
        trace = {
            "format": "heirloom.padawan_trace",
            "version": 0,
            "episode_id": hid,
            "task_understanding": "Solve and publicly justify the governed algebra item.",
            "assumptions": [],
            "compressed_rationale": _compressed_rationale(attempt),
            "subgoals": ["derive result", "check against symbolic verifier"],
            "tool_calls": [call.model_dump(mode="json") for call in attempt.tool_calls],
            "observations": [evidence.statement for evidence in grade.evidence]
            or ["No deterministic evidence was available."],
            "failed_checks": [
                evidence.statement for evidence in grade.evidence if grade.student_failure
            ],
            "repairs": [],
            "final_validation": {
                "validator": f"{grade.grader_type}:{grade.grader_version}",
                "expected": grade.outcome.value,
                "evidence_refs": [],
            },
            "final_artifact_ref": f"artifact://padawan/finals/{hid}.json",
            "public_derivation": public_derivation,
            "private_reasoning": {
                "exported": False,
                "reason": "private reasoning is excluded by default export policy",
            },
            "raw_provider_response_exported": include_raw,
        }
        teacher_brief = {
            "format": "padawan.normalized_teacher_brief",
            "episode_id": episode_id,
            "teacher": (
                {
                    "provider": intervention.get("provider"),
                    "model_id": intervention.get("model_id"),
                    "mode": intervention.get("mode"),
                    "lesson": intervention.get("lesson"),
                    "repair": intervention.get("repair"),
                    "citations": intervention.get("citations", []),
                    "validation_status": intervention.get("validation_status"),
                }
                if intervention
                else {"availability": "unavailable"}
            ),
            "teacher_tokens_masked_from_loss": True,
        }
        final = {
            "attempt_id": attempt.attempt_id,
            "final_answer": attempt.final_answer,
            "public_derivation": public_derivation,
        }
        grade_payload = grade.model_dump(mode="json")
        rationale = _compressed_rationale(attempt)

        paths_and_bytes = {
            f"padawan/traces/{hid}.json": _json_bytes(trace),
            f"padawan/briefs/{hid}.json": _json_bytes(teacher_brief),
            f"padawan/rationales/{hid}.txt": rationale.encode("utf-8"),
            f"padawan/finals/{hid}.json": _json_bytes(final),
            f"padawan/verifiers/{hid}-result.json": _json_bytes(grade_payload),
        }
        for relative, content in paths_and_bytes.items():
            _atomic_write(root, relative, content)

        artifacts = [
            {
                "name": relative.removeprefix("padawan/").replace("/", "__"),
                "ref": f"artifact://{relative}",
                "media_type": ("application/json" if relative.endswith(".json") else "text/plain"),
                "sha256": sha256_digest(content),
                "bytes": len(content),
            }
            for relative, content in sorted(paths_and_bytes.items())
        ]
        artifact_hashes = {item["name"]: item["sha256"] for item in artifacts}
        verifier_pack_hash = sha256_digest(
            {
                "grader_type": grade.grader_type,
                "grader_version": grade.grader_version,
                "deterministic": grade.deterministic,
            }
        )
        hidden_case = hmac.new(
            self.audit_hmac_key,
            f"{episode_id}:symbolic-algebra".encode(),
            hashlib.sha256,
        ).hexdigest()
        audit_payload = {
            "episode_id": episode_id,
            "source_episode_hash": sha256_digest(episode.model_dump(mode="json")),
            "source_grade_hash": sha256_digest(grade_payload),
            "note": (
                "Restricted identifiers attest linkage only; no hidden semantic test is claimed."
            ),
        }
        audit_relative = f"padawan/restricted/{hid}-audit.json"
        _atomic_write(root, audit_relative, _json_bytes(audit_payload), mode=0o600)
        bundle = {
            "format": "heirloom.padawan_artifact_bundle",
            "version": 0,
            "episode_id": hid,
            "artifacts": artifacts,
            "restricted_audit": {
                "verifier_pack_id": "padawan.sympy.public.v1",
                "verifier_pack_hash": verifier_pack_hash,
                "public_verifier_hash": sha256_digest(grade_payload),
                "hidden_verifier_hash": sha256_digest(
                    b"no-hidden-semantic-verifier-in-padawan-export-v1"
                ),
                "hidden_case_ids_hmac": [f"hmac-sha256:{hidden_case}"],
                "artifact_hashes": artifact_hashes,
                "result_digest": sha256_digest({"episode": episode_id, "grade": grade_payload}),
                "restricted_bundle_ref": f"artifact://{audit_relative}",
            },
        }
        bundle_relative = f"padawan/artifacts/{hid}-bundle.json"
        bundle_path = _atomic_write(root, bundle_relative, _json_bytes(bundle))
        episode_export = {
            "format": "heirloom.padawan_episode",
            "version": 0,
            "episode_id": hid,
            "source_id": "heirloom.padawan.v0.padawan_audit_export",
            "created_at": _rfc3339(episode.created_at),
            "checkpoint_ref": attempt.checkpoint_id,
            "task": {
                "domain": "symbolic_algebra",
                "task_kind": str(item["template_family_id"]),
                "prompt": str(item["prompt"]),
                "artifact_type": "public_derivation",
                "difficulty": float(item["difficulty"]),
            },
            "teacher": {
                "model": str(intervention.get("model_id") or "unavailable"),
                "guidance_level": 1 if intervention else 0,
                "guidance_policy": {
                    "difficulty_band": "governed",
                    "distance_proxy": "matched_sibling_transfer",
                    "over_guidance_score": 0.0,
                },
                "brief_ref": f"artifact://padawan/briefs/{hid}.json",
                "teacher_tokens_masked_from_loss": True,
                "forbidden_outputs_checked": True,
            },
            "padawan": {
                "model": attempt.model_id,
                "trace_ref": f"artifact://padawan/traces/{hid}.json",
                "compressed_rationale_ref": f"artifact://padawan/rationales/{hid}.txt",
                "artifact_ref": f"artifact://padawan/finals/{hid}.json",
                "artifact_bundle_ref": f"artifact://{bundle_relative}",
                "final_response_ref": f"artifact://padawan/finals/{hid}.json",
            },
            "verifier": {
                "verifier_id": f"{grade.grader_type}:{grade.grader_version}",
                "verifier_pack_hash": verifier_pack_hash,
                "tests_passed": grade.deterministic,
                "schema_valid": attempt.public_derivation is not None,
                "hidden_tests_passed": False,
                "unrelated_changes": 0,
                "reward": grade.score,
                "reward_components": {"deterministic_grade": grade.score},
                "failure_class": grade.error_class,
            },
            "memory": {
                "selection_report_ref": None,
                "smft_access_counts_ref": None,
                "smft_mask_ref": None,
            },
            "selection": {
                "eligible_for_sft": False,
                "eligible_for_preference": False,
                "eligible_for_rlvr_replay": False,
                "eligible_for_smft": False,
                "requires_teacherless_replay": False,
                "teacherless_replay_episode_id": None,
                "sft_weight": 0.0,
            },
        }
        episode_path = _atomic_write(
            root, f"episodes/{hid}.jsonl", _jsonl_record_bytes(episode_export)
        )
        return HeirloomExportResult(
            episode_id=episode_id,
            episode_jsonl=episode_path,
            artifact_root=root / "padawan",
            bundle_path=bundle_path,
            artifact_count=len(artifacts),
        )

    async def _load(self, episode_id: str) -> dict[str, Any]:
        async with self.database.transaction() as session:
            row = await session.get(EpisodeRow, episode_id)
            if row is None:
                raise KeyError(episode_id)
            if row.status not in {"complete", "failed", "review_required"}:
                raise ValueError("only closed episodes can be exported")
            episode = DevelopmentalEpisode.model_validate(row.record_json, strict=False)
            if episode.initial_attempt_id is None or episode.grade_id is None:
                raise ValueError("episode has no initial attempt and grade to export")
            attempt_row = await session.get(AttemptRow, episode.initial_attempt_id)
            grade_row = await session.get(GradeRow, episode.grade_id)
            item_row = await session.get(CorpusItemRow, episode.task_item_id)
            if attempt_row is None or grade_row is None or item_row is None:
                raise ValueError("episode references missing normalized records")
            intervention: dict[str, Any] = {}
            if episode.intervention_id:
                intervention_row = await session.scalar(
                    select(TeacherInterventionRow).where(
                        TeacherInterventionRow.intervention_id == episode.intervention_id
                    )
                )
                if intervention_row is not None:
                    intervention = dict(intervention_row.record_json)
            return {
                "episode": episode,
                "attempt": AttemptRecord.model_validate(attempt_row.record_json, strict=False),
                "grade": GradeRecord.model_validate(grade_row.record_json, strict=False),
                "item": {
                    "template_family_id": item_row.template_family_id,
                    "prompt": item_row.prompt,
                    "difficulty": item_row.difficulty,
                },
                "intervention": intervention,
            }


def _heirloom_id(episode_id: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in "_.-" else "-" for character in episode_id
    )
    return f"padawan_{cleaned}"


def _compressed_rationale(attempt: AttemptRecord) -> str:
    if attempt.public_derivation:
        steps = attempt.public_derivation.get("steps", [])
        summary = "; ".join(
            f"{step.get('step_id')}: {step.get('operation')}"
            for step in steps
            if isinstance(step, dict)
        )
        return (summary or "Public derivation was recorded.")[:1200]
    return "The response was retained, but no valid public derivation could be parsed."


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8")


def _jsonl_record_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _rfc3339(value: datetime) -> str:
    utc_value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return utc_value.isoformat().replace("+00:00", "Z")


def _atomic_write(root: Path, relative: str, content: bytes, *, mode: int = 0o644) -> Path:
    safe = ExportPolicy.safe_relative_name(relative)
    target = (root / safe).resolve()
    if root != target and root not in target.parents:
        raise ValueError("unsafe export target")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".export-", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
