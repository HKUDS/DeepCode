# Changelog

Notable changes to DeepCode, newest first. Each date has one entry.
Released versions are named in the entry title; the version on PyPI is
[deepcode-hku](https://pypi.org/project/deepcode-hku/). 中文版见
[CHANGELOG_ZH.md](CHANGELOG_ZH.md).

## 2026-10-31 · Preserve code examples in paper sections

- **Keep fenced code in its real paper section.** Python comments and other
  hash-prefixed lines inside backtick or tilde code blocks no longer create
  false headings or break the paper context passed to the workflow.

## 2026-10-29 · Lighter compaction, checked Goal completion, and folder browsing on the Web

- **Clear old tool output first.** With `agents.defaults.microcompact` on,
  a long session that nears its context limit replaces stale tool results
  with a short placeholder, keeping saved-output paths, before it asks the
  model for a summary. (#279)
- **Check a Goal before it completes.** With `goal.completionVerifier` set to
  `"rule"`, a claim that the Goal is done is turned back while the latest
  check failed or no work was done, and the Goal is marked blocked after
  repeated rejections. (#281)
- **Browse folders on the Web.** Opening a project in the Web client lists the
  service machine's folders to click through, instead of only a path box.
- **Clearer working rules for the agent.** It says what it is about to do
  as it works, edits files with its edit tools, checks its changes before
  calling them done, keeps a progress file on long tasks, and leaves your own
  uncommitted work and git history alone.
- **Optional extra risk check.** With `DEEPCODE_RISK_EVALUATOR=1`, a tool
  call that would run without asking is screened first, and a risky one asks
  for approval instead. Off by default; Full Access skips it. (#257)
- **Approve project hooks.** Hooks a repository declares run only once
  approved; trusting a project approves its current hooks, and a later change
  waits for `deepcode hooks approve`. `deepcode hooks status` shows where
  things stand. (#282)
- **Fixes.** A Goal on DeepSeek with thinking on Auto no longer fails with
  "reasoning_content must be passed back", and the model picker's notes wrap
  instead of running under its controls.
- **Clearer approvals in the terminal.** While a call waits for you, the TUI
  says so and lists the keys; edits show their diff and commands their full
  text. Text typed meanwhile stays in the prompt instead of reaching the
  model. A Turn waiting for a free slot shows as queued.
- **"Always allow" covers one kind of call.** Allowing `ls` for the session
  no longer allows `rm`: commands are matched by program and subcommand,
  edits by workspace. Desktop and Web label the button with exactly what it
  covers.
- **Safer Desktop and Web dialogs.** Trusting a folder first shows the access
  new Turns will get and the project hooks it approves. Every dialog closes
  with Esc, keeps keyboard focus inside and returns it afterwards.
- **First run and exit.** With no API key the TUI says so at startup and
  shows the command to add one; Ctrl-D during a Turn prints how to reattach;
  the trust prompt accepts `y`.
- **New README layout.** Release notes moved here, with new security,
  configuration and contributing guides.

## 2026-10-28 · Presets that remove tools, guarded hook files, and community fixes

- **Remove tools from a preset.** Agent preset files take `disallowed-tools`
  (Claude Code's `disallowedTools`), and `/preset` shows what is removed. (#283)
- **Hook files need approval.** File tools ask before writing the files hooks
  are loaded from, except under Full Access. (#271)
- **Unpinned MCP packages are flagged.** `deepcode mcp list` marks npx and uvx
  servers whose package has no fixed version. (#275)
- **Community fixes.** Anthropic prompt caching now covers the latest message
  (#280); the monitor's JSON mode reports failures in its exit code, Responses
  tool call ids stay intact across turns, and verification runs the project's
  own Python (#270); Desktop bundles patched Python dependencies (#278); and
  token estimation has its own tests (#284).

## 2026-10-21 · A quiet Windows service, a separate code font, and community fixes

- **No console windows on Windows.** The background service no longer opens a
  console window for each program it starts, such as shells and MCP servers,
  and closing such a window can no longer stop the service. (#267)
- **A font for code.** The Appearance settings in Desktop and Web take a list
  of preferred monospace fonts for code blocks, diffs and other monospace text,
  separate from the interface font. (#265)
- **Community fixes.** Adding or removing an MCP server keeps its settings and
  its saved sign-in in step when one of the two writes fails (#259); the
  bundled code and document MCP tools accept `null` for their optional
  arguments (#261); and two TUI tests pass on Windows (#264).

## 2026-10-14 · Sessions that keep their history, a tighter sandbox, and the Desktop and Web apps in Chinese

- **Your history stays.** A session that cannot be read is listed with the
  reason instead of losing its history, and a turn that is stopped or fails
  keeps what it already wrote. Each message reaches the session file as the
  model reads it, so a crash mid-turn no longer loses the turn, a resumed
  session gives the model the history it had, and a line a crash cut short no
  longer breaks the session.
- **A tighter sandbox.** Agent commands no longer see the model keys DeepCode
  uses (`DEEPCODE_BASH_FULL_ENV=1` still hands over everything). Credential
  files and private keys in `~/.ssh` stay out of reach, while `git` over SSH
  works through your SSH agent. A command can no longer plant hooks or loosen
  the next command's sandbox, and DeepCode's own `git` calls run no program a
  repository names. A repository's config can no longer turn the sandbox off
  or choose Full access; that is a user setting.
- **Tools that don't stall.** `apply_patch` applies all of its hunks or none;
  `read`, `glob` and `grep` no longer freeze other sessions; stopping a turn
  ends the processes it started; an MCP server that is slow to start no longer
  holds up new sessions; and long requests no longer time out or hold up the
  Desktop and Web apps. A Goal the model never settles stops after 20 continuations.
- **Desktop and Web in Chinese.** Settings, MCP, automations, Paper2Code, the
  terminal pane and the sidebar's service state follow the interface language.
  A Settings save that fails keeps your edits, and the Latest button no longer
  hides under the composer.
- **Windows.** The bash tool starts on Windows and never picks WSL's `bash`,
  and code mode and the job sandbox work in the packaged Desktop app.

## 2026-10-13 · Smoother streaming, a self-managing background service, and safer Web sign-in

- **Answers stream smoothly.** Live text goes straight to every client instead
  of being saved piece by piece, so long answers keep flowing when the database
  is busy, and a client that reconnects or reloads picks up the answer where it
  is. The TUI follows the service's updates instead of polling it.
- **The background service manages itself.** If port 3081 is taken, the
  service moves to a free port. After an upgrade, a newer client restarts an
  idle older service so both run the same version.
- **Safer Web sign-in.** The browser session no longer travels in a cookie that
  other programs on your computer could receive.
- **Clearer problems, tidier projects.** An update that could not be saved
  shows as a warning instead of a failed task, logs keep the full traceback,
  and DeepCode no longer leaves a `logs/` folder in your project.

## 2026-10-12 · DeepCode v2.3.1: Markdown answers in the TUI, a Windows service that stays up, `/compact` for coding sessions, and community fixes

- **Answers render as Markdown while they stream.** Headings, lists, bold
  text and tables are formatted as each line completes; code blocks are not
  indented or wrapped by DeepCode, links show their address, and the line
  still being written is previewed above the status line. `/raw` prints
  answers exactly as written, for copying. The reasoning card now comes
  before the answer, and `Ctrl+O` mid-answer no longer repeats or drops
  lines. See [The terminal UI](docs/guide/the-tui.md).
- **The Windows service stays up.** On a Windows install in a virtual
  environment, the background service no longer stops right after it starts,
  whether the TUI, `deepcode service start`, or `deepcode web` launched it.
- **`/compact` handles coding sessions.** Tool-call arguments, such as the file
  a `write_file` call carries, now count when compaction picks what to keep and
  checks that the summary is smaller. Sessions that wrote large files compact
  instead of being refused, and the report shows the characters actually
  replaced. (#258)
- **Community fixes.** Diff lines that contain a Unicode line separator or a
  form feed stay whole (#253); web fetch refuses private addresses tunnelled
  through NAT64 or IPv4-compatible IPv6 (#254); the destructive-command screen
  also catches absolute paths such as `/bin/rm -rf /` (#255); and `glob` finds
  files when the project sits under a directory named `build` or `venv` (#256).
- **Security updates.** pypdf, PyJWT, urllib3, and multidict move to patched
  releases.

## 2026-10-10 · A steadier terminal UI: answers shown once, keys that keep your draft, and an orderly `/compact`

- **Answers appear once.** The TUI no longer prints an answer a second time
  when it finishes, and a stopped answer keeps the text already shown, in every
  client.
- **Keys that keep your draft.** `Esc` interrupts only from an empty prompt and
  otherwise closes a completion menu; `Ctrl+C` first clears what you are
  typing. Stopping a command no longer closes the TUI. See
  [The terminal UI](docs/guide/the-tui.md).
- **Calm when the service is slow.** Typing, completion, and `Ctrl+C` stay
  responsive while the background service is busy; a slow answer is reported
  instead of retried three times, and `/reconnect` brings your conversation
  back after a restart.
- **`/compact` waits its turn.** A message sent during compaction waits for the
  summary, the next reply no longer starts with a failed request, and a refusal
  says why instead of "internal error". See [Sessions](docs/guide/sessions.md).
- **Current settings, safer Goals.** Default model and access changes show up
  in the TUI, `/new` follows your defaults, and `/goal clear out …` sets a new
  objective instead of clearing the Goal.

## 2026-09-28 · DeepCode v2.3.0: voice dictation, Opper and Bedrock, Desktop polish, and opt-in hardening

- **Dictate your prompt.** Add a `dictation` block to your config and the
  Desktop and Web composer gains a microphone. See
  [Voice dictation](docs/guide/dictation.md). (#240)
- **More providers and models.** Opper and Amazon Bedrock join the provider
  templates, and DeepSeek V4 gets its own 1M context window and pricing.
  (#242, #208, #249)
- **Desktop polish.** Drag the conversation's edge to resize it, import a
  VS Code color theme, and choose from fonts that are actually installed.
  (#238, #207, #239)
- **Opt-in hardening.** `providers.egress` limits which hosts receive your
  prompts, `DEEPCODE_COMMAND_SCREEN=strict` screens risky commands,
  `DEEPCODE_BASH_SCRUB_ENV=1` keeps provider keys out of shell commands, and
  `DEEPCODE_KEYRING=1` reads keys from the OS keychain. (#229, #230, #232,
  #246)
- **Leaner memory and clearer signals.** `MEMORY.md` can be a short index of
  topic files, `mcp list` flags servers worth a second look, and the agent is
  reminded when a call keeps returning the same result. (#233, #241, #247)
- **Steadier everywhere.** The state database keeps its locks when several
  DeepCode processes share it, and a round of Windows fixes landed. (#210,
  #235, #236, #237)

## 2026-09-09 · TUI, Desktop, and Web share one local background service

- **Choose your interface.** Run `deepcode` for the TUI, `deepcode desktop`
  for the native app, or `deepcode web` to open the workbench in your browser.
  All three connect to the same local service; Web needs no DeepCode account.
- **Continue the same task across interfaces.** Closing a client leaves running
  work in the background. Reconnect from another interface to follow the same
  conversation, review tool activity, and respond to approvals. The computer
  must remain awake, and pending approvals still need your response.
- **Configure and verify your models.** Save cloud or local model connections
  for use across all three interfaces. Browse available models, check a model's
  response, and test streaming and tool calls before using a custom server.
  See [Models and providers](docs/guide/models.md).
- **Manage your background service and prepare for upgrades.** Use
  `deepcode service` commands to check status, read logs, or stop after current
  work finishes. Create a runtime snapshot before upgrading and restore it when
  needed; keep project files in your usual version control or backup.
  See [Upgrading DeepCode](docs/UPGRADE_AND_RESTORE.md).

Start with the updated [Quick start](README.md#quick-start) and
[first coding task tutorial](docs/guide/getting-started.md).
([#212](https://github.com/HKUDS/DeepCode/pull/212))

## 2026-09-06 · DeepCode v2.2.0: Session context-window caps, compaction that leaves a memory, and a fully localized Desktop

- **Cap a Session's context window.** `/context 64k` in the TUI or the
  preset next to the Desktop model picker narrows the model's published
  window for future Turns; the cap is frozen into each accepted Turn and
  feeds the compaction gate directly. `/context auto` follows the model
  again. (#203)
- **Compaction leaves a note in memory, and memory notes cannot escape their
  boundary.** A compaction summary is deposited into the workspace memory on
  a background thread, and injected memory content is wrapped in an
  `<untrusted-data>` boundary whose closing tags are escaped, so a poisoned
  note cannot forge instructions. (#204)
- **Appearance settings speak Simplified Chinese too**, completing the
  Desktop shell's zh-CN coverage. (#202)

## 2026-09-03 · Desktop catalog race, an honest command screen, GLM-5.2, and two dependency bumps

- **Switching projects no longer strands the MCP catalog.** A probe or
  mutation started in one project can no longer invalidate the load of the
  project you switched to, so the catalog stops sticking in a loading state.
  (#201)
- **The legacy `execute_bash` screen matches on argv, not substrings.**
  `rm -r -f`, `--recursive --force`, `chmod 0777` and a destructive stage
  inside a pipeline are caught; `touch rm-rf-notes.txt` is not. It stays a
  cheap first pass in front of the sandbox, which remains the boundary.
  (#195)
- **GLM-5.2 is in the model catalog** — 1M context, 128K output,
  `reasoning_effort` `high` / `max`. (from #198)
- **Security bumps:** `browserslist` past GHSA-c83g-rgw3-j3cx in the Desktop
  toolchain, and the App Server sidecar's `pypdf` to 6.16.1
  (CVE-2026-84309/84310/84311).

## 2026-08-28 · Community fixes: plugin credential warnings and a wider zh-CN Desktop

- **Agent Plugin manifests now warn about plaintext credentials.** Literal
  `env` and `headers` values in a Plugin's `mcp.json` that look like secrets
  produce a redacted warning instead of loading silently, and the docs point
  to runtime credential bindings instead. (#194, landed 2026-08-27)
- **Most of the Desktop shell speaks Simplified Chinese.** The thread header,
  Goal rail, approval card, inspector, sidebar, and the Updates and
  Diagnostics settings gained about a hundred zh-CN strings; English stays
  the inline default. (#187)

## 2026-08-25 · Command executors close two bypasses

- **Case and whitespace no longer slip past the dangerous-command check**, and
  the native `mkdir`/`touch`/`rm`/`cp`/`mv` fast path declines any operand
  outside the working directory, so those commands run through the sandbox
  like every other command instead of as in-process file operations. (#193)

## 2026-08-23 · Five repaired contributions land together

- **MCP servers can start lazily.** `deferLoading` keeps a server off until
  the agent calls `activate_server`; deferred servers stay reachable and
  cancellation-safe. (#184)
- **Hooks see the whole lifecycle.** `SessionEnd` runs exactly once at
  teardown and `PreCompact` can inject context before a compaction. (#172)
- **Instruction files can be excluded, and the dangerous preset must be named
  explicitly.** (#186)
- **Windows private files get a fail-safe ACL**, applied once at creation
  (#164); the Desktop sidecar's pip pin also moved past PYSEC-2026-3721 (#191).

## 2026-08-19 · Sessions that survive resume, compaction, and a second window

- **Resuming a session restores what the agent did, not only what it said.**
  Tool calls and their results are part of the canonical record now, so a
  resumed agent can answer "what did you just run?". A test makes the rule
  executable: every request a run sends must be rebuildable from the session
  file alone.
- **Compaction keeps the recent tail.** The checkpoint replaces the older
  range and everything recent survives verbatim — assistant messages and tool
  results included — instead of dropping them and putting the summary last.
  Manual `/compact` used to refuse almost every real conversation; it works,
  and the checkpoint speaks as the model's own history, so it stops being
  discounted as someone else's report.
- **Context pressure is measured, not guessed.** The gate now anchors on the
  size the provider itself reports. The built-in estimator prices Chinese
  prose at more than twice its real cost, which was compacting Chinese
  conversations at roughly half the context they could hold.
- **Two windows on one Session no longer crash each other.** Each Session
  now has one live writer, enforced by an OS lock held around a turn: the
  second window gets a sentence naming who is running it, instead of a
  process dying to a database constraint about half the time. Alternate
  freely between Desktop and the terminal — just not mid-turn.

## 2026-08-18 · Steadier context handling in the runtime, and the TUI gets a face

- **A new turn no longer throws away the prompt cache.** The environment
  block used to be re-inserted before your newest message every turn, so each
  turn diverged from the previous request right where that block had been:
  7,420 prompt tokens recomputed at every turn boundary, now 28.
- **The TUI has a logo, a live status line, and tool cards that read.** A
  spinner and a sweep while work runs, paths written the way you would type
  them, the plan tool's checklist visible at last, and a turn footer with its
  time and token usage. An idle TUI costs nothing to animate.
- **A source-level comparison with another agent runtime became a plan.** Where the message
  list the model sees comes from, what compaction should keep, and what the
  session record must contain — measured and written down, and four
  unreferenced documents retired.

## 2026-08-17 · Fixes from a review pass over the new settings and TUI work

- **A declared model reads the same everywhere.** Per-model declarations now
  shape the catalog a picker shows, not only what a Turn executes with, so a
  context window you set to avoid overflow is the number you see.
- **Adopting fetched models shows them immediately.** The catalog cache is
  keyed on the settings that shape it, instead of serving the previous remote
  listing for up to an hour.
- **Escape closes one thing.** Dismissing the provider editor no longer tears
  down the settings dialog around it and discards a half-typed key.
- **A removed model row takes its own capacities with it** rather than leaving
  them on the row that survived — which then saved them onto the wrong model.
- **Automated runs keep their tools.** A default agent preset chosen for
  interactive chatting no longer narrows `deepcode exec` and goal runs behind
  your back.
- **The provider section says it is user-scoped**, instead of letting the
  dialog's project write scope imply otherwise.

## 2026-08-16 · The TUI answers every bare command with a selector

- **Type the command, pick from a list.** `/model` now opens the full model
  directory — every configured connection's catalog (remote snapshot or
  declared entries, near a thousand rows on OpenRouter) in one filterable
  selector with the current route pinned first and each model's published
  reasoning ladder on Shift+Tab; Enter commits model and effort together.
  `/preset`, `/effort`, `/permissions`, `/transcript` and `/skill` join
  `/resume`: a bare invocation opens a picker with the current choice
  marked, while argument forms and piped runs keep their text paths.
- **Background tracebacks can no longer shred the transcript.** Stdlib
  logging is bridged into loguru with call-site fidelity, so file-only
  transports really are file-only; and the durable event relay backs off
  exponentially on persistent failure — one traceback per streak, one-line
  repeats, a recovery notice.

## 2026-08-15 · Settings grow up: a new settings dialog, declared models, and a safer config

- **A connection's key now reaches exactly the requests it was resolved
  for.** Building a provider no longer exports the key into the process
  environment, where every same-template connection could pick it up as its
  own; removing a provider un-configures every key source instead of letting
  it reappear; and when a launch environment variable shadows a pasted key,
  the editor says which one and that it wins. Verification also works from
  async hosts now.
- **The agent-preset picker grew up.** A menu with per-preset descriptions
  and trust marking replaces the cramped native dropdown, the duplicate
  "Default" row is gone, and a started session shows a read-only label of
  what it runs.
- **Manage what each connection actually serves.** The provider editor can
  fetch the live model list from a connection's endpoint and adopt picked
  models as its manual list, an environment-provided key locks the paste
  field instead of silently outranking it, and connection rows show the
  discovered model count.
- **`deepcode chat` reads like a terminal app, not a log.** A restyled
  transcript — brand-gradient banner, status-colored tool cards with result
  elbows, block spacing, word-boundary wrapping — plus streaming that
  survives the prompt redraw with real colors instead of `?[36m` litter.
- **Desktop Settings became a dialog.** General / Models /
  Plugins / Agent presets in a left rail, with Open configuration file and
  the write-scope picker in the header. General gains the five canonical
  rows — default agent preset for new sessions (applied as a by-value
  snapshot at creation, CLI and TUI included), permissions, language
  (English/中文, first translated batch), Light/Dark/System appearance
  cards, and a steer-or-queue busy-Enter preference.
- **Models are declarations now.** `manualModels` entries can carry a
  label, capacities, and the published reasoning ladder; declarations are
  authoritative offline for execution profiles and every picker. Discovery
  probes the editor form as shown — unsaved URL or key included — and
  adopted picks land as accurate declaration rows. Config writes gained
  optimistic concurrency (a changed file conflicts instead of being
  clobbered) and a settings.changed push keeps an open dialog fresh.
- **Session and model commands now hold up.** `/resume` and `/model` open
  an inline selector — type to filter by title, arrows move, Enter picks,
  Tab flips directory scope, Shift+Tab cycles the route's published
  reasoning effort — and resume replays the conversation tail. Bare model
  names resolve their connection, typos no longer kill the REPL, and
  `/compact` summarizes with the model you actually selected. New verbs:
  `/rename`, `/delete`, `/retry`, with tab completion for session ids,
  transcript modes, permission presets, and effort levels.

## 2026-08-14 · Subagent runtime, compaction, and one-way persistence

- **Delegate to Codex or Claude Code as a subagent.** `spawn_agent` gains
  external backends that run the installed CLI with a scrubbed environment and
  strict success criteria, while native subagents gain personas, validated
  tool allowlists, JSON output schemas, follow-up conversation via
  `send_message`, and full transcripts in the parent session.
- **Guard the loop.** Tools can declare timeouts the runner enforces, and a
  repeat-call tracker injects an escalating, model-visible reminder when
  identical calls repeat.
- **Compact on demand.** `/compact` summarizes older turns in the resident
  context; every refusal is a stable, readable reason (busy Turn, too little
  history, a failed or non-shrinking summary) and canonical session data is
  never touched.
- **See which file sets every configuration value.** Diagnostics report each
  configured leaf with its providing layer and a bounded preview;
  credential-shaped subtrees are redacted whole. A verbatim deepseek-harness
  Skill also loads unchanged from `.agents/skills`.
- **Relieve context pressure with the cheapest sufficient measure.** Under
  pressure the runtime first middle-prunes oversized tool results — a free,
  convergent pass covering MCP and custom tools alike — and only summarizes
  if that is not enough; the summarization call replays the routed request as
  a genuine prefix so provider prompt caching is reused, and a summary that
  would not shrink the conversation is rejected.
- **Deleted sessions stay deleted.** The Desktop projection no longer adopts
  a thread back into canonical storage unless SQLite genuinely owns it
  (legacy import or automation bootstrap), and every mid-turn message the
  model sees — goal updates, recovery prompts, injected results — now lands
  in the canonical session log, so a resumed session rebuilds exactly the
  history the model saw.
- **Open a session as a different agent.** Named agent presets bundle a
  persona and a tool face — `deepcode exec --preset code-reader`, `/preset`
  in the TUI, or the picker beside the Desktop model control — while the
  model route and permissions stay independent knobs. A preset is one
  markdown agent file in the `.claude/agents`/`.agents` dialect, so
  definitions written for other harnesses load unchanged; the resolved
  composition is snapshotted into the session record and locked once the
  conversation starts, at which point the Desktop picker becomes a
  read-only label of what the session runs.

## 2026-08-13 · Desktop typography, themes, and native controls

- **Make the type-size preference actually resize the app.** Every interface
  size now derives from one rem-based scale, so the Appearance slider moves the
  whole window. It previously moved almost nothing: 255 hardcoded pixel values
  ignored it, and the smallest labels sat at 7px.
- **Pick from eight themes, including high contrast.** Light, Dark, Paper, and
  Midnight are joined by two terracotta palettes and a WCAG AAA high-contrast
  option. Tests compare each palette against the complete token set, so a theme
  cannot half-apply and leave a stray default showing through.
- **See one design language rather than the operating system's.** Sliders,
  selects, and focus rings now follow the active theme instead of falling back
  to platform chrome, and an explicit theme choice moves `color-scheme` with it
  so native controls stop rendering in the OS scheme.
- **Read the quiet text.** The light palette's secondary tones and every accent
  colour were refitted to clear WCAG AA against the surfaces they actually sit
  on, several of which previously measured under 3:1.

## 2026-08-12 · Reviewed upstream core Skills, a managed MCP catalog, OAuth, and real connection checks

- **Start with reviewed upstream Skills.** DeepCode now bundles eight pinned,
  provenance-tracked Agent Skills from OpenAI, Codex, and Anthropic for Skill
  authoring, code review, security analysis, frontend design, MCP construction,
  and web application testing. They use the same catalog and Turn runtime as
  project, user, and Plugin Skills.

- **Start from reviewed templates without hidden execution.** DeepCode bundles
  a validated catalog based on Nanobot's current 16 MCP templates. Adding
  one copies ordinary user configuration in a disabled state; it never silently
  runs `npx`, Docker, or a remote server.
- **Configure MCP consistently in every interface.** Desktop and TUI now join
  the management CLI and App Server on one preset, configuration, test, OAuth,
  and live-status service rather than maintaining interface-specific clients.
- **Distinguish config, authorization, and connectivity.** A real MCP probe
  initializes the server and counts tools, resources, and prompts. Browser
  OAuth uses a loopback callback and a private credential file outside project
  configuration, while Agent startup never opens a browser implicitly.

## 2026-08-10 · Generic MCP runtime and portable local Plugins

- **Connect standard MCP servers from every interface.** User and trusted
  project configuration, App Server, CLI, and Desktop now share one generic
  stdio/SSE/Streamable HTTP client runtime with bounded discovery, stable tool
  identities, timeouts, cancellation, and session-owned cleanup.
- **Keep credentials and authority explicit.** Executable config layers replace
  whole entries, child processes receive a minimal environment, provider keys
  stay in private DeepCode connections, and MCP policy can only narrow global
  trust, read-only, approval, and sandbox decisions.
- **Run Agent Plugins 1.0 MCP components without format forks.** Valid
  `mcp.json` servers join the same runtime only when an Agent Session starts,
  receive isolated `PLUGIN_ROOT` / `PLUGIN_DATA`, and fail independently from
  bundled Skills and other servers.
- **Connect OpenSpace as MCP plus two ordinary Skills.** A reviewed recipe binds
  an OpenRouter connection without plaintext secrets, imports OpenSpace's host
  Skills into the standalone catalog, and keeps cloud/upload tools disabled by
  default.

- **Register trusted local Plugin folders.** CLI and Desktop now share a
  user-level Plugin registry with add, inspect, enable, disable, and unregister
  operations; unregistering never deletes source files.
- **Keep one execution path.** Enabled Plugins contribute authority-bound
  Skills through the existing Skill Provider host, so selection, dependencies,
  progressive reads, permissions, revisions, and audit metadata remain the
  same across CLI, TUI, headless execution, and Desktop.
- **Preserve standalone Skills.** Project and user Skills remain independently
  installable and take precedence over a same-named Plugin Skill. Plugins are
  an optional packaging source, never a replacement Skill lifecycle.
- **Use one portable package contract.** Local Plugins use the Agent Plugins
  1.0.0 root manifest and fixed `skills/` layout; experimental DeepCode
  manifests are no longer accepted. Hooks, Apps, Marketplaces, downloads, and
  updates remain inert.

## 2026-08-09 · Skills now have a real runtime contract

- **Load guidance through one provider boundary.** Skill discovery, content
  reads, and package search now stay with the provider that owns the Skill,
  while the catalog remains metadata-only. Local Skills keep their existing
  precedence and identity, and future providers no longer need filesystem
  shortcuts to fit the runtime.
- **Compose Skills without hiding missing capabilities.** Skills can declare
  tool and Skill dependencies; DeepCode expands them in order, detects cycles,
  and fails before the first model request when a requirement is unavailable.
- **Reveal only what the task needs.** The Agent can search and read bounded
  package resources progressively, with revision, traversal, symlink, and size
  checks applied at the shared provider contract.
- **Keep execution constrained and auditable.** A Skill can narrow the tools
  already allowed by the Session but cannot grant new permissions. CLI, TUI,
  and Desktop share the same immutable Turn snapshot and persist only Skill
  identity, invocation kind, and revision—not the instruction body.
- **See changes everywhere without restarting.** One workspace host now owns
  the shared catalog and provider cache, while each Agent Session keeps its own
  Turn context. Local Skill and policy changes refresh Desktop automatically,
  and Composer, management, CLI, and headless execution resolve through the
  same lifecycle.

## 2026-08-07 · Thinking controls, more providers, and a Desktop you can tune

- **Reasoning controls work again across the board.** Thinking levels are now
  resolved from the model catalog, so Claude, GPT-5, Kimi, Qwen and Grok all
  offer their real effort levels, and DeepSeek, GLM and MiniMax get a working
  thinking toggle. A newly released model inherits its family's controls
  instead of silently losing them.
- **Three more providers.** Requesty and Forge join as gateways, and MiniMax
  arrives with its own catalog entry — including the 1M-context M3 tier.
- **Make the Desktop yours.** Settings → Appearance adds conversation width,
  a light/dark override that no longer follows the OS blindly, font size, and
  font selection filtered to what is actually installed on your machine.
- **Safer command execution on Windows.** A Job Object backend gives the
  sandbox real process-tree isolation where previously there was none.

## 2026-08-04 · Skills that work wherever you do

- **Keep reusable expertise close to your work.** DeepCode discovers Skills
  from the current project as well as your personal collection, while keeping
  existing DeepCode and Claude-compatible Skill locations working.
- **Use the same Skill from CLI or Desktop.** Select it for a task and DeepCode
  carries its identity and version with the Turn, so the Session shows which
  guidance shaped the result.
- **Create Skills without leaving DeepCode.** The built-in Skill Creator helps
  you scaffold and validate focused, reusable workflows from either interface.
- **Stay focused as your library grows.** Context-aware discovery keeps the
  Agent prompt concise while the complete Skill catalog remains available to
  browse and manage.

## 2026-08-03 · 🎉 DeepCode v2.0 is here

DeepCode v2.0 introduces a new general-purpose Coding Agent framework for
building, fixing, understanding, and improving real software projects.

- **Take on real repository work.** DeepCode can explore a codebase, edit
  files, run commands and tests, review changes, and carry a task through to a
  working result.
- **Keep complex goals moving with Loop Engineering.** Give DeepCode a goal and
  it can continue through understanding, implementation, verification, and
  repair instead of stopping after one plausible answer.
- **Stay in control while the Agent works.** Add requirements, correct its
  direction, switch models, stop, resume, or revise the goal without throwing
  away the work already completed.
- **See what you are getting.** Plans, tool activity, code changes, test results,
  and verification evidence stay visible so the result is easier to review and
  trust.
- **Build Automations around the way you work.** Turn any natural-language
  instruction into a project-specific task: run it on demand or on a recurring
  interval, then edit, pause, resume, and review every result. Use it for the
  work you want DeepCode to keep taking care of, from regression checks and
  test repair to documentation upkeep and repository maintenance.
- **Work your way.** Use Desktop or CLI, bring your own models and Skills, and
  delegate focused work without changing the underlying Agent workflow.

DeepCode v2.0 is built to help you spend less time supervising every step and
more time shipping software you are proud of. We cannot wait to see what you
build! 🚀

## 2026-07-31 · One execution model across CLI and Desktop

- Interactive, headless, Goal, Automation, and Desktop work share the same durable Project, Session, Thread, and Turn lifecycle. Workspace trust stays independent from the Session access preset, and reasoning controls remain model-aware.

## 2026-07-21 · Durable Goals and safe Session lifecycle

- Long-running Goals are resumable across CLI and Desktop, while guarded archival and deletion preserve repository files and recover safely after interruption.

## 2026-07-20 · Session-level model control and shared Skills

- Named LLM connections and future-Turn model switches preserve conversation history; project and user Skills are shared across entry points.

## 2026-07-17 · Durable Session navigation and replay

- Projects organize collapsible Session history, long conversations replay incrementally, and approvals, reviews, tests, and Artifacts remain attached to their task.

## 2026-07-10 · Loop Engineering and parallel agents

- Mutable Goals can inspect, implement, verify, and repair across steerable Turns, while focused work can be delegated in isolated worktrees with explicit conflict handling.

## 2026-07-08 · General coding agent, durable Sessions, and memory

- Session history survives restarts, project instructions can live in `AGENTS.md` or `DEEPCODE.md`, and persistent notes remain with the workspace.

- The free-form TUI, native file and shell tools, headless execution, context compaction, and cross-directory resume established the current product foundation.

## 2026-07-04 · Agent Harness foundation

- A shared execution contract, three-valued permissions, sensitive-path protection, platform sandboxing, and normalized events made supervised local execution possible.

The complete pre-restructure history is preserved in the [legacy README](docs/archive/README_LEGACY_2026-07-20.md).
