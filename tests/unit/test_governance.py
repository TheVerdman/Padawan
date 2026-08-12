from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from padawan.artifacts.store import LocalArtifactStore
from padawan.config.settings import Settings
from padawan.governance.policy import AccessContext, ExportPolicy, RetentionPolicy
from padawan.models.contracts import (
    DistributionScope,
    RightsBasis,
    RightsReviewStatus,
    RightsUse,
    SourceRights,
    legacy_source_rights,
)


def test_export_policy_denies_restricted_and_private_by_default(tmp_path) -> None:
    reference = LocalArtifactStore(tmp_path).put_text("trace", restricted=True, raw_data=True)
    context = AccessContext("researcher", frozenset(), "audit")
    assert not ExportPolicy().decide(reference, context=context).allowed
    assert (
        not ExportPolicy(allow_restricted=True)
        .decide(reference, context=context, contains_private_reasoning=True)
        .allowed
    )


def test_export_policy_requires_role_and_validates_paths(tmp_path) -> None:
    reference = LocalArtifactStore(tmp_path).put_text("trace", restricted=True)
    policy = ExportPolicy(allow_restricted=True)
    context = AccessContext("auditor", frozenset({"restricted-artifact-export"}), "audit")
    assert policy.decide(reference, context=context).allowed
    assert policy.safe_relative_name("padawan/traces/value.json") == ("padawan/traces/value.json")
    with pytest.raises(ValueError):
        policy.safe_relative_name("../secret")


def test_retention_cannot_delete_referenced_or_normalized_data() -> None:
    policy = RetentionPolicy(raw_data_age=timedelta(days=30))
    assert policy.delete_only_when_unreferenced
    with pytest.raises(ValueError):
        RetentionPolicy(raw_data_age=timedelta(days=1), delete_only_when_unreferenced=False)
    with pytest.raises(ValueError):
        RetentionPolicy(raw_data_age=timedelta(days=1), normalized_records_age=timedelta(days=365))


def test_command_configuration_manifest_redacts_all_credentials() -> None:
    settings = Settings.model_validate(
        {
            "database_url": "postgresql://padawan:database-secret@localhost/padawan",
            "OPENAI_API_KEY": "openai-secret",
            "ANTHROPIC_API_KEY": "anthropic-secret",
            "INKLING_API_KEY": "inkling-secret",
            "compatible_api_key": "compatible-secret",
        }
    )

    serialized = str(settings.redacted_manifest())

    assert "database-secret" not in serialized
    assert "openai-secret" not in serialized
    assert "anthropic-secret" not in serialized
    assert "inkling-secret" not in serialized
    assert "compatible-secret" not in serialized
    assert settings.redacted_manifest()["openai_api_key_configured"] is True
    assert settings.redacted_manifest()["anthropic_api_key_configured"] is True
    assert settings.redacted_manifest()["inkling_api_key_configured"] is True
    assert settings.redacted_manifest()["compatible_api_key_configured"] is True


def test_rights_prohibition_and_legacy_import_are_conservative() -> None:
    reviewed_at = datetime(2026, 8, 1, tzinfo=UTC)
    prohibited = SourceRights(
        rights_id="policy.prohibited",
        version="1",
        basis=RightsBasis.UNKNOWN,
        basis_detail="The source was reviewed and rejected for downstream use.",
        permitted_uses=(),
        distribution_scope=DistributionScope.INTERNAL_ONLY,
        review_status=RightsReviewStatus.PROHIBITED,
        restrictions=("do not use",),
        reviewed_by="test-reviewer",
        reviewed_at=reviewed_at,
    )
    legacy = legacy_source_rights(
        source="third-party:legacy",
        license_id="Apache-2.0",
        reviewed_at=reviewed_at,
    )

    assert not prohibited.permits(RightsUse.EVIDENCE_RETENTION)
    assert legacy.basis == RightsBasis.UNKNOWN
    assert legacy.license_id == "Apache-2.0"
    assert legacy.review_status == RightsReviewStatus.REVIEW_REQUIRED
