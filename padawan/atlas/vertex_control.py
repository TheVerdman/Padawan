"""Own one finite Vertex deployment with expiry-aware authentication and an independent guard.

All resource names, budgets and deadlines come from a frozen, explicitly authorized plan.
This controls only the experiment's exact endpoint/model. It does not launch model trials.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from padawan.adapters.openai_compatible.gcp_credentials import (
    CredentialRefreshError,
    GcloudCredentialSource,
)

GUARD_HEARTBEAT_MAX_AGE_SECONDS = 60
GUARD_CLOUD_PROOF_MAX_AGE_SECONDS = 180
GUARD_RECOVERY_SECONDS = 180


class ControlRequestError(RuntimeError):
    """A bounded HTTP status, without response bodies or credentials."""

    def __init__(self, method: str, status_code: int) -> None:
        super().__init__(f"GCP control request {method} returned HTTP {status_code}")
        self.status_code = status_code


def transient_cloud_error(error: BaseException) -> bool:
    return isinstance(error, (httpx.TransportError, CredentialRefreshError)) or (
        isinstance(error, ControlRequestError)
        and (error.status_code == 429 or 500 <= error.status_code < 600)
    )


def now() -> datetime:
    return datetime.now(UTC)


def digest(path: str | Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path: str | Path) -> Any:
    return json.loads(Path(path).read_text())


def atomic_save(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(path: str | Path, *, blocking: bool = True) -> Iterator[None]:
    with Path(path).open("a") as stream:
        os.chmod(path, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def alive(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def validate_budget(config: dict[str, Any]) -> dict[str, Any]:
    """Use the larger retained compute estimate; never subtract unverified billing credits."""
    prior_runs = config.get("prior_runs") or [
        {
            "directory": config["prior_run_directory"],
            "cloud_state_sha256": config["prior_cloud_state_sha256"],
        }
    ]
    prior_hours = 0.0
    retained_prior_usd = 0.0
    seen = set()
    for retained in prior_runs:
        prior_path = Path(retained["directory"]).resolve() / "cloud-state.json"
        if prior_path in seen or digest(prior_path) != retained["cloud_state_sha256"]:
            raise ValueError("prior spending evidence is duplicated or changed after freezing")
        seen.add(prior_path)
        prior = load(prior_path)
        if not prior.get("cleanup_verified_at"):
            raise ValueError("prior paid resources have not been verified stopped")
        prior_hours += (
            datetime.fromisoformat(prior["cleanup_verified_at"])
            - datetime.fromisoformat(prior["deployment_started_at"])
        ).total_seconds() / 3600
        retained_prior_usd += prior["conservative_compute_usd"]
    prior_usd = max(config["prior_compute_usd"], retained_prior_usd)
    ceiling = config["approved_spending_limit_usd"]
    hours = config["maximum_deployment_hours"]
    compute = hours * config["published_fleet_hourly_usd"]
    reserve = config["noncompute_and_cleanup_reserve_usd"]
    if not 0 < ceiling <= 125 or reserve < 20 or prior_usd + compute + reserve > ceiling:
        raise ValueError("deployment would exceed the user's cumulative $125 limit or reserve")
    if prior_hours + hours > 5:
        raise ValueError("cumulative conservative deployment time would exceed five hours")
    dispatch = config["stop_dispatch_after_hours"] * 3600
    teardown = config["begin_teardown_after_hours"] * 3600
    capture = config["capture_after_hours"] * 3600
    if not (
        dispatch + config["request_timeout_seconds"] + 60 <= teardown
        and dispatch + config["request_timeout_seconds"] + config["judge_deadline_seconds"]
        < capture
        and teardown < capture < hours * 3600
    ):
        raise ValueError("dispatch must leave a full request, judging and cleanup allowance")
    if config["replicas"] != 4 or config["accelerators_per_replica"] != 1:
        raise ValueError("this authorization is bounded to the four declared serving GPUs")
    return {
        "prior_compute_usd": prior_usd,
        "second_compute_ceiling_usd": compute,
        "reserved_other_usd": reserve,
        "planned_cumulative_upper_allocation_usd": prior_usd + compute + reserve,
        "approved_total_usd": ceiling,
        "cumulative_deployment_hours": prior_hours + hours,
        "invoice_reconciled": False,
        "prior_deployments_retained": len(seen),
        "prior_compute_semantics": (
            "conservative allocation hold including provisioning; not confirmed charges"
        ),
    }


class API:
    def __init__(self, gcloud: str) -> None:
        self.credentials = GcloudCredentialSource(gcloud)
        self.client = httpx.Client(timeout=45, follow_redirects=False)

    def request(self, method: str, url: str, body: Any = None, *, allow404: bool = False) -> Any:
        credential = self.credentials.get(120)
        response = self.client.request(
            method,
            url,
            json=body,
            headers={"Authorization": "Bearer " + credential.access_token},
        )
        # Only control-plane requests are retried after an explicit authentication rejection.
        # Inference uses a different adapter with exactly one attempt and no automatic retries.
        if response.status_code == 401:
            credential = self.credentials.get(120, force=True)
            response = self.client.request(
                method,
                url,
                json=body,
                headers={"Authorization": "Bearer " + credential.access_token},
            )
        if response.status_code == 404 and allow404:
            return None
        if response.is_error:
            raise ControlRequestError(method, response.status_code)
        return response.json() if response.content else {}

    def close(self) -> None:
        self.client.close()


class Control:
    def __init__(
        self,
        config_path: str | Path,
        root: str | Path,
        gcloud: str,
        *,
        entrypoint: Path | None = None,
    ) -> None:
        self.entrypoint = (
            entrypoint or Path(__file__).resolve().parents[2] / "scripts/control_atlas_vertex.py"
        ).resolve()
        self.config_path = Path(config_path).resolve()
        self.config = load(self.config_path)
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.plan = load(self.root / "vertex-launch/launch-plan.json")
        self.config_digest = digest(self.config_path)
        self.base = f"https://{self.config['region']}-aiplatform.googleapis.com/v1/"
        self.api = API(gcloud)
        self.gcloud = gcloud
        self.budget = validate_budget(self.config)

    def state(self) -> dict[str, Any]:
        path = self.root / "cloud-state.json"
        value = load(path) if path.exists() else {}
        if value and value.get("config_sha256") != self.config_digest:
            raise ValueError("cloud control state belongs to a different frozen configuration")
        if value.get("deployment_started_at"):
            start = datetime.fromisoformat(value["deployment_started_at"])
            for field, configured in (
                ("dispatch_deadline", "stop_dispatch_after_hours"),
                ("capture_deadline", "capture_after_hours"),
                ("teardown_at", "begin_teardown_after_hours"),
                ("cleanup_deadline", "maximum_deployment_hours"),
            ):
                expected = start + timedelta(hours=self.config[configured])
                if datetime.fromisoformat(value[field]) != expected:
                    raise ValueError("deployment deadline differs from the frozen budget")
        return value

    def update(self, **changes: Any) -> dict[str, Any]:
        with locked(self.root / ".cloud-state.lock"):
            value = self.state()
            value.update(changes)
            value["config_sha256"] = self.config_digest
            atomic_save(self.root / "cloud-state.json", value)
            return value

    def log(self, event: str, **details: Any) -> None:
        row = {"at": now().isoformat(), "event": event, **details}
        encoded = json.dumps(row) + "\n"
        with (
            locked(self.root / ".cloud-events.lock"),
            (self.root / "cloud-events.jsonl").open("a") as stream,
        ):
            os.chmod(stream.name, 0o600)
            stream.write(encoded)
        print(encoded.rstrip(), flush=True)

    def operation(self, name: str, *, timeout: float = 600) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result: dict[str, Any] = self.api.request("GET", self.base + name)
            if result.get("done"):
                if result.get("error"):
                    raise RuntimeError(
                        "GCP operation completed with an error; inspect retained operation"
                    )
                return result
            time.sleep(5)
        raise TimeoutError("original GCP operation remains pending")

    def owned(self, resource: str) -> Any:
        value = self.api.request("GET", self.base + self.plan[resource], allow404=True)
        if value is not None:
            expected = self.config["experiment_id"]
            if value.get("labels", {}).get("padawan-experiment") != expected:
                raise ValueError("resource ownership label differs from the exact experiment")
            if resource == "endpoint_resource":
                for model in value.get("deployedModels", []):
                    if model.get("model", "").split("@")[0] != self.plan["model_resource"]:
                        raise ValueError("owned endpoint contains an unexpected model")
        return value

    def inventory(self) -> None:
        parent = self.plan["endpoint_resource"].rsplit("/endpoints/", 1)[0]
        endpoints = self.api.request("GET", self.base + parent + "/endpoints?pageSize=100")
        if endpoints.get("nextPageToken") or any(
            e.get("deployedModels") for e in endpoints.get("endpoints", [])
        ):
            raise ValueError("serving inventory is active or incomplete; inspect before deployment")
        for key in ("model_resource", "endpoint_resource"):
            if self.api.request("GET", self.base + self.plan[key], allow404=True) is not None:
                raise ValueError("planned resource already exists; do not duplicate or adopt it")
        quota = self.api.request("GET", self.config["serving_quota_url"])
        limits = [
            int(bucket["effectiveLimit"])
            for limit in quota["consumerQuotaLimits"]
            for bucket in limit["quotaBuckets"]
            if bucket.get("dimensions", {}).get("region") == self.config["region"]
        ]
        if not limits or min(limits) < self.config["replicas"]:
            raise ValueError("current serving quota is below the planned GPU count")
        atomic_save(
            self.root / "fresh-inventory.json",
            {
                "at": now().isoformat(),
                "endpoints": endpoints,
                "quota": quota,
                "budget": self.budget,
            },
        )
        self.log("inventory_passed", serving_limit=min(limits), budget=self.budget)

    def create(self) -> None:
        if self.state():
            raise ValueError("creation has already been attempted; preserve the original effects")
        self.inventory()
        self.update(
            created_at=now().isoformat(), authorization_ref=self.config["authorization_ref"]
        )
        for index, name in ((0, "model_upload"), (1, "endpoint_create")):
            spec = self.plan["requests"][index]
            self.update(**{name + "_intent_at": now().isoformat()})
            response = self.api.request(
                spec["method"],
                spec["url"],
                load(self.root / "vertex-launch" / spec["body_file"]),
            )
            self.update(**{name + "_response": response})
            result = (
                self.operation(response["name"])
                if "/operations/" in response.get("name", "")
                else response
            )
            self.update(**{name + "_result": result})
            self.log(name + "_complete")

    def deploy(self) -> None:
        current = self.state()
        if (
            "deployment_started_at" in current
            or "endpoint_create_result" not in current
            or "model_upload_result" not in current
        ):
            raise ValueError("requires a new owned endpoint and no previous deployment attempt")
        evidence = load(self.root / "ready-to-deploy.json")
        # Pin this implementation, while the guard retains its original script entry point.
        # A historical receipt for the old script implementation cannot authorize this code.
        if (
            evidence.get("passed") is not True
            or evidence.get("config_sha256") != self.config_digest
            or evidence.get("controller_sha256") != digest(__file__)
            or evidence.get("operational_validation_sha256")
            != digest(self.root / "operational-validation.json")
        ):
            raise ValueError(
                "deployment requires passing validation bound to this exact configuration"
            )
        start = now()
        self.update(
            deployment_started_at=start.isoformat(),
            dispatch_deadline=(
                start + timedelta(hours=self.config["stop_dispatch_after_hours"])
            ).isoformat(),
            capture_deadline=(
                start + timedelta(hours=self.config["capture_after_hours"])
            ).isoformat(),
            teardown_at=(
                start + timedelta(hours=self.config["begin_teardown_after_hours"])
            ).isoformat(),
            cleanup_deadline=(
                start + timedelta(hours=self.config["maximum_deployment_hours"])
            ).isoformat(),
        )
        with (self.root / "guard.log").open("a") as stream:
            os.chmod(stream.name, 0o600)
            guard = subprocess.Popen(
                [
                    "/usr/bin/caffeinate",
                    "-i",
                    "-s",
                    sys.executable,
                    str(self.entrypoint),
                    "--config",
                    str(self.config_path),
                    "--root",
                    str(self.root),
                    "--gcloud",
                    self.gcloud,
                    "guard",
                ],
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self.update(guard_pid=guard.pid)
        ready_by = time.monotonic() + GUARD_RECOVERY_SECONDS
        while time.monotonic() < ready_by:
            try:
                check_guard(self.root, self.config_digest)
                break
            except (ValueError, FileNotFoundError):
                if guard.poll() is not None:
                    raise RuntimeError("independent guard exited before GPU deployment") from None
                time.sleep(0.25)
        else:
            raise RuntimeError(
                "independent guard did not validate its cloud access before deployment"
            )
        self.update(guard_verified_at=now().isoformat())
        spec = self.plan["requests"][2]
        self.update(deployment_effect_intent_at=now().isoformat())
        response = self.api.request(
            "POST", spec["url"], load(self.root / "vertex-launch" / spec["body_file"])
        )
        self.update(deployment_response=response)
        self.log("deployment_submitted", operation=response.get("name"), budget=self.budget)

    def status(self) -> None:
        current = self.state()
        operation = current.get("deployment_response", {}).get("name")
        result = {
            "at": now().isoformat(),
            "state": current,
            "endpoint": self.owned("endpoint_resource"),
            "operation": self.api.request("GET", self.base + operation) if operation else None,
        }
        if current.get("deployment_started_at"):
            ended = datetime.fromisoformat(current.get("cleanup_verified_at", now().isoformat()))
            hours = (
                ended - datetime.fromisoformat(current["deployment_started_at"])
            ).total_seconds() / 3600
            result["conservative_second_compute_usd"] = (
                hours * self.config["published_fleet_hourly_usd"]
            )
        atomic_save(self.root / "latest-status.json", result)
        self.log(
            "status",
            operation_done=(result["operation"] or {}).get("done", False),
            operation_error=(result["operation"] or {}).get("error"),
            deployed_models=len((result["endpoint"] or {}).get("deployedModels", [])),
            conservative_second_compute_usd=result.get("conservative_second_compute_usd"),
        )

    def cleanup(self) -> bool:
        try:
            with locked(self.root / ".cleanup.lock", blocking=False):
                return self._cleanup()
        except BlockingIOError:
            self.log("cleanup_already_owned_by_another_controller")
            return False

    def _cleanup(self) -> bool:
        if self.state().get("cleanup_verified_at"):
            return True
        self.update(stop_requested_at=now().isoformat(), cleanup_started_at=now().isoformat())
        self.log("cleanup_started")
        original = self.state().get("deployment_response", {}).get("name")
        if original:
            value = self.api.request("GET", self.base + original)
            if not value.get("done"):
                try:
                    self.api.request("POST", self.base + original + ":cancel", {})
                    self.log("deployment_cancel_requested")
                except Exception as error:
                    self.log("deployment_cancel_unavailable", error_type=type(error).__name__)
        endpoint = self.owned("endpoint_resource")
        if endpoint is not None:
            for model in endpoint.get("deployedModels", []):
                key = "undeploy_" + model["id"]
                receipt = self.state().get(key)
                if receipt is None:
                    receipt = self.api.request(
                        "POST",
                        self.plan["cleanup"]["undeploy_url"],
                        {"deployedModelId": model["id"], "trafficSplit": {}},
                    )
                    self.update(**{key: receipt})
                    self.log("undeploy_submitted", operation=receipt.get("name"))
                self.operation(receipt["name"])
            endpoint = self.owned("endpoint_resource")
            if endpoint is not None and not endpoint.get("deployedModels"):
                receipt = self.api.request(
                    "DELETE", self.base + self.plan["endpoint_resource"], allow404=True
                )
                if receipt and receipt.get("name"):
                    self.operation(receipt["name"])
        if self.owned("endpoint_resource") is not None:
            raise RuntimeError("owned endpoint remains; cleanup will be retried")
        if self.owned("model_resource") is not None:
            receipt = self.api.request(
                "DELETE", self.base + self.plan["model_resource"], allow404=True
            )
            if receipt and receipt.get("name"):
                self.operation(receipt["name"])
        if self.owned("model_resource") is not None:
            raise RuntimeError("owned model record remains; cleanup will be retried")
        current = self.state()
        ended = now()
        hours = (
            (ended - datetime.fromisoformat(current["deployment_started_at"])).total_seconds()
            / 3600
            if current.get("deployment_started_at")
            else 0
        )
        cost = hours * self.config["published_fleet_hourly_usd"]
        self.update(
            cleanup_verified_at=ended.isoformat(),
            conservative_compute_usd=cost,
            cumulative_conservative_compute_usd=self.budget["prior_compute_usd"] + cost,
        )
        self.log(
            "cleanup_verified",
            second_compute_usd=cost,
            cumulative_compute_usd=self.budget["prior_compute_usd"] + cost,
        )
        return True

    @contextmanager
    def guard_heartbeat(self) -> Iterator[Callable[[str | None, bool], None]]:
        """Keep process liveness separate from the age of the last successful cloud probe."""
        finished = threading.Event()
        lock = threading.Lock()
        health: dict[str, Any] = {"last_cloud_probe_at": None, "healthy": False}

        def write() -> None:
            with lock:
                atomic_save(
                    self.root / "guard-heartbeat.json",
                    {
                        "at": now().isoformat(),
                        "pid": os.getpid(),
                        "config_sha256": self.config_digest,
                        **health,
                    },
                )

        def update(last_probe_at: str | None, healthy: bool) -> None:
            with lock:
                health.update(last_cloud_probe_at=last_probe_at, healthy=healthy)
            write()

        def pulse() -> None:
            while not finished.wait(5):
                try:
                    write()
                except Exception as error:
                    self.log("guard_heartbeat_write_failed", error_type=type(error).__name__)

        write()
        publisher = threading.Thread(target=pulse, name="atlas-guard-heartbeat", daemon=True)
        publisher.start()
        try:
            yield update
        finally:
            finished.set()
            publisher.join(timeout=10)

    def guard(self) -> None:
        with self.guard_heartbeat() as publish_heartbeat:
            self._guard_loop(publish_heartbeat)

    def _guard_loop(self, publish_heartbeat: Callable[[str | None, bool], None]) -> None:
        self.log("independent_guard_started", pid=os.getpid())
        last_probe = 0.0
        last_success = None
        failures = 0
        transient_since = None
        while True:
            try:
                current = self.state()
                if current.get("cleanup_verified_at"):
                    self.log("independent_guard_complete")
                    return
                if time.monotonic() - last_probe >= 30:
                    last_probe = time.monotonic()
                    self.owned("endpoint_resource")
                    self.owned("model_resource")
                    operation = current.get("deployment_response", {}).get("name")
                    if operation and not current.get("deployment_ready_at"):
                        value = self.api.request("GET", self.base + operation)
                        if value.get("done") and not value.get("error"):
                            self.update(
                                deployment_ready_at=now().isoformat(),
                                deployment_terminal_operation=value,
                            )
                        elif value.get("error"):
                            self.update(
                                stop_requested_at=now().isoformat(),
                                cleanup_requested=True,
                                deployment_terminal_operation=value,
                            )
                    last_success = now().isoformat()
                    failures = 0
                    transient_since = None
                current = self.state()
                publish_heartbeat(last_success, failures == 0 and last_success is not None)
                start = datetime.fromisoformat(current["deployment_started_at"])
                due = now() >= datetime.fromisoformat(current["teardown_at"])
                due |= now() >= start + timedelta(
                    minutes=self.config["maximum_startup_minutes"]
                ) and not current.get("deployment_ready_at")
                if current.get("controller_pid") and not current.get("controller_finished_at"):
                    heartbeat = self.root / "runner-heartbeat.json"
                    missing = not heartbeat.exists()
                    stale = (
                        missing
                        or (now() - datetime.fromisoformat(load(heartbeat)["at"])).total_seconds()
                        > 90
                    )
                    if not alive(current["controller_pid"]) or stale:
                        self.update(
                            stop_requested_at=now().isoformat(),
                            cleanup_requested=True,
                            controller_lost_at=now().isoformat(),
                        )
                        due = True
                elif current.get("deployment_ready_at") and not current.get("controller_pid"):
                    due |= (
                        now() - datetime.fromisoformat(current["deployment_ready_at"])
                    ).total_seconds() > 600
                if (due or current.get("cleanup_requested")) and self.cleanup():
                    return
            except Exception as error:
                failures += 1
                recoverable = transient_cloud_error(error)
                if transient_since is None:
                    transient_since = time.monotonic()
                self.log(
                    "guard_error_retained_for_retry",
                    error_type=type(error).__name__,
                    status_code=getattr(error, "status_code", None),
                    failures=failures,
                    recoverable=recoverable,
                )
                publish_heartbeat(last_success, False)
                # Authentication/network failures never terminate the only cleanup process.
                try:
                    if not recoverable or (
                        time.monotonic() - transient_since >= GUARD_RECOVERY_SECONDS
                    ):
                        current = self.state()
                        self.update(
                            stop_requested_at=current.get("stop_requested_at") or now().isoformat(),
                            cleanup_requested=True,
                            guard_terminal_error={
                                "error_type": type(error).__name__,
                                "status_code": getattr(error, "status_code", None),
                                "recoverable": recoverable,
                                "failures": failures,
                                "recovery_seconds": GUARD_RECOVERY_SECONDS,
                            },
                        )
                    due = self.state().get("cleanup_requested") or now() >= datetime.fromisoformat(
                        self.state()["teardown_at"]
                    )
                    if due and self.cleanup():
                        return
                except Exception as cleanup_error:
                    self.log("guard_cleanup_retry_pending", error_type=type(cleanup_error).__name__)
            time.sleep(5)


class GuardHealthError(ValueError):
    """An admission refusal with bounded control evidence and no credential or model text."""

    def __init__(self, evidence: dict[str, Any]) -> None:
        super().__init__("independent guard refused admission: " + ", ".join(evidence["reasons"]))
        self.evidence = evidence

    @property
    def recoverable(self) -> bool:
        return bool(self.evidence["reasons"]) and set(self.evidence["reasons"]) <= {
            "guard_unhealthy",
            "heartbeat_stale",
            "cloud_access_stale",
            "cloud_access_unavailable",
        }


def check_guard(root: str | Path, config_digest: str) -> None:
    root = Path(root)
    try:
        state = load(root / "cloud-state.json")
        heartbeat = load(root / "guard-heartbeat.json")
        observed = now()
        evidence: dict[str, Any] = {
            "checked_at": observed.isoformat(),
            "control_stopped": bool(
                state.get("cleanup_verified_at") or state.get("stop_requested_at")
            ),
            "state_config_matches": state.get("config_sha256") == config_digest,
            "heartbeat_config_matches": heartbeat.get("config_sha256") == config_digest,
            "guard_alive": alive(state.get("guard_pid")),
            "guard_pid_matches": state.get("guard_pid") == heartbeat.get("pid"),
            "heartbeat_healthy": heartbeat.get("healthy") is True,
            "heartbeat_age_seconds": (
                observed - datetime.fromisoformat(heartbeat["at"])
            ).total_seconds(),
            "cloud_probe_age_seconds": (
                observed - datetime.fromisoformat(heartbeat["last_cloud_probe_at"])
            ).total_seconds()
            if heartbeat.get("last_cloud_probe_at") is not None
            else None,
            "heartbeat_max_age_seconds": GUARD_HEARTBEAT_MAX_AGE_SECONDS,
            "cloud_probe_max_age_seconds": GUARD_CLOUD_PROOF_MAX_AGE_SECONDS,
            "guard_terminal_error": state.get("guard_terminal_error"),
        }
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise GuardHealthError(
            {"reasons": ["guard_evidence_unreadable"], "error_type": type(error).__name__}
        ) from error
    reasons = []
    for field, reason in (
        ("state_config_matches", "state_config_mismatch"),
        ("heartbeat_config_matches", "heartbeat_config_mismatch"),
        ("guard_alive", "guard_process_unavailable"),
        ("guard_pid_matches", "guard_pid_mismatch"),
        ("heartbeat_healthy", "guard_unhealthy"),
    ):
        if not evidence[field]:
            reasons.append(reason)
    if evidence["control_stopped"]:
        reasons.append("control_stopped_admission")
    if evidence["heartbeat_age_seconds"] < -5 or (
        evidence["cloud_probe_age_seconds"] is not None and evidence["cloud_probe_age_seconds"] < -5
    ):
        reasons.append("guard_clock_invalid")
    if evidence["heartbeat_age_seconds"] > GUARD_HEARTBEAT_MAX_AGE_SECONDS:
        reasons.append("heartbeat_stale")
    if evidence["cloud_probe_age_seconds"] is None:
        reasons.append("cloud_access_unavailable")
    elif evidence["cloud_probe_age_seconds"] > GUARD_CLOUD_PROOF_MAX_AGE_SECONDS:
        reasons.append("cloud_access_stale")
    if reasons:
        raise GuardHealthError({**evidence, "reasons": reasons})


def retain_guard_refusal(control: Control, error: BaseException) -> dict[str, Any]:
    """Persist the first terminal stop; a transient pause does not enter this path."""
    state = control.state()
    receipt: dict[str, Any] | None = state.get("admission_stop")
    path = control.root / "admission-stop.json"
    if receipt is not None:
        if not path.exists():
            atomic_save(path, receipt)
        return receipt
    receipt = {
        "at": now().isoformat(),
        "reason": "independent_guard_refusal",
        "error_type": type(error).__name__,
        "guard_evidence": getattr(error, "evidence", None),
        "admitted_work_will_drain": True,
        "retry_authorized": False,
    }
    control.update(
        stop_requested_at=state.get("stop_requested_at") or receipt["at"],
        admission_stop=receipt,
    )
    atomic_save(path, receipt)
    control.log("dispatch_guard_refusal", **receipt)
    return receipt


class GuardAdmission:
    """Pause new effects on transient health loss; resume only untouched work after recovery."""

    def __init__(self, control: Control) -> None:
        self.control = control
        pause = control.state().get("admission_pause")
        self.paused_since = (
            time.monotonic() - max(0, (now() - datetime.fromisoformat(pause["at"])).total_seconds())
            if pause
            else None
        )

    def check(self) -> bool:
        try:
            check_guard(self.control.root, self.control.config_digest)
        except GuardHealthError as error:
            if error.recoverable:
                if self.paused_since is None:
                    self.paused_since = time.monotonic()
                    pause = {"at": now().isoformat(), "guard_evidence": error.evidence}
                    self.control.update(admission_pause=pause)
                    self.control.log("dispatch_guard_paused", **pause)
                elapsed = time.monotonic() - self.paused_since
                if elapsed < GUARD_RECOVERY_SECONDS:
                    return False
                error = GuardHealthError(
                    {
                        **error.evidence,
                        "reasons": [*error.evidence["reasons"], "guard_recovery_exhausted"],
                        "paused_seconds": elapsed,
                        "recovery_limit_seconds": GUARD_RECOVERY_SECONDS,
                    }
                )
            retain_guard_refusal(self.control, error)
            raise error
        if self.paused_since is not None:
            self.control.log(
                "dispatch_guard_resumed", paused_seconds=time.monotonic() - self.paused_since
            )
            self.control.update(admission_pause=None, admission_resumed_at=now().isoformat())
            self.paused_since = None
        return True

    def wait(self, deadline: datetime) -> None:
        """Control-plane waiting only, bounded by the existing financial deadline."""
        while now() < deadline:
            if self.check():
                return
            time.sleep(min(5, max(0, (deadline - now()).total_seconds())))
        error = GuardHealthError({"reasons": ["dispatch_deadline_reached"]})
        retain_guard_refusal(self.control, error)
        raise error


def main(*, entrypoint: Path | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--gcloud", default="gcloud")
    parser.add_argument(
        "action", choices=("inventory", "create", "deploy", "status", "cleanup", "guard")
    )
    args = parser.parse_args()
    control = Control(args.config, args.root, args.gcloud, entrypoint=entrypoint)
    try:
        getattr(control, args.action)()
    except Exception:
        if args.action in {"create", "deploy"} and control.state():
            control.update(stop_requested_at=now().isoformat(), cleanup_requested=True)
            try:
                control.cleanup()
            except Exception as error:
                control.log("foreground_cleanup_pending", error_type=type(error).__name__)
        raise
    finally:
        control.api.close()


async def await_guard_admission(
    admission: GuardAdmission, stop: asyncio.Event, deadline: datetime
) -> bool:
    """Wait before any native call intent; never replay a dispatched request."""

    while not stop.is_set():
        try:
            if datetime.now(UTC) >= deadline:
                raise GuardHealthError({"reasons": ["dispatch_deadline_reached"]})
            if admission.check():
                return True
        except Exception as error:
            stop.set()
            retain_guard_refusal(admission.control, error)
            return False
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=1)
    return False
