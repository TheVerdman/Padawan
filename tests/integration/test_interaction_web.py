from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from padawan.interaction.composition import InteractionApplication
from padawan.interaction.web import create_interaction_web_app
from padawan.models.tables import InteractionSessionRow, InteractionTraceRow
from tests.support.interaction import _count, _lab


def _sse_events(payload: str) -> list[tuple[str, dict[str, Any]]]:
    records: list[tuple[str, dict[str, Any]]] = []
    for block in payload.strip().split("\n\n"):
        event = "message"
        data: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data.append(line.removeprefix("data:").strip())
        if data:
            records.append((event, json.loads("\n".join(data))))
    return records


async def test_private_web_boundary_rejects_a_weak_access_token(
    database: Any, tmp_path: Path
) -> None:
    service, store, artifacts, _client = await _lab(database, tmp_path / "artifacts")
    application = InteractionApplication(
        database=database,
        artifacts=artifacts,
        targets=service.targets,
        store=store,
        service=service,
    )

    with pytest.raises(ValueError, match="at least 16"):
        create_interaction_web_app(
            application,
            access_token="weak",
            close_application=False,
        )


async def test_private_web_boundary_enforces_auth_csrf_and_explicit_reasoning_reveal(
    database: Any, tmp_path: Path
) -> None:
    service, store, artifacts, _client = await _lab(database, tmp_path / "artifacts")
    application = InteractionApplication(
        database=database,
        artifacts=artifacts,
        targets=service.targets,
        store=store,
        service=service,
    )
    app = create_interaction_web_app(
        application,
        access_token="lab-access-token",
        close_application=False,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://lab.test") as client:
        index = await client.get("/")
        assert index.status_code == 200
        assert "Padawan Interaction Lab" in index.text
        assert "Raw restricted artifacts" in index.text
        assert "Reveal restricted research trace" not in index.text
        assert index.headers["content-security-policy"].startswith("default-src 'self'")
        browser_code = await client.get("/assets/app.js")
        assert 'summary.textContent = "Reasoning"' in browser_code.text
        assert "reasoningDisclosure(turn.trace_id)" in browser_code.text
        assert (await client.get("/api/bootstrap")).status_code == 401
        assert (
            await client.post("/api/auth/login", json={"access_token": "wrong-access-token"})
        ).status_code == 401

        login = await client.post("/api/auth/login", json={"access_token": "lab-access-token"})
        assert login.status_code == 200
        csrf = login.json()["csrf_token"]
        cookie = login.headers["set-cookie"]
        assert "HttpOnly" in cookie
        assert "SameSite=strict" in cookie

        bootstrap = await client.get("/api/bootstrap")
        serialized_bootstrap = bootstrap.text
        assert bootstrap.status_code == 200
        assert "lab-access-token" not in serialized_bootstrap
        assert bootstrap.json()["temporary_chat"] == {
            "durable_storage": False,
            "creates_research_trace": False,
            "creates_memory": False,
            "creates_training_candidate": False,
        }
        assert bootstrap.json()["targets"][0]["status"]["status"] == "configured"

        no_csrf = await client.post(
            "/api/sessions",
            json={"title": "Blocked", "research_trace_consent": False},
        )
        assert no_csrf.status_code == 403
        headers = {"X-Padawan-CSRF": csrf}
        created = await client.post(
            "/api/sessions",
            headers=headers,
            json={"title": "Private conversation", "research_trace_consent": True},
        )
        assert created.status_code == 201
        session_id = created.json()["session_id"]

        generated = await client.post(
            f"/api/sessions/{session_id}/generations",
            headers=headers,
            json={
                "target_id": "student.test.primary",
                "content": "Test the browser boundary",
                "parent_message_id": None,
                "mode": "message",
                "source_turn_id": None,
                "sampling": {
                    "temperature": 0.2,
                    "top_p": 0.95,
                    "max_output_tokens": 128,
                    "seed": 17,
                    "top_logprobs": None,
                    "stop": [],
                },
                "declared_axis_changes": [],
            },
        )
        assert generated.status_code == 200
        assert generated.headers["x-accel-buffering"] == "no"
        events = _sse_events(generated.text)
        assert [event for event, _data in events] == [
            "start",
            "delta",
            "delta",
            "completed",
        ]
        trace_id = events[-1][1]["trace_id"]

        conversation = await client.get(f"/api/sessions/{session_id}")
        assert conversation.status_code == 200
        assert "hidden plan" not in conversation.text
        normal_trace = await client.get(f"/api/traces/{trace_id}")
        assert normal_trace.status_code == 200
        assert "hidden plan" not in normal_trace.text
        assert normal_trace.json()["manifest"]["sampling"]["seed"] == 17
        forbidden = await client.get(f"/api/traces/{trace_id}?include_restricted=true")
        assert forbidden.status_code == 403
        reasoning_forbidden = await client.get(f"/api/traces/{trace_id}/private-reasoning")
        assert reasoning_forbidden.status_code == 403
        reasoning = await client.get(
            f"/api/traces/{trace_id}/private-reasoning",
            headers={"X-Padawan-Research-Trace": "reveal"},
        )
        assert reasoning.json() == {
            "trace_id": trace_id,
            "private_reasoning": "hidden plan 1",
        }
        revealed = await client.get(
            f"/api/traces/{trace_id}?include_restricted=true",
            headers={"X-Padawan-Research-Trace": "reveal"},
        )
        assert revealed.status_code == 200
        assert revealed.json()["restricted_research_view"]["private_reasoning"] == ("hidden plan 1")

        temporary_rows_before = (
            await _count(database, InteractionSessionRow),
            await _count(database, InteractionTraceRow),
        )
        temporary = await client.post(
            "/api/temporary/generations",
            headers=headers,
            json={
                "target_id": "student.test.primary",
                "history": [{"role": "user", "content": "Do not persist this"}],
                "sampling": {
                    "temperature": 0.2,
                    "top_p": 0.95,
                    "max_output_tokens": 128,
                    "seed": None,
                    "top_logprobs": None,
                    "stop": [],
                },
            },
        )
        assert temporary.status_code == 200
        assert _sse_events(temporary.text)[0][1]["durable_storage"] is False
        assert (
            await _count(database, InteractionSessionRow),
            await _count(database, InteractionTraceRow),
        ) == temporary_rows_before


async def test_readiness_is_explicit_and_does_not_probe_on_bootstrap(
    database: Any, tmp_path: Path
) -> None:
    service, store, artifacts, _client = await _lab(database, tmp_path / "artifacts")
    target = service.targets.get("student.test.primary")
    application = InteractionApplication(
        database=database,
        artifacts=artifacts,
        targets=service.targets,
        store=store,
        service=service,
    )
    app = create_interaction_web_app(
        application,
        access_token="lab-access-token",
        close_application=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://lab.test"
    ) as client:
        login = await client.post("/api/auth/login", json={"access_token": "lab-access-token"})
        csrf = login.json()["csrf_token"]
        assert target._checked_at is None
        await client.get("/api/bootstrap")
        assert target._checked_at is None
        readiness = await client.post(
            "/api/targets/student.test.primary/readiness",
            headers={"X-Padawan-CSRF": csrf},
        )
        assert readiness.status_code == 200
        assert readiness.json()["status"] == "ready"
        assert target._checked_at is not None
