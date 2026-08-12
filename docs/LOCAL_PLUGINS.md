# Local Plugins

A Skill remains a first-class DeepCode resource. It can live independently in
`.agents/skills`, `~/.agents/skills`, or a supported compatibility root. A
Plugin is an optional package that can contribute additional Skills to the
same catalog; it does not replace the standalone Skill lifecycle.

```text
Standalone Skill ─┐
                  ├── SkillCatalog → SkillRuntime
Plugin Skill ─────┘
```

Local Skills retain precedence when a Plugin contributes a Skill with the same
name. The Plugin copy remains visible as shadowed metadata instead of changing
the active local workflow.

## Supported package format

DeepCode recognizes Agent Plugins 1.0.0 and its fixed Skill location:

```text
review-tools/
├── plugin.json
├── skills/
│   ├── review/SKILL.md
│   └── verify/SKILL.md
└── mcp.json                 # optional Agent Plugins 1.0 MCP component
```

The smallest manifest is:

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "review-tools"
}
```

`version` and `description` are optional metadata. No experimental DeepCode
manifest fallback exists: `$schema` must identify Agent Plugins 1.0.0. Skills
are discovered only as immediate child directories of the fixed `skills/`
directory; the manifest does not list Skill paths. Unknown manifest fields are
reported and ignored. Unknown `extensions` namespaces remain opaque and are
not validated by DeepCode.

Plugin Skills use the Agent Skills contract: the frontmatter `name` is required,
must be a lowercase slug, and must exactly match its parent directory. An
invalid Skill is skipped with a diagnostic while the remaining Skills and
components continue loading. Standalone DeepCode Skills retain their existing
compatibility parser; this stricter package boundary does not migrate or break
user Skill directories.

DeepCode validates the Agent Plugins 1.0 `mcp.json` contract independently of
the manifest and Skills. A valid component contributes stdio, SSE, or
Streamable HTTP servers to the same session-scoped MCP runtime used by
standalone configuration. Invalid server entries are skipped independently;
an invalid MCP component does not disable valid Plugin Skills.

Discovery remains inert: listing or registering a Plugin never imports Python
code or starts a process. When an Agent Session first uses the package,
DeepCode supplies immutable `PLUGIN_ROOT` and installation-specific
`PLUGIN_DATA` paths, applies tool filters and permissions, then starts the MCP
server. Startup failure is non-fatal unless a user policy explicitly marks the
server required.

The package cannot embed DeepCode credential references. User configuration
may bind credentials and narrow an active Plugin server without replacing its
command, URL, arguments, or working directory:

```json
{
  "pluginMcpServers": {
    "review-tools/analyzer": {
      "enabledTools": ["inspect"],
      "approvalMode": "writes",
      "credentialEnv": {
        "SERVICE_API_KEY": {
          "credentialRef": "provider:service"
        }
      }
    }
  }
}
```

## Local lifecycle

```console
deepcode plugin add ./review-tools
deepcode plugin list
deepcode plugin disable review-tools
deepcode plugin enable review-tools
deepcode plugin remove review-tools --yes
```

The registry lives under `$DEEPCODE_HOME/plugins/registry.json` (normally
`~/.deepcode/plugins/registry.json`). `add` assigns an opaque `plg_...`
installation ID and links the canonical source directory; it does not copy it.
`remove` unregisters the installation; it does not delete the source. Desktop
uses installation IDs and exposes the same operations in the Plugins workspace.

Plugin Skills use the ordinary `/skill`, `$name`, `--skill`, and Desktop
Composer flows. Plugin MCP tools use the ordinary `mcp__server__tool` catalog,
approval, timeout, and cancellation flow. Disabling a Plugin invalidates idle
Sessions; an already running Turn keeps its immutable Skill and tool snapshot.

## Security boundary

DeepCode rejects oversized or malformed manifests, duplicate JSON keys,
unsupported schemas, external symbolic-link targets, and authority/package
mismatches. An invalid package contributes no Skills; invalid fixed components
and individual Skills are reported independently; standalone and bundled
Skills continue to work in every case.

There is no Marketplace, Git download, remote update, signing, rollback, Hook,
App, or arbitrary Plugin-code hook execution in this phase. An MCP subprocess
declared by a valid `mcp.json` is executable code and therefore runs only as a
session capability, with a minimal environment and explicit user policy.
