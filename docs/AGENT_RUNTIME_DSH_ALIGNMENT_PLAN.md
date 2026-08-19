# Agent Runtime Alignment Plan — Context, Cache, and the Session Record

Status: partially landed (see §12) · Author: investigation of 2026-08-18 · Reference system: dsh
(`deepseek-harness`), read at source.

This plan addresses one root cause with four symptoms. It does **not** propose
adopting dsh's plugin architecture, replacing the SQLite projection, or
rewriting the kernel as an event-sourced system. Every phase is additive and
independently revertible.

It opens with a narrowed Phase S that extracts **two** strategies —
compaction and token measurement — from `AgentRunner`. A third extraction
(context assembly) was dropped after a source re-audit: the prefix bug is
two call sites, not a missing plugin host, and extracting it before the
durable/transient split is decided would be theater.

### Revision 2026-08-19 (source re-audit)

Re-read DeepCode `core/agent_runtime`, `core/events/session.py`,
`core/application/{session_runtime,turn_projection,turn_service}.py`,
`core/sessions/store.py`, `core/harness/memory.py`, `core/skills/prompting.py`,
`core/events/protocol.py`, and dsh `time-context`, `agent-instructions`,
`compaction-basic`, `token-meter`. The four measured defects still stand.
These are the plan changes:

1. **Do 0.1 before Phase S.** Highest measured ROI, two files, no Protocol
   required. Phase S does not make 0.1 cheaper.
2. **Shrink Phase S to `CompactionStrategy` + `TokenMeter`.** Drop
   `ContextContributor`. Transient assembly already lives in
   `session.py:824-839`; `_with_transient_context` only composes. The
   prefix breaker is ENV's *placement*, not a missing registry.
3. **Rewrite 1.1's write path.** `ToolStarted` has no `arguments` by
   design (`protocol.py:330-336`, `summarize_call` refuses to guess
   values so credentials stay out of the event ledger).
   `turn_projection.py:163-168` therefore only stores `callId` / `name` /
   `detail` / `activity`. Collecting from the projection cannot rebuild a
   model request. The complete copy is `AgentSession.history` after the
   turn (`session.py:957`). Persist from there.
4. **1.2 without persisted compaction is unsafe.** Auto-compact mutates
   resident `_history` only; `/compact` documents the canonical file as
   append-only (`session.py:559-560`). That is safe today because jsonl
   is a lossy excerpt. After 1.1 it holds full tool results — resume
   would reload the uncompacted giant. 2.1 must write the checkpoint
   into jsonl in the same train as 1.1/1.2.
5. **§3.2 must not compare jsonl to the request view.** The request
   still contains transients (skill catalog folded into system, selected
   `<skill>` block). Assert `rehydrate(jsonl) == AgentSession.history`,
   then `request_view = compose(history, transients)`.
6. **0.1 refresh is field-change, not a timer.** dsh `time-context` is
   **opt-in and off by default**; `refreshIntervalMs` is for the Schedule
   overlay that injects every step. DeepCode's block is cwd/shell/date —
   rewrite only when one of those values changes.

---

## 1. Executive summary

DeepCode rebuilds the model's message list on every request from several
sources, and persists only a lossy excerpt of it. dsh derives the message list
from one append-only log by a pure projection. That single difference produces
four measured defects:

| Symptom | Measured today | After the fix |
|---|---|---|
| Cross-turn prefix cache is lost | turn 2 reuses **904 of 8,291** prompt tokens; **7,420** recomputed per turn boundary | **28** tokens recomputed (the new user message only) |
| Resume forgets the agent's actions | tool calls and tool results absent from the rehydrated history | full history restored |
| Compaction forgets recent work | assistant + tool messages dropped wholesale | recent tail retained verbatim |
| Input-side context overflow | no recovery path; the turn fails | one bounded recovery attempt |

All four numbers are reproducible with the harnesses in Appendix A.

## 2. Evidence

### 2.1 Prefix instability across turns

Recording the exact `messages` handed to the provider across a three-turn
conversation with tool calls (tiktoken-priced, longest common prefix against
the previous request):

```
 req  msgs   tokens  reusable  recompute   note
   1     3     1020         0       1020
   2     5     8296      1020       7276   within-turn append (expected)
   3     7     8324       904       7420   turn 2 opens: only the system prompt survives
   4     9    15600      8324       7276   within-turn append (expected)
   5    11    15628      8208       7420   turn 3 opens: breaks again
   6    13    22904     15628       7276
```

