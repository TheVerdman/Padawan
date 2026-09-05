import copy
from datetime import timedelta

import pytest

from padawan.pprl.container_contracts import ProcessContainerProfile
from padawan.pprl.container_driver import DockerContainerDriver, container_wire, supervisor_bytes
from tests.container_helpers import container_profile, synthetic_inspection
from tests.pprl_helpers import NOW


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("HostConfig", "NetworkMode", "host"),
        ("HostConfig", "Dns", []),
        ("HostConfig", "DnsSearch", ["inherited-host-search"]),
        ("HostConfig", "DnsOptions", []),
        ("HostConfig", "RestartPolicy", {"Name": "always", "MaximumRetryCount": 0}),
        ("HostConfig", "Init", True),
        ("HostConfig", "Privileged", True),
        ("HostConfig", "Binds", ["/host:/work"]),
        ("HostConfig", "Devices", [{"PathOnHost": "/dev/fake"}]),
        ("HostConfig", "DeviceRequests", [{"Capabilities": [["gpu"]]}]),
        ("HostConfig", "PidMode", "host"),
        ("HostConfig", "IpcMode", "host"),
        ("HostConfig", "ReadonlyRootfs", False),
        ("HostConfig", "Memory", 1_073_741_824),
        ("HostConfig", "MemorySwap", -1),
        ("HostConfig", "NanoCpus", 0),
        ("HostConfig", "CapAdd", ["CAP_SYS_ADMIN"]),
        ("HostConfig", "SecurityOpt", []),
        ("HostConfig", "LogConfig", {"Type": "json-file", "Config": {}}),
        ("Config", "Env", ["SECRET=fixture-only"]),
        ("Config", "Entrypoint", ["/bin/sh"]),
        ("Config", "Cmd", ["unadmitted command"]),
        ("Config", "User", "1000"),
    ],
)
def test_container_inspection_rejects_authority_expansion(section, key, value):
    profile = container_profile(NOW)
    name = "padawan-cpu-" + "1" * 32
    image, original = synthetic_inspection(profile, name)
    DockerContainerDriver.validate_inspection(
        original,
        profile=profile,
        source=supervisor_bytes(),
        name=name,
        image=image,
        container_id=original["Id"],
    )
    damaged = copy.deepcopy(original)
    damaged[section][key] = value
    with pytest.raises(PermissionError):
        DockerContainerDriver.validate_inspection(
            damaged,
            profile=profile,
            source=supervisor_bytes(),
            name=name,
            image=image,
            container_id=original["Id"],
        )


@pytest.mark.parametrize(
    "change",
    [
        {"socket_uri": "tcp://127.0.0.1:2375"},
        {"docker_binary": "docker"},
        {"argv": ("relative",)},
        {"filesystem_scopes": ()},
        {"network": "host"},
        {"memory_bytes": True},
        {"maximum_wall_ms": 0},
    ],
)
def test_profile_never_coerces_or_inherits_execution_authority(change):
    with pytest.raises(ValueError):
        ProcessContainerProfile.model_validate({**container_profile(NOW).model_dump(), **change})


def test_wire_is_exact_and_container_identity_stays_outside_worker_input():
    profile = container_profile(NOW)
    data = b'{"objective":"public fixture"}'
    deadline = NOW + timedelta(minutes=1)
    assert container_wire(profile, data, deadline) == container_wire(profile, data, deadline)
    assert b"engine_id" not in container_wire(profile, data, deadline)
    with pytest.raises(ValueError):
        container_wire(profile, b"x" * (profile.maximum_input_bytes + 1), deadline)
