"""Download the authorized, pinned benchmark and freeze a stratified diagnostic scout.

This command performs no model calls, GPU operations, cloud writes, or code execution.
Dataset contents and test packages remain in the explicitly chosen local output directory.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
from pathlib import Path

import httpx

from padawan.atlas.coding_judge import file_sha256, read_statement_shards, unpack_package

STATEMENTS = "QAQAQAQAQ/LiveCodeBench-Pro"
STATEMENT_REVISION = "adebffce047dddb7768a86bace6aea4f7425e3bc"
TESTS = "QAQAQAQAQ/LiveCodeBench-Pro-Testcase"
TEST_REVISION = "5257736c0a4e30ba0949d41c56a257c323d9c600"


def download(client: httpx.Client, url: str, path: Path, *, maximum_bytes: int) -> None:
    if path.exists():
        raise ValueError("download destination already exists; use a fresh preparation directory")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with client.stream("GET", url) as response:
            if response.status_code in {401, 403}:
                raise PermissionError("Hugging Face requires a token with approved dataset access")
            response.raise_for_status()
            count = 0
            with path.open("xb") as output:
                os.chmod(path, 0o600)
                for chunk in response.iter_bytes():
                    count += len(chunk)
                    if count > maximum_bytes:
                        raise ValueError("download exceeds the declared per-file byte ceiling")
                    output.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def select_scout(rows: dict, available: set[str], *, seed: int) -> tuple[list[dict], list[dict]]:
    """Freeze 8 easy anchors, 24 medium and 32 hard tasks without observing model outcomes."""
    selected, excluded = [], []
    randomizer = random.Random(seed)
    groups: dict[str, list[dict]] = {label: [] for label in ("easy", "medium", "hard")}
    for pid, row in sorted(rows.items()):
        if pid not in available:
            excluded.append({"problem_id": pid, "reason": "no pinned official test archive"})
        else:
            groups[row["difficulty"]].append(row)
    contest_counts: dict[str, int] = {}
    for label, count in (("hard", 32), ("medium", 24), ("easy", 8)):
        candidates = groups[label]
        randomizer.shuffle(candidates)
        chosen = []
        for row in candidates:
            match = re.fullmatch(r"([0-9]+)[A-Z][0-9]*", row["problem_id"])
            contest = f"codeforces:{match[1]}" if match else row["problem_id"]
            if contest_counts.get(contest, 0) >= 2:
                continue
            chosen.append({**row, "contest_group": contest})
            contest_counts[contest] = contest_counts.get(contest, 0) + 1
            if len(chosen) == count:
                break
        if len(chosen) < count:
            raise ValueError(f"insufficient {label} tasks for the declared allocation")
        selected.extend(chosen)
    randomizer.shuffle(selected)
    return selected, excluded


def prepare(output: Path, *, credential_file: Path | None, seed: int) -> dict:
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if credential_file is not None:
        from dotenv import dotenv_values

        values = dotenv_values(credential_file, interpolate=False)
        token = values.get("HF_TOKEN") or values.get("HUGGING_FACE_HUB_TOKEN")
    if not token:
        raise PermissionError("supply HF_TOKEN through the environment or an authorized .env path")
    if output.exists():
        raise ValueError("preparation output must be a new directory")
    output.mkdir(parents=True, mode=0o700)
    with httpx.Client(
        headers={"Authorization": f"Bearer {token}"}, follow_redirects=True, timeout=60
    ) as client:
        metadata = client.get(
            f"https://huggingface.co/api/datasets/{STATEMENTS}/revision/{STATEMENT_REVISION}"
        )
        metadata.raise_for_status()
        statement_files = sorted(
            entry["rfilename"]
            for entry in metadata.json()["siblings"]
            if entry["rfilename"].endswith(".parquet")
        )
        if len(statement_files) != 6:
            raise ValueError("pinned benchmark shard inventory differs from the reviewed six files")
        for filename in statement_files:
            download(
                client,
                f"https://huggingface.co/datasets/{STATEMENTS}/resolve/{STATEMENT_REVISION}/{filename}",
                output / filename,
                maximum_bytes=16 * 1024**2,
            )
        rows = read_statement_shards(list((output / "data").glob("*.parquet")))
        metadata = client.get(
            f"https://huggingface.co/api/datasets/{TESTS}/revision/{TEST_REVISION}"
        )
        metadata.raise_for_status()
        available = {
            Path(e["rfilename"]).stem
            for e in metadata.json()["siblings"]
            if e["rfilename"].endswith(".zip")
        }
        selected, excluded = select_scout(rows, available, seed=seed)
        frozen = {
            "statement_repository": STATEMENTS,
            "statement_revision": STATEMENT_REVISION,
            "test_repository": TESTS,
            "test_revision": TEST_REVISION,
            "seed": seed,
            "source_problem_count": len(rows),
            "selected": selected,
            "excluded_before_sampling": excluded,
            "classification": "public_pre_model_release_diagnostic",
            "allocation": {"easy": 8, "medium": 24, "hard": 32},
            "maximum_items_per_codeforces_contest": 2,
            "repetitions": 4,
            "base_output_tokens": 8192,
            "budget_ladder_output_tokens": [32768, 65536],
            "source_files": {f: file_sha256(output / f) for f in statement_files},
        }
        (output / "scout-selection.json").write_text(json.dumps(frozen, indent=2) + "\n")
        packages = []
        total_bytes = 0
        for row in selected:
            pid = row["problem_id"]
            archive = output / "archives" / f"{pid}.zip"
            download(
                client,
                f"https://huggingface.co/datasets/{TESTS}/resolve/{TEST_REVISION}/{pid}.zip",
                archive,
                maximum_bytes=512 * 1024**2,
            )
            total_bytes += archive.stat().st_size
            if total_bytes > 16 * 1024**3:
                raise ValueError("selected judge downloads exceed the 16 GiB preparation ceiling")
            digest = file_sha256(archive)
            try:
                package = unpack_package(
                    archive,
                    output / "packages" / pid,
                    problem_id=pid,
                    expected_digest=digest,
                    include_extra_cases=True,
                )
            except (ValueError, KeyError) as error:
                packages.append(
                    {
                        "problem_id": pid,
                        "archive_sha256": digest,
                        "supported": False,
                        "reason": str(error),
                    }
                )
                continue
            packages.append(
                {
                    "problem_id": pid,
                    "archive_sha256": digest,
                    "supported": True,
                    "declared_cases": package.declared_case_count,
                    "retained_cases": len(package.cases),
                }
            )
        frozen["packages"] = packages
        frozen["ready"] = all(p["supported"] for p in packages)
        # Unsupported selected tasks stay in the denominator. No outcome-dependent replacement.
        (output / "prepared-dataset.json").write_text(json.dumps(frozen, indent=2) + "\n")
        return {
            "selected": len(selected),
            "supported": sum(p["supported"] for p in packages),
            "ready": frozen["ready"],
            "manifest": str(output / "prepared-dataset.json"),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--seed", type=int, default=20260906)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output, credential_file=args.credential_file, seed=args.seed)))


if __name__ == "__main__":
    main()
