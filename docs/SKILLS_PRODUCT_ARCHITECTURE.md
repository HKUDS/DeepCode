# DeepCode Skills product architecture

Status: implementation contract
Scope: Skills only; MCP, plugins, marketplaces, and remote Skill distribution are
explicitly out of scope.

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
- `scope`: `project` or `user`
- `source_root`: `deepcode` or `claude`
- `relative_path`: the normalized path of the Skill directory below its
  configured root

The backend derives an opaque `skillId` from the key. Absolute paths are never
accepted from protocol clients as Skill identity. Moving a Project or Skill
creates a new identity. Editing it in place preserves identity and changes its
SHA-256 `revision`. User-owned aliases may target plugin/cache locations.
Project aliases may target only paths inside the trusted workspace.

Catalog entries retain all valid candidates. For a duplicated, case-folded
name, the existing precedence is preserved:

1. project `.deepcode/skills`
2. project `.claude/skills`
3. user `~/.deepcode/skills`
4. user `~/.claude/skills`

Only the first enabled candidate is active for implicit name lookup. Other
candidates remain visible as `shadowed`. Structured selection by `skillId` is
unambiguous. A plain `$name` that is ambiguous among selectable entries fails
before model execution instead of guessing.

## Invocation semantics

An explicit selection is submitted as a structured `SkillSelection`. The
backend resolves it against the Session's immutable catalog snapshot and
injects the exact instructions before the first model request.

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
- are deduplicated by `(skillId, revision)`;
- are limited to eight loaded Skills per Turn;
- share a bounded instruction budget;
- fail before model execution if a selected Skill is disabled, missing,
  invalid, ambiguous, or over budget.

Skill instructions are contextual workflow guidance. They are lower priority
than system, security, sandbox, approval, and explicit user requirements.

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
- model discovery preamble: at most 64 entries and 16,000 characters;
- one Turn: at most eight loaded Skills across every invocation path;
- injected Skill instructions: 48,000 characters maximum until the runtime
  owns a model-token estimator;
- catalog warnings: at most 100 per response;
- imported folder: one Skill, no symlinks, no overwrite without an explicit
  request, atomic destination replacement;
- no Git or network installation in the first production release.

## Release gates

The feature is complete only when:

- Desktop and CLI return identical IDs and revisions for the same workspace;
- structured and unambiguous `$name` invocation resolve to the same snapshot;
- explicit instructions are present before the first provider call;
- duplicate, disabled, invalid, stale, oversized, and untrusted cases fail
  deterministically;
- permission and Hook behavior remains unchanged;
- old Sessions and text-only clients pass regression tests;
- Python unit/contract/E2E tests, Desktop Vitest/typecheck/build, lint, and
  pre-commit all pass from the current source state.
