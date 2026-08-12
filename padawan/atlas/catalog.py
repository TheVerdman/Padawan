# ruff: noqa: E501
"""Primary-source claims and fail-closed dataset governance for Capability Atlas v0.

The records in this module are catalog facts, not evaluation results.  In particular, the
Thinking Machines scores are retained as priors for campaign design and can never be surfaced as
Padawan observations without separately attributable trial evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from padawan.atlas.contracts import (
    AccessClassification,
    BenchmarkClaim,
    ClaimSourceKind,
    ContaminationClassification,
    DatasetGovernance,
    EvaluationClass,
    ExtractionProvenance,
    RedistributionClassification,
    content_id,
)
from padawan.models.contracts import (
    DistributionScope,
    RightsBasis,
    RightsReviewStatus,
    RightsUse,
    SourceRights,
    project_authored_internal_rights,
)
from padawan.models.hashing import sha256_digest

CATALOG_TIME = datetime(2026, 8, 12, tzinfo=UTC)
INKLING_PREVIEW_URL = "https://thinkingmachines.ai/news/introducing-inkling/"
INKLING_RELEASE_URL = "https://thinkingmachines.ai/news/inkling-small/"
INKLING_MODEL_CARD_URL = "https://huggingface.co/thinkingmachines/Inkling-Small"
INKLING_MODEL_CARD_REVISION = "b2d4f225a02032c5d154bff748ab5a00c5ca26e4"


@dataclass(frozen=True)
class _ClaimRow:
    benchmark_id: str
    benchmark_version: str
    split: str
    metric_id: str
    preview_score: float
    release_score: float
    benchmark_caveat: str
    tool_caveat: str


_CLAIM_ROWS = (
    _ClaimRow(
        "gpqa-diamond",
        "source-declared-2026-07",
        "diamond",
        "accuracy",
        88.3,
        89.5,
        "The publication does not bind a content-addressed GPQA item manifest or grader revision.",
        "No tools are reported for GPQA Diamond.",
    ),
    _ClaimRow(
        "aime-2026",
        "2026",
        "tests-1-and-2",
        "average_accuracy",
        95.1,
        95.5,
        "The release reports an average across AIME 2026 tests 1 and 2; item and scorer manifests are not published in the table.",
        "No tools are reported for AIME 2026.",
    ),
    _ClaimRow(
        "swe-bench-verified",
        "source-declared-2026-07",
        "verified",
        "resolved_rate",
        77.4,
        80.2,
        "Repository revisions, container images, patch parser, and retry policy are not content-addressed in the score table.",
        "The release labels the tool surface as Bash only.",
    ),
    _ClaimRow(
        "humanitys-last-exam",
        "source-declared-2026-07",
        "full",
        "accuracy",
        46.6,
        47.8,
        "The gated HLE dataset, exact split revision, grader, and item manifest are not published with the score table.",
        "The score is explicitly HLE with tools; the tool implementation and per-item budget are not fully bound.",
    ),
    _ClaimRow(
        "ifbench",
        "source-declared-2026-07",
        "source-default",
        "accuracy",
        83.4,
        82.2,
        "The score table does not bind the IFBench data revision, evaluator revision, or provider-output provenance.",
        "No tools are reported for IFBench.",
    ),
    _ClaimRow(
        "mmmu-pro",
        "source-declared-2026-07",
        "standard-10",
        "accuracy",
        73.1,
        74.0,
        "The source labels MMMU-Pro Standard 10 but does not publish a content-addressed item/media or judge manifest.",
        "No tools are reported for MMMU-Pro Standard 10.",
    ),
    _ClaimRow(
        "mmau",
        "source-declared-2026-07",
        "source-default",
        "accuracy",
        77.5,
        77.0,
        "The table does not bind an MMAU data revision, private-answer evaluator, media manifest, or trial policy.",
        "No tools are reported for MMAU.",
    ),
)


def source_claims() -> tuple[BenchmarkClaim, ...]:
    """Return immutable upstream score claims, separated by preview and released model.

    The July 15 preview and July 30 released model are distinct revisions.  The latter therefore
    does not supersede the former as a correction even when the benchmark name is the same.
    """

    claims: list[BenchmarkClaim] = []
    for release in (False, True):
        for row in _CLAIM_ROWS:
            score = row.release_score if release else row.preview_score
            source_url = INKLING_RELEASE_URL if release else INKLING_PREVIEW_URL
            published_at = datetime(2026, 7, 30 if release else 15, tzinfo=UTC)
            publication_title = (
                "Inkling-Small: our first open-weight model" if release else "Introducing Inkling"
            )
            source_revision = (
                "thinking-machines:inkling-small:2026-07-30"
                if release
                else "thinking-machines:introducing-inkling:2026-07-15"
            )
            model_revision = (
                "released-2026-07-30-after-additional-post-training-and-coding-rl"
                if release
                else "preview-2026-07-15-unreleased"
            )
            harness = [
                "Vendor-reported score; no Padawan ResearchExecutionManifest exists for it.",
                row.benchmark_caveat,
                "The publication reports effort=0.99; it does not establish equivalence to Padawan's standardized or optimized harness.",
            ]
            context = [
                "Exact prompt templates, chat formatting, context utilization, continuation semantics, and compaction policy are not fully disclosed.",
            ]
            budget = [
                "Per-item input, output, wall-time, retry, and cost limits are not fully disclosed.",
            ]
            if release:
                harness.append("The release reports temperature=1 for the benchmark table.")
                context.append(
                    "The release states a 256K maximum sequence length for coding evaluations; this is not evidence of 256K reasoning quality."
                )
                if row.benchmark_id in {"gpqa-diamond", "humanitys-last-exam", "mmmu-pro"}:
                    harness.append(
                        "The release says this evaluation was provided by Artificial Analysis; Padawan has not reproduced that external harness."
                    )
            else:
                harness.append(
                    "The preview table predates the released checkpoint and its additional post-training plus two weeks of coding RL."
                )
            identity = {
                "source_url": source_url,
                "source_revision": source_revision,
                "model_revision": model_revision,
                "benchmark_id": row.benchmark_id,
                "benchmark_version": row.benchmark_version,
                "split": row.split,
                "metric_id": row.metric_id,
                "score": score,
                "effort": 0.99,
            }
            claims.append(
                BenchmarkClaim(
                    claim_id=content_id("source-claim", identity),
                    source_kind=ClaimSourceKind.VENDOR,
                    source_url=source_url,
                    publication_title=publication_title,
                    source_revision=source_revision,
                    source_published_at=published_at,
                    model_id="thinkingmachines/Inkling-Small",
                    model_revision=model_revision,
                    benchmark_id=row.benchmark_id,
                    benchmark_version=row.benchmark_version,
                    split=row.split,
                    metric_id=row.metric_id,
                    reported_value=score,
                    reported_unit="percent",
                    effort=0.99,
                    harness_assumptions=tuple(harness),
                    tool_assumptions=(row.tool_caveat,),
                    context_assumptions=tuple(context),
                    budget_assumptions=tuple(budget),
                    contamination_caveats=(
                        "No Padawan exposure audit is available for the vendor training corpus.",
                        "The source score is a prior only and cannot be promoted into a local observation.",
                    ),
                    extraction=ExtractionProvenance(
                        method="manual-primary-source-table-transcription-with-double-entry",
                        extracted_by="padawan.atlas.catalog.v0",
                        extracted_at=CATALOG_TIME,
                        source_excerpt_digest=sha256_digest(identity),
                        notes=(
                            "Score and table label were transcribed from the official Thinking Machines publication.",
                            f"The released BF16 model card is {INKLING_MODEL_CARD_URL}; model-card metadata is not local benchmark evidence.",
                            f"The pre-community-metadata model-card revision inspected for v0 is {INKLING_MODEL_CARD_REVISION}.",
                        ),
                    ),
                    created_at=CATALOG_TIME,
                )
            )
    return tuple(claims)


def _rights(
    *,
    rights_id: str,
    basis: RightsBasis,
    detail: str,
    review_status: RightsReviewStatus,
    license_id: str | None = None,
    terms_uri: str | None = None,
    uses: tuple[RightsUse, ...] = (RightsUse.EVALUATION, RightsUse.INTERNAL_RESEARCH),
    distribution_scope: DistributionScope = DistributionScope.INTERNAL_ONLY,
    restrictions: tuple[str, ...] = (),
) -> SourceRights:
    canonical_uses = tuple(sorted(uses, key=lambda value: value.value))
    canonical_restrictions = tuple(sorted(restrictions))
    reviewed = review_status in {RightsReviewStatus.CONFIRMED, RightsReviewStatus.PROHIBITED}
    return SourceRights(
        rights_id=rights_id,
        version="1.0.0",
        basis=basis,
        basis_detail=detail,
        permitted_uses=() if review_status == RightsReviewStatus.PROHIBITED else canonical_uses,
        distribution_scope=distribution_scope,
        review_status=review_status,
        license_id=license_id,
        terms_uri=terms_uri,
        terms_digest=sha256_digest({"uri": terms_uri, "reviewed": "2026-08-12"})
        if terms_uri is not None
        else None,
        restrictions=canonical_restrictions,
        reviewed_by="padawan.atlas.primary-source-review" if reviewed else None,
        reviewed_at=CATALOG_TIME if reviewed else None,
    )


def _governance(
    *,
    benchmark_id: str,
    benchmark_version: str,
    dataset_revision: str,
    source_url: str,
    rights: SourceRights,
    access: AccessClassification,
    redistribution: RedistributionClassification,
    contamination: ContaminationClassification,
    evaluation_class: EvaluationClass,
    license_expression: str | None = None,
    requirements: tuple[str, ...] = (),
    redistribution_notes: tuple[str, ...] = (),
    contamination_evidence: tuple[str, ...] = (),
    sealed_handling: tuple[str, ...] = (),
) -> DatasetGovernance:
    identity = {
        "benchmark_id": benchmark_id,
        "benchmark_version": benchmark_version,
        "dataset_revision": dataset_revision,
        "source_url": source_url,
        "rights_id": rights.rights_id,
        "access": access,
        "redistribution": redistribution,
        "evaluation_class": evaluation_class,
    }
    return DatasetGovernance(
        governance_id=content_id("dataset-governance", identity),
        benchmark_id=benchmark_id,
        benchmark_version=benchmark_version,
        dataset_revision=dataset_revision,
        source_url=source_url,
        rights=rights,
        access=access,
        redistribution=redistribution,
        contamination=contamination,
        evaluation_class=evaluation_class,
        license_expression=license_expression,
        access_requirements=requirements,
        redistribution_notes=redistribution_notes,
        contamination_evidence=contamination_evidence,
        sealed_handling=sealed_handling,
        reviewed_at=CATALOG_TIME,
    )


def dataset_governance_records() -> tuple[DatasetGovernance, ...]:
    """Return local and public benchmark governance records.

    A public URL or repository license never implies that benchmark content is installed.  All
    external entries remain non-executable until an operator freezes the exact permitted content
    and completes any item/media-specific rights review.
    """

    local_rights = project_authored_internal_rights(reviewed_at=CATALOG_TIME)
    appellate_rights = _rights(
        rights_id="padawan.appellate.synthetic-federal.internal",
        basis=RightsBasis.PROJECT_AUTHORED,
        detail=(
            "Project-authored synthetic records and task compilations with attributed official "
            "federal excerpts and task-scoped rule renderings for internal research."
        ),
        review_status=RightsReviewStatus.CONFIRMED,
        restrictions=(
            "internal research only",
            "official-source attribution and source digests must be retained",
        ),
    )
    local_specs = (
        (
            "padawan-algebra",
            "padawan-algebra-v1",
            "https://github.com/andrewverdiramo/PADAWAN",
            local_rights,
        ),
        (
            "padawan-temporal-grounding",
            "padawan-temporal-grounding-v1",
            "https://github.com/andrewverdiramo/PADAWAN",
            local_rights,
        ),
        (
            "padawan-lean-math",
            "padawan-lean-math-v1",
            "https://github.com/andrewverdiramo/PADAWAN",
            local_rights,
        ),
        (
            "padawan-appellate",
            "padawan-appellate-synthetic-v1",
            "https://github.com/andrewverdiramo/PADAWAN",
            appellate_rights,
        ),
        (
            "padawan-magellan",
            "padawan-magellan-scenarios-v1",
            "https://github.com/andrewverdiramo/PADAWAN",
            local_rights,
        ),
    )
    # Governance is lane-specific.  A dataset name and generator revision do not authorize the
    # same content to move between adaptive search, development, challenge, and sealed promotion.
    # Keeping separate append-only records makes those boundaries auditable and prevents a suite
    # from borrowing a permissive lane's rights/contamination decision.
    local_classes = {
        "padawan-algebra": (
            EvaluationClass.ADAPTIVE_SEARCH,
            EvaluationClass.DEVELOPMENT,
            EvaluationClass.SEALED_PROMOTION,
        ),
        "padawan-temporal-grounding": (
            EvaluationClass.ADAPTIVE_SEARCH,
            EvaluationClass.CHALLENGE,
        ),
        "padawan-lean-math": (EvaluationClass.CHALLENGE,),
        "padawan-appellate": (EvaluationClass.CHALLENGE,),
        "padawan-magellan": (EvaluationClass.ADAPTIVE_SEARCH,),
    }
    records = [
        _governance(
            benchmark_id=benchmark_id,
            benchmark_version=version,
            dataset_revision=f"generator-source@c2e7a25:{evaluation_class.value}",
            source_url=url,
            rights=rights,
            access=AccessClassification.LOCAL,
            redistribution=RedistributionClassification.METADATA_ONLY,
            contamination=ContaminationClassification.NO_KNOWN_EXPOSURE,
            evaluation_class=evaluation_class,
            requirements=(
                "Generate from the pinned Padawan source revision and declared seed.",
                f"Retain the disjoint {evaluation_class.value} lane identity.",
            ),
            redistribution_notes=("Generated prompts and answers remain internal research data.",),
            contamination_evidence=(
                "Fresh deterministic seeds are partitioned across curriculum, adaptive, challenge, and promotion lanes.",
            ),
            sealed_handling=(
                "No local generator output becomes a sealed promotion suite without a core EvaluationSuiteManifest bridge.",
            ),
        )
        for benchmark_id, version, url, rights in local_specs
        for evaluation_class in local_classes[benchmark_id]
    ]

    records.extend(
        (
            _governance(
                benchmark_id="gpqa-diamond",
                benchmark_version="main-repository-current-at-review",
                dataset_revision="not-vendored-or-frozen",
                source_url="https://github.com/idavidrein/gpqa",
                rights=_rights(
                    rights_id="gpqa.repository.mit",
                    basis=RightsBasis.OPEN_LICENSE,
                    detail="The official GPQA repository declares the MIT License for repository content.",
                    review_status=RightsReviewStatus.CONFIRMED,
                    license_id="MIT",
                    restrictions=("retain the repository canary and attribution",),
                ),
                access=AccessClassification.REGISTRATION_ONLY,
                redistribution=RedistributionClassification.PERMITTED,
                contamination=ContaminationClassification.UNASSESSED,
                evaluation_class=EvaluationClass.CHALLENGE,
                license_expression="MIT",
                requirements=(
                    "Vendor and content-address the exact CSV revision before execution.",
                ),
                sealed_handling=(
                    "Never expose answer-bearing content to training or adaptive search.",
                ),
            ),
            _governance(
                benchmark_id="aime-2026",
                benchmark_version="matharena-2026",
                dataset_revision="not-vendored-or-frozen",
                source_url="https://github.com/eth-sri/matharena",
                rights=_rights(
                    rights_id="matharena.dataset.cc-by-nc-sa-4.0",
                    basis=RightsBasis.OPEN_LICENSE,
                    detail="The MathArena project declares CC-BY-NC-SA-4.0 for benchmark data and MIT for code.",
                    review_status=RightsReviewStatus.CONFIRMED,
                    license_id="CC-BY-NC-SA-4.0",
                    restrictions=("non-commercial and share-alike dataset terms apply",),
                ),
                access=AccessClassification.REGISTRATION_ONLY,
                redistribution=RedistributionClassification.METADATA_ONLY,
                contamination=ContaminationClassification.UNASSESSED,
                evaluation_class=EvaluationClass.CHALLENGE,
                license_expression="CC-BY-NC-SA-4.0",
                requirements=("Freeze a specific MathArena data revision and scorer.",),
            ),
            _governance(
                benchmark_id="swe-bench-verified",
                benchmark_version="source-current-at-review",
                dataset_revision="not-vendored-or-frozen",
                source_url="https://www.swebench.com/SWE-bench/guides/datasets/",
                rights=_rights(
                    rights_id="swe-bench.target-repositories.review-required",
                    basis=RightsBasis.UNKNOWN,
                    detail="SWE-bench code is MIT, while each target repository retains its own license and artifact obligations.",
                    review_status=RightsReviewStatus.REVIEW_REQUIRED,
                    restrictions=("review every target repository and container artifact",),
                ),
                access=AccessClassification.LEGALLY_UNCLEAR,
                redistribution=RedistributionClassification.METADATA_ONLY,
                contamination=ContaminationClassification.UNASSESSED,
                evaluation_class=EvaluationClass.CHALLENGE,
                requirements=(
                    "Complete per-repository rights review and freeze tasks, repositories, images, patch parser, and grader.",
                ),
            ),
            _governance(
                benchmark_id="ifbench",
                benchmark_version="source-current-at-review",
                dataset_revision="not-vendored-or-frozen",
                source_url="https://github.com/allenai/IFBench",
                rights=_rights(
                    rights_id="ifbench.data.odc-by-1.0.review-required",
                    basis=RightsBasis.OPEN_LICENSE,
                    detail="The official repository declares ODC-BY-1.0 for data and Apache-2.0 for code, with Ai2 responsible-use and third-party output conditions.",
                    review_status=RightsReviewStatus.REVIEW_REQUIRED,
                    license_id="ODC-BY-1.0",
                    restrictions=(
                        "review Ai2 responsible-use terms and third-party model outputs",
                    ),
                ),
                access=AccessClassification.REQUIRES_APPROVAL,
                redistribution=RedistributionClassification.METADATA_ONLY,
                contamination=ContaminationClassification.UNASSESSED,
                evaluation_class=EvaluationClass.CHALLENGE,
                license_expression="ODC-BY-1.0",
                requirements=("Approve terms and freeze data plus evaluator revisions.",),
            ),
            _governance(
                benchmark_id="humanitys-last-exam",
                benchmark_version="source-current-at-review",
                dataset_revision="gated-not-acquired",
                source_url="https://huggingface.co/datasets/cais/hle",
                rights=_rights(
                    rights_id="hle.gated-provider-terms.review-required",
                    basis=RightsBasis.PROVIDER_TERMS,
                    detail="The official gated dataset requires acceptance and imposes restrictions including no public redistribution.",
                    review_status=RightsReviewStatus.REVIEW_REQUIRED,
                    terms_uri="https://huggingface.co/datasets/cais/hle",
                    restrictions=("do not publicly redistribute gated questions or answers",),
                ),
                access=AccessClassification.REQUIRES_APPROVAL,
                redistribution=RedistributionClassification.PROHIBITED,
                contamination=ContaminationClassification.UNASSESSED,
                evaluation_class=EvaluationClass.SEALED_PROMOTION,
                requirements=(
                    "Obtain explicit dataset access and legal approval; register a sealed evaluator.",
                ),
                redistribution_notes=(
                    "Only non-content metadata and aggregate results may leave the restricted store.",
                ),
                sealed_handling=(
                    "Keep gated items and answers in a restricted sealed-evaluation lane.",
                ),
            ),
            _governance(
                benchmark_id="mmmu-pro",
                benchmark_version="source-current-at-review",
                dataset_revision="not-vendored-or-frozen",
                source_url="https://github.com/MMMU-Benchmark/MMMU",
                rights=_rights(
                    rights_id="mmmu-pro.embedded-media.review-required",
                    basis=RightsBasis.OPEN_LICENSE,
                    detail="The official repository declares Apache-2.0, but embedded third-party media needs item-level review.",
                    review_status=RightsReviewStatus.REVIEW_REQUIRED,
                    license_id="Apache-2.0",
                    restrictions=(
                        "review embedded media provenance and redistribution item by item",
                    ),
                ),
                access=AccessClassification.LEGALLY_UNCLEAR,
                redistribution=RedistributionClassification.METADATA_ONLY,
                contamination=ContaminationClassification.UNASSESSED,
                evaluation_class=EvaluationClass.CHALLENGE,
                license_expression="Apache-2.0 repository; media review required",
                requirements=(
                    "Complete media rights review, freeze Standard-10 items, and pass Inkling image validation.",
                ),
            ),
            _governance(
                benchmark_id="mmau",
                benchmark_version="MMAU-v05.15.25",
                dataset_revision="public-media-private-answers",
                source_url="https://github.com/Sakshi113/MMAU",
                rights=_rights(
                    rights_id="mmau.media-and-private-evaluator.review-required",
                    basis=RightsBasis.UNKNOWN,
                    detail="The official code is Apache-2.0; media provenance requires review and full answers are withheld.",
                    review_status=RightsReviewStatus.REVIEW_REQUIRED,
                    restrictions=("do not infer media rights from the code license",),
                ),
                access=AccessClassification.UNAVAILABLE,
                redistribution=RedistributionClassification.METADATA_ONLY,
                contamination=ContaminationClassification.UNASSESSED,
                evaluation_class=EvaluationClass.CHALLENGE,
                requirements=(
                    "Secure an authoritative evaluator, freeze the dataset revision, review media, and pass image/audio gates.",
                ),
            ),
            _governance(
                benchmark_id="arc-agi-2",
                benchmark_version="official-repository-current-at-review",
                dataset_revision="not-vendored-or-frozen",
                source_url="https://github.com/arcprize/ARC-AGI-2",
                rights=_rights(
                    rights_id="arc-agi-2.apache-2.0",
                    basis=RightsBasis.OPEN_LICENSE,
                    detail="The official ARC-AGI-2 repository declares Apache-2.0.",
                    review_status=RightsReviewStatus.CONFIRMED,
                    license_id="Apache-2.0",
                ),
                access=AccessClassification.REGISTRATION_ONLY,
                redistribution=RedistributionClassification.PERMITTED,
                contamination=ContaminationClassification.UNASSESSED,
                evaluation_class=EvaluationClass.CHALLENGE,
                license_expression="Apache-2.0",
                requirements=(
                    "Freeze the official tasks and deterministic scorer before execution.",
                ),
            ),
            _governance(
                benchmark_id="arc-agi-3",
                benchmark_version="2026-competition",
                dataset_revision="private-evaluation-not-acquired",
                source_url="https://arcprize.org/competitions/2026",
                rights=_rights(
                    rights_id="arc-agi-3.private-evaluation.review-required",
                    basis=RightsBasis.UNKNOWN,
                    detail="The public toolkit is MIT; private competition evaluation access and rules are a separate authority.",
                    review_status=RightsReviewStatus.REVIEW_REQUIRED,
                    restrictions=("competition rules and private evaluator govern access",),
                ),
                access=AccessClassification.UNAVAILABLE,
                redistribution=RedistributionClassification.METADATA_ONLY,
                contamination=ContaminationClassification.UNASSESSED,
                evaluation_class=EvaluationClass.SEALED_PROMOTION,
                requirements=(
                    "Obtain authorized private evaluator access and bind competition rules.",
                ),
                sealed_handling=(
                    "Private evaluation content never enters adaptive search or training.",
                ),
            ),
        )
    )
    return tuple(
        sorted(records, key=lambda record: (record.benchmark_id, record.evaluation_class.value))
    )


def governance_by_benchmark_and_class() -> dict[tuple[str, EvaluationClass], DatasetGovernance]:
    """Return the canonical v0 governance record for each benchmark/evaluation lane."""

    return {
        (record.benchmark_id, record.evaluation_class): record
        for record in dataset_governance_records()
    }
