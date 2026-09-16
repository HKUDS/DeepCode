# Goals, automation, and headless runs

Once you have completed your [first task](getting-started.md), you can ask
DeepCode to work toward a longer objective, call it from a script, or run a
recurring task. Choose the option that fits your work:

| What you want to do | Use |
|---|---|
| Keep working toward an objective across several turns | `/goal` in a conversation, or `deepcode loop` from the shell |
| Run one task from a script | `deepcode exec` |
| Run a recurring task after you close the interface | An Automation |
| Repeat a task while a scheduling command stays open | `deepcode schedule` |

## Goals — an objective the Session keeps

A Goal keeps an objective attached to your conversation as DeepCode works on it.
Describe the outcome you want, including how it should be checked. In the TUI:

```text
/goal migrate the config loader off pyyaml, keeping every test green
```

Use `/goal show` to see progress. If you need a break, enter `/goal pause`;
use `/goal resume` when you want to continue. To change the objective, enter
`/goal edit` followed by the updated instructions.

You can close the conversation and return to the saved Goal later. DeepCode
reports when it considers the work complete or needs help to proceed. Review
its changes and test results before accepting the outcome. Additional controls
include `/goal continue`, `/goal wait`, `/goal reopen`, and `/goal clear`.

## Work on a Goal from the shell: `deepcode loop`

For the trusted demo workspace from [Getting started](getting-started.md), replace
`MODEL_ID` with your configured model. Run this as one line in your shell:

```console
deepcode loop "Refactor greet without changing its tested behavior" --workspace ./deepcode-demo --connection my-openrouter --model MODEL_ID --test-cmd "python -m unittest discover -v" --token-budget 200000
```

Use the Python command available in your project. `--test-cmd` tells the agent
which test command to run before reporting completion; `--token-budget` limits
how many tokens the Goal may use.

When the run finishes, review the changed files and test output. The agent
reports Goal completion based on its work; the test command is an instruction
to the agent, rather than an independent test runner built into `loop`.
For a script, exit code 0 means the Goal was marked complete; other settled
states return 1. You can resume it with `deepcode loop --resume SESSION_ID`.

## One task, scripted: `deepcode exec`

From an already trusted project, select the connection/model explicitly. As in
the first-task guide, replace `MODEL_ID` with the verified model ID:

```console
deepcode exec "Summarize this repository's test structure without changing files" --workspace . --connection my-openrouter --model MODEL_ID --access read-only
deepcode exec "Summarize this repository's test structure without changing files" --workspace . --connection my-openrouter --model MODEL_ID --access read-only --json | jq -r '.msg.type'
```

The second example requires `jq` and starts a separate task; choose one format.
`--json` emits one JSON event per line, with the event payload under `msg` and
its type under **`msg.type`**. Useful options include `--resume`, `--model`,
`--connection`, `--preset`, and repeatable `--skill`.

For unattended scripts, choose permissions that let the task run without asking
you for input. In `ask` mode, tools needing interactive approval can be denied.
To leave a task running in the background, add `--detach` and save the returned
IDs so you can check its progress later.

## Persistent interval Automations

Use an Automation for a task you want DeepCode to repeat. In a project you have
already opened and trusted, create an hourly review of TODO comments. Start it
paused so you can try it once before turning on the timer:

```console
deepcode automation create "Review TODOs" --workspace . --prompt "Summarize TODO comments without modifying files" --schedule interval --interval-seconds 3600 --disabled
deepcode automation list --workspace .
```

Each Automation has its own Goal Thread. Before its first run, use **Open Thread**
in Desktop/Web and select the intended connection/model in that thread's composer.
You can also resume its Thread ID in the TUI and use `/model`. Without a thread
selection, it uses the configured defaults; a model selected in your earlier demo
Session is not automatically copied into this new thread.

Copy the Automation ID from the output and use it in place of `AUTO_ID` below.
Run it once, inspect its run history, then enable the hourly schedule:

```console
deepcode automation run AUTO_ID
deepcode automation runs AUTO_ID
deepcode automation enable AUTO_ID
```

To pause future runs, use `deepcode automation disable AUTO_ID`. A task already
running will keep going; open its thread if you want to stop it. Review the
Project's permissions before unattended use, since tasks that need approval
still require your response.

You can close Desktop, Web, and the TUI while an Automation is scheduled. Keep
the computer awake and the background service running. Use
`deepcode service status` to check it, or `deepcode service install --at-login`
to start the service automatically after signing into your computer.

## Foreground schedules

`deepcode schedule` is a foreground scheduling client. For example, from the
parent of the trusted demo workspace:

```console
deepcode schedule loop "Check greet and fix any regressions" --workspace ./deepcode-demo --connection my-openrouter --model MODEL_ID --test-cmd "python -m unittest discover -v" --every 86400 --max-runs 30
```

Closing this scheduling client stops future submissions; already accepted work
continues in the service. Use Automations when the schedule itself must survive
client exit. The test-command limitation described above also applies here.
See [service operation](../SERVICE_RUNTIME.md) for shutdown and drain semantics.

## DeepCode as an MCP server

Expose DeepCode task tools to another agent or editor with:

```console
deepcode mcp serve
```

Use this command as the server entry in your editor's MCP configuration.
When DeepCode will work in a project, open and trust that folder first.

To give DeepCode tools from another MCP server, start by browsing the presets
and testing a connection:

```console
deepcode mcp presets
deepcode mcp add context7
deepcode mcp test context7
```

Added templates start disabled. After reviewing and testing a server, use
`deepcode mcp enable context7`. Services requiring OAuth have a separate
`deepcode mcp login SERVER_NAME` flow. The TUI's `/mcp` exposes management too.
See the [MCP client guide](../integrations/MCP.md) for transports and policy.

## Hooks

Use hooks when you want to run your own checks or logging around agent activity.
For example, `PreToolUse` runs before a tool, while `PostToolUse` observes its
result afterward. See [Headless and Automation](../HEADLESS_AND_AUTOMATION.md)
for configuration and the actions each hook supports.
