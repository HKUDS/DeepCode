# Goals, automation, and headless runs

Once you have completed your [first task](getting-started.md), you can ask
DeepCode to work toward a longer objective, call it from a script, or run a
recurring task. Choose the option that fits your work:

| What you want to do | Use |
|---|---|
| Keep working toward an objective across several Turns | `/goal` in a conversation, or `deepcode loop` from the shell |
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

If your objective starts with the name of a Goal command, write `/goal set`
before it, for example `/goal set clear out the unused feature flags`. Text
after a command that takes no argument, such as `clear`, is also treated as a
new objective, so `/goal clear out the unused feature flags` never clears the
current Goal. A Session holds one Goal at a time: if it already has one, use
`/goal edit`, or `/goal clear` first.

You can close the conversation and return to the saved Goal later. DeepCode
reports when it considers the work complete or needs help to proceed. Review
its changes and test results before accepting the outcome. Additional controls
include `/goal continue`, `/goal wait`, `/goal reopen`, and `/goal clear`.
`/goal wait` follows the Goal until it finishes; press `Ctrl+C` to stop
waiting while the Goal keeps working.

A Goal can have a token budget, a time limit (its active time, set in the
Desktop Goal editor or with `deepcode loop --time-budget MINUTES`), or both.
A Goal without either continues on its own at most 20 times in a row
without reporting complete or blocked. It then stops as blocked, so you can
check its progress, and `/goal resume` lets it continue. Sending a message,
editing, resuming, or continuing the Goal starts the count again. A Goal with
a budget is limited by its budget instead. The budget also holds inside a
Turn: once it is used up, the agent gets one more answer, without tools, to say
what it finished and what is left, and the Turn ends there. It is asked to
report only what its tool results showed and to say what it did not verify.

With or without a budget, a Goal also stops as blocked when no step succeeded
in three automatic continuations in a row: each of those Turns did no work at
all, or every step it took failed. The agent itself can report a
Goal blocked only from the Goal's third Turn on, unless you declined an
approval it asked for in that Turn; before that it is told to try another
approach.

If DeepCode restarts while a Goal is active, the Goal is paused rather than
continued, so it does not spend tokens while nobody is watching. Resume it to
go on, for example with `/goal resume` or `deepcode loop --resume SESSION_ID`.
A Goal that an Automation runs, or one with a message you queued before the
restart, continues as before.

Any Turn, with or without a Goal, stops when the agent's tool calls keep
bringing nothing new: 12 results in a row it had already seen, or 40 calls in a
row it had already made. The agent is warned first. The Turn ends as failed, so
a Goal stops as blocked and you can check what went wrong. A message you send
during the Turn starts the count again.

## Check completion claims

By default the agent decides when a Goal is complete: before it says so, it is
asked to check every requirement of the Goal against the workspace. A claim
made while sub-agents it started are still running is not recorded: the agent
is told to wait for them and look at their results first. After three such
claims in a row the next one is judged anyway, so a stuck sub-agent cannot
keep the Goal open; stop it before accepting the outcome. There are two ways
to check the claim as well, and they work together.

### A checklist you confirm

Early in a Goal the agent proposes a checklist: the Goal's requirements, each
with the command that proves it where one exists. Nothing runs until you
confirm it. In Desktop the proposal appears on the Goal card with **Use these
checks** and **Dismiss**; in the TUI, `/goal checks` shows it, `/goal checks
confirm` uses the list you were shown (if the agent replaced it meanwhile, you
are shown the new one first), `/goal checks dismiss` drops it, `/goal checks add
COMMAND` adds a command of your own and `/goal checks clear` removes them all.
After you dismiss a proposal the agent is not asked for another. With
`deepcode loop`, `--test-cmd` and each `--check COMMAND` become confirmed checks
of the new Goal.

Once confirmed, DeepCode runs each command itself whenever the agent claims
the Goal complete, in the project folder. A proposed command is an argument
list run without a shell. A command you type yourself runs the same way unless
it uses shell syntax (a pipe, `&&`, a glob, a variable); then it runs in
`bash -o pipefail -c`, so a failing stage fails the check: `pytest | tail`
cannot hide failing tests. A command several checks share runs once. Each
command may take up to 10 minutes, all of them 30 minutes, and anything a command leaves running is stopped when it exits. If
one does not exit 0, the claim is turned back with the end of its output, and
the agent keeps working. A check runs with the same rights as the agent's own
commands, in the same sandbox: the agent may have written the code it runs. A
requirement without a command is checked only by the reviewer below, if you
use one. Checks run whatever `goal.completionVerifier` is set to.

### A verifier

Set `goal.completionVerifier` in your [configuration](configuration.md), or
in Desktop under **Settings → General → Goal completion checks**:

