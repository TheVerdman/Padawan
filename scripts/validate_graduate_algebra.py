"""Exact finite group checks supporting (but not replacing) graduate proof review."""

from __future__ import annotations

import argparse
import itertools
import json
from collections import deque
from pathlib import Path

Perm = tuple[int, ...]


def compose(a: Perm, b: Perm) -> Perm:
    return tuple(a[i] for i in b)


def inverse(a: Perm) -> Perm:
    return tuple(a.index(i) for i in range(len(a)))


def generated(generators: set[Perm]) -> set[Perm]:
    identity = tuple(range(len(next(iter(generators)))))
    found, queue = {identity}, deque([identity])
    while queue:
        current = queue.popleft()
        for g in generators:
            product = compose(current, g)
            if product not in found:
                found.add(product)
                queue.append(product)
    return found


def orbits(group: set[Perm]) -> list[list[int]]:
    unseen = set(range(len(next(iter(group)))))
    result = []
    while unseen:
        point = min(unseen)
        orbit = {g[point] for g in group}
        result.append(sorted(orbit))
        unseen -= orbit
    return result


def conjugate(group: set[Perm], g: Perm) -> set[Perm]:
    inv = inverse(g)
    return {compose(compose(g, h), inv) for h in group}


def facts(group: set[Perm], h: set[Perm], k: set[Perm]) -> dict:
    k_orbits = orbits(k)
    cores = [sum(all(g[x] == x for x in orbit) for g in k) for orbit in k_orbits]
    normalizer_size = sum(conjugate(k, g) == k for g in group)
    core = set(k)
    for g in group:
        core &= conjugate(k, g)
        if len(core) == 1:
            break
    return {
        "G_order": len(group),
        "H_order": len(h),
        "K_order": len(k),
        "H_intersect_K_order": len(h & k),
        "generated_H_K_order": len(generated(h | k)),
        "E_degree": len(group) // len(h),
        "F_degree": len(group) // len(k),
        "EF_degree": len(group) // len(h & k),
        "K_orbits": [[x + 1 for x in orbit] for orbit in k_orbits],
        "factor_degrees": [len(orbit) for orbit in k_orbits],
        "factor_normal_closure_group_orders": [len(k) // size for size in cores],
        "K_global_core_order": len(core),
        "K_normalizer_order": normalizer_size,
        "Aut_F_over_k_order": normalizer_size // len(k),
    }


def cycles(g: Perm) -> tuple[int, ...]:
    unseen = set(range(len(g)))
    sizes = []
    while unseen:
        start, size = min(unseen), 0
        current = start
        while current in unseen:
            unseen.remove(current)
            size += 1
            current = g[current]
        sizes.append(size)
    return tuple(sorted(sizes))


def finite_checks() -> dict:
    gl = set()
    for u, v, w in itertools.product(range(1, 8), repeat=3):
        if v == u or w in {u, v, u ^ v}:
            continue
        gl.add(
            tuple(
                ((u if x & 1 else 0) ^ (v if x & 2 else 0) ^ (w if x & 4 else 0)) - 1
                for x in range(1, 8)
            )
        )
    planes = sorted(
        {
            tuple(sorted((a - 1, b - 1, (a ^ b) - 1)))
            for a, b in itertools.combinations(range(1, 8), 2)
        }
    )
    w0 = frozenset({0, 1, 2})
    h = {g for g in gl if g[0] == 0}
    k = {g for g in gl if frozenset(g[x] for x in w0) == w0}
    dual = {
        g: tuple(planes.index(tuple(sorted(g[x] for x in plane))) for plane in planes) for g in gl
    }
    gl_facts = facts(gl, h, k)
    gl_facts["point_plane_cycle_types_agree_for_every_element"] = all(
        cycles(g) == cycles(dual[g]) for g in gl
    )
    gl_facts["plane_stabilizer_has_global_fixed_point"] = any(
        all(g[x] == x for g in k) for x in range(7)
    )
    group5 = set(itertools.permutations(range(5)))
    h5 = {g for g in group5 if g[0] == 0}
    k5a = {g for g in group5 if {g[0], g[1]} == {0, 1}}
    k5b = {
        g
        for g in group5
        if g[4] == 4 and sum(g[i] > g[j] for i in range(5) for j in range(i + 1, 5)) % 2 == 0
    }
    group6 = set(itertools.permutations(range(6)))
    h6 = {g for g in group6 if g[0] == 0}

    def stabilizer(blocks):
        partition = {frozenset(b) for b in blocks}
        return {g for g in group6 if {frozenset(g[x] for x in b) for b in partition} == partition}

    k6c = stabilizer(({0, 1, 2}, {3, 4, 5}))
    k6a = stabilizer(({0, 1}, {2, 3}, {4, 5}))
    k6b = stabilizer(({0, 1, 2, 3}, {4, 5}))
    result = {
        "gl3_points_planes": gl_facts,
        "s5_two_three": facts(group5, h5, k5a),
        "s5_a4": facts(group5, h5, k5b),
        "s6_three_three": facts(group6, h6, k6c),
        "s6_three_pairs": facts(group6, h6, k6a),
        "s6_four_two": facts(group6, h6, k6b),
    }
    expected = {
        "gl3_points_planes": (168, 24, 8, [3, 4], 21, [6, 24], 1),
        "s5_two_three": (120, 12, 6, [2, 3], 20, [2, 6], 1),
        "s5_a4": (120, 12, 3, [4, 1], 40, [12, 1], 2),
        "s6_three_three": (720, 72, 12, [6], 60, [72], 1),
        "s6_three_pairs": (720, 48, 8, [6], 90, [48], 1),
        "s6_four_two": (720, 48, 12, [4, 2], 60, [24, 2], 1),
    }
    for key, (g, k, intersection, degrees, ef, closures, aut) in expected.items():
        record = result[key]
        assert record["G_order"] == g
        assert record["K_order"] == k
        assert record["H_intersect_K_order"] == intersection
        assert record["factor_degrees"] == degrees
        assert record["EF_degree"] == ef
        assert record["factor_normal_closure_group_orders"] == closures
        assert record["Aut_F_over_k_order"] == aut
        assert record["generated_H_K_order"] == g
        assert record["K_global_core_order"] == 1
    assert gl_facts["point_plane_cycle_types_agree_for_every_element"]
    assert not gl_facts["plane_stabilizer_has_global_fixed_point"]
    return {"finite_checks_passed": True, "full_proofs_kernel_verified": False, "tasks": result}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = finite_checks()
    if args.output:
        args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True))
