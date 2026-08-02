from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from padawan.domains.magellan_improvement.contracts import (
    MagellanAgentProtocol,
    MagellanEnvironmentHandshake,
    MagellanExternalEffects,
    MagellanIdempotencyMode,
    MagellanMatchedWorldManifest,
    MagellanNetworkPolicy,
    MagellanRuntimeSecretPolicy,
    MagellanSourceIsolation,
    MagellanTenantIsolation,
    MagellanToolCapability,
    MagellanToolTier,
    MagellanWorldAllocation,
    MagellanWorldIsolation,
)
from padawan.domains.magellan_improvement.environment import MagellanEnvironmentInspector
from padawan.models.hashing import sha256_digest

NOW = datetime(2026, 8, 1, tzinfo=UTC)


def _git(repository, *arguments: str) -> None:
    subprocess.run(("git", "-C", str(repository), *arguments), check=True)


def _repository(tmp_path):
    repository = tmp_path / "magellan"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Padawan Tests")
    _git(repository, "config", "user.email", "padawan@example.invalid")
    (repository / ".gitignore").write_text(".env*\n!.env.example\n", encoding="utf-8")
    (repository / "requirements.txt").write_text("sqlalchemy==2.0.42\n", encoding="utf-8")
    migrations = repository / "backend" / "alembic" / "versions"
    migrations.mkdir(parents=True)
    (migrations / "0001_initial.py").write_text("revision = '0001'\n", encoding="utf-8")
    (repository / "backend" / "agent.py").write_text("ENABLED = True\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "initial")
    return repository


def _handshake(source_digest: str, **updates: object) -> MagellanEnvironmentHandshake:
    payload: dict[str, object] = {
        "environment_id": "magellan-test-environment",
        "repository_source_digest": source_digest,
        "driver_digest": sha256_digest("driver-v1"),
        "database_backend": "postgresql",
        "database_schema_digest": sha256_digest("database-schema-v1"),
        "authorization_policy_digest": sha256_digest("authorization-v1"),
        "world_schema_version": "1.0.0",
        "source_isolation": MagellanSourceIsolation.CONTENT_ADDRESSED_COPY,
        "runtime_secret_policy": MagellanRuntimeSecretPolicy.ENV_ALLOWLIST_ONLY,
        "world_isolation": MagellanWorldIsolation.DATABASE_CLONE,
        "reset_protocol_version": "1.0.0",
        "reset_verified": True,
        "matched_worlds_independent": True,
        "network_policy": MagellanNetworkPolicy.DENIED,
        "external_effects": MagellanExternalEffects.RECORDED_MOCKS,
        "agent_protocol": MagellanAgentProtocol.RESPONSES,
        "tenant_isolation": MagellanTenantIsolation.ENFORCED,
        "idempotency_mode": MagellanIdempotencyMode.DURABLE,
        "tool_surface": (
            MagellanToolCapability(
                tool_name="query_shipment",
                tier=MagellanToolTier.READ,
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                externally_effectful=False,
            ),
            MagellanToolCapability(
                tool_name="book_carrier",
                tier=MagellanToolTier.COMMIT_REGULATED,
                validators=("CarrierApproved", "ShipperAuthorizationValid"),
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                externally_effectful=True,
            ),
        ),
        "created_at": NOW,
    }
    payload.update(updates)
    return MagellanEnvironmentHandshake.model_validate(payload)


def test_repository_fingerprint_tracks_dirty_sources_without_disclosing_root(tmp_path) -> None:
    repository = _repository(tmp_path)
    inspector = MagellanEnvironmentInspector()

    clean = inspector.inspect_repository(repository, created_at=NOW)
    (repository / "backend" / "agent.py").write_text("ENABLED = False\n", encoding="utf-8")
    (repository / "backend" / "new_driver.py").write_text("DRIVER = 1\n", encoding="utf-8")
    (repository / ".DS_Store").write_bytes(b"volatile")
    (repository / ".env.secret").write_text("API_KEY=never-serialize\n", encoding="utf-8")
    dirty = inspector.inspect_repository(repository, created_at=NOW)

    assert clean.dirty is False
    assert dirty.dirty is True
    assert dirty.source_digest != clean.source_digest
    assert dirty.tracked_changed_paths == ("backend/agent.py",)
    assert dirty.included_untracked_paths == ("backend/new_driver.py",)
    assert dirty.excluded_volatile_paths == (".DS_Store",)
    assert dirty.excluded_sensitive_paths == (".env.secret",)
    serialized = json.dumps(dirty.model_dump(mode="json"), sort_keys=True)
    assert str(repository) not in serialized
    assert "never-serialize" not in serialized


def test_environment_assessment_requires_exact_ready_handshake(tmp_path) -> None:
    repository = _repository(tmp_path)
    inspector = MagellanEnvironmentInspector()
    snapshot = inspector.inspect_repository(repository, created_at=NOW)
    handshake = _handshake(snapshot.source_digest)
    handshake_path = tmp_path / "handshake.json"
    handshake_path.write_text(handshake.model_dump_json(), encoding="utf-8")

    assessment = inspector.assess(
        repository,
        handshake_path=handshake_path,
        created_at=NOW,
    )

    assert assessment.ready is True
    assert assessment.blockers == ()
    assert assessment.handshake is not None
    assert assessment.handshake.fingerprint == handshake.fingerprint

    drifted = _handshake(sha256_digest("another-worktree"))
    handshake_path.write_text(drifted.model_dump_json(), encoding="utf-8")
    mismatch = inspector.assess(repository, handshake_path=handshake_path, created_at=NOW)
    assert mismatch.ready is False
    assert "handshake repository digest does not match the worktree" in mismatch.blockers


def test_environment_blockers_cover_current_magellan_audit_gaps(tmp_path) -> None:
    repository = _repository(tmp_path)
    snapshot = MagellanEnvironmentInspector().inspect_repository(repository, created_at=NOW)
    blocked = _handshake(
        snapshot.source_digest,
        database_backend="sqlite",
        source_isolation=MagellanSourceIsolation.LIVE_WORKTREE,
        runtime_secret_policy=MagellanRuntimeSecretPolicy.MOUNTED_SECRET_FILES,
        world_isolation=MagellanWorldIsolation.NONE,
        reset_verified=False,
        matched_worlds_independent=False,
        network_policy=MagellanNetworkPolicy.UNRESTRICTED,
        external_effects=MagellanExternalEffects.LIVE,
        agent_protocol=MagellanAgentProtocol.CHAT_COMPLETIONS,
        tenant_isolation=MagellanTenantIsolation.DECLARED_ONLY,
        idempotency_mode=MagellanIdempotencyMode.PROCESS_LOCAL,
    )

    assert blocked.evaluation_blockers() == (
        "evaluation database is not PostgreSQL",
        "runtime executes from the mutable live worktree",
        "runtime secrets are not confined to an environment allowlist",
        "world isolation is absent",
        "world reset is not verified",
        "matched runs do not have independent mutable worlds",
        "scenario network access is unrestricted",
        "external side effects are live",
        "agent endpoint does not use the Responses protocol",
        "tenant isolation is not enforced",
        "tool idempotency is not durable",
    )


def test_matched_world_contract_rejects_shared_mutable_identity() -> None:
    state_digest = sha256_digest({"shipment": "draft"})
    fingerprint = sha256_digest("environment")
    allocations = (
        MagellanWorldAllocation(
            allocation_id="allocation-target",
            condition_id="target",
            world_id="world-target",
            isolation_token_digest=sha256_digest("shared-token"),
            environment_fingerprint=fingerprint,
            initial_state_digest=state_digest,
        ),
        MagellanWorldAllocation(
            allocation_id="allocation-control",
            condition_id="control",
            world_id="world-control",
            isolation_token_digest=sha256_digest("shared-token"),
            environment_fingerprint=fingerprint,
            initial_state_digest=state_digest,
        ),
    )

    with pytest.raises(ValidationError, match="isolation token"):
        MagellanMatchedWorldManifest(
            matched_group_id="matched-1",
            source_state_digest=state_digest,
            allocations=allocations,
            created_at=NOW,
        )


def test_missing_runtime_paths_do_not_leak_from_inspection_errors(tmp_path) -> None:
    missing_repository = tmp_path / "private-missing-magellan"
    missing_handshake = tmp_path / "private-missing-handshake.json"
    inspector = MagellanEnvironmentInspector()

    with pytest.raises(ValueError, match="repository is unavailable") as repository_error:
        inspector.inspect_repository(missing_repository)
    with pytest.raises(ValueError, match="handshake is unavailable") as handshake_error:
        inspector.load_handshake(missing_handshake)

    assert str(missing_repository) not in str(repository_error.value)
    assert str(missing_handshake) not in str(handshake_error.value)

    invalid_handshake = tmp_path / "invalid-handshake.json"
    invalid_handshake.write_text('{"api_key":"must-not-leak"}', encoding="utf-8")
    with pytest.raises(ValueError, match="handshake is invalid") as invalid_error:
        inspector.load_handshake(invalid_handshake)
    assert "must-not-leak" not in str(invalid_error.value)
