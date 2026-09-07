"""Independent CPU checks for the real SCC serving probes; not benchmark observations."""

from __future__ import annotations

import json
import random
from pathlib import Path

from padawan.atlas.coding_judge import JudgeCase, JudgePackage, file_sha256
from padawan.models.hashing import sha256_digest

SCC_TASK = (
    "Read a directed graph as n m followed by m 1-based edges. Print the number "
    "of strongly connected components. Handle n up to 200000 without recursion."
)


def scc_probe_package(root: Path) -> JudgePackage:
    """Use mutual reachability for small graphs, plus analytically known large cases."""
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    generator = random.Random(20260906)
    specimens: list[tuple[int, list[tuple[int, int]], int]] = []
    for n in range(1, 13):
        edges = [(u, v) for u in range(n) for v in range(n) if generator.random() < 0.23]
        reach = [[u == v or (u, v) in edges for v in range(n)] for u in range(n)]
        for via in range(n):
            for u in range(n):
                for v in range(n):
                    reach[u][v] |= reach[u][via] and reach[via][v]
        classes = {frozenset(v for v in range(n) if reach[u][v] and reach[v][u]) for u in range(n)}
        specimens.append((n, edges, len(classes)))
    specimens.extend(
        [
            (5, [], 5),
            (4, [(0, 1), (1, 0), (1, 2), (2, 3), (3, 2)], 2),
            (200000, [(u, u + 1) for u in range(199999)], 200000),
            (200000, [(u, (u + 1) % 200000) for u in range(200000)], 1),
        ]
    )
    cases = []
    identities = []
    for index, (n, edges, answer) in enumerate(specimens):
        inp, out = root / f"{index}.in", root / f"{index}.ans"
        inp.write_text(f"{n} {len(edges)}\n" + "".join(f"{u + 1} {v + 1}\n" for u, v in edges))
        out.write_text(f"{answer}\n")
        cases.append(JudgeCase(inp, out, 5, 512 * 1024**2))
        identities.append({"input": file_sha256(inp), "answer": file_sha256(out)})
    checker = root / "checker.cpp"
    checker.write_text(
        '#include "testlib.h"\n'
        "int main(int argc,char**argv){registerTestlibCmd(argc,argv);"
        "int expected=ans.readInt();int actual=ouf.readInt();"
        'if(!ouf.seekEof())quitf(_pe,"unexpected extra output");'
        'if(expected!=actual)quitf(_wa,"different SCC count");quitf(_ok,"correct");}\n'
    )
    manifest = {
        "kind": "generated_scc_runtime_probe_v1",
        "cases": identities,
        "checker_sha256": file_sha256(checker),
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return JudgePackage(
        "runtime-scc-probe", sha256_digest(manifest), root, checker, tuple(cases), len(cases)
    )
