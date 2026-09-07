"""Original graduate-level proof problems and researcher-only reference rubrics.

Only each task's prompt is admitted to the student's request. Difficulty is a
curricular judgment, not a measured human or model difficulty calibration.
"""

COMMON = (
    "Let k be a field of characteristic zero and L/k a finite Galois extension with the "
    "specified Galois group G. For a subgroup J write L^J for its fixed field. "
    "All composita and intersections use the given embeddings in L. Give proofs, not just "
    "group orders or final degrees. You may use standard Galois correspondence and basic "
    "facts about finite separable extensions; justify the subgroup, tensor-factor, and "
    "normal-closure identifications you need. "
)

SN_REQUESTS = (
    "\n(a) Determine |H|, |K|, [E:k], [F:k], E intersection F, and [EF:k]. "
    "Decide whether these embedded fields are linearly disjoint over k, and justify why "
    "an intersection calculation alone does or does not settle this."
    "\n(b) Choose a primitive element alpha of E and decompose E tensor_k F into a product "
    "of fields as an F-algebra. Give the degrees and describe every factor as a fixed "
    "field of an appropriate subgroup of K. Identify the factor obtained from the given "
    "embedding E into L. Establish the correspondence with the irreducible factors of "
    "the minimal polynomial of alpha over F."
    "\n(c) For each factor in (b), determine the Galois group of its normal closure over F "
    "and identify the kernel that must be divided out of K. Distinguish each individual "
    "factor's normal closure from the splitting field over F of the whole polynomial."
    "\n(d) Determine Aut_k(F), proving the normalizer calculation. Identify the normal "
    "closures of E/k and F/k inside L."
    "\n(e) Decide whether the minimal polynomial of alpha is solvable by radicals over F "
    "and over k. Decide whether F/k itself is contained in a radical extension of k. "
    "Explain why these answers are compatible."
)

SN_RUBRIC = [
    "Correct subgroup orders, fixed-field degrees, generated "
    "subgroup/intersection, and linear-disjointness argument.",
    "Proved orbit/double-coset tensor decomposition with correct "
    "factor fields and the selected embedded compositum.",
    "Correct kernels, factor normal-closure groups, and full "
    "splitting field; no conflation of these objects.",
    "Proved normalizer quotient for Aut_k(F) and both normal closures over k.",
    "Correct radical-solvability conclusions with hypotheses and a "
    "coherent explanation of the base-change distinction.",
]

