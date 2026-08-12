# DeepCode Skills product architecture

Status: implementation contract
Scope: Skills, local Agent Plugins 1.0 packages, and their MCP components.
Marketplace, remote distribution, Hooks, Apps, and arbitrary Plugin lifecycle
code are explicitly out of scope.

## Product invariants

1. Desktop, interactive CLI, and headless CLI use the same Skill catalog,
   resolver, and runtime. A frontend may render a selection, but it never reads
   or injects `SKILL.md` itself.
2. Existing `UserInput(text=...)`, Agent loop, canonical SessionStore, and
   cross-directory Session discovery remain backward compatible.
3. A Skill never grants filesystem, process, network, or tool permissions. Its
   `allowed-tools` declaration can only narrow the tools already allowed by the
   Session policy.
4. Project Skills are code from the working tree. Desktop may expose their
   metadata while a Project is untrusted, but it must not expose instructions
   or execute a Turn until the Project is trusted. Starting the CLI with an
   explicit workspace is the CLI trust grant already implied by its ability to
   execute code in that workspace.
5. A running Turn consumes an immutable Skill snapshot. Filesystem or
   configuration changes affect the next Turn only.
6. Historical Skill references are audit data. Resuming a Session never
   re-invokes a historical Skill.

## Identity and revisions

`SkillKey` is `(catalog_skill_path, scope, source_root, relative_path)`.

- `catalog_skill_path`: the absolute path of the catalog entry before resolving
  a symbolic-link target, hashed into the ID and never exposed as part of the
  protocol identity; this keeps intentional aliases independently addressable
- `scope`: `project`, `user`, or read-only `system`
- `source_root`: canonical `agents`, compatibility `deepcode` / `claude`, or
  bundled `system`
- `relative_path`: the normalized path of the Skill directory below its
  configured root

The backend derives an opaque `skillId` from the key. Absolute paths are never
accepted from protocol clients as Skill identity. Moving a Project or Skill
creates a new identity. Editing it in place preserves identity and changes its
SHA-256 `revision`. User-owned aliases may target plugin/cache locations.
Project aliases may target only paths inside the trusted workspace.

Catalog entries retain all valid candidates. Discovery precedence is:

1. project `.agents/skills`, from the working directory up to the registered
   workspace boundary;
2. project `.deepcode/skills` and `.claude/skills` compatibility roots;
3. user `~/.agents/skills`;
4. user `~/.deepcode/skills` and `~/.claude/skills` compatibility roots;
5. bundled system Skills.

New imports and creator output use `.agents/skills` only. Compatibility roots
are never migrated, overwritten, or deleted automatically, so existing IDs and
Sessions remain valid.

Only the first enabled candidate is active for implicit name lookup. Other
candidates remain visible as `shadowed`. Structured selection by `skillId` is
unambiguous. A plain `$name` that is ambiguous among selectable entries fails
before model execution instead of guessing.

## Provider boundary

`SkillRuntime` depends on a `SkillProvider` contract with three operations:

- `list` returns the immutable catalog snapshot used for one Turn;
- `read` resolves an opaque package resource through the Provider that listed it;
- `search` performs bounded catalog or package-resource search.

The initial `LocalSkillProvider` owns project, user, compatibility, and bundled
system roots. It retains the existing discovery order, filesystem cache,
policy layering, IDs, revisions, statuses, and warnings. The previous
`SkillCatalogProvider` name remains a compatibility alias.

Runtime code must not turn a provider record into an ambient local path and
then bypass the provider for read or search. This keeps the source boundary
usable by later providers without adding remote installation, federation,
marketplace, or Plugin behavior to this release.

Every catalog record carries a `SkillReference` composed of:

- `authority`: provider kind plus an opaque provider ID;
- `package`: an opaque package ID;
- `resource`: an opaque provider-owned resource ID.

The local authority is `local:local`. Its package remains the existing opaque
`skillId`, and its main resource is `SKILL.md`; adding provider identity does
not change existing Skill IDs, revisions, precedence, or source labels.

