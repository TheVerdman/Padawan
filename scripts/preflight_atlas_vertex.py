"""Run four real BF16 protocol probes after explicit GPU activation, retaining native evidence.

This is serving validation, not a benchmark score. It never substitutes a model or replays an
unresolved probe. The output directory must be new. Cloud deployment remains a separate action.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import traceback
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from padawan.adapters.base import GenerationRequest
from padawan.adapters.openai_compatible.gcp_credentials import GcloudCredentialSource
from padawan.adapters.openai_compatible.vertex import VertexRawPredictClient
from padawan.artifacts.store import LocalArtifactStore
from padawan.atlas.coding_judge import DockerBatchJudge
from padawan.atlas.coding_runner import extract_cpp, judge_with_deadline
from padawan.atlas.preflight_fixtures import SCC_TASK, scc_probe_package
from padawan.models.contracts import SamplingConfiguration
from padawan.models.database import Database
from padawan.models.hashing import sha256_digest
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from padawan.orchestration.state_machine import RunStore


async def preflight(args) -> None:
    inputs = json.loads(args.inputs.read_text())
    launch = inputs["launch"]
    plan = json.loads(args.launch_plan.read_text())
    if not args.authorization_ref:
        raise PermissionError("real model probes require explicit activation authority")
    deployed_at = datetime.fromisoformat(args.deployment_started_at)
    if deployed_at.tzinfo is None or not timedelta(0) <= datetime.now(
        UTC
    ) - deployed_at < timedelta(hours=launch["stop_dispatch_after_hours"]):
        raise ValueError(
            "preflight requires the actual deployment start within the dispatch window"
        )
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    credential = await GcloudCredentialSource(args.gcloud).get_async(120)
    resource = plan["endpoint_resource"]
    expected_resource = (
        f"projects/{launch['project_number']}/locations/{launch['region']}/endpoints/"
        f"{launch.get('endpoint_id', '2026090606')}"
    )
    if resource != expected_resource:
        raise ValueError("preflight endpoint differs from the frozen launch plan")
    base = f"https://{launch['region']}-aiplatform.googleapis.com/v1/"
    async with httpx.AsyncClient(timeout=45, follow_redirects=False) as transport:
        headers = {"Authorization": f"Bearer {credential.access_token}"}
        endpoint_response = await transport.get(base + resource, headers=headers)
        endpoint_response.raise_for_status()
        endpoint = endpoint_response.json()
        models = endpoint.get("deployedModels", [])
        if len(models) != 1 or models[0].get("model") not in {
            plan["model_resource"],
            plan["model_resource"] + "@1",
        }:
            raise ValueError("preflight endpoint has an unexpected deployed model")
        spec = models[0].get("dedicatedResources", {})
        if (spec.get("minReplicaCount"), spec.get("maxReplicaCount")) != (4, 4):
            raise ValueError("preflight requires the four declared serving replicas")
        expected_machine = {
            "machineType": launch["machine_type"],
            "acceleratorType": launch["accelerator_type"],
            "acceleratorCount": 1,
        }
        if any(spec.get("machineSpec", {}).get(k) != v for k, v in expected_machine.items()):
            raise ValueError("actual serving machine differs from the approved configuration")
        model_response = await transport.get(base + plan["model_resource"], headers=headers)
        model_response.raise_for_status()
        model = model_response.json()
        container = model["containerSpec"]
        if (
            container.get("imageUri") != launch["artifact_registry_image"]
            or container.get("args") != launch["server_args"]
            or container.get("predictRoute") != "/v1/responses"
            or container.get("command") != ["python3", "-m", "vllm.entrypoints.openai.api_server"]
            or {e["name"]: e["value"] for e in container.get("env", [])}
            != launch["server_environment"]
        ):
            raise ValueError("actual serving image, arguments or Responses route differ")
        dns = endpoint.get("dedicatedEndpointDns")
        if not dns or not endpoint.get("dedicatedEndpointEnabled"):
            raise ValueError("provider has not returned a dedicated prediction hostname")
    base_dns = dns.rstrip("/") if dns.startswith("https://") else f"https://{dns}"
    url = f"{base_dns}/v1/{resource}:rawPredict"
    client = VertexRawPredictClient(
        raw_predict_url=url,
        model=launch["model_id"],
        timeout_seconds=launch["request_timeout_seconds"],
        gcloud=args.gcloud,
        compiler_tools=bool(launch.get("compiler_tool_policy")),
    )
    database = Database.sqlite(args.output / "preflight.sqlite3")
    artifacts = LocalArtifactStore(args.output / "artifacts")
    observations = []
    try:
        await database.create_schema()
        async with database.transaction() as session:
            run_id = await RunStore().create(
                session,
                run_id=args.output.name,
                retry_budget=0,
                payload={
                    "workflow": "serving_protocol_preflight",
                    "authorization_ref": args.authorization_ref,
                },
            )
        executor = IdempotentGenerationExecutor(
            database=database, artifacts=artifacts, client=client
        )
        # These real calls and generated fixtures are outside the benchmark population/score.
        package = scc_probe_package(args.output / "scc-fixtures")
        judge = DockerBatchJudge(
            image=launch["judge_image"],
            scratch=args.output / "judge-scratch",
            testlib=Path(inputs["testlib"]),
            testlib_digest=inputs["environment_parameters"]["judge_testlib_digest"],
        )
        probe_inputs = [SCC_TASK] * launch["maximum_preflight_requests"]
        long_prompt_tokens = None
        if launch.get("preflight_long_prompt_tokens"):
            from transformers import AutoTokenizer

            tokenizer_root = inputs["tokenizer_audit"]["tokenizer_identity"].get("path")
            if not tokenizer_root:
                tokenizer_root = inputs.get("tokenizer_directory")
            if not tokenizer_root:
                raise ValueError("long-context preflight requires the frozen local tokenizer path")
            tokenizer = AutoTokenizer.from_pretrained(
                tokenizer_root, local_files_only=True, trust_remote_code=False
            )
            target = launch["preflight_long_prompt_tokens"]
            low, high = 0, target * 2

            def padded(repetitions):
                return (
                    "The repeated word below is padding for a runtime capacity check.\n"
                    + "padding " * repetitions
                    + "\nTask:\n"
                    + SCC_TASK
                )

            def count(text):
                return len(
                    tokenizer.apply_chat_template(
                        [
                            {
                                "role": "system",
                                "content": (
                                    "Return a self-contained C++17 implementation "
                                    "in the final answer."
                                ),
                            },
                            {"role": "user", "content": text},
                        ],
                        tokenize=True,
                        add_generation_prompt=True,
                        return_dict=True,
                    )["input_ids"]
                )

            while low < high:
                middle = (low + high + 1) // 2
                if count(padded(middle)) <= target:
                    low = middle
                else:
                    high = middle - 1
            long_input = padded(low)
            long_prompt_tokens = count(long_input)
            if not target - 8 <= long_prompt_tokens <= target:
                raise ValueError("capacity-check prompt did not reach the declared context size")
            probe_inputs[launch["preflight_long_prompt_index"]] = long_input
        judge_slots = asyncio.Semaphore(launch["max_concurrent_judges"])
        probe_slots = asyncio.Semaphore(launch.get("preflight_concurrency", 1))

        async def probe(index):
            if datetime.now(UTC) >= deployed_at + timedelta(
                hours=launch["stop_dispatch_after_hours"]
            ):
                raise TimeoutError("approved preflight dispatch window has ended")
            request = GenerationRequest(
                request_id=f"{run_id}-probe-{index}",
                instructions="Return a self-contained C++17 implementation in the final answer.",
                input=probe_inputs[index],
                sampling=SamplingConfiguration(
                    temperature=launch["temperature"],
                    top_p=launch["top_p"],
                    max_output_tokens=launch.get(
                        "preflight_output_allowances", [8192] * len(probe_inputs)
                    )[index],
                    seed=launch["base_seed"] + index,
                ),
                store=False,
            )
            prepared = client.prepare_generation(request)
            async with probe_slots:
                result = await executor.execute(
                    run_id=run_id,
                    purpose="serving_protocol_preflight",
                    provider=client.provider,
                    request=request,
                )
            actual = json.loads(result.raw_response)
            observation = {
                "request_id": request.request_id,
                "response_id": result.response_id,
                "actual_model": actual.get("model"),
                "protocol": result.protocol,
                "final_present": bool(result.output_text.strip()),
                "private_channel_present": bool(result.private_reasoning),
                "usage": result.usage,
                "finish_reason": result.finish_reason,
                "latency_ms": result.latency_ms,
                "requested_output_allowance": request.sampling.max_output_tokens,
                "capacity_prompt_tokens": long_prompt_tokens
                if index == launch.get("preflight_long_prompt_index")
                else None,
                "configuration_digest": prepared.configuration_digest,
            }
            observations.append(observation)
            if (
                actual.get("model") != launch["model_id"]
                or result.protocol != "responses"
                or not result.output_text.strip()
                or not result.private_reasoning
            ):
                raise ValueError(
                    "real BF16 Responses preflight did not satisfy the declared protocol"
                )
            checked = await judge_with_deadline(
                semaphore=judge_slots,
                judge=judge,
                package=package,
                source=extract_cpp(result.output_text),
                timeout_seconds=launch["judge_deadline_seconds"],
            )
            observation["independent_scc_checks"] = asdict(checked)
            if not checked.success:
                raise ValueError("real serving probe did not pass its independent CPU checks")

        outcomes = await asyncio.gather(
            *(probe(index) for index in range(len(probe_inputs))), return_exceptions=True
        )
        observations.sort(key=lambda row: row["request_id"])
        observed_path = args.output / "probe-observations.json"
        observed_path.write_text(
            json.dumps(
                {
                    "observations": observations,
                    "failures": [
                        type(value).__name__
                        for value in outcomes
                        if isinstance(value, BaseException)
                    ],
                    "failure_details": [
                        {
                            "request_id": f"{run_id}-probe-{index}",
                            "error_type": type(value).__name__,
                            "sqlite_error_code": getattr(
                                getattr(value, "orig", None), "sqlite_errorcode", None
                            ),
                            "frames": [
                                {
                                    "file": Path(frame.filename).name,
                                    "line": frame.lineno,
                                    "function": frame.name,
                                }
                                for frame in traceback.extract_tb(value.__traceback__)
                            ],
                        }
                        for index, value in enumerate(outcomes)
                        if isinstance(value, BaseException)
                    ],
                },
                indent=2,
            )
            + "\n"
        )
        os.chmod(observed_path, 0o600)
        if any(isinstance(value, BaseException) for value in outcomes):
            raise ValueError("one or more real probes failed; all started probes were drained")
        compiler_evidence = None
        if launch.get("compiler_tool_policy"):
            from padawan.atlas.coding_tool_contracts import CompilerPolicy
            from padawan.atlas.coding_tool_preflight import compiler_preflight

            if launch.get("maximum_tool_preflight_model_requests") != 2:
                raise ValueError("compiler preflight requires exactly two authorized model calls")
            compiler_evidence = await compiler_preflight(
                executor=executor,
                run_id=run_id,
                judge=judge,
                policy=CompilerPolicy.model_validate_json(
                    json.dumps(launch["compiler_tool_policy"])
                ),
                output=args.output,
            )
        receipt = {
            "passed": True,
            "provider": client.provider,
            "model_id": launch["model_id"],
            "protocol": "responses",
            "checkpoint_revision": launch["model_revision"],
            "serving_artifact_digest": launch["source_image"].split("@", 1)[1],
            "server_configuration_digest": sha256_digest(launch),
            "destination": url,
            "configuration_digest": observations[0]["configuration_digest"],
            "authorization_ref": args.authorization_ref,
            "deployment_started_at": deployed_at.isoformat(),
            "validated_at": datetime.now(UTC).isoformat(),
            "endpoint_resource": resource,
            "deployment": models[0],
            "container": container,
            "observations": observations,
            "compiler_tool_preflight": compiler_evidence,
            "evidence_scope": (
                "Real protocol probes and provider configuration; "
                "not hardware attestation or a capability score"
            ),
        }
        path = args.output / "preflight.json"
        path.write_text(json.dumps(receipt, indent=2) + "\n")
        os.chmod(path, 0o600)
        print(
            json.dumps(
                {
                    "passed": True,
                    "real_model_requests": len(observations)
                    + (compiler_evidence["model_requests"] if compiler_evidence else 0),
                    "receipt": str(path),
                }
            )
        )
    finally:
        await client.close()
        await database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--launch-plan", type=Path, required=True)
    parser.add_argument("--authorization-ref", required=True)
    parser.add_argument("--deployment-started-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gcloud", default="gcloud")
    asyncio.run(preflight(parser.parse_args()))