First divergence at index 1 on request 3: the previous request carried
`<environment_context>` there, this one carries the first user message.

Root cause: `core/agent_runtime/context.py:57` emits the environment block as a
`role="user"` transient message, and `core/agent_runtime/runner.py`
(`_with_transient_context`) inserts turn context **immediately before the last
user message**. That insertion point moves forward every turn, so each new turn
diverges from the previous request at the position the block occupied.

Control experiment (same scenario, environment block moved to a stable
position): recompute at turn boundaries drops from 7,420 to **28** tokens;
total recomputed prompt tokens across the session drops **36,668 → 21,884**.

### 2.2 Resume drops the agent's own actions

Two `TuiApp` instances, the second resuming the first's session:

```
request 2 — live process
  [3] assistant tool_calls=['bash']
  [4] tool      SECRET-42

request 3 — after /resume
  [1] user      run echo
  [2] assistant reply 2
  [3] user      <environment_context> …
  [4] user      what did you just run?     ← the model can no longer answer
```

Root cause: `core/application/session_runtime.py:638`

```python
def _visible_history(session):
    return [... for m in session.messages
            if m.role in {"user", "assistant"} and m.content]
```

This is the input to `agent.load_history(...)` at both call sites (runtime
creation and cross-process reload).

### 2.3 The canonical record is the wrong record

`docs/P1_APP_SERVER_ARCHITECTURE.md` states the intended split:

> `SessionStore` JSONL is the **canonical** identity and user-visible
> conversation record shared by CLI and Desktop. The Desktop database is a
> **rebuildable projection** plus Desktop-only runtime state.

The CQRS split is sound; the choice of canonical content is not. Today:

| Store | Holds | Problem |
|---|---|---|
| `~/.deepcode/sessions/<id>/session.jsonl` (canonical) | metadata + user/assistant text | no tool calls, no tool results |
| `~/.deepcode/state/deepcode.sqlite3` (projection) | `event_log` (2,498 rows incl. `item.delta`), `items` (82 `tool_call`), `turns` | richer than the canonical record; `tool_call` payload carries no arguments and a ~400-char `resultPreview` |
| `<ws>/.deepcode/tool-results/` | full oversized tool output | 7-day retention, 32-bucket cap |
| RAM: `AgentSession._history` | the only complete history | never persisted |

The most complete copy is the one that does not survive the process.

### 2.4 Compaction shape

`core/agent_runtime/runner.py:1803` (`_build_compacted_history`) returns
`system + recent user messages (60,000-char budget) + summary last`. Assistant
and tool messages are dropped. dsh
(`packages/compaction/compaction-basic/src/region.ts`) instead replaces a
head-anchored range, retains a window-proportional recent tail verbatim
(default `retainRatio` 0.16, including assistant and tool results), never
splits a tool-call/result pair, and places the checkpoint before the retained
tail so the order stays chronological.

### 2.5 No input-side overflow recovery

`_MAX_LENGTH_RECOVERIES` (`runner.py:66`) recovers from `finish_reason ==
"length"` — output truncation. A provider rejection for exceeding the context
window has no handler. dsh's `compaction-basic` bypasses normal pressure and
retention on confirmed overflow, prunes, performs one maximal balanced head
reduction, and retries (`maxOverflowRetries`, default 1).

### 2.6 Memory layer defects

Reproduced: a 9 KB repo-root `AGENTS.md` starves the nearest, highest-precedence
`packages/app/AGENTS.md` entirely — `project_instructions`
(`core/harness/memory.py:64`) walks root-first while consuming one shared
8,000-character budget.

Project instruction content is also concatenated into the system prompt
verbatim. dsh frames the same content in `<system-reminder>` and escapes any
literal closing tag inside file content, so repository-controlled text cannot
close the frame.

## 3. What must not change

- The event protocol (`core/events/protocol.py`) and its consumers.
- `turn_service`, `session_runtime` ownership and locking semantics.
- The SQLite projection's role: rebuildable, Desktop-owned, not authoritative.
- The permission / approval / execution-security chain.
- The CLI's independence from the Desktop application layer
  (`P1_APP_SERVER_ARCHITECTURE.md` dependency direction).

## 4. Migration property that makes this cheap

`core/sessions/store.py` reads a session by scanning lines and selecting
`_type in {"metadata", "message", "task"}`. **Unknown `_type` values are
silently skipped.** New record types can therefore be appended to
`session.jsonl` without a format version bump: an older build ignores them and
behaves exactly as before.

---

