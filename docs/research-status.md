# Research status and publication scope

Status: 2026-09-24 documentation follow-up, with source checked at
`b8d57436268f7c19b463948ea7ae751e66849e72`, the release-audit snapshot. The research objective remains
improving smaller open models and project-scale behavior through teaching and persistent processes.
The current implementation provides in-context teaching/transfer experiments and persistent
research, evidence, and data-generation infrastructure.

## Evidence and current limits

The [graduate-algebra pilot](graduate-algebra-64k-results.md) had mixed preliminary results:

| Cold answer | Feedback-assisted revision | Fresh treatment | Fresh control | Admitted lessons | Parameter updates |
| --- | --- | --- | --- | --- | --- |
| 4/10 | 6/10 | 4/10 | 7/10 | 0 | 0 |

The transfer tasks differed, their difficulty was uncalibrated, and there was only one
treatment/control pair. The same session authored the teaching and reviewed the proofs. These
scores support a descriptive account of feedback use, not reliable transfer, a causal benefit or
harm, statistical significance, or retained model learning. The original result and its negative
transfer outcome remain unchanged.

There is no implemented parameter-update backend: the
[backend refuses parameter updates](../padawan/updates/backends.py#L150), and
[PPRL projection receipts](../padawan/training/process_projection_contracts.py#L180) enforce
`parameter_training_ready=false`. [Scripted continuity fixtures](pprl-scripted-continuity-boundary.md)
establish bounded committed-state continuity through worker replacement. They do not demonstrate
optimization, a persistence advantage, or scientific learning. The
[PPRL research program](pprl-epsilon-charity-program.md) retains its distribution-level objective.

**Teacher exclusion is limited.** Recorded direct influence is filtered, but inherited retrieved
teacher lessons can enter later prompts and explicitly admitted training inputs. The
[training-product boundary](training-products.md#teacher-influence-and-exported-inputs) distinguishes
the audit's reproduced PROCESS input counterexample from conditional SFT/preference source
inferences and untested loss targets. Bundle verification did not establish teacher-free inputs.

The separate 2026-09-24 release audit reported eight passing offline software tests, the synthetic
PROCESS counterexample, and an independent default-gated fixture with zero training rows. No model
was trained. Those audit reports and reproduction details are retained outside this source tree;
their tests and the historical pilot were not rerun for this documentation-only follow-up. Current
source was rechecked. Historical plans/results remain dated evidence; broader exclusion wording
in older plans is superseded by the current training-product limitation above.

## Publication scope and pending owner decision

Authenticated GitHub metadata confirmed [TheVerdman/Padawan](https://github.com/TheVerdman/Padawan)
was public on 2026-09-24, with remote `main` matching the source snapshot above. The original audit's
private-visibility snapshot is historical and is preserved unchanged.

The tracked
[instrumented probe report](../reports/live/2026-08-11-padawan-retry-2-live-run/vertex-controller/instrumented-probe.json)
contains six encoded request echoes in error fields at lines 5, 20, 35, 50, 65, and 80. The release
audit classified four as fixed probes and two as cold algebra prompt echoes with empty teacher
intervention/memory, plus request metadata. It found no verified credentials or teacher responses
in these six echoes. This follow-up confirmed their presence without decoding or reproducing them.

**Pending owner decision:** whether these specific own-prompt/request-metadata bytes are intended
to remain public, including their presence in Git history. Public repository visibility does not
resolve that decision. A HEAD-only edit cannot erase historical copies. This follow-up leaves the
report and history unchanged; the coordinating review will collect the owner's decision.

Ignored raw runs, teacher traces, training products, and task assets remain outside this patch and
publication decision. Links into ignored `runs/` or `artifacts/` locations in historical reports
refer to local evidence and need not resolve in a source clone. No such material is added here.
Licenses, notices, attribution, and historical result/hash records are unchanged.

## Separate future work

- Track inherited/derived teacher influence through retrieval, prompts, and compiler lanes, with
  regressions for later cold/control inputs and preserved default eligibility gates. Validate actual
  downstream inputs and loss targets before claiming teacher-free training.
- If separately requested, prepare a small reviewed public evidence bundle and label local-only
  evidence links without adding raw runs or changing historical results.

Neither item is implemented by this documentation follow-up or made a prerequisite for outreach.
