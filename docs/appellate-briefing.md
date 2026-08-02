# Closed-Record Appellate Briefing

Padawan's first legal package is `legal.appellate.fourth_circuit@1.0.0`. It is a
corpus-and-verifier package for synthetic civil appeals from final summary judgment. It does not
file briefs, access live client material, provide legal advice, call a model, or claim an autonomous
developmental workflow.

## Court pack

The immutable `fourth-circuit-summary-judgment-2026-03-23` pack pins:

- the United States Court of Appeals for the Fourth Circuit and an appellant's principal brief;
- a December 1, 2025 national-rule effective date and the March 23, 2026 edition date shown on the
  Fourth Circuit's published federal/local rule pages;
- the task's governing-law cutoff, source URLs, admitted text digests, and pack digest;
- a closed authority set comprising *Tolan v. Cotton*, *Scott v. Harris*, and the published Fourth
  Circuit decision in *Brown v. Wal-Mart Stores East, LP*;
- a separately content-addressed, project-authored synthetic joint appendix for every scenario;
- a coverage declaration that is task-complete but not globally complete; and
- no dependable citator source.

The rule sources are the Fourth Circuit's official [Rule 28 page](https://www.ca4.uscourts.gov/rules/rule28.html)
and [Rule 32 page](https://www.ca4.uscourts.gov/rules/Rule32.html). The admitted opinions come from
the Supreme Court's official bound [volume 572](https://www.supremecourt.gov/opinions/boundvolumes/572bv.pdf),
[volume 550](https://www.supremecourt.gov/opinions/boundvolumes/550bv.pdf), and the Fourth Circuit's
official [Brown opinion](https://www.ca4.uscourts.gov/opinions/241102.P.pdf). Source snapshots are
attributed and digested. The jurisdictional statutes come from GovInfo's official, static
[2024 United States Code, Title 28](https://www.govinfo.gov/content/pkg/USCODE-2024-title28/pdf/USCODE-2024-title28.pdf).
Padawan stores compact task-scoped rule renderings and verbatim, pinpointed opinion passages; it is
not a comprehensive research database. Rule renderings are marked non-verbatim and cannot satisfy
the quotation-fidelity gate.

Absence from this closed set means `unresolved in the declared corpus`. It does not prove that an
authority was fabricated. The package never upgrades an unresolved source to a hallucination label
without independent evidence.

## Output contract

An `AppellateSubmission` contains the ordered brief and its machine-readable evidence map:

- structural sections contain no substantive claims;
- every substantive paragraph is exactly the ordered join of its mapped claims;
- each claim occurs in exactly one paragraph;
- every citation is attached to one claim and identifies an exact rule, opinion passage, or joint
  appendix page;
- controlled record assertions reproduce one admitted synthetic record fact and cite a page that
  supports it; and
- quoted spans must be declared on a citation and match a verbatim source admitted under that
  locator; non-verbatim rule renderings cannot be quoted as source text.

This constrained v1 representation is intentional. It makes claim-map completeness and factual
provenance deterministic. More flexible paraphrase verification can be added later as semantic
evidence; it must not silently weaken the record hard gate.

## Verification layers

The verifier emits separate results rather than a single legal-correctness score.

The noncompensable hard gates cover court-pack integrity, task binding, section/order/word-count and
certificate rules, claim-map integrity, record resolution, authority resolution, quotation
fidelity, and sealed/evaluator leakage. A failure in any one prevents scalar reward and target
training eligibility.

Proposition support, applicability, adverse-authority treatment, and issue/preservation/remedy
coverage consume an optional `AppellateSemanticAssessment`. A human or governed model assessment
must bind the exact submission digest and repeat the exact proposition while citing only passages,
rules, and record facts already attached to that claim. Missing, mismatched, or incomplete evidence
leaves the component `unknown`; a teacher's confidence cannot override a hard-gate failure.

Currentness is independent of citation existence, quotation fidelity, support, and applicability.
The first pack declares no dependable citator. Its currentness result and reward observation are
therefore always `unknown`, even when every other result is verified. The reward policy uses the
explicit `omit` action for that missing component; it is never converted to zero and never reported
as verified good-law status.

## Transfer families and training use

The deterministic generator creates matched siblings across three declared families:

- `ambiguous_video` combines conflicting testimony with partially obstructed footage;
- `conclusive_video` changes the video evidence to force explicit engagement with *Scott*; and
- `preservation_transfer` changes how the summary-judgment objection was preserved.

New generation is allowed only in curriculum, rotating-shadow, or sealed-anchor pools. Synthetic
records and task compilations are admitted for internal research with official-source attribution;
redistribution is not authorized by the package manifest. Baseline and teacher submissions are
retained as evidence but are evaluation-only. A target curriculum brief can enter RLVR/SFT only
after every hard gate passes and the semantic components are adjudicated as verified. Adjudicated
failures may support preference/process products; unadjudicated evidence remains evaluation-only.
Brief episodes are never automatically reclassified as continued-pretraining sources.

## CLI

Generate and register matched tasks after database migration:

```text
padawan corpus generate appellate \
  --pool curriculum \
  --groups-per-family 1 \
  --siblings-per-group 2 \
  --seed 20260802
```

Verify a captured submission without semantic adjudication:

```text
padawan verify appellate \
  --scenario-file scenario.json \
  --submission-file submission.json
```

Add `--semantic-assessment-file assessment.json` only when the assessment satisfies the typed,
digest-bound evidence contract. The CLI will still report currentness as `unknown` for this pack.