- `"rule"` turns the claim back while the latest check failed or nothing was
  done in that Turn. It reads a command's exit code, which for a pipeline is
  the last command's: `pytest | tail` reads as passed even when the tests
  fail, so the agent is asked to run checks without pipes.
- `"reviewer"` runs the same rule, then has a second agent review the work
  before the Goal is recorded complete. The reviewer has its own context: it
  sees the Goal, the agent's reason and its latest steps, reads the changes,
  and re-runs the checks. For each requirement it records whether the evidence
  proves it, and the claim passes only when every one is proved. It cannot
  change anything: it has no editing tools, runs no hooks, and its commands
  run with a read-only workspace and no network. Where commands cannot be kept
  read-only (Windows, or Linux without bubblewrap), it only reads files.
  When it finds something missing, the agent gets the list and keeps
  working. When it finds the Goal cannot be met at all, for example because a
  needed key or service is missing, the Goal stops as blocked. When the review
  itself cannot run (the model service fails three times, or it takes longer
  than 15 minutes), the Goal also stops as blocked, with that reason, but this
  does not count as a rejected claim: resume it to try again.
  A review can take a few minutes, and its tokens count toward the Goal's
  token budget.

  The reviewer also gets your confirmed checklist, and checks each item.

The Goal stops as blocked after `goal.maxRejections` (3) rejected claims in a
row with no file changes in between, or after 10 rejected claims in all.

Every check of a claim is kept with the Session: what was checked, the result,
and, for a review, each requirement with its finding and evidence. The Desktop
Goal card shows the latest result next to the Goal's status and the full table
when you open the card; `/goal show` prints the latest one.

## Work on a Goal from the shell: `deepcode loop`

For the trusted demo workspace from [Getting started](getting-started.md), replace
`MODEL_ID` with your configured model. Run this as one line in your shell:

```console
deepcode loop "Refactor greet without changing its tested behavior" --workspace ./deepcode-demo --connection my-openrouter --model MODEL_ID --test-cmd "python -m unittest discover -v" --token-budget 200000
```

Use the Python command available in your project. `--test-cmd` tells the agent
which test command to run before reporting completion, and DeepCode runs it
itself before it accepts the claim (see
[A checklist you confirm](#a-checklist-you-confirm)); `--check COMMAND` adds
more such commands. `--token-budget` limits how many tokens the Goal may use
and `--time-budget MINUTES` how long it may work.

When the run finishes, review the changed files and test output.
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

Each Automation has its own Goal Session. Before its first run, use **Open Session**
in Desktop/Web and select the intended connection/model in that Session's composer.
You can also resume its Session ID in the TUI and use `/model`. Without a model
selected there, it uses the configured defaults; a model selected in your earlier demo
Session is not automatically copied into this new one.

Copy the Automation ID from the output and use it in place of `AUTO_ID` below.
Run it once, inspect its run history, then enable the hourly schedule:

```console
deepcode automation run AUTO_ID
deepcode automation runs AUTO_ID
deepcode automation enable AUTO_ID
```

To pause future runs, use `deepcode automation disable AUTO_ID`. A task already
running will keep going; open its Session if you want to stop it. Review the
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
client exit. As with `deepcode loop`, DeepCode runs the test command itself
before it accepts a completion claim.
See [service operation](../SERVICE_RUNTIME.md) for shutdown and drain semantics.

### Change-gated schedules

A schedule that only needs to act when something changed can put a monitor in
front of the model call. `python -m core.schedule.monitor` runs a script or
fetches a URL, hashes the output, and reports `changed` only when it differs
from the last run (the first run records a baseline). Unchanged ticks cost no
model call. On a change it prints a context block with the diff to hand to the
next Turn:

```console
python -m core.schedule.monitor --job-id releases --url https://example.com/releases.json --json
```

Exit code 0 with `"changed": true` means "act now"; feed `context_block` to
`deepcode exec` in the same cron entry. URLs follow the same public-host policy
as the web tools, scripts pass the destructive-command screen, and state lives
in `<deepcode home>/state/monitor.sqlite3`.

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

Hooks a project declares in `.deepcode/hooks.json` or `.claude/settings.json`
run only while they are approved. Trusting a project approves the hooks it
declares at that moment. If those declarations change later, for example after
a `git pull`, DeepCode skips the project's hooks and logs a notice until you
approve them again: run `deepcode hooks status` in the project to see them,
then `deepcode hooks approve` to approve exactly what was listed. The approval
covers the declarations (events, matchers and commands), not the scripts they
run, so a hook such as `sh .deepcode/check.sh` keeps its approval when
`check.sh` changes. Your own hooks in `~/.deepcode/hooks.json` always run.