## 5. Phase S — Extract two seams (pure refactor)

DeepCode's layering is sound: `core/agent_runtime`, `core/harness`,
`core/providers`, and `core/events` never import `core/application`, and
`core/domain` imports nothing upward. The rigidity that matters for later
phases is narrower: compaction shape and token measurement are private
methods on `AgentRunner` (~1,975 lines). Context assembly is **not** in
that class — it is three appends in `session.py:824-839` plus a
composition helper. Extracting a `ContextContributor` Protocol before the
durable/transient split is decided would force 0.1 to rewrite the Protocol
it just introduced.

| Strategy | Where it lives today | In Phase S? |
|---|---|---|
| Environment / skills / MCP | `session.py:824-839` builds the tuple; `_with_transient_context` only composes | **No.** 0.1 edits those two call sites directly |
| Compaction | `_maybe_compact` / `_summarize` / `_build_compacted_history` / `compact_history` / `_snip_history` | **Yes** — `CompactionStrategy` |
| Token measurement | `estimate_prompt_tokens_chain` via `_estimate_prompt` (already prices the *request view*, transients included) | **Yes** — `TokenMeter` |

The instinct already exists in the same file: the tool-result pruner is
**injected** (`AgentRunSpec.tool_result_pruner`) and therefore swappable.
Phase S copies that shape. No plugin kernel.

### S.1 `CompactionStrategy`

```python
class CompactionStrategy(Protocol):
    """Relieves context pressure; returns the history to send."""
    async def compact(self, request: CompactionRequest) -> CompactionResult: ...
```

`_maybe_compact`, `_summarize`, and `_build_compacted_history` move behind it
as `DefaultCompactionStrategy`, preserving today's ladder (pressure gate →
model-free prune → remeasure → summarize) and its convergence rule. The
existing `tool_result_pruner` injection stays as-is and is consumed by the
strategy.

### S.2 `TokenMeter`

```python
class TokenMeter(Protocol):
    """Prices a prospective request."""
    def measure(self, messages, tools, model) -> TokenMeasurement: ...
```

Wraps today's `estimate_prompt_tokens_chain` as `HeuristicTokenMeter`. 3.1
then ships as a second implementation that anchors on provider-reported usage
instead of a runner edit.

### Acceptance criteria