`SkillProviderSource` declares which authorities a Provider owns.
`SkillProviders` routes `read` and `search` only to an owning source, rejects
catalog entries from an unowned authority, rejects duplicate package identities,
and verifies that returned resources still belong to the requested
authority/package/resource. A Turn retains the immutable reference on its
selected `SkillRecord`.

Configured Provider catalogs are merged in source order. Existing local
precedence remains inside the local Provider; the first active package with a
given name wins across Providers and later candidates remain visible as
`shadowed`. A Provider may explicitly report a temporary availability failure,
which becomes a bounded warning while the remaining catalogs continue.
Validation, ownership, and policy errors are not swallowed. A colliding
`skillId` across authorities is visible in the catalog but cannot be selected by
ID, because `SkillSelection` intentionally remains backward-compatible and
contains no Provider authority.

## Host lifecycle and change propagation

`SkillCatalogHost` is the application-lifetime owner for one canonical
workspace. It owns the layered `SkillPolicyStore`, the provider router, and
provider discovery caches. `SkillWorkspaceRegistry` canonicalizes paths and
ensures Application services, interactive CLI Sessions, headless Turns, and
the App Server all obtain the same host for that workspace.

An `AgentSession` never shares mutable Turn context with another Session. The
registry creates a distinct `SkillRuntime` for each Session over the host's
shared provider backend; `begin_turn` still creates an immutable catalog and
instruction snapshot in a runtime-local `ContextVar`. Replacing a provider,
editing a Skill, or changing policy therefore affects only a subsequent Turn.

The host has two source layers:

- base sources owned by the core runtime, currently the local Provider;
- keyed contributors owned independently by extension systems.

`SkillWorkspaceRegistry.register_contributor` assigns each owner a lifecycle
handle. Refreshing or unregistering one contributor recomputes the combined
source set without overwriting another owner's Providers. MCP uses a parallel
session-plan contribution boundary; it is not represented as a Skill Provider.

`LocalPluginHost` is the first production contributor. It reads a user-owned
registry, resolves Agent Plugins 1.0.0 packages into format-neutral metadata
and component models, and contributes one authority-bound source per enabled
Plugin installation. There is no legacy manifest adapter. The Skill runtime
has no Plugin-format branch and does not interpret Plugin paths. Standalone
local Skills remain the base source and win deterministic precedence over a
same-named contributed Skill.

The registry separates installation identity from package metadata. Each entry
has an opaque `plg_...` ID, the declared package name, enabled state, and a
typed linked-directory source. Skill authorities use the installation ID, so a
package rename cannot silently reuse an earlier provider identity.

Plugin discovery is inert. It never imports Plugin code or starts a process.
The fixed Agent Plugins 1.0 `mcp.json` component is schema-validated during
discovery, then converted into immutable server contributions only while an
Agent Session is assembled. The generic MCP runtime, not the Plugin host, owns
transport startup, tool publication, timeout, cancellation, and shutdown.
Registry, manifest, and fixed-component revisions are monitored at application
lifetime; Skill file changes remain owned by the contributed Provider.
Disabling or removing a Plugin replaces sources for the next Turn, while an
active Turn retains its catalog and provider-routing snapshot. Removing a
registration never deletes the Plugin source directory.

`SkillCatalogMonitor` is one portable, application-owned monitor for every
registered workspace. It polls bounded, equality-comparable change tokens from
mutable Providers. The local token covers every discoverable `SKILL.md`,
optional OpenAI metadata, and effective user/project policy. A change
invalidates the shared host and publishes one workspace event. This avoids a
platform-specific watcher dependency in the packaged sidecar while retaining
deterministic manual polling in tests. Providers with their own push channel
may omit a token and call the registry's explicit `invalidate` boundary through
their contribution owner.

`SkillService` maps changed workspaces back to registered Project IDs. The App
Server emits a body-free `skills.changed` notification containing only the
Project ID. Desktop keeps one runtime-scoped Catalog Store shared by Composer
and the Skills workspace, deduplicates concurrent loads, and force-refreshes
that Project on the notification. Detail views are discarded when their Skill
revision disappears or changes.

## Catalog, content, and package resources

