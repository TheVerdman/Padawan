from __future__ import annotations

from padawan.artifacts.store import ArtifactBackend, LocalArtifactStore
from padawan.config.settings import Settings


def build_artifact_backend(settings: Settings) -> ArtifactBackend:
    if settings.artifact_backend == "local":
        return LocalArtifactStore(settings.artifact_root)
    if settings.gcs_bucket is None:
        raise ValueError("GCS artifact backend requires PADAWAN_GCS_BUCKET")
    try:
        from padawan.artifacts.gcs import GCSArtifactStore
    except ImportError as exc:
        raise RuntimeError("GCS support requires installing the 'gcs' optional dependency") from exc
    return GCSArtifactStore(
        bucket_name=settings.gcs_bucket,
        prefix=settings.gcs_prefix,
        project=settings.gcs_project,
        timeout_seconds=settings.gcs_timeout_seconds,
    )
