"""Compatibility entry point for the finite Vertex controller."""

from pathlib import Path

from padawan.atlas.vertex_control import (
    API as API,
)
from padawan.atlas.vertex_control import (
    GUARD_CLOUD_PROOF_MAX_AGE_SECONDS as GUARD_CLOUD_PROOF_MAX_AGE_SECONDS,
)
from padawan.atlas.vertex_control import (
    GUARD_HEARTBEAT_MAX_AGE_SECONDS as GUARD_HEARTBEAT_MAX_AGE_SECONDS,
)
from padawan.atlas.vertex_control import (
    GUARD_RECOVERY_SECONDS as GUARD_RECOVERY_SECONDS,
)
from padawan.atlas.vertex_control import (
    Control as Control,
)
from padawan.atlas.vertex_control import (
    ControlRequestError as ControlRequestError,
)
from padawan.atlas.vertex_control import (
    GuardAdmission as GuardAdmission,
)
from padawan.atlas.vertex_control import (
    GuardHealthError as GuardHealthError,
)
from padawan.atlas.vertex_control import (
    alive as alive,
)
from padawan.atlas.vertex_control import (
    atomic_save as atomic_save,
)
from padawan.atlas.vertex_control import (
    check_guard as check_guard,
)
from padawan.atlas.vertex_control import (
    digest as digest,
)
from padawan.atlas.vertex_control import (
    load as load,
)
from padawan.atlas.vertex_control import (
    locked as locked,
)
from padawan.atlas.vertex_control import (
    main as controller_main,
)
from padawan.atlas.vertex_control import (
    now as now,
)
from padawan.atlas.vertex_control import (
    retain_guard_refusal as retain_guard_refusal,
)
from padawan.atlas.vertex_control import (
    transient_cloud_error as transient_cloud_error,
)
from padawan.atlas.vertex_control import (
    validate_budget as validate_budget,
)


def main() -> None:
    controller_main(entrypoint=Path(__file__).resolve())


if __name__ == "__main__":
    main()
