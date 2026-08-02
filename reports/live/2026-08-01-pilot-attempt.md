# Live pilot attempt — 2026-08-01

Status: **blocked before episode creation**. No live pedagogical result is claimed.

The bounded command requested two bootstrapped matched blocks with the real Inkling adapter
(`w8a16-balanced-v1`) and the real OpenAI Responses teacher adapter. No mocks or deterministic test
clients were enabled. Preflight and both command attempts established:

- Inkling repository `aa0a70e40ddab8f5fb00f111814ae9a3073e952a` was clean;
- `http://127.0.0.1:8000/v1/models` refused the connection (`HTTP_STATUS=000`), including an
  unsandboxed loopback retry;
- no OpenAI/Anthropic API key or model was configured in either attempted process;
- no alternate compatible endpoint/model was configured.

Both CLI invocations exited `1` with `OpenAI teacher requires a model and API key`. Governance
validation occurred before database, HTTP-client, episode, or model-call creation, so there are zero
live episode IDs and zero live model calls. The two immutable command manifests are retained at:

- `artifacts/blobs/sha256/02/0f89235b2fb72ae38e9c9be341b7e46cb38e8efa8c717dab0850ecc9740125`
- `artifacts/blobs/sha256/2d/b8015edab346702da351ddcd62503707d5888fb1601fb257aa5acb026143b3`

Their hashes match their content and include the exact parsed invocation, credential-presence
booleans, timestamps, failure status, and error. This is a truthful failed live-integration attempt,
not a skipped test and not a completed live episode. The complete machine-readable record is
`2026-08-01-pilot-attempt.json`.

Later Round 2 setup confirmed that an explicitly selected external env file contains the expected
OpenAI and Anthropic key variable names. No value was read into this report, and that later check
does not retroactively turn either pilot process into a configured or successful run.
