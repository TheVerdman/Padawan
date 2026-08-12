# Capability Atlas v0 catalog data

These declarative files are review aids for the typed builders in `padawan.atlas.catalog` and
`padawan.atlas.campaigns`. They contain no benchmark questions, answers, media, local model output,
or activation data.

- `inkling-source-claims-v0.json` separates the July 15 preview from the July 30 release and stores
  official scores only as upstream priors.
- `benchmark-governance-v0.json` lists authoritative public source and licensing/access decisions.
- `first-inkling-campaign-v0.json` records the generated campaign/verification digests and exact
  external allocation ceilings.

The Python builders are authoritative because they validate every record against the public Atlas
contracts and recompute all content digests. Tests detect drift between the durable decisions and
the generated campaign.
