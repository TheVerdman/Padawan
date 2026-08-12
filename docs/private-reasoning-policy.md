# Private reasoning policy

Private reasoning is sensitive observational evidence. It is not required for an attempt, not
treated as causal truth, and never synthesized when an endpoint does not expose it.

Every runtime reports capability availability separately for private reasoning, reasoning
boundaries, token IDs, log probabilities, GPU telemetry, and router telemetry. `unavailable` means
the endpoint cannot provide it; `unknown` means Padawan has not established the capability. An empty
string is not used as evidence of successful capture.

When a runtime returns private reasoning, Padawan writes it as a restricted raw artifact. Token
channel spans must be within the returned token array and must not overlap public spans. A private
artifact is invalid unless the runtime capability says it was available. Teacher access requires an
explicit policy decision and any citation must provide valid span bounds. The current algebra live
composition sets teacher private-reasoning access to false.

The self-hosted Inkling runtime enables capture of its model-emitted Responses `reasoning_text`
channel, including streaming `response.reasoning_text.done` events. Frontier-provider adapters keep
that capture disabled. This records the reasoning text Inkling actually emits; it does not claim to
capture hidden activations or any internal state the serving runtime did not return.

Raw requests and responses are also restricted because a provider may include reasoning or other
sensitive trace data in its wire representation. Default export policy denies restricted and
private-reasoning artifacts. Export requires both a policy opt-in and the corresponding principal
role; the Heirloom exporter excludes raw/private material by default.

Private reasoning can help identify hypotheses about student behavior, but deterministic grades and
observable tool results outrank it. It cannot rescue an invalid public derivation, silently enter a
validated lesson, or become a training target solely because a teacher cited it.