SUITE = {
    "version": "graduate-galois-authored-v1",
    "domain": "math.graduate_algebra",
    "difficulty": {
        "intended_level": "difficult graduate abstract algebra proofs",
        "empirically_calibrated": False,
        "rationale": "Multi-part Galois theory, etale decomposition, and character proofs.",
        "scope_reference": "https://math.stanford.edu/~akshay/math210B.html",
        "independent_expert_difficulty_review": False,
    },
    "design": {
        "blocks": 2,
        "items_per_block": 3,
        "transfer_matching": "Matched G, |K|, [F:k]; distinct actions; difficulty uncalibrated.",
        "second_cold": "Fresh S6 problem probes any admitted lesson.",
        "teacher_and_grader": "Shared Codex author; no independent or kernel verification.",
    },
    "groups": [
        {
            "id": "point-plane-to-quintic-base-change",
            "tasks": [
                {
                    "id": "gl3_points_planes",
                    "prompt": COMMON
                    + (
                        "Take G = GL(3,F_2) acting on V = F_2^3. Put v = e_1, W = span(e_1,e_2), "
                        "H = Stab_G(v), K = Stab_G(W), E = L^H and F = L^K."
                        "\n(a) Determine |G|, the structures and orders of H and K, their "
                        "intersection, "
                        "and [E:k], [F:k], [EF:k]. Prove E intersection F = k."
                        "\n(b) Prove E and F are not isomorphic as extensions of k, "
                        "despite having the same degree."
                        "\n(c) Decompose E tensor_k F into fields as an F-algebra, with "
                        "degrees and subgroup "
                        "descriptions. Identify the factor given by the specified "
                        "embedding, and decide linear "
                        "disjointness. For each factor determine the normal-closure "
                        "Galois group over F and "
                        "the subgroup of K fixed by that normal closure."
                        "\n(d) Compare the permutation characters of G on nonzero vectors "
                        "and on two-dimensional "
                        "subspaces. Prove that for every g in G the two permutations have "
                        "the same multiset of "
                        "cycle lengths, and explain why this does not contradict (b)."
                        "\n(e) If k = Q, explain precisely what (d) implies about "
                        "factorization types modulo "
                        "primes of minimal polynomials of integral primitive elements of "
                        "E and F, including "
                        "the exceptional primes that must be excluded. Do not infer that "
                        "the two polynomials "
                        "or fields are equal."
                    ),
                    "rubric": [
                        "Correct GL3/stabilizer/intersection structures, degrees, and "
                        "proof the generated subgroup is all G.",
                        "Proved nonconjugacy of point and plane stabilizers and its "
                        "implication for k-isomorphism of fixed fields.",
                        "Correct and justified tensor factors of degrees 3 and 4, "
                        "selected compositum, and both normal-closure kernels/groups.",
                        "General proof of equal permutation characters and cycle types, "
                        "distinguishing representation equivalence from conjugate "
                        "stabilizers.",
                        "Correct Frobenius/factorization conclusion for almost all primes "
                        "with ramification and index/discriminant exceptions made "
                        "explicit.",
                    ],
                    "reference": (
                        "|G|=(8-1)(8-2)(8-4)=168. H and K have order 24 and are (C2)^2 "
                        "semidirect GL(2,2), "
                        "hence S4. H intersect K is the upper triangular unipotent "
                        "subgroup of order 8 (D8). "
                        "Each of H,K has prime index 7, so is maximal; they are distinct "
                        "(K fixes no nonzero "
                        "vector). Thus <H,K>=G. E and F each have degree 7, their "
                        "intersection is k, and EF "
                        "has degree 21. Every conjugate of H fixes a nonzero vector, "
                        "while K has orbits of "
                        "sizes 3 (W minus zero) and 4 (outside W), hence K is not "
                        "conjugate to H. Any k-isomorphism "
                        "between intermediate fields extends to an element of G, so the "
                        "fields are not k-isomorphic. "
                        "Using E=k(alpha), E tensor_k F=F[x]/m_alpha(x); roots correspond "
                        "to G/H. K-orbits give "
                        "factors L^(K intersect gHg^-1) with F-degrees 3 and 4. The "
                        "incident vector v gives "
                        "the cubic factor EF. On W minus zero the action factors through "
                        "GL(2,2)=S3, kernel "
                        "U of order 4, so its normal closure is L^U with group S3. On the "
                        "four outside points "
                        "the action is the faithful affine group AGL(2,2)=S4, so the "
                        "quartic factor's normal "
                        "closure is L. The tensor product is not a field, hence E and F "
                        "are not linearly disjoint. "
                        "For any g, the number of fixed nonzero vectors is 2^dim "
                        "ker(g-I)-1. Planes are kernels "
                        "of unique nonzero covectors over F2. Fixed planes correspond to "
                        "fixed covectors of "
                        "g inverse transpose; nullities agree, so characters agree. Apply "
                        "this to every power "
                        "g^r; fixed-point counts of powers determine cycle counts by "
                        "divisor inversion. Equal "
                        "characters/cycle types do not identify the two G-sets. Over Q, "
                        "Frobenius cycle types "
                        "give equal residue/factor-degree multisets at primes unramified "
                        "in L and avoiding "
                        "the indices of the chosen primitive integral orders "
                        "(equivalently exclude the finitely "
                        "many relevant discriminant/index primes). No field isomorphism follows."
                    ),
                },
                {
                    "id": "s5_two_three",
                    "prompt": COMMON
                    + (
                        "Take G=S_5 in its natural action, H=Stab_G(1), and K the setwise "
                        "stabilizer of {1,2}. Set E=L^H and F=L^K."
                    )
                    + SN_REQUESTS,
                    "rubric": SN_RUBRIC,
                    "reference": (
                        "H=S4 order24, K=S2 x S3 order12. H intersect K is S3 on {3,4,5}, order6. "
                        "Degrees E=5,F=10,EF=20. H is maximal and K not contained in H, "
                        "so <H,K>=S5 and "
                        "E intersect F=k. K-orbits on roots/points are {1,2} and {3,4,5}; "
                        "tensor factor degrees "
                        "2,3. Selected factor is quadratic EF=L^(S3 on {3,4,5}); the other is "
                        "L^(S2 on {1,2} x S2 on two of {3,4,5}). The quadratic orbit "
                        "kernel is the S3 factor, "
                        "so that factor is already Galois C2. The cubic orbit kernel is "
                        "the S2 factor, so its "
                        "normal closure has group S3 and fixed field L^(S2 on {1,2}). "
                        "Their splitting fields "
                        "together generate L; Gal(L/F)=S2 x S3. Tensor has two factors, "
                        "not a field, so no "
                        "linear disjointness; intersection k is insufficient. K's orbit "
                        "sizes are distinct, "
                        "so its normalizer preserves each orbit and is K; Aut_k(F)=1. "
                        "Core_G(H)=Core_G(K)=1: "
                        "natural point action is faithful, and K contains no nontrivial "
                        "normal subgroup of S5. "
                        "Both normal closures over k are L. Gal(L/F) solvable but S5 "
                        "nonsolvable. The polynomial "
                        "is solvable by radicals over F, not k; F itself is not contained "
                        "in a radical extension "
                        "of k, since its normal closure is nonsolvable."
                    ),
                },
                {
                    "id": "s5_a4",
                    "prompt": COMMON
                    + (
                        "Take G=S_5 in its natural action, H=Stab_G(1), and K the "
                        "alternating group on {1,2,3,4}, fixing 5. Set E=L^H and F=L^K."
                    )
                    + SN_REQUESTS,
                    "rubric": SN_RUBRIC,
                    "reference": (
                        "H=S4 order24, K=A4 order12, H intersect K=C3 on {2,3,4}. Degrees "
                        "E=5,F=10,EF=40. "
                        "H maximal and K not contained in H imply generated group S5 and "
                        "intersection k. "
                        "K-orbits on roots are {1,2,3,4} and {5}; tensor is M x F with degrees4,1, "
                        "M=EF=L^(C3 on {2,3,4}). The quartic orbit action of A4 is "
                        "faithful, its stabilizer "
                        "core is trivial, and its normal closure is L with group A4. The "
                        "linear factor has "
                        "kernel all K, normal closure F, trivial group. The whole "
                        "polynomial splits in L over F. "
                        "Not linearly disjoint (tensor not a field, 40<50), although "
                        "intersection is k. The unique "
                        "point fixed by K is 5, so its normalizer lies in Stab(5)=S4; A4 "
                        "is normal there, hence "
                        "normalizer=S4 and Aut_k(F)=C2. The fixed field F equals "
                        "L^Stab(5) compositum L^A5, "
                        "a quadratic extension of a conjugate root field. Cores of H and "
                        "K in S5 are trivial, "
                        "so both normal closures over k are L. A4 is solvable but S5 is "
                        "not: polynomial solvable "
                        "by radicals over F but not k, and F/k is not itself contained in "
                        "a radical extension."
                    ),
                },
            ],
        },
        {
            "id": "sextic-base-change-retention-and-transfer",
            "tasks": [
                {
                    "id": "s6_three_three",
                    "prompt": COMMON
                    + (
                        "Take G=S_6 in its natural action, H=Stab_G(1), and K the setwise "
                        "stabilizer of the unordered partition {{1,2,3},{4,5,6}} (so "
                        "block interchange is allowed). Set E=L^H and F=L^K."
                    )
                    + SN_REQUESTS,
                    "rubric": SN_RUBRIC,
                    "reference": (
                        "H=S5 order120; K=S3 wreath C2=(S3 x S3) semidirect C2 order72. H "
                        "intersect K "
                        "fixes1 and cannot swap blocks, so is S2 x S3 order12. Degrees "
                        "E=6,F=10,EF=60. "
                        "Point stabilizer H is maximal in S6; K moves1, hence generated "
                        "group S6 and "
                        "intersection k. K is transitive on six roots, so E tensor_k F is "
                        "the field EF, "
                        "degree6 over F; the minimal polynomial remains irreducible. Thus "
                        "the fields ARE "
                        "linearly disjoint, with degree product60. K's natural six-point "
                        "action is faithful; "
                        "the core of H intersect K in K is trivial, so normal closure of "
                        "EF/F is L with "
                        "group K. Normalizer of K is K: its characteristic normal "
                        "3-subgroup C3 x C3 has "
                        "the two triples as its orbits, so any normalizer preserves their "
                        "unordered partition. "
                        "Aut_k(F)=1. H,K have trivial cores in S6, by faithfulness and "
                        "the normal-subgroup "
                        "structure of S6, so E and F have normal closure L over k. K is "
                        "solvable (extension "
                        "of solvable S3 x S3 by C2), S6 is not. Polynomial solvable by "
                        "radicals over F, not k; "
                        "F/k cannot be contained in a radical extension."
                    ),
                },
                {
                    "id": "s6_three_pairs",
                    "prompt": COMMON
                    + (
                        "Take G=S_6 in its natural action, H=Stab_G(1), and K the setwise "
                        "stabilizer of the unordered partition {{1,2},{3,4},{5,6}} (all "
                        "permutations of the three pairs are allowed). Set E=L^H and "
                        "F=L^K."
                    )
                    + SN_REQUESTS,
                    "rubric": SN_RUBRIC,
                    "reference": (
                        "H=S5 order120; K=C2 wreath S3=(C2)^3 semidirect S3 order48. Stabilizing1 "
                        "also fixes its partner2, leaving flips of the other two pairs "
                        "and their interchange; "
                        "H intersect K is D8 order8. Degrees E=6,F=15,EF=90. H maximal "
                        "and K not contained "
                        "in H imply generated group S6 and intersection k. K transitive "
                        "on six roots gives "
                        "one tensor factor EF of degree6/F, so linear disjointness and "
                        "irreducibility over F. "
                        "The natural action is faithful and the stabilizer's core is1, so "
                        "normal closure over "
                        "F is L with group K. Normalizer K is K: the single "
                        "transpositions in K are precisely "
                        "the three pair flips, and conjugation preserves them and their "
                        "pair partition. "
                        "Aut_k(F)=1. Cores in S6 of H,K are1 (use normal subgroups of "
                        "S6), so both normal "
                        "closures over k are L. K solvable as extension of C2^3 by S3. "
                        "Polynomial solvable "
                        "by radicals over F but not over k; F not contained in a radical "
                        "extension of k "
                        "because its normal closure has group S6."
                    ),
                },
                {
                    "id": "s6_four_two",
                    "prompt": COMMON
                    + (
                        "Take G=S_6 in its natural action, H=Stab_G(1), and K the setwise "
                        "stabilizer of {1,2,3,4}. Set E=L^H and F=L^K."
                    )
                    + SN_REQUESTS,
                    "rubric": SN_RUBRIC,
                    "reference": (
                        "H=S5 order120; K=S4 x S2 order48; H intersect K=S3 on {2,3,4} x "
                        "S2 on {5,6}, "
                        "order12. Degrees E=6,F=15,EF=60. H maximal and K moves1 imply "
                        "generated S6 and "
                        "intersection k. K-orbits are the four-point and two-point "
                        "blocks, so tensor factors "
                        "have F-degrees4,2. The selected factor is quartic EF. Its orbit "
                        "kernel is S2 on {5,6}, "
                        "so its normal closure is L^(S2 on {5,6}) with group S4; NOT all "
                        "L. The quadratic "
                        "factor is L^(S4 on {1,2,3,4}), already Galois C2, with kernel "
                        "S4. The whole polynomial "
                        "has splitting field L over F and group S4 x S2, since both "
                        "kernels intersect trivially. "
                        "No linear disjointness (two tensor factors; 60<90). Distinct "
                        "orbit sizes force "
                        "normalizer K=K, hence Aut_k(F)=1. Trivial cores of H,K in S6 "
                        "imply normal closures "
                        "of E and F over k are L. K solvable, S6 not, hence polynomial "
                        "solvable by radicals "
                        "over F but not k; F/k itself is not contained in a radical extension."
                    ),
                },
            ],
        },
    ],
}
