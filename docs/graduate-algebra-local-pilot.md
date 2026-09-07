# Local Nemotron through the Padawan teaching loop

This exploratory pilot asks whether reviewed feedback changes Nemotron's public
graduate algebra proofs, whether a change transfers to a fresh problem, and whether
the native memory gate admits a reusable lesson. It changes context and lesson
memory; it does not train model weights or run a PPRL swarm.

The first 64K episode is complete. See the
[results and retained evidence](graduate-algebra-64k-results.md): cold 4/10,
revision 6/10, treatment transfer 4/10, control transfer 7/10, and no admitted lesson.

## Tasks and scoring

The six original problems are in
[`graduate_algebra_suite.py`](../padawan/domains/graduate_algebra_suite.py). They
combine finite groups, nonnormal Galois extensions, tensor products of fields,
subgroup cores, normalizers, and radical solvability. The first cold task also
requires proving that degree-seven nonisomorphic fields have equal permutation
characters and almost-everywhere equal polynomial factorization types. This is
graduate abstract algebra, with substantial proof obligations rather than elementary
symbol manipulation. The level is a curricular judgment, without empirical or
independent expert difficulty calibration. The subject scope is consistent with
[Stanford's graduate Galois theory course](https://math.stanford.edu/~akshay/math210B.html);
the problems are project-authored, not copied examination questions.

The frozen corpus contains two blocks. Each has a cold proof, authored feedback,
a revision, and separate fresh treatment/control transfer tasks. The current 64K
attempt runs the first block only. A future second block could start from the native
canonical state and probe a lesson admitted by the first block.
Transfer pairs match the ambient group and subgroup order but use different subgroup
actions. Their difficulty has not been calibrated, so a score difference can also
reflect unequal task difficulty and sampling noise.

Each proof has five preregistered criteria worth two points each: zero for missing
or wrong work, one for substantial correct work with a material gap, and two for
correct, adequately justified work. Full success requires 10/10 and no significant
errors. The rubric and reference solution remain outside student requests. The
current Codex session reviews public proofs and authors feedback; grading is neither
independent nor fully blind, and is not kernel verification. Exact finite-group
enumerations check numerical and action facts underlying all six references, without
verifying the general mathematical proofs.

## Native implementation

The runner uses `DomainDevelopmentalWorkflowHandler`, registered corpus and research
identities, the state store, experiment branches, teacher evidence checks, and lesson
memory. The existing transfer and consolidation gates decide whether a lesson is
admitted. A treatment score improvement alone does not bypass the full-success gate.

The local adapter calls the loopback Responses endpoint with public proof text,
thinking disabled, temperature 0.7, top-p 0.95, a fixed task seed, and at most 65,536
output tokens. No grammar decoder is requested: the pinned Metal SSM path does not
support it. Tool use and provider continuation are disabled. This harness differs
from the earlier compiler run and does not support a model-only comparison to it.

The local adapter's native `generate()` method sends the exact prepared request;
this is checked through a mock HTTP transport inside the full native workflow test.
The Qwen3 think-tag parser is used instead of the Nemotron parser's optional
reasoning-to-content fallback, so a reasoning-only response remains outside public
proof grading. Empty public answers stop the episode before mathematical grading.

Proof reviews and teaching files bind their exact requests by digest and record the
author and evidence basis. Feedback uses the cold public proof and its grade, with
no future task-specific solutions in the reusable lesson. Raw traffic and private
provider records remain researcher-only; the authored teacher rejects private
reasoning input. No additional teacher API or paid inference service is used.

## Runtime limits and retained evidence

`scripts/run_padawan_local.py prepare` requires a fresh run directory and an explicit
local runtime manifest. It verifies the pinned runtime and rehashes all cached model
files against the reviewed capsule, then records source, corpus, model, and sampling
identities. It imports no previous campaign tasks or pending model calls.

The current live attempt allows one episode, four student calls, and 4.5 hours
including server startup and review waits. Student calls time out at 3,600 seconds;
each authored review wait is limited to 300 seconds. There are no automatic model
call retries or worker replay. A runtime directory cannot be launched a second time.
These bounds replace the initial short pilot's limits following the user's explicit
request to test a 64K output ceiling. All four student phases receive the same
65,536-token allowance and may finish naturally before using it.

The server binds only `127.0.0.1:18768`, uses one sequence and a 131,072-token context,
and runs from the existing cached Nemotron checkpoint. The launch verifies listener
ownership, served model identity, Metal configuration, and a live independent host
watchdog. The watchdog stops on the first observed thermal or memory pressure,
swap growth, power loss, competing model, operator stop, or runtime deadline.
The parent stops if the watchdog exits. Cleanup targets only recorded owned process
groups and retains a postflight host observation.

Inputs, offline validation, fixed rubrics, public proofs and reviews, reviewed
teaching, native episodes, lesson admissions, host observations, and cleanup are
retained under the fresh run directory. Missing or interrupted outcomes remain
missing. Two blocks can provide a descriptive teaching diagnostic; they cannot
establish a causal teaching effect, durable skill acquisition, general competence,
or scientific PPRL performance.

## First live attempt and correction

The first attempt (`runs/padawan-graduate-algebra-20260907-v2`) stopped after one
151-second call consumed 8,192 tokens entirely in the provider reasoning channel.
Its actual HTTP request omitted `chat_template_kwargs`: native `generate()` had
bypassed the newly customized `prepare_generation()` path. No mathematical grade,
teaching intervention, or lesson was admitted. This is a transport-integration
failure, not evidence of graduate algebra ability.

A regression test using the actual native client and a mock HTTP transport
reproduced the missing setting before the fix. The corrected path sends the prepared
body. The second attempt (`runs/padawan-graduate-algebra-20260907-v3`) confirmed that
the actual request carried the thinking setting and returned public proof text.
It exhausted 8,192 tokens during part (b), leaving parts (c)-(e) absent. The fixed
rubric assigned 1/10 to that truncated response; no teaching intervention or lesson
was admitted before the user's ceiling correction stopped this attempt.

The 64K attempt (`runs/padawan-graduate-algebra-64k-20260907`) begins again with a
fresh cold call at the larger ceiling. The fixed tasks, rubrics, sampling parameters,
and reviewed-teacher mechanism are retained. Earlier 8K results are token-limited
diagnostics and are excluded from this comparison. The larger-ceiling episode can
test immediate revision, transfer, and lesson admission, but will not measure
subsequent lesson retrieval on the unused second block.
