"""Native disposable broker fixture. No listener, model, container or training launch."""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from padawan.artifacts.store import ArtifactCatalog, LocalArtifactStore
from padawan.governance.amber_store import AmberStore
from padawan.models.database import Database
from padawan.pprl.abandonment import ProcessAbandonmentStore
from padawan.pprl.abandonment_contracts import ProcessAbandonmentRequest
from padawan.pprl.recovery import ProcessRecoveryStore
from padawan.pprl.recovery_contracts import ProcessRecoveryRequest
from padawan.pprl.store import ProcessStore
from padawan.pprl.tasks import ProcessTaskStore
from padawan.pprl.worker_broker import ProcessWorkerBroker
from tests.worker_helpers import disposable_client, worker_request


async def main() -> None:
    data = json.loads(sys.stdin.buffer.read(131_073))
    database = Database(data["database_url"])
    processes = ProcessStore(AmberStore())
    catalog = ArtifactCatalog(LocalArtifactStore(Path(data["artifact_root"])))
    recovery = ProcessRecoveryStore(processes, catalog)
    try:
        async with database.transaction() as session:
            if data["mode"] == "inspect_task":
                from padawan.models.tables import ProcessRolloutRow

                row = await session.get(ProcessRolloutRow, data["rollout_id"])
                if row is None:
                    raise ValueError("fixture task owner missing")
                plan = await ProcessTaskStore().check_rollout(session, row)
                if plan is None:
                    raise ValueError("fixture task owner is not planned")
                result = plan.model_dump(mode="json")
            elif data["mode"] in {"abandon", "inspect_abandonment"}:
                dispositions = ProcessAbandonmentStore(recovery)
                if data["mode"] == "abandon":
                    request = ProcessAbandonmentRequest.model_validate(
                        data["request"], strict=False
                    )
                    abandoned = await dispositions.abandon(session, request, now=datetime.now(UTC))
                else:
                    abandoned = await dispositions.read(
                        session, abandonment_id=data["abandonment_id"]
                    )
                result = abandoned.model_dump(mode="json")
            elif data["mode"] == "recover":
                request = ProcessRecoveryRequest.model_validate(data["request"], strict=False)
                receipt = await recovery.recover(session, request, now=datetime.now(UTC))
                result = receipt.model_dump(mode="json")
            else:
                receipt = await recovery.read(session, recovery_id=data["recovery_id"])
                original, _ = await processes.workers.registration(
                    session, receipt.previous_worker_id
                )
                registration, access = await processes.workers.issue(
                    session,
                    execution_digest=receipt.execution_digest,
                    role_id=original.role_id,
                    worker_model_digest=original.worker_model_digest,
                    declared_capabilities=original.declared_capabilities,
                    issued_by="reviewer-a",
                    evidence="separate native replacement fixture",
                    expires_at=datetime.now(UTC) + timedelta(minutes=20),
                    now=datetime.now(UTC),
                )
        if data["mode"] == "replace":
            broker = ProcessWorkerBroker(
                database=database, store=processes, broker_audience="fixture-broker"
            )
            # A new native client receives only its scoped credential and public reply.
            reply = await disposable_client(
                broker,
                worker_request(
                    SimpleNamespace(
                        worker_id=registration.worker_id,
                        worker_access=access,
                    )
                ),
            )
            result = {"worker_id": registration.worker_id, "reply": reply.model_dump(mode="json")}
        sys.stdout.write(json.dumps(result))
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
