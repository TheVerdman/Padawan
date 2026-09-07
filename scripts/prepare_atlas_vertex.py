"""Write exact Vertex deployment requests and cleanup instructions; perform no cloud I/O."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def prepare(config: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    region = config["region"]
    parent = f"projects/{config['project_number']}/locations/{region}"
    api = f"https://{region}-aiplatform.googleapis.com/v1/{parent}"
    model_id = config.get("cloud_model_id", "atlas-nemotron-bf16-20260906")
    endpoint_id = config.get("endpoint_id", "2026090606")
    if not endpoint_id.isdecimal() or endpoint_id.startswith("0") or len(endpoint_id) > 10:
        raise ValueError("this rawPredict plan requires a numeric endpoint ID of 1–10 digits")
    model_resource = f"{parent}/models/{model_id}"
    endpoint_resource = f"{parent}/endpoints/{endpoint_id}"
    labels = {"padawan-workflow": "atlas-frontier", "padawan-model": "nemotron-bf16"}
    if config.get("experiment_id"):
        labels["padawan-experiment"] = config["experiment_id"]
    upload = {
        "modelId": model_id,
        "model": {
            "displayName": model_id,
            "labels": labels,
            "containerSpec": {
                "imageUri": config["artifact_registry_image"],
                "command": ["python3", "-m", "vllm.entrypoints.openai.api_server"],
                "args": config["server_args"],
                "env": [
                    {"name": key, "value": value}
                    for key, value in config["server_environment"].items()
                ],
                "ports": [{"containerPort": config["container_port"]}],
                "predictRoute": config["predict_route"],
                "healthRoute": config["health_route"],
                "deploymentTimeout": f"{config.get('maximum_startup_minutes', 120) * 60}s",
            },
        },
    }
    endpoint = {
        "displayName": model_id,
        "labels": labels,
        "dedicatedEndpointEnabled": True,
        "clientConnectionConfig": {"inferenceTimeout": f"{config['endpoint_timeout_seconds']}s"},
    }
    deploy = {
        "deployedModel": {
            "model": model_resource,
            "displayName": model_id,
            "dedicatedResources": {
                "machineSpec": {
                    "machineType": config["machine_type"],
                    "acceleratorType": config["accelerator_type"],
                    "acceleratorCount": config["accelerators_per_replica"],
                },
                "minReplicaCount": config["replicas"],
                "maxReplicaCount": config["replicas"],
            },
        },
        "trafficSplit": {"0": 100},
    }
    destination_tag = config["artifact_registry_image"].split("@", 1)[0] + ":v0.27.1-amd64"
    plan = {
        "status": "prepared_not_executed",
        "model_resource": model_resource,
        "endpoint_resource": endpoint_resource,
        "image_copy_commands": [
            ["gcloud", "auth", "configure-docker", f"{region}-docker.pkg.dev", "--quiet"],
            [
                "docker",
                "buildx",
                "imagetools",
                "create",
                "--prefer-index=false",
                "--tag",
                destination_tag,
                config["source_image"],
            ],
            ["docker", "buildx", "imagetools", "inspect", config["artifact_registry_image"]],
        ],
        "requests": [
            {"method": "POST", "url": f"{api}/models:upload", "body_file": "model-upload.json"},
            {
                "method": "POST",
                "url": f"{api}/endpoints?endpointId={endpoint_id}",
                "body_file": "endpoint-create.json",
            },
            {
                "method": "POST",
                "url": f"{api}/endpoints/{endpoint_id}:deployModel",
                "body_file": "deploy-model.json",
            },
        ],
        "execution_rules": [
            "Use the approved model, project, four serving GPUs, duration and spend cap.",
            "Recheck serving quota, usage and resource-name collisions before creation.",
            "Use GCP credentials only in request headers; keep the HF token local.",
            "Verify copied image digest; retain and await each operation before continuing.",
            f"Clock starts before deployModel. Stop dispatch by hour "
            f"{config['stop_dispatch_after_hours']}; finish teardown by hour "
            f"{config['maximum_deployment_hours']}.",
            "Read dedicatedEndpointDns from the created endpoint for the prediction URL.",
            "Validate real BF16 Responses before Atlas registration. No substitute model.",
            "Run each wave once. Retain unknown and missing outcomes without automatic retries.",
        ],
        "cleanup": {
            "read_endpoint_url": f"{api}/endpoints/{endpoint_id}",
            "undeploy_url": f"{api}/endpoints/{endpoint_id}:undeployModel",
            "undeploy_body": {
                "deployedModelId": "USE_EXACT_RETURNED_DEPLOYED_MODEL_ID",
                "trafficSplit": {},
            },
            "ownership_rule": "Undeploy only the specified model from the new endpoint.",
            "verification": "Await undeploy; GET the endpoint and verify no deployed models.",
            "delete_endpoint": f"{api}/endpoints/{endpoint_id}",
            "delete_model": f"{api}/models/{model_id}",
            "rule": "Delete these new empty resources after cleanup. Retain local evidence.",
        },
        "maximum_deployment_hours": config["maximum_deployment_hours"],
        "proposed_spending_limit_usd": config["proposed_spending_limit_usd"],
        "budget_limit_semantics": config["budget_limit_semantics"],
    }
    for name, value in (
        ("model-upload.json", upload),
        ("endpoint-create.json", endpoint),
        ("deploy-model.json", deploy),
        ("launch-plan.json", plan),
    ):
        (output / name).write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"prepared": str(output), "gpu_count": 4, "cloud_operations": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(json.loads(args.config.read_text()), args.output)
