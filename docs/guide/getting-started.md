# Getting started

Fifteen minutes from nothing to a working session. Three steps: install,
connect one model, run.

## 1 · Install

```console
uv tool install --python 3.12 deepcode-hku
deepcode init
```

`pip install deepcode-hku` and `pipx` work too, in a Python 3.12+ environment.
`deepcode init` creates `~/.deepcode/` — configuration lives there, credentials
in private storage beside it, never inside conversation history.

> The Desktop app installs separately — grab a release bundle from
> [GitHub Releases](https://github.com/HKUDS/DeepCode/releases). Everything in
> these guides about sessions, skills, and permissions applies to it too.

## 2 · Connect a model

One connection is enough to start. `--api-key` opens a hidden prompt — the key
never appears in your shell history:

```console
deepcode provider set my-openrouter --template openrouter --api-key
deepcode provider models my-openrouter --refresh
deepcode provider test my-openrouter --model deepseek/deepseek-v4-pro
```

Read that as: *create a connection → see what it can serve → prove one model
answers*. Templates exist for OpenAI, Anthropic, OpenRouter, DeepSeek, Gemini,
Ollama, vLLM and more; `deepcode provider list` shows what's configured.
(Source of truth: `cli/provider_cli.py`.)

## 3 · Run

```console
cd <your-project>
deepcode
```

The first time you point DeepCode at a folder, it asks before touching
anything:

```text
DeepCode can read files and run tools in this workspace:
  /Users/you/your-project
Trust this folder? Type yes to continue:
```

Trust is per-folder and remembered. Then the banner:

```text
 ██████  ·──○
 ██       ○──·
 ██████  ·──○
 DeepCode · open agentic coding
 deepseek/deepseek-v4-pro · ~/your-project
 session 3f2a91c0 · access default (ask) · effort auto
 /help for commands · esc interrupts · ctrl+o transcript detail
```

That third line is your session's identity: **the model it will use, the
session id you can resume later, and how much the agent may do without
asking**.

## 4 · Read your first turn

Type a task in plain language:

```text
› find where retries are configured and raise the limit to 5
```

The reply is a rhythm of cards you'll see constantly, so learn it once:

```text
● Search retry
  ⎿ ✓ 3 files matched
● Read src/client/http.py
  ⎿ ✓ 1: import httpx
● Edit src/client/http.py
  ⎿ ✓ http.py

Raised MAX_RETRIES from 3 to 5 in src/client/http.py.
· 12s · 9.4k in · 210 out
```

- `●` — a tool starts: what the agent is *doing* (Read, Search, Run, Edit…)
  and on what.
- `⎿ ✓` / `⎿ ✗` — that tool settled, with the first line of its result.
- Plain text — the agent talking to you.
- The dim `· 12s · 9.4k in · 210 out` footer — the turn is finished: wall
  time, prompt tokens in, completion tokens out. Real numbers from the
  provider, not estimates.

When the agent wants to do something sensitive under the default access
preset, it stops and asks:

```text
◆ approval needed bash
  pip install requests
  ⎿ reply y once · a session · n deny
```

Reply `y` (this once), `a` (for the whole session), or `n`. Nothing sensitive
runs without an answer.

## Where to next

- Every command and shortcut: [The terminal UI](the-tui.md)
- Pick up this conversation tomorrow: [Sessions](sessions.md)
- Teach it your project's conventions: [Skills and memory](skills-and-memory.md)