Catalog records contain identity, description, policy, dependency metadata,
status, byte size, and revision, but never retain the `SKILL.md` body. Selection
and progressive disclosure issue a `read` to the same Provider that listed the
package. The returned package revision must match the catalog revision before
the body can enter a Turn snapshot. A body changed between list and read fails
closed instead of mixing revisions.

`SkillReadResult` carries text, package revision, resource revision, and the
exact `SkillReference`. `SkillSearchMatch` carries an exact reference plus a
bounded title and snippet. Provider responses are size-checked at the shared
contract, so a custom Provider cannot bypass local limits.

The `skill` tool accepts a Skill name and optionally either `resource` or
`query`. With neither it loads the main instructions. `resource` reads one
opaque resource and `query` searches within the selected package. Callers never
convert remote resource IDs into filesystem paths. The local Provider accepts
relative POSIX paths only, rejects traversal and symlink escape, reads UTF-8
text within a fixed byte limit, and searches a deterministic bounded number of
files.

## Dependencies and capabilities

Optional `agents/openai.yaml` metadata may declare `dependencies.tools` and
`dependencies.skills`. Tool dependency records retain Codex-compatible
`type`, `value`, `description`, `transport`, `command`, and `url` fields. DeepCode
currently resolves `tool`, Codex-compatible `mcp` / `cli`, and the `command`
alias; an unsupported type is blocked explicitly instead of being guessed.

At Turn start, `AgentSession` supplies the actually registered tool names.
The capability resolver reports `ready`, `unavailable`, or `blocked`, validates
`allowed-tools` consistency, expands Skill dependencies in topological order,
detects cycles, and fails before the first model request. Dependency Skills use
the same Provider reads, revision checks, count budget, character budget,
invocation ledger, and permission narrowing as selected Skills. Dependencies
never register a tool, bypass Session permissions, or turn a resource into an
ambient executable path.

## Invocation semantics

An explicit selection is submitted as a structured `SkillSelection`. The
backend resolves it against the Session's immutable catalog snapshot and
injects the exact instructions before the first model request.

The base DeepCode prompt, permission policy, and security boundaries remain
system instructions. Skill catalog metadata is developer-priority capability
guidance; selected Skill bodies are transient user-priority context for the
current Turn. The runner folds privileged guidance into the provider's single
instruction block, reattaches selected Skill context to each task request, and
excludes both from Session history and compaction summaries. This gives OpenAI,
Anthropic, and compatible providers one consistent role model without
elevating user-authored Skill bodies.

Plain `$name` mentions are a compatibility path. They are parsed in the core
runtime, never in a frontend, and become explicit selections only when the name
is unambiguous. The visible user message is not rewritten.

The model may still use the progressive-disclosure `skill` tool for implicit
selection. A per-Turn invocation ledger ensures the same Skill revision is
injected at most once, regardless of whether it was selected explicitly and
then requested by the model.

All explicit, `$name`, and progressive-disclosure loads share one Turn budget.
Multiple Skills:

- retain user selection order;
- are deduplicated by `(authority, package)`;
- are limited to eight loaded Skills per Turn;
- share a bounded instruction budget;
- fail before model execution if a selected Skill is disabled, missing,
  invalid, ambiguous, or over budget.

Skill instructions are contextual workflow guidance. They are lower priority
than system, security, sandbox, approval, and explicit user requirements.

## Authoring and optional metadata

The bundled `skill-creator` is a normal read-only system Skill. CLI invokes it
with `$skill-creator` or `/skill skill-creator`; Desktop's Create Skill action
starts a normal Thread with the same Skill selected. Creation therefore uses
the ordinary AgentSession, tools, trust, approvals, and audit trail.

`agents/openai.yaml` is optional. Supported interface fields are
`display_name`, `short_description`, relative icon paths, `brand_color`, and
`default_prompt`. `policy.allow_implicit_invocation` defaults to true. False
removes a Skill from model-driven discovery while preserving structured and
`$name` selection. `dependencies.tools` and `dependencies.skills` declare
capability requirements and composable Skill prerequisites. Invalid optional
metadata produces a bounded catalog warning and never invalidates an otherwise
valid `SKILL.md`.

## Configuration

