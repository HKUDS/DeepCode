# Models and providers

DeepCode is model-agnostic: connections are yours, defined once, switchable
mid-conversation without losing history.

## Connections

A *connection* is one route to a provider — a template plus your credentials:

```console
deepcode provider set my-openrouter --template openrouter --api-key
deepcode provider set office-vllm  --template vllm --api-base http://gpu-box:8000/v1
deepcode provider list
deepcode provider test my-openrouter --model deepseek/deepseek-v4-pro
```

- `--api-key` prompts without echo; `--api-key-env NAME` reads an environment
  variable instead — the config file never holds a literal secret.
- `deepcode provider models <id> --refresh` fetches the connection's live
  model directory.
- Removing is `deepcode provider remove <id>`.

Connections live under `providers` in `~/.deepcode/deepcode_config.json`
(named entries under `providers.profiles`; the classic per-vendor blocks still
work). Project-level config deliberately cannot add connections — credentials
stay user-scoped.

## Declared models

When a gateway serves a model the catalog doesn't know — or knows wrongly —
declare it yourself:

```jsonc
"profiles": {
  "my-openrouter": {
    "template": "openrouter",
    "modelCatalog": "manual",
    "manualModels": [
      { "id": "deepseek/deepseek-v4-pro",
        "contextWindow": 1048576,
        "maxOutputTokens": 384000,
        "reasoningEfforts": ["off", "low", "medium", "high"] }
    ]
  }
}
```

A declaration is authoritative everywhere the model appears: the `/model`
picker shows your numbers, and the runtime budgets context with them. Declare
only what you need to correct — absent fields fall through to the built-in
catalog.

## Switching in the TUI

```text
› /model
```

opens a picker over **every configured connection's full directory** — type to
filter, `Tab` switches connection scope, `Shift+Tab` cycles the highlighted
model's reasoning effort, `Enter` commits. Or be explicit:

```text
› /model my-openrouter deepseek/deepseek-v4-pro
```

Switching affects *future* turns only — history is never rewritten, and an
active turn finishes on the model it started with.

## Reasoning effort

```text
› /effort high     # or: auto · off · low · medium · high
```

Effort is a property of the model: the picker only offers levels the selected
model actually supports (declared or catalog-known). `auto` lets the provider
decide; `off` disables extended thinking where the model allows it.

While the model thinks, the status line shows the effort and the newest
thought; `Ctrl+O`'s verbose mode prints full reasoning summaries when the
provider exposes them.

## Context window cap

The model picker in Desktop can give the current Session a smaller context
budget. The TUI exposes the same future-Turn setting directly:

```text
› /context 64k
› /context auto     # return to the model's published window
```

The cap can only narrow the selected model's published context window. It does
not claim that a model accepts more context than its catalog entry, and it must
leave room for the configured generation limit. Each Turn freezes the effective
window when it is accepted, so changing the Session later does not reinterpret
an active or already queued Turn. A lower cap makes DeepCode compact long
history sooner.

## What the turn footer tells you about cost

```text
· 41s · 38.2k in · 512 out
```

Those are the provider's own reported token counts, not estimates — the same
numbers that drive automatic compaction. If a conversation compacts earlier or
later than you expect, this footer is the ground truth to reason from.
