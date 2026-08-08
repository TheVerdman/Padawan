# Evidence hierarchy

Padawan resolves conflicting claims in this order:

1. deterministic environment evidence (Lean kernel, SymPy, executable tests, formal or tool results);
2. observable actions and tool observations;
3. private generated reasoning, only when the runtime exposes it;
4. public explanation;
5. teacher interpretation.

The hierarchy is an authority rule, not a weighted vote. A high-confidence teacher cannot override
a symbolic counterexample. `AlgebraGrader` parses the public derivation, checks each feasible
transformation with SymPy, and records the first objectively invalid step. The comment validator
then requires the teacher to cite real evidence and rejects contradictory localization, unsupported
certainty, malformed repairs, and mode-specific leakage.

For Lean mathematics, the pinned kernel's final exit status is the correctness gate. A proof-policy
rejection and a kernel proof rejection are both hard failures but remain distinguishable; a missing
toolchain, dependency drift, sandbox failure, timeout, signal, or output overflow is infrastructure
failure and cannot be scored as student error. Tactic-state or teacher judgments may later provide
process components, but cannot compensate for a final kernel rejection.

For appellate briefing, court-pack, rule, record, citation, quotation, and leakage checks precede
claim-scoped semantic adjudication. The adjudicator can use only evidence already attached to a
claim and cannot establish authority currentness. Currentness requires an independently admitted,
fresh citator result; negative treatment rejects it, while missing, stale, ambiguous, or unlicensed
evidence stays unknown. A student, teacher, or adjudicator assertion is not citator evidence.

Evidence records are immutable and content-backed. Provider bytes and rendered inputs remain raw
artifacts; normalized grades and interventions refer to them but do not replace them. Provenance
events commit hashes of the normalized event payload and link events in order. Neither a stored
provider assertion nor a manually supplied validation boolean counts as verification.

Model judgment remains useful where deterministic evidence ends: diagnosing a misconception,
proposing a general principle, or choosing a pedagogical presentation. Its downstream value is
measured by revision, unseen transfer, delayed retention when available, cost, latency, and harm—not
by the teacher's self-confidence or a preference-model score.

If evidence is malformed or authorities conflict, Padawan either retries the teacher with validator
feedback, records a failed episode, or closes the run as `REVIEW_REQUIRED`. It never averages
objective truth with model opinion.
