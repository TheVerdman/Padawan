"""Stdlib-only, trusted scripted worker. No repository/store imports or file access.

Launched with -I -S in an empty scratch directory. This is not an OS sandbox.
Only its own inherited sockets, scoped credential and public replies are inputs.
"""

import json
import socket
import sys

MAXIMUM_REPLY = 1_200_000


def line():
    data = sys.stdin.buffer.readline(65_537)
    if not data:
        return None
    if len(data) > 65_536 or not data.endswith(b"\n"):
        raise ValueError("bounded control frame required")
    return json.loads(data)


def exchange(descriptor, request):
    with socket.socket(fileno=descriptor) as channel:
        channel.settimeout(8)
        data = json.dumps(request).encode()
        if len(data) > 65_536:
            raise ValueError("request too large")
        channel.sendall(len(data).to_bytes(4, "big") + data)

        def exact(size):
            result = bytearray()
            while len(result) < size:
                part = channel.recv(size - len(result))
                if not part:
                    raise ValueError("incomplete reply")
                result.extend(part)
            return bytes(result)

        size = int.from_bytes(exact(4), "big")
        if not 0 < size <= MAXIMUM_REPLY:
            raise ValueError("reply too large")
        raw = exact(size).decode()
        return raw, json.loads(raw)


def main():
    descriptors = [int(value) for value in sys.argv[1:]]
    identity = line()  # plaintext credential is deliberately never echoed
    if identity is None:
        return
    observed = None
    observation_request_id = None
    for index, descriptor in enumerate(descriptors):
        command = line()
        if command is None:
            return
        kind = command["kind"]
        request_id = f"scripted-{index}"
        request = {**identity, "request_id": request_id, "kind": kind}
        if kind == "propose":
            if observed is None:
                raise ValueError("proposal needs a local delivered observation")
            supported = any(
                item["status"] == "supported"
                for item in observed["observation"]["state"]["hypotheses"]
            )
            request.update(
                assignment_id=observed["assignment_id"],
                observation_request_id=observation_request_id,
                proposal={
                    "event_kind": "project_completed" if supported else "hypothesis_updated",
                    "target_class": "scientific_math",
                    "incremental_usage": {"actions": 1, "wall_time_seconds": 0.01},
                },
            )
            if command.get("premature_completion"):
                request["proposal"]["event_kind"] = "project_completed"
            if "synthetic_tool" in command:
                # Explicit fixture stop probe, distinct from the mathematical action menu.
                tool = command["synthetic_tool"]
                request["proposal"].update(
                    event_kind="tool_invoked",
                    tool_id="calculator",
                    tool_digest=tool["digest"],
                    tool_operation="evaluate",
                    incremental_usage={
                        "actions": 1,
                        "wall_time_seconds": 60.0,
                        "artifact_bytes": tool["maximum_retention_bytes"],
                    },
                )
        elif kind != "claim":
            raise ValueError("unknown scripted command")
        raw, reply = exchange(descriptor, request)
        if kind == "claim" and "observation" in reply:
            observed, observation_request_id = reply, request_id
        print(json.dumps({"local_index": index, "reply_bytes": raw}), flush=True)


if __name__ == "__main__":
    main()
