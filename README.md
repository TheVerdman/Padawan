# Padawan

Padawan is a research system for improving smaller open-weight models. It connects controlled
experiments, evidence-citing teaching, lesson memory, capability measurement, and governed training
products. The research spans individual developmental episodes and persistent institutions with
replaceable workers. Domains supply their own task semantics and grading authorities.

The developmental loop measures revision and fresh-task transfer before admitting a lesson.
Capability Atlas measures fixed model/harness conditions and feeds explicitly bound studies.
Persistent-process reinforcement learning (**PPRL**) studies distributions of longer-lived
processes; **PPRL-VR** is reserved for verifiable outcomes. The Interaction Lab provides a separate
exploratory surface. These paths share evidence infrastructure, with explicit boundaries for what
may enter worker context, memory, comparisons, or training.

## Start here

| Need | Canonical guide |
| --- | --- |
| Subsystem purposes, owners, connections, and remaining gaps | [Architecture](docs/architecture.md) |
| Reproducible setup, commands, and historical recipe prerequisites | [Operations](docs/operations.md) |
| Developmental experiments and measurement identity | [Experiment semantics](docs/experiment-semantics.md), [research controls](docs/research-controls.md) |
| Capability measurement | [Capability Atlas](docs/capability-atlas.md) |
| Persistent institutions and the scientific program | [PPRL and epsilon-charity](docs/pprl-epsilon-charity-program.md), [four-fabric architecture](docs/pprl-four-fabric-architecture.md) |
| Process contracts and authorization | [Persistent-process RL](docs/persistent-process-rl.md), [ADR 0013](docs/adr/0013-persistent-process-reinforcement-learning.md), [Amber ADR](docs/adr/0014-amber-protocol.md) |
| Evidence, storage, and learning admission | [Data model](docs/data-model.md), [private-reasoning policy](docs/private-reasoning-policy.md), [training products](docs/training-products.md) |
| Domain and exploratory surfaces | [Appellate briefing](docs/appellate-briefing.md), [temporal grounding](docs/temporal-grounding.md), [Magellan contract](docs/magellan-improvement.md), [Interaction Lab](docs/interaction-lab.md) |
| Dated outcomes and superseded plans | [Retained evidence and plans](docs/architecture.md#retained-evidence-and-plans), [consolidation audit](reports/verification/2026-09-07-stage-2-consolidation-audit.md) |

Repository editing guidance is in [AGENTS.md](AGENTS.md).

## Development

Python 3.12 or 3.13 is supported. The clean-install validation uses Python 3.12.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev,gcs,coding]"
make check
.venv/bin/padawan --help
```

`make check` runs formatting, lint, strict package typing, generated-schema checks, and the ordinary
offline suite. Tests use disposable fixtures. The selector excludes `postgres`, `live`, `lean`,
`docker`, and `gcs` consistently with CI. The development extras include dependencies needed to
collect and exercise fake-backend and coding tests; they do not launch a runtime or select credentials.

The base package and core CLI also work with `pip install .`; optional features report their
required extras when selected. For a local application database, run `make migrate` after choosing
its configuration. PostgreSQL, Lean, Docker, GCS, and live-model tests have separate `make test-*`
targets and require the runtime and configuration described in [operations](docs/operations.md).

## Implemented boundaries

The repository implements durable developmental workflows for algebra, Lean mathematics,
closed-record appellate briefing, and temporal grounding; Atlas execution and study binding;
PPRL control-plane, admission, accounting, recovery, and compilation contracts; and local/GCS
content-addressed evidence storage. The [architecture map](docs/architecture.md#subsystem-map)
links these implementations to their tests and limits.

No external trainer or parameter-update backend is bundled. Atlas learning materialization,
general PPRL scheduling and live coordination, and the Magellan execution integration remain
separate work. Appellate currentness remains unknown without an admitted
[citator](docs/adr/0011-citator-currentness-boundary.md). Private reasoning and raw traffic remain
researcher-only records unless a separate reviewed derivative crosses an explicit admission boundary.

Offline tests establish software behavior. Dated live reports retain their actual outcomes,
interruptions, and coverage; they do not establish a general learning or institutional advantage.
Model runtimes remain external to Padawan and are accessed through adapters.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