Phase S was specified to change **no behavior**, provable by byte-identical
requests. **As landed, S.2 shipped together with its second implementation**
(§8's tail-retaining strategy is the registered default), so that criterion no
longer applies to the compaction seam and the two changes cannot be bisected
apart. The criteria that remained applicable, and were met:

1. The existing suites covering these modules stay green:
   `test_agent_runner_kernel`, `test_agent_session`, `test_session_compaction`,
   `test_manual_compact`, `test_session_runtime`, `test_turn_projection`, plus
   the full run.
2. No new dependency edges: `core/agent_runtime` still imports nothing from
   `core/application`.
3. The behavior that *did* change is measured rather than asserted: §2.1's
   harness reports the turn-boundary recompute cost, and §7's resume harness
   reports what a resumed request contains.

The lesson for the phases still ahead (3.1 in particular): register the new
implementation in a **separate** change from the one that extracts the seam,
or the seam's whole purpose — being able to A/B and revert one registration —
is spent on the first use.

### What the later phases become

| Phase | After this revision |
|---|---|
| 0.1 environment context | **Does not wait for S.** Two-file edit (`context.py` + `session.py`) |
| 2.1 compaction shape | Second `CompactionStrategy`, A/B-able — but must persist the result (see §8) |
| 3.1 provider-anchored pressure | Second `TokenMeter` |

`0.2` and `0.3` stay independent of S.

## 6. Phase 0 — Stop the bleeding

Three independent changes, no architectural movement.

### 0.1 Durable environment context

**Change** `core/agent_runtime/context.py` and `core/events/session.py:824-828`:
append the environment block **once** into `_history` (ahead of the first user
message of the session) instead of putting it in `transient_context_messages`.
Rewrite that history slot only when `cwd`, `shell`, or `current_date` actually
changes. Do **not** copy dsh `refreshIntervalMs`: that plugin is opt-in, off
in default compositions, and exists so the Schedule overlay can stamp every
step. DeepCode's block is workspace identity + calendar date, not a
per-step clock.

Leave the other two transients alone — they are not the prefix bug:

| Source (`session.py:824-839`) | Role today | After 0.1 |
|---|---|---|
| `EnvironmentContext.message()` | `user`, inserted before last user | **durable** in `_history` |
| Skill catalog (`prompting.py:40-48`) | `developer` → folded into system | stays transient; stable catalog ⇒ identical system prefix |
| Selected skill (`prompting.py:50-56`) | `user` `<skill>` before last user | stays transient; only present when a skill is selected this turn |
| MCP `instruction_context()` | `system` → folded into system | stays transient; stable text ⇒ identical system prefix |

`_with_transient_context`'s insertion rule stays. Anthropic/OpenAI
`cache_control` on `messages[-2]` then lands on the last durable turn
item instead of on a hopping ENV block.

**Pinned test that will move** `tests/test_skills_product.py:134` asserts
ENV is `request_messages[-3]`. After 0.1 it sits after the system block.
Update the assertion; do not keep the old position to "pass the test".

**Contract** for consecutive requests R(n) and R(n+1) with no selected
skill and no compaction, R(n) is a prefix of R(n+1) except for the new
user message (and any new tool loop of the current turn).

**Verification** Appendix A.1 ≤ new-user-message tokens as `recompute` at
every turn boundary.

**Risk** low. **Rollback** revert the two files.

### 0.2 Memory budget and framing

**Change** `core/harness/memory.py`:
- Allocate the instruction budget **nearest-first**, or give each file its own
  quota, so the most specific file can never be starved by an ancestor.
- Wrap injected project/user instructions in a `<system-reminder>` frame and
  escape any literal closing tag occurring in file content or paths.

**Verification** the Appendix A.3 reproduction must show the nearest file
present; a fixture file containing a literal closing tag must render escaped.

**Risk** low. **Rollback** revert one module.

### 0.3 Input-side overflow recovery

**Change** `core/agent_runtime/runner.py`: classify provider errors that report
context-window exhaustion; on a confirmed one, bypass the pressure gate, run the
tool-result pruner, apply one maximal head reduction that preserves tool-call
pairing and the newest indivisible unit, and retry once. A second failure
preserves the original provider error.

**Contract** at most one recovery attempt per request; no silent truncation of
a request that did not overflow.

**Verification** a scripted provider that raises an overflow error on the first
call and succeeds on a smaller prompt must produce a completed turn.

**Risk** medium (touches the error path). **Rollback** the classifier returns
`False` unconditionally.

## 7. Phase 1 — Make the canonical record complete

### 1.0 The invariant this phase installs

`core/agent_runtime/runner.py:121` already states the rule, names its source,
and describes this exact failure:

> Model-visible means logged (the dsh session-log rule): mid-turn messages the
> runner itself adds to the PERSISTED model history … reach the model but are
> invisible to the host's canonical persistence, so a resumed Session would
> silently rebuild a DIFFERENT history than the model actually saw.

DeepCode built `AgentRunSpec.context_note_sink` to honour it — for *injected*
mid-turn messages only. Two categories were never covered, and they are the
two this phase adds:

| Reaches a model request | Persisted today |
|---|---|
| user message | yes |
| final assistant answer | yes (`turn_service.py:1369`, `projection.final_text`) |
| injected mid-turn message | yes, via `context_note_sink` |
| **intermediate assistant step** (the one carrying `tool_calls`) | **no** |
| **tool result** | **no** |

dsh cannot have this gap by construction: its loop appends the
`assistant/message` event *before* it knows whether the step is final
(`packages/core/agent-loop/src/agent.ts:374` — the `completed` decision is made
after the append), and the appended message object is the same frozen object
the next request sends. Logging and model-visibility are one action, separated
afterwards by the surface projection rather than by the writer. DeepCode's
runtime memory and its canonical file are two different paths, so the rule has
to be asserted instead of structurally guaranteed. §3.2 adds that assertion.

### 1.1 Persist the complete model-visible record

**Do not collect this from `TurnProjection` or `ToolStarted`.** The event
protocol is a display ledger: `ToolStarted` carries `call_id` / `name` /
`detail` / `activity` and **no arguments** (`protocol.py:330-336`).
`summarize_call` explicitly refuses to persist guessed values so
credentials and signed URLs stay out of the event ledger. The projection
payload (`turn_projection.py:163-168`) is the same four fields plus a
~400-character `resultPreview`. That is enough to render a card. It is
not enough to rebuild a provider request.

**Source of truth for the write:** `AgentSession.history` after the turn
(`session.py:957` — `result.messages` minus system). That list already
has intermediate assistant steps with `tool_calls`, and `tool` messages
with `tool_call_id` + full content. The host that already owns
`SessionStore` (`turn_service.py` next to the existing
`append_message(..., projection.final_text)` at 1369) walks the history
delta and appends the missing records. Kernel still does not import
`core.application`. The event protocol does not grow.

**Change** `core/sessions/models.py` + `core/sessions/store.py`: add two
record types (`tool_call`, `tool_result`) and allow assistant `message`
rows to carry `toolCallIds`. `turn_service.py` writes them from
`agent.history`.

```jsonc
// intermediate assistant step — a new record only in the sense that today
// only `projection.final_text` is written; the record TYPE is the existing one
{"_type":"message","role":"assistant","content":"I'll read the file first.",
 "timestamp":"…",
 "metadata":{"turnId":"turn_6b3a…","step":1,"toolCallIds":["call_9806…"]}}

{"_type":"tool_call","call_id":"call_9806…","name":"read",
 "arguments":{"file_path":"core/tui/renderer.py"},
 "timestamp":"…","metadata":{"turnId":"turn_6b3a…","step":1}}

{"_type":"tool_result","call_id":"call_9806…","is_error":false,
 "content":"1: from __future__ import annotations\n…","timestamp":"…"}

// oversized result: bounded excerpt + locator + original size
{"_type":"tool_result","call_id":"call_75ad…","is_error":false,
 "content":"1: Source: https://arxiv.org/search/\n…",
 "spill":".deepcode/tool-results/<session-id>/call_75ad….txt",
 "content_bytes":100415,"timestamp":"…"}
```

Design decisions:

- **`call_id` is the pairing key.** Concurrent tools and out-of-order settling
  both rebuild correctly; no positional assumption.
- **`toolCallIds` on the assistant step** is what makes rebuilding
  `assistant(content=…, tool_calls=[…])` unambiguous. Without it the reader
  would have to synthesise an assistant message per step and would lose the
  commentary text a model writes between tool calls.
- **Oversized results keep a bounded excerpt inline**, never the full payload.
  The format's stated virtue is that it stays `jq`- and `tail -f`-readable
  (`core/sessions/models.py` docstring); a 100 KB line destroys that.
- **Spill retention becomes session-lifetime** instead of the current 7-day /
  32-bucket window (`core/agent_runtime/helpers.py:48`), or a resumed session
  finds its locator dangling.
- **Deliberately excluded**: streaming chunks (the SQLite projection already
  holds `item.delta`), request headers, step boundaries, approvals, and
  **raw tool arguments on `ToolStarted`** — that event stays a card summary.
  The canonical file carries what the model saw, copied from kernel history.

**Compatibility** guaranteed additive by §4 — a build without this change skips
the unknown `_type` values and behaves exactly as before. No version bump.

**Verification** the §3.2 reconstruction assertion, plus a turn with two
concurrent tool calls whose results settle out of order must rebuild in the
original order.

### 1.2 Rehydrate a complete history

**Change** `core/application/session_runtime.py:638`: rebuild the history from
all four record shapes instead of filtering to user/assistant text — an
assistant record carrying `toolCallIds` becomes
`assistant(content=…, tool_calls=[…])`, and each `tool_result` becomes the
`tool` message paired by `call_id`. Preserve the existing repair helpers
(`_drop_orphan_tool_results`, `find_legal_message_start`) so a truncated tail
still yields a legal message sequence.

**Verification** Appendix A.2 must show the resumed request containing the
`assistant tool_calls` / `tool` pair; the model must answer "what did you just
run?" correctly.

**Risk** medium (changes what the model sees after resume). **Rollback** restore
the filter; new records are then ignored, exactly as an older build behaves.

## 8. Phase 2 — Compaction shape

### 2.1 Retain the recent tail — and persist it

**Change** `core/agent_runtime/runner.py:1803` (`_build_compacted_history`):
- Retain a **window-proportional** recent tail verbatim (start at 0.15 ×
  `context_window_tokens`) instead of `_COMPACT_KEEP_USER_CHARS = 60_000`
  user-only, and keep assistant and tool messages inside it.
- Place the summary checkpoint **before** the retained tail.
- Never cut between an assistant tool call and its result.

**Must also persist.** Today two facts coexist:

- Runner comment (`runner.py:72-74`): compacted history "is persisted by
  the session, so it survives across turns". That is **resident `_history`
  only** (`session.py:957`).
- `/compact` docstring (`session.py:559-560`): "the canonical Session log
  is append-only and stays untouched".

Safe while jsonl is a lossy user/assistant excerpt. **Unsafe after 1.1**:
resume would reload every tool result the live process had already
summarized, and overflow. Same train as 1.1/1.2: write a checkpoint
user-message (existing `_type: message`) plus enough records that
`_visible_history` rebuilds the compacted shape. Full `surfaceOp: replace`
shadowing stays in optional 2.2.

**Contract** compaction is convergent (already implemented) and the
retained tail is a legal message sequence. Resume after compact must
rehydrate the compacted shape, not the pre-compact giant.

**Verification** a synthetic history over the trigger threshold produces
`[system][checkpoint][… recent assistant/tool …]`; a second pass is a
no-op; a third process `/resume` sees the same compacted list.

**Risk** medium. **Rollback** one function plus the persist call.

### 2.2 (Optional, later) Shadowing instead of replacement

Record compaction as an appended record carrying the shadowed range rather than
rewriting the list, so the pre-compaction conversation survives for the UI and
for audit. Evaluate only after 2.1 has shipped; it touches the store read path
and is not required for any of the four measured defects.

## 9. Phase 3 — Measurement and guards

### 3.1 Anchor pressure on provider usage

**Change** feed the provider's reported `prompt_tokens` / `cached_tokens` back
into the pressure decision: pressure = last reported prompt size + heuristic
price of surface changes since that sample. Keep the estimator for the delta
only. Revisit the 0.9 trigger fraction once the anchor lands.

**Rationale** today the decision is entirely estimated
(`estimate_prompt_tokens_chain`), and the reported size is used only for goal
budget accounting. dsh's `token-meter` anchors on provider usage for exactly
this reason. Measured here against DeepSeek's reported `prompt_tokens` for
identical content:

| Content | Estimated | Reported | Error |
|---|---:|---:|---:|
| English source | 1,336 | 1,392 | −4% |
| JSON tool schemas | 1,425 | 1,425 | 0% |
| **Chinese prose** | **1,344** | **624** | **+115%** |

The estimator is accurate where it was tuned and off by more than a factor of
two on CJK. At a 0.9 trigger that means a Chinese conversation compacts at
roughly half the context it could hold: a model round-trip spent condensing
history that still fit, and a discarded head that did not need to go. This is
the defect 3.1 removes — not a hypothetical bias, a measured one.

### 3.2 Regression guards

- **Unit**: reconstruction — `rehydrate(session.jsonl) == AgentSession.history`
  after the turn (roles, tool_calls, tool results). This is "model-visible
  means logged" for the *persisted* list. Do **not** require jsonl to equal
  the provider request: `_request_view` still folds skill catalog / MCP into
  system and may prepend a selected `<skill>` block
  (`prompting.py:37-57`). Those transients are exempt, as
  `runner.py:127-129` already says.
- **Unit**: prefix stability — consecutive requests with no selected skill
  and no compaction: longest common prefix equals the predecessor's full
  length. A selected skill is an expected per-turn insertion, same class as
  a new user message.
- **E2E (key-gated)**: against a real provider, every request after the first
  reports `cached_tokens > 0`. dsh ships the same test
  (`packages/core/agent-loop/tests/request-cache.e2e.ts`).

## 10. Sequencing

```
0.1 durable ENV (field-change refresh)
      ──► measured: 7,420 → 28 tokens per turn boundary
      ──► update test_skills_product.py ENV position pin

0.2 memory budget + framing          (independent)
0.3 overflow recovery                (independent)

S  CompactionStrategy + TokenMeter   (after 0.1; no behavior change)
 ├─► 2.1 tail-retaining compact + persist checkpoint
 └─► 3.1 provider-anchored pressure ──► 3.2 guards

1.1 persist agent.history delta ──► 1.2 rehydrate ──► 2.1
    (1.2 must not ship without 2.1 persist)
```

Recommended order: **0.1 first** (two files, measured 40% win, unblocks
honest prefix tests), then **0.2**, then **S + 1.1/1.2/2.1 as one train**
so a jsonl that suddenly holds tool results is never rehydrated without a
persisted checkpoint. `0.3` can ship any time.

## 11. Non-goals

- Porting cordis or any plugin/service-seam architecture.
- Demoting or replacing the SQLite projection.
- Rewriting the kernel as event-sourced in one step.
- Chasing dsh's package count. dsh reaches its flexibility with 226 packages
  and a three-way split (Definition / Provider / Consumer) per capability,
  sustained by per-package READMEs and design notes. Phase S extracts only
  the two seams later phases actually register against.
- Growing `ToolStarted` with raw arguments. The event protocol is a display
  ledger; credentials must not land there.
- Changing the CLI's adapter boundary.


## 12. Status

Landed and independently verified:

| Item | Result |
|---|---|
| S.2 `CompactionStrategy`, S.3 `TokenMeter` | injected on `AgentRunSpec`; both wired through every call site |
| S.1 `ContextContributor` | deferred (§5) |
| 6.1 durable environment context | turn-boundary recompute **7,420 → 28** tokens; whole-session recompute 36,668 → 21,884 |
| 6.2 memory budget + framing | nearest-first allocation; `<system-reminder>` frame with escaped closing tag |
| 6.3 overflow recovery | one bounded attempt, classifier over provider error fields |
| 7.1 / 7.2 complete record + rehydration | a resumed request now carries the `assistant tool_calls` / `tool` pair |
| 8.1 tail-retaining compaction | checkpoint leads, tool-bearing tail retained, replays correctly after resume |
| 9.2 reconstruction guard | `tests/test_model_visible_is_logged.py`, with a negative control |

Corrections applied after review of the first implementation:

- Removed a runner-level message whitelist that duplicated the provider
  layer's own filtering and was narrower than it — it dropped
  `thinking_blocks` before either adapter could see them.
- Restored the turn metadata (`skillInvocations`) that the new history write
  path had silently stopped recording on assistant records.
- Spill retention: dropped the rank-based bucket cap, which deleted an older
  session's spilled tool output while its history still referenced it, and
  lengthened the age horizon; the sweep now runs once per root per process
  instead of on every oversized result.
- The environment slot is recognised by an explicit marker key instead of by
  sniffing message content, so a user message quoting the block is not
  mistaken for the slot and overwritten.
- `TokenMeter` is now consulted by `_snip_history` as well as the compaction
  gate; previously a replacement meter would have changed one and not the other.
- Removed the unused `DefaultCompactionStrategy` and a no-op re-export line.

| 9.1 provider-anchored pressure | `ProviderAnchoredTokenMeter` is the default; measured CJK bias was **+115%** |

Found by real-model validation, after the suites were already green:

- **Manual `/compact` could never succeed.** The retained budget was derived
  from the context window alone, so against a million-token window every real
  conversation fit inside it: the strategy kept everything, adding a
  checkpoint made the result larger than its input, and the convergence rule
  correctly refused. Measured on a real session — 12 messages / 5,859
  characters — refused for any summary longer than ~390 characters, which is
  every summary a model actually writes. The budget is now the smaller of a
  window share and a share of the conversation, with two floors: the newest
  indivisible unit and everything back to the newest user message always
  survive. Re-verified against the live model: `9 → 5 messages`, and the
  next turn answered from the checkpoint.
- The Desktop projection opened a phantom Turn per compaction checkpoint
  (it rides a user-role record); `thread_service` now excludes it from
  turn grouping and from prompt derivation.

Migration verified against six of this machine's real stored sessions
(including a 29-message, 12-tool-record session carrying a live compaction
checkpoint): all resume into a legal message sequence.

Validation performed after the fixes, and what it found:

| Check | Result |
|---|---|
| Six of this machine's real stored sessions resumed | all legal, including a 29-message / 12-tool-record session with a live checkpoint |
| Desktop projection, real sessions, before/after control | clean HEAD produced a phantom Turn titled with the checkpoint text; the fix removes it and changes nothing else |
| Desktop app launched against the changed core | boots, sidecar ready |
| Two processes driving one session concurrently | canonical record stayed intact and legal every run; **one process crashes ~50% of the time on SQLite coordination (`FOREIGN KEY constraint failed` / `disk I/O error`) — reproduced identically on clean HEAD, so pre-existing and outside this plan** |
| Auto-compaction under real pressure (12k declared window, real model) | fires and succeeds — `9 → 5`, `8 → 4`, `9 → 4` messages |

Found by testing the wiring rather than the unit — the meter's own defect:

- **The anchor did not survive a Turn boundary.** `AgentRunSpec` is rebuilt
  every Turn, and the meter was a spec field with a `default_factory`, so
  each Turn started with an empty anchor and fell straight back to the
  estimator — at exactly the point where an accurate number matters most,
  the first request of a new Turn over a long history. The meter now belongs
  to `AgentSession`, one per conversation. `tests/test_token_meter_wiring.py`
  drives real Turns and fails without it (verified by reverting the change).
  Unit tests had passed throughout: they proved the meter prices correctly,
  never that anything asked it to.

Acted on after the validation round:

- **A refused compaction is no longer paid for twice.** Under sustained
  pressure the gate fires every step, and a history with nothing older to
  replace gets the same summary refused every time — one model round-trip
  per step, for nothing. The runner remembers the history signature it was
  refused on and skips until the history changes
  (`tests/test_compaction_retry_memo.py`, with a negative control). Manual
  `/compact` is never memoised: an explicit request always tries.
- **A session rebuilt from JSONL now shows what the agent did.** Phase 1
  persisted tool calls and results, but `_reconcile_transcript` still
  filtered to user/assistant, so Desktop rebuilt a conversation with the
  agent's actions missing. The reconciliation now keeps two views — the
  transcript it compares against, and a timeline that carries tool records —
  and maps them through the live path's own kind families. Verified on this
  machine's real sessions: `command_execution 3, plan 5, file_change 4`
  where both before and after previously showed only `assistant_message`.
  Idempotence is pinned by test, since this path reconciles against items
  the live stream may also have written.

- **The checkpoint speaks in the first person now.** Framed as "an earlier
  agent produced this summary", the model discounted it as hearsay — observed
  verbatim under pressure: *"the earlier handoff mentioned doc2.md, but only
  as a comparison; it was not actually read in this session"*, about a file
  this same conversation had read three turns earlier. Reframed as "this is
  your own earlier conversation, compacted", the same scenario recovers from
  one file recalled to two, and the model now cites it as "the session
  record". The summarization prompt also asks for concrete artifacts by
  name; that part is reasoned, **not** measured — it produced no
  improvement on its own.

Known limits observed, not defects of the shape:

- A single-turn history cannot be compacted: the retention floor keeps the
  newest user turn, so there is no older range to replace and the
  convergence rule refuses. The cost is one wasted summarization call per
  step while the pressure persists.
- Compaction is lossy by construction, and repeated compaction compounds it:
  a summary of a summary drops what the previous one already thinned. Under a
  12k window with three 4k documents read — a deliberately extreme ratio —
  the model afterwards recalls two of the three across runs. What survives is
  whatever each generated summary chose to carry, and no framing recovers
  what an earlier summary already dropped.

Not started: 8.2 (shadowing). The Desktop rebuild now projects tool records
(see above). The concurrent-process crash is mitigated by a per-Session run
lease (one live writer, the dsh contract enforced; deterministic collision
test with negative control) plus worker-registration read-back with one
retry — stress rate 4/8 → 0/8 with the full mitigation, details in
`docs/investigations/2026-08-19-concurrent-turn-submit.md`.

## Appendix A — Reproduction harnesses

These were written for the investigation and should be promoted into `tests/`
as part of Phase 3.

1. **Prefix cost** — a recording provider captures every `messages` list across
   a multi-turn conversation; prices each with tiktoken; reports longest common
   prefix per request. Detects §2.1.
2. **Resume view** — runs one turn with a tool call in one `TuiApp`, resumes the
   session in a second `TuiApp`, prints both request shapes. Detects §2.2.
3. **Instruction budget** — a repo-root `AGENTS.md` larger than the shared
   budget plus a nearer file; asserts the nearer file survives. Detects §2.6.

## Appendix B — Source references

| Concern | DeepCode | dsh |
|---|---|---|
| Per-request context assembly | `session.py:824-839` (sources) + `runner.py` `_with_transient_context` (compose) | `packages/core/session/src/surface.ts` `deriveEventMessage`, `packages/core/session/src/index.ts:726` `deriveMessages` |
| Environment / time context | `core/agent_runtime/context.py` (always on, hops each turn) | `packages/context/time-context` (opt-in, default off, durable append) |
| Tool event vs model history | `protocol.py` `ToolStarted` (no args) / `AgentSession.history` (full) | one frozen message object shared by delivery, log, and request |
| Project instructions | `core/harness/memory.py` | `packages/context/agent-instructions` |
| Compaction | `runner.py` `_maybe_compact` / `_build_compacted_history`, `core/agent_runtime/pruner.py` | `packages/compaction/compaction-basic/src/region.ts`, `compaction-tool-result-pruner` |
| Token measurement | `core/agent_runtime/helpers.py` `estimate_prompt_tokens_chain` | `packages/llm/token-meter` |
| Session storage | `core/sessions/store.py`, `core/persistence/*` | `packages/session/session-persistence-jsonl`, `-sqlite` |
| Oversized tool output | `core/agent_runtime/helpers.py` (`.deepcode/tool-results`) | `packages/spill/*` |
