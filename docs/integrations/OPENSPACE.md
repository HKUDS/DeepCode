# OpenSpace with DeepCode

OpenSpace connects to DeepCode through two ordinary extension seams:

1. `openspace-mcp` is a standard MCP server in DeepCode's top-level
   `mcpServers` configuration.
2. OpenSpace's `skill-discovery` and `delegate-task` directories are imported
   as ordinary standalone DeepCode Skills.

There is no OpenSpace-specific runtime, tool-name switch, or Plugin adapter in
DeepCode. This is intentional: the same MCP lifecycle, credential references,
tool filtering, permissions, timeouts, cancellation, and Skill dependency
checks apply to OpenSpace and every other MCP server.

## Install OpenSpace

OpenSpace currently requires Python 3.12 or newer. Install it from its source
repository and verify its declared console entry point:

```console
git clone https://github.com/HKUDS/OpenSpace.git
cd OpenSpace
python3.12 -m pip install -e . "mcp>=1.29,<2"
openspace-mcp --help
```

The explicit MCP range is currently required because OpenSpace's source uses
the MCP Python SDK 1.x `FastMCP` import while its upstream dependency range is
still `mcp>=1.0.0`; without the upper bound a fresh resolver can install MCP
2.x and make `openspace-mcp` fail before startup. DeepCode's own MCP client is
also pinned below 2, but the OpenSpace executable needs the constraint in its
separate Python environment.

Keep the OpenSpace repository available when importing its host Skills. The
runtime recipe uses DeepCode's `${workspace}` placeholder, so one user-level
MCP definition follows the active CLI, Desktop, or App Server project instead
of hard-coding one task repository.

## Import the two host Skills

Choose either a project catalog or the user catalog. Project imports below are
copied into `<project>/.agents/skills` and remain reviewable repository files:

```console
cd /absolute/path/to/project
deepcode skill import /absolute/path/to/OpenSpace/openspace/host_skills/skill-discovery
deepcode skill import /absolute/path/to/OpenSpace/openspace/host_skills/delegate-task
deepcode skill list
```

Use `--scope user` on both import commands to make the Skills available to all
projects. The example resolves `OPENSPACE_HOST_SKILL_DIRS` to the active
project's `.agents/skills` directory, not to OpenSpace's source copy.

## Add the MCP server

Copy
[`examples/mcp/openspace.deepcode_config.json`](../../examples/mcp/openspace.deepcode_config.json)
into the relevant parts of `~/.deepcode/deepcode_config.json`, then replace the
model if needed. The example deliberately:

- resolves the OpenRouter key from DeepCode's private provider credential
  store instead of serializing it into JSON;
- disables implicit `.env` loading so the child receives only declared
  credentials;
- keeps OpenSpace cloud-auth, cloud import, and upload tools disabled;
- asks before long-running or mutating OpenSpace tools and permits local
  `search_skills` to inherit DeepCode's global policy;
- gives `execute_task` up to 1,200 seconds, matching OpenSpace's guidance for
  long tasks.

DeepCode expands `${workspace}` only when the MCP process starts. It is used
for the child working directory, `OPENSPACE_WORKSPACE`, and
`OPENSPACE_HOST_SKILL_DIRS`; it is not an ambient shell-variable expansion.

The referenced DeepCode connection must exist:

```console
deepcode provider set openrouter --template openrouter --api-key
deepcode provider test openrouter --model <model-id>
deepcode mcp list
```

The same server can be created in Desktop under **MCP → Connect an MCP
server**. Select user scope for credential bindings.

## Permit nested coding deliberately

Approving `mcp__openspace__execute_task` authorizes one outer MCP call. The
OpenSpace agent started by that call has its own permission engine and cannot
open a DeepCode approval dialog. In OpenSpace's default mode, headless file
writes therefore stop with `permission_denied` even after DeepCode approves the
outer call.

For a coding project, create a gitignored
`<project>/.openspace/settings.local.json` with the narrowest paths and test
commands that OpenSpace may use. For example:

```json
{
  "permissions": {
    "defaultMode": "default",
    "allow": [
      "Edit(src/**)",
      "Edit(test/**)",
      "Write(src/**)",
      "Write(test/**)",
      "Bash(npm test:*)",
      "Bash(git diff:*)",
      "Bash(git status:*)"
    ]
  }
}
```

Adapt those rules to the repository. `acceptEdits` is broader because it
allows non-sensitive writes throughout the working directory;
`bypassPermissions` is broader still and is not the recommended default.
DeepCode intentionally does not generate or weaken this OpenSpace policy.

## Verify the complete path

Start a new DeepCode Session in a trusted test repository. First select
`$skill-discovery` and ask for a local Skill search. Then select
`$delegate-task` and give OpenSpace a small, reversible coding task. Verify:

- the selected host Skill is recorded in the Turn;
- tools named `mcp__openspace__...` appear only after server initialization;
- `search_skills` completes without an upload/cloud credential;
- DeepCode asks before `execute_task` or `fix_skill`;
- interruption or timeout terminates the pending MCP call;
- the resulting repository diff and tests are reviewed in DeepCode.

Because the example uses `approvalMode: "prompt"`, choose DeepCode's **Ask**
access preset for `execute_task`. A non-interactive `never` approval policy
cannot pause for confirmation and safely denies the call; MCP policy never
widens the Session's global approval policy.

## Security boundary

An `execute_task` call starts a nested OpenSpace agent. DeepCode can approve or
deny that outer MCP call, but it cannot individually mediate shell or MCP calls
made inside OpenSpace. Configure OpenSpace's own backend and sandbox policy,
use a disposable worktree for initial testing, and review the final diff.
DeepCode's project trust and global deny/read-only rules still win at the outer
tool boundary; MCP-specific policy can only make that decision stricter.

Cloud use is optional. If enabled later, provision OpenSpace's cloud key using
its `openspace-cloud-auth bootstrap-agent-key` flow and bind/forward it from
private user configuration. Never place a raw cloud or LLM key in
`deepcode_config.json`.