User and project `deepcode_config.json` files may contain:

```json
{
  "skills": {
    "disabled": ["sk_..."]
  }
}
```

Effective disabled IDs are the union of the user and project layers. A project
cannot re-enable a Skill disabled by the user. Configuration writes are
validated, locked, atomic, and preserve unrelated configuration.

## Protocol and persistence

`turn/start` and `turn/enqueue` retain `prompt` and add an optional `skills`
array. Old clients remain valid.

Skill management methods:

- `skills/list`
- `skill/read`
- `skills/import`
- `skills/set-enabled`
- `skills/delete`
- `skills/reload`

Catalog responses also expose provider-neutral presentation and capabilities:

- `originKind`, `originLabel`, and `location` for display;
- `providerKind`, `providerId`, and `packageId` for opaque ownership;
- `configurableScopes` so a frontend never guesses whether policy can change;
- `authoringSkillId`, resolved from a backend-only bundled-role registry.

Frontends must not infer these values from paths, source strings, or a Skill
name. `skills.changed` is an ephemeral invalidation notification, not durable
Session history and never contains instructions.

User Session messages remain ordinary text. New clients add versioned metadata:

```json
{
  "schemaVersion": 2,
  "client": "desktop",
  "turnId": "turn_...",
  "skillInvocations": [
    {
      "skillId": "sk_...",
      "name": "security-review",
      "revision": "sha256:...",
      "source": "project:deepcode",
      "invocation": "explicit"
    }
  ]
}
```

Old readers ignore the metadata. New readers treat missing metadata as a
legacy text-only message.

Runtime lifecycle events never contain Skill bodies:

- `skill.loaded`
- `skill.load_failed`

Logs and telemetry may contain opaque IDs, source labels, revision hashes, and
error classes, but not Skill instructions or user prompts.

## Resource and security limits

- one `SKILL.md`: 64 KiB UTF-8 maximum;
- one catalog response/runtime snapshot: at most 256 entries, with an explicit
  truncation warning;
- model discovery directory: at most two percent of a known model context
  window, or 8,000 characters when the window is unknown; descriptions are
  shortened before entries are omitted;
- one Turn: at most eight loaded Skills across every invocation path;
- injected Skill instructions: 48,000 characters maximum until the runtime
  owns a model-token estimator;
- catalog warnings: at most 100 per response;
- imported folder: one Skill, no symlinks, no overwrite without an explicit
  request, atomic destination replacement;
- optional Skill metadata: 32 KiB maximum with relative, in-directory assets;
- optional dependencies: at most 64 tool and Skill entries combined;
- one Provider resource response: 1 MiB UTF-8 maximum;
- local resource search: at most 256 files, 256 KiB per searched file, and 100
  matches per request;
- no Git or network installation in this release.

## Release gates

The feature is complete only when:

- Desktop and CLI return identical IDs and revisions for the same workspace;
- every surface in one process resolves through the same workspace host while
  AgentSession Turn contexts remain isolated;
- external Skill or policy changes invalidate the host, emit
  `skills.changed`, and become visible on the next Turn without mutating the
  current Turn;
- Desktop Composer and management views share one deduplicated catalog state;
- a fake contributed Provider works through the public source seam without
  local-path access or frontend special cases;
- structured and unambiguous `$name` invocation resolve to the same snapshot;
- explicit instructions are present before the first model-provider call;
- catalog listing retains no instruction bodies and every content read stays on
  the listing Provider;
- Provider outage isolation never hides validation, ownership, or policy errors;
- resource traversal/symlink escape and oversized Provider responses fail
  deterministically;
- missing, conflicting, and cyclic dependencies fail before model execution;
- Skill bodies never appear in system messages or persisted history;
- project/user/system `.agents` discovery and legacy roots remain deterministic;
- bundled creator scripts reject overwrite and pass production validation;
- duplicate, disabled, invalid, stale, oversized, and untrusted cases fail
  deterministically;
- permission and Hook behavior remains unchanged;
- old Sessions and text-only clients pass regression tests;
- Python unit/contract/E2E tests, Desktop Vitest/typecheck/build, lint, and
  pre-commit all pass from the current source state.
