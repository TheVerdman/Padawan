from __future__ import annotations

import json

import pytest

from padawan.config.settings import EnvFileSecurityWarning, Settings


def test_explicit_env_file_loads_allowlisted_secrets_without_manifest_disclosure(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "external.env"
    env_file.write_text(
        "OPENAI_API_KEY=test-openai-secret\n"
        "ANTHROPIC_API_KEY=test-anthropic-secret\n"
        "INKLING_API_KEY=test-inkling-secret\n"
        "PADAWAN_OPENAI_MODEL=test-openai-model\n"
        "UNRELATED_SECRET=must-not-enter-settings\n"
    )
    env_file.chmod(0o600)
    monkeypatch.setenv("PADAWAN_ENV_FILE", str(env_file))

    settings = Settings.load()

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "test-openai-secret"
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "test-anthropic-secret"
    assert settings.inkling_api_key is not None
    assert settings.inkling_api_key.get_secret_value() == "test-inkling-secret"
    assert settings.openai_model == "test-openai-model"
    serialized = json.dumps(settings.redacted_manifest(), sort_keys=True)
    assert "test-openai-secret" not in serialized
    assert "test-anthropic-secret" not in serialized
    assert "test-inkling-secret" not in serialized
    assert str(env_file) not in serialized
    assert "UNRELATED_SECRET" not in serialized


def test_process_environment_overrides_explicit_env_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "external.env"
    env_file.write_text("OPENAI_API_KEY=file-secret\n")
    env_file.chmod(0o600)
    monkeypatch.setenv("OPENAI_API_KEY", "process-secret")

    settings = Settings.load(env_file=env_file)

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "process-secret"


def test_broad_env_file_permissions_warn_in_development(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "external.env"
    env_file.write_text("OPENAI_API_KEY=test-secret\n")
    env_file.chmod(0o644)
    monkeypatch.delenv("PADAWAN_ENVIRONMENT", raising=False)

    with pytest.warns(EnvFileSecurityWarning):
        settings = Settings.load(env_file=env_file)

    assert settings.environment == "development"


def test_broad_env_file_permissions_fail_in_production(tmp_path) -> None:
    env_file = tmp_path / "external.env"
    env_file.write_text("PADAWAN_ENVIRONMENT=production\nOPENAI_API_KEY=test-secret\n")
    env_file.chmod(0o644)

    with pytest.raises(ValueError, match="group/other"):
        Settings.load(env_file=env_file)


def test_missing_explicit_env_file_fails_without_exposing_path(tmp_path) -> None:
    missing = tmp_path / "sensitive-location.env"

    with pytest.raises(ValueError, match="does not exist") as captured:
        Settings.load(env_file=missing)

    assert str(missing) not in str(captured.value)


def test_magellan_local_paths_are_replaced_by_configuration_flags(tmp_path) -> None:
    repository = tmp_path / "private-magellan-location"
    handshake = tmp_path / "private-handshake.json"
    settings = Settings(
        magellan_repository_root=repository,
        magellan_handshake_path=handshake,
    )

    serialized = json.dumps(settings.redacted_manifest(), sort_keys=True)

    assert str(repository) not in serialized
    assert str(handshake) not in serialized
    assert settings.redacted_manifest()["magellan_repository_configured"] is True
    assert settings.redacted_manifest()["magellan_handshake_configured"] is True


def test_run_retry_budget_can_disable_durable_action_retries() -> None:
    settings = Settings(run_retry_budget=0)

    assert settings.run_retry_budget == 0


def test_interaction_lab_is_loopback_only_and_redacts_its_access_token() -> None:
    with pytest.raises(ValueError, match="loopback"):
        Settings(interaction_host="0.0.0.0")
    with pytest.raises(ValueError, match="at least 16"):
        Settings(interaction_access_token="too-short")
    settings = Settings(
        interaction_host=" ::1 ",
        interaction_access_token="private-lab-token",
    )

    serialized = json.dumps(settings.redacted_manifest(), sort_keys=True)
    assert "private-lab-token" not in serialized
    assert settings.interaction_host == "::1"
    assert settings.redacted_manifest()["interaction_access_token_configured"] is True
