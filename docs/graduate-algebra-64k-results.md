# Graduate algebra through the native Padawan loop: 64K results

The episode completed with a modest improvement on the same problem after feedback,
but no successful transfer or admitted lesson. The cold answer scored **4/10** and
the feedback-assisted revision **6/10**. On distinct fresh problems, treatment scored
**4/10** and the no-intervention control **7/10**. These are fixed-rubric reviews by
the same Codex session that authored the teaching, not independently verified proofs.

All four student requests actually sent a **65,536-token output ceiling**, and all
four ended naturally with provider status `completed` and no incomplete reason.
The earlier 8K attempts are excluded from these results.

| Phase | Problem | Score | Output tokens | Completion |
|---|---|---:|---:|---|
| Cold | GL(3, F₂), point and plane stabilizers | 4/10 | 51,591 | Natural |
| Revision with feedback | Same GL(3, F₂) problem | 6/10 | 53,239 | Natural |
| Treatment transfer | S₅, K = A₄ fixing one point | 4/10 | 47,937 | Natural |
| Control transfer | S₅, K = S₂ × S₃ | 7/10 | 58,457 | Natural |

The student generated **211,224 output tokens**. Launch through cleanup took **95.52
minutes**, within the 4.5-hour pilot limit. The model was the cached
`mlx-community/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-4bit`, with one sequence,
a 131,072-token context, temperature 0.7, top-p 0.95, and thinking disabled.
The response receipts confirm the output allowance and template setting for every
call. No additional teacher API or paid inference service was used.

## What changed in the proofs

The revision recovered the correct field degrees, a usable nonconjugacy argument,
and the 3+4 tensor-factor and S₃/S₄ normal-closure answers. It still explicitly
confused a compositum with an intersection, left the tensor correspondence
insufficiently justified, and assumed the permutation-character equality that it
was supposed to prove. This was partial uptake of direct feedback on the same task.

The treatment transfer recovered the correct 1+4 tensor factors, but assigned the
compositum degree 120 even though the product of the two field degrees was only 50.
It also assigned V₄ as the kernel of the faithful A₄ action and wrote A₄/V₄ ≅ S₃.
The correct kernel is trivial and the normal-closure group is A₄; A₄/V₄ is C₃.
These are substantive failures to apply the subgroup and field correspondences.

The control got more of the field arithmetic and normalizer argument right. It
still gave S₃ as the Galois group of a quadratic extension, repeatedly confused the
original polynomial with the minimal polynomial over the larger field, and made
invalid claims supporting radical containment. Neither fresh proof earned full
credit.

## Native state and memory outcome

The run traversed all ten native workflow transitions through `COMPLETE`, with four
student calls, one file-authored teaching intervention, and four proof reviews.
Treatment and control used separate branches from the same empty-memory state.
The treatment received the reviewed lesson and cold-task repair; control received
no intervention. Neither transfer request included the revision proof or its grade.
The revision was a fresh solve of the same problem with feedback, rather than an
edit supplied with the full cold answer.

Padawan recorded `revision_gain = +0.2` and `immediate_transfer_gain = -0.3`. Its
operational label was `harmful_intervention`, because treatment scored below
control. **That label does not establish that feedback caused harm:** the transfer
tasks differ, their difficulty is uncalibrated, and this is one sampled pair.

The memory gate rejected consolidation with reason **“no successful unseen
transfer.”** There are zero lesson versions and the final canonical state follows
the control branch with an empty lesson-reference list. It retains the episode's
score summary. No later retrieval or retention test was run, and model parameters
were not updated.

## Validation and retained evidence

Before launch, the relevant tests passed: **10 tests**, Ruff on the eight new Python
files, and mypy on the five new Padawan modules. The native workflow regression test
uses the actual local client with a mock HTTP transport and checks all four sent
64K requests. Exact finite-group checks passed for all six prepared problems;
these validate finite facts, not the general proofs. The prepared source identity
still matched at completion.

All 1,123 host samples reported normal thermals; swap stayed at 786,432 bytes.
Minimum reported free memory was 53%, returning to 89% after shutdown. Cleanup and
a separate postflight check confirmed every recorded process group had exited and
the loopback model port refused connections.

The [run directory](../runs/padawan-graduate-algebra-64k-20260907/) contains:

- [Native results](../runs/padawan-graduate-algebra-64k-20260907/results.json),
  [receipt audit](../runs/padawan-graduate-algebra-64k-20260907/receipt-audit.json), and
  [postflight audit](../runs/padawan-graduate-algebra-64k-20260907/postflight-audit.json).
- Full public answers: [cold](../runs/padawan-graduate-algebra-64k-20260907/public-proofs/cold.md),
  [revision](../runs/padawan-graduate-algebra-64k-20260907/public-proofs/revision.md),
  [treatment](../runs/padawan-graduate-algebra-64k-20260907/public-proofs/transfer-treatment.md),
  and [control](../runs/padawan-graduate-algebra-64k-20260907/public-proofs/transfer-control.md).
- [Digest-bound proof reviews](../runs/padawan-graduate-algebra-64k-20260907/proof-reviews/),
  [authored teaching](../runs/padawan-graduate-algebra-64k-20260907/teaching/), and the
  [problem set](../runs/padawan-graduate-algebra-64k-20260907/problem-set.md).

The [pilot design](graduate-algebra-local-pilot.md) records the task construction,
runtime boundaries, and implementation. This single episode supports a descriptive
diagnostic of immediate feedback use. It does not establish a causal teaching
effect, durable skill acquisition, broad graduate algebra competence, or scientific
PPRL performance. The teacher and grader were the same session, the reviews were
not fully blind, and no proof kernel or independent expert adjudication was used.
