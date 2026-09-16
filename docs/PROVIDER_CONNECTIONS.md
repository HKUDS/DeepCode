# Provider connections and verification

Desktop, Web, TUI and CLI resolve the same named connections. A connection
selects the endpoint and credentials; a model declaration describes capacities
and supported input/tool/reasoning behavior.

## Explicit protocol selection

| Setting | Behavior |
| --- | --- |
| `auto` | Preserves existing template/model routing and legacy Responses fallback |
| `openai_chat` | Uses Chat Completions even for model names previously routed to Responses |
| `openai_responses` | Uses the configured endpoint's Responses API; failures never switch to Chat |
| `anthropic_messages` | Uses the native Anthropic Messages adapter |

`adapter` remains readable for older configurations. A contradictory explicit
protocol/adapter pair is rejected. OAuth currently supports only the official
OpenRouter Chat endpoint. `auth: none` supports local OpenAI endpoints without
an SDK Authorization header; it does not borrow an ambient API key. Explicit
custom headers remain private connection configuration.

```sh
deepcode provider set local --template custom \
  --api-base http://127.0.0.1:1234/v1 --protocol openai_chat --auth none \
  --catalog manual --model my-model

deepcode provider set gateway --template custom \
  --api-base https://gateway.example/v1 --protocol openai_responses --api-key \
  --catalog manual --model my-model
```

`--api-key` prompts without echoing the secret; keys are not command arguments.
Compatibility overrides are typed and validated, rather than arbitrary request
JSON. Connection values apply first, then per-model values override them.

| Override | Chat | Responses |
| --- | --- | --- |
| `tokenLimitField` | `max_tokens` / `max_completion_tokens` | Uses `max_output_tokens` |
| `temperature` | Include/omit | Include/omit |
| `systemRole` | `system` / `developer` / `user` | Unsupported override |
| `reasoningField` | `reasoning_effort` / `reasoning` / `omit` | `reasoning` / `omit` |
| `reasoningContent` | Preserve / ensure empty missing field / omit | Native opaque reasoning history |
| `toolMessageName` | Include/omit tool-result name | Native call/output IDs |
| `parallelToolCalls` | Boolean | Boolean |

Anthropic Messages supports the `temperature` include/omit override; the other
listed overrides are rejected for Anthropic. Overrides under `auto` are rejected.
Use `--compat '{"temperature": false}'` with an explicit matching protocol.
The settings editor provides equivalent controls and a clear-overrides action.
Anthropic sampling is sent through the SDK's documented `extra_body` option,
which preserves older model behavior with SDK 0.x and 1.x. For models that no
longer support sampling, select `compat: {"temperature": false}`. See the
[official SDK migration guide](https://github.com/anthropics/anthropic-sdk-python/blob/main/MIGRATION.md#removed-deprecated-request-parameters).

`--model-declarations` accepts a JSON array with `id`, `label`, `contextWindow`,
`maxOutputTokens`, `reasoningEfforts`, `inputModalities`, `toolCalling`, and
`compat`. An explicit unsupported input/tool capability is rejected before a
request, including before legacy image fallback. An unknown capability remains
unknown; it is not displayed as verified support.

## Configuration during a task

An accepted Turn stores its model, generation/capability settings and a private
provider revision reference. The revision freezes endpoint, protocol and
compatibility; secret headers remain in owner-only revision files. API-key
bodies are stored only in the credential store, not Turn metadata or events.
Per-model compatibility changes invalidate idle Session runtimes too.

Before each model request, DeepCode checks current credentials and whether the
connection is still enabled. Same-account key rotation at the same endpoint can
continue. Removing a credential, disabling the connection, changing authenticated
accounts or changing header credentials stops the next request without automatic
retry. Changing both credential and endpoint requires a new Turn. In-flight
requests are not redirected to newly configured endpoints.

After a stream has delivered output, a failure is returned as partial output;
the retry layer does not emit a second completion over it. Tool effects are not
replayed by protocol fallback. Session-owned pools close at Session end, after
child/tool cleanup; a borrowed runtime remains its owner's responsibility.

## Quick test and Agent compatibility

```sh
deepcode provider test gateway --model my-model
deepcode provider test gateway --model my-model --agent --json
```

The quick test separates credential, catalog and minimal inference checks.
Successful inference alone does not establish Agent compatibility.

Agent verification makes at most three model requests within 90 seconds. It
uses production adapters and reasoning-history serialization, with one local
verification function; it does not expose shell, file or external-network tools.
The model must request the function and reproduce a nonce available only in its
result. The output reports streaming, tool call, continuation, reasoning and
image stages separately. Image protocol acceptance does not evaluate vision
quality. A stage without evidence remains skipped/not run.

The per-request output setting is at most 1,024 tokens, subject to a smaller
published model limit. Provider-specific reasoning modes can raise their wire
budget. Discovery has a separate bounded request timeout. The RPC transport
allows the complete verification budget, and never automatically retries this
potentially billable operation after a lost response.

The editor's **Verify current settings** probes the complete visible draft,
including unsaved protocol, endpoint, credentials and model declarations. It
writes no configuration, credentials, catalog cache or revision. Editing the
form invalidates its displayed result. Real coding-task acceptance is separate
from this controlled protocol probe.

## OpenRouter login

```sh
deepcode provider set router-login --template openrouter \
  --protocol openai_chat --auth oauth
deepcode provider login router-login
deepcode provider logout router-login
```

Select **Sign in with OpenRouter** as the authentication method and save before
using the connection's login button. Login uses a short-lived loopback callback,
S256 PKCE and a single-use flow identity. The verifier and returned key never
enter the frontend. Login can be cancelled and expires after five minutes.

The stored key is bound to the Provider account. A different account cannot
silently replace it: disconnect first. Credential changes/clear operations
invalidate old pending flows across processes, so an old callback cannot undo
logout. Signing in does not export a credential into process environment.

OpenRouter's flow exchanges a code for a user-controlled API key; it does not
issue a refresh token. No refresh API is advertised. Disconnect removes local
credentials and blocks further calls by old Turns. Remote revocation is a
separate operation in [OpenRouter key settings](https://openrouter.ai/settings/keys).
The management API requires a management credential; DeepCode does not pretend
a regular login key grants that authority.

This first login adapter assumes a browser on the machine running DeepCode.
Remote/SSH callback forwarding and other Provider OAuth adapters are not
implemented. Real-account authorization remains an interactive user action.

Protocol references: [OpenRouter PKCE](https://openrouter.ai/docs/guides/overview/auth/oauth),
[management credentials](https://openrouter.ai/docs/guides/overview/auth/management-api-keys).
