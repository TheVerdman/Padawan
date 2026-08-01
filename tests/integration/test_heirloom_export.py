from __future__ import annotations

import hashlib
import json
from pathlib import Path

from padawan.adapters.heirloom.exporter import HeirloomAuditExporter
from padawan.governance.policy import ExportPolicy
from tests.helpers import build_test_workflow


async def test_restricted_heirloom_bundle_is_hash_valid_but_claims_no_semantics(
    database, tmp_path
) -> None:
    _, supervisor, runs, _, _, state_id = await build_test_workflow(
        database, tmp_path / "artifacts"
    )
    async with database.transaction() as session:
        await runs.create(
            session,
            run_id="run-export",
            payload={
                "student_id": "student-test",
                "state_id": state_id,
                "pool": "curriculum",
                "experiment_seed": 17,
                "teacher_mode": "diagnostic_critique",
                "treatment_condition": "frontier_teacher_critique",
                "control_condition": "no_intervention",
            },
        )
    await supervisor.run(budget=256)
    exporter = HeirloomAuditExporter(
        database=database,
        policy=ExportPolicy(),
        audit_hmac_key=b"a-real-test-audit-key-with-32-bytes-minimum",
    )
    result = await exporter.export(
        episode_id="episode-run-export", output_root=tmp_path / "heirloom-export"
    )
    assert len(result.episode_jsonl.read_text().splitlines()) == 1
    exported_episode = json.loads(result.episode_jsonl.read_text())
    bundle = json.loads(result.bundle_path.read_text())
    assert result.semantic_verification_claimed is False
    assert exported_episode["selection"] == {
        "eligible_for_preference": False,
        "eligible_for_rlvr_replay": False,
        "eligible_for_sft": False,
        "eligible_for_smft": False,
        "requires_teacherless_replay": False,
        "sft_weight": 0.0,
        "teacherless_replay_episode_id": None,
    }
    assert exported_episode["verifier"]["hidden_tests_passed"] is False
    assert exported_episode["teacher"]["teacher_tokens_masked_from_loss"] is True
    trace_ref = exported_episode["padawan"]["trace_ref"]
    trace = json.loads(_ref_path(result.artifact_root, trace_ref).read_text())
    assert trace["private_reasoning"]["exported"] is False
    assert trace["raw_provider_response_exported"] is False
    for artifact in bundle["artifacts"]:
        content = _ref_path(result.artifact_root, artifact["ref"]).read_bytes()
        assert artifact["bytes"] == len(content)
        assert artifact["sha256"] == f"sha256:{hashlib.sha256(content).hexdigest()}"
        assert (
            bundle["restricted_audit"]["artifact_hashes"][artifact["name"]] == (artifact["sha256"])
        )


def _ref_path(root: Path, reference: str) -> Path:
    return root / reference.removeprefix("artifact://padawan/")
