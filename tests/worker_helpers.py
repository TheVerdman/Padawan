"""Disposable authenticated clients and privileged fixture composition; no models."""

import asyncio
import json
import socket
import sys
from types import SimpleNamespace

from padawan.governance.amber import AmberActionRequest, AmberAdmissionDecision
from padawan.models.hashing import canonical_json_bytes
from padawan.models.tables import AmberAdmissionDecisionRow, ProcessRolloutRow
from padawan.pprl.contracts import ProcessEventKind, ProjectBudgetUsage
from padawan.pprl.store import ClaimedProcessRollout, _rollout_from_row
from padawan.pprl.worker_broker import ProcessWorkerBroker, serve_worker_stream
from padawan.pprl.worker_protocol_contracts import (
    ProcessWorkerProposal,
    ProcessWorkerReply,
    ProcessWorkerRequest,
)
from tests.container_helpers import container_context


async def broker_context(database, tmp_path, clock, **kwargs):
    ctx = await container_context(
        database, tmp_path, clock, enrolled=True, prepare_action=False, **kwargs
    )
    ctx.broker = ProcessWorkerBroker(
        database=database, store=ctx.process, broker_audience="fixture-broker"
    )
    return ctx


def worker_request(ctx, request_id="claim-1", kind="claim", **fields):
    return ProcessWorkerRequest(
        worker_id=ctx.worker_id,
        broker_audience="fixture-broker",
        credential=ctx.worker_access.credential,
        request_id=request_id,
        kind=kind,
        **fields,
    )


def worker_proposal(ctx, **updates):
    return ProcessWorkerProposal.model_validate(
        {
            "event_kind": ProcessEventKind.TOOL_INVOKED,
            "target_class": "scientific_math",
            "tool_id": ctx.profile.tool_id,
            "tool_digest": ctx.profile.tool_digest,
            "tool_operation": ctx.profile.tool_operation,
            "incremental_usage": ProjectBudgetUsage(
                actions=1,
                artifact_bytes=ctx.profile.maximum_retention_bytes,
                wall_time_seconds=60.0,
            ),
            **updates,
        }
    )


def wire_request(request):
    return json.dumps(
        {
            **request.model_dump(mode="json"),
            "credential": request.credential.get_secret_value(),
        }
    ).encode()


_CLIENT = """
import socket, sys
sock = socket.socket(fileno=int(sys.argv[1]))
sock.settimeout(8)
data = sys.stdin.buffer.read(65537)
sock.sendall(len(data).to_bytes(4, 'big') + data)
def exact(size):
    chunks = bytearray()
    while len(chunks) < size:
        part = sock.recv(size-len(chunks))
        if not part:
            raise RuntimeError('incomplete reply')
        chunks.extend(part)
    return bytes(chunks)
size = int.from_bytes(exact(4), 'big')
if not 0 < size <= 1200000:
    raise RuntimeError('invalid reply size')
sys.stdout.buffer.write(exact(size))
sock.close()
"""


async def disposable_client(broker, request):
    parent, child = socket.socketpair()
    process = None
    serving = None
    try:
        reader, writer = await asyncio.open_connection(sock=parent)
        serving = asyncio.create_task(serve_worker_stream(reader, writer, broker))
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-S",
            "-c",
            _CLIENT,
            str(child.fileno()),
            pass_fds=(child.fileno(),),
            env={"PATH": "/usr/bin:/bin"},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        child.close()
        async with asyncio.timeout(12):
            stdout, stderr = await process.communicate(wire_request(request))
            await serving
        assert process.returncode == 0 and not stderr
        return ProcessWorkerReply.model_validate_json(stdout)
    finally:
        parent.close()
        child.close()
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        if serving is not None and not serving.done():
            serving.cancel()
            await asyncio.gather(serving, return_exceptions=True)


async def prepare_broker_action(ctx, *, native=False, proposal=None):
    request = worker_request(ctx)
    call = (lambda r: disposable_client(ctx.broker, r)) if native else ctx.broker.request
    observed = await call(request)
    proposed = worker_request(
        ctx,
        "proposal-1",
        "propose",
        assignment_id=observed.assignment_id,
        observation_request_id=request.request_id,
        proposal=proposal or worker_proposal(ctx),
    )
    reply = await call(proposed)
    async with ctx.database.transaction() as session:
        receipt, _ = await ctx.broker.inspect_request(
            session, worker_id=ctx.worker_id, request_id=proposed.request_id
        )
        assignment = await ctx.process.workers.assignment(session, receipt.assignment_id)
        row = await session.get(ProcessRolloutRow, assignment.rollout_id)
        state = await ctx.process.get_state(session, state_id=assignment.state_id)
        decision = await session.get(AmberAdmissionDecisionRow, receipt.decision_id)
        ctx.claim = ClaimedProcessRollout(
            _rollout_from_row(row), state, row.lease_token, assignment.assignment_id
        )
        ctx.action = AmberActionRequest.model_validate(decision.request_json, strict=False)
        ctx.decision = AmberAdmissionDecision.model_validate(decision.record_json, strict=False)
        ctx.worker_access = ctx.worker_access.assigned(assignment.assignment_id)
        ctx.observed = SimpleNamespace(
            observation_id=receipt.observation_id,
            observation=observed.observation,
            observation_bytes=canonical_json_bytes(observed.observation),
        )
        ctx.kwargs = dict(
            decision_id=ctx.decision.decision_id,
            rollout_id=row.rollout_id,
            lease_token=row.lease_token,
            worker_id=ctx.worker_id,
            worker_access=ctx.worker_access,
        )
    return request, proposed, observed, reply
