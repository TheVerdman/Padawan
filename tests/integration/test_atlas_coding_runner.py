"""Mock model HTTP plus the real pinned official judge; never model capability evidence."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from padawan.atlas.adapters import AdapterReadinessContext
from padawan.atlas.coding_judge import DockerBatchJudge, file_sha256, unpack_package
from padawan.atlas.coding_manifests import coding_item
from padawan.atlas.coding_runner import run_coding_trials
from padawan.atlas.contracts import Modality
from padawan.models.tables import AtlasTrialResultRow, ExternalCallRow, VerifierResultRow
from tests.integration.test_atlas_activation import fixture


@pytest.mark.docker
async def test_real_hard_judge_is_retained_in_native_atlas_and_replayed(database, tmp_path):
    location = os.environ.get("PADAWAN_TEST_CODING_ROOT")
    if location is None:
        pytest.skip("requires explicit official coding-judge fixture directory")
    root = Path(location)
    archive = root / "upstream/1983F.zip"
    header = root / "upstream/LightCPVerifier/include/testlib.h"
    source = (root / "controls/1983F.cpp").read_text()
    package = unpack_package(
        archive,
        tmp_path / "package",
        problem_id="1983F",
        expected_digest="c91d0774737b2ff74fef97e677ceebb4271a1174039d993b601f1121cfcf54f7",
        include_extra_cases=True,
    )
    judge = DockerBatchJudge(
        image="docker.io/library/gcc@sha256:b99b86a28812b1e6453a231a947dc43d76fe192788a12f344a9b568bf9f5d24c",
        scratch=tmp_path / "judge",
        testlib=header,
        testlib_digest=file_sha256(header),
    )
    item = coding_item(
        {
            "problem_id": "1983F",
            "platform": "codeforces",
            "difficulty": "hard",
            "problem_statement": "Offline HTTP fixture for the retained 1983F control solution.",
        },
        package,
        judge,
    )
    calls, details = [], {}

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "id": "offline-coding-response",
                "model": "student-model",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": source}],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 100, "total_tokens": 110},
            },
        )

    executor, _, _, activation = await fixture(
        database, tmp_path, handler, coding=item, details=details
    )
    arguments = dict(
        executor=executor,
        client=details["client"],
        activation_ref=activation,
        requests=details["plan"].requests,
        items={item.item_digest: item},
        profile=details["profile"],
        execution=details["execution"],
        configuration=details["configuration"],
        readiness=AdapterReadinessContext(
            required_modalities=(Modality.TEXT,), modality_gates=details["suite"].modality_gates
        ),
        packages={item.item_digest: package},
        judge=judge,
    )
    first = await run_coding_trials(**arguments)
    assert set(first.values()) == {"verified_success"}, first
    assert await run_coding_trials(**arguments) == first
    assert len(calls) == 1
    async with database.transaction() as session:
        results = (await session.scalars(select(AtlasTrialResultRow))).all()
        verifiers = (await session.scalars(select(VerifierResultRow))).all()
        external = (await session.scalars(select(ExternalCallRow))).all()
        assert len(results) == len(verifiers) == len(external) == 1
        assert verifiers[0].record_json["evidence"]["cases_passed"] == 58
        assert verifiers[0].record_json["evidence"]["cases_total"] == 58
        assert results[0].record_json["score"] == 1.0
        assert external[0].purpose == "capability_atlas"
    assert "archive_sha256" not in json.loads(calls[0].content)["input"]
