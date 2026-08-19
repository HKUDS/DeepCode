# Skills and memory

Three layers of durable knowledge, from "how to do a kind of task" to "what
this project demands" to "what we learned last week". All three are plain
files you can read, edit, and version.

## Skills — reusable playbooks

A skill is a folder with a `SKILL.md` (YAML frontmatter + instructions) and
optional reference files — the open Agent-Skills format, so skills written for
Claude Code or Codex work here unchanged.

Where they live:

```text
<repo>/.agents/skills/     ← project skills, travel with the repository
~/.agents/skills/          ← user skills, available in every project
```

(`.deepcode/skills/` and `.claude/skills/` remain readable for compatibility.)

Using them:

```text
› /skills                        list everything discovered
› /skill code-review             arm one for the next turn
› $code-review this branch       or invoke inline, mid-sentence
```

A skill loads for **one turn** — progressive disclosure keeps its full text
out of every request. Creating one is itself an agent task:

```text
› $skill-creator a skill for our release checklist
```

The shell CLI mirrors this: `deepcode skill list | show | import | remove |
reload`. Skills guide the agent; they can never grant permissions or bypass
trust — policy always wins.

**Plugins** package skills behind a validated manifest: `deepcode plugin add
<path>` registers a trusted folder, its skills join the ordinary catalog, and
a compliant `mcp.json` may contribute MCP servers (inert until a session
starts them). Details: [`docs/LOCAL_PLUGINS.md`](../LOCAL_PLUGINS.md).

## Project instructions — `AGENTS.md`

Standing rules the agent reads every session, discovered from your repository
root down to the working directory — nearest file wins where they overlap:

```text
<repo>/AGENTS.md               broad conventions
<repo>/packages/app/AGENTS.md  specifics for this package (takes precedence)
~/.deepcode/AGENTS.md          your personal rules, across all projects
```

Per directory, the first of `AGENTS.md` > `DEEPCODE.md` > `CLAUDE.md` is used
— existing Claude Code setups work as-is. Budgeting is nearest-first: a huge
root file can no longer starve the specific one beside your code. Injected
content is framed and escaped, so a repository file cannot impersonate system
instructions.

## Memory — what the agent writes down

The agent has a `memory` tool over `<workspace>/.deepcode/memory/`:

```text
.deepcode/memory/
  MEMORY.md        ← the index — loaded into every session automatically
  decisions.md     ← anything else the agent (or you) files
```

When it learns something durable — a convention, a gotcha, a decision — it
records a note and keeps `MEMORY.md` as the index. You can read and edit these
files like any others; the agent is told notes may be stale and to verify
before relying on them.

**The practical loop:** correct the agent once ("we never use fixtures like
that — do X"), ask it to remember, and the correction is there next session.

> **In development — DeepCode Evolve:** an opt-in loop that distills your own
> sessions into project skills automatically, each with provenance, a
> guardrail check, and one-click rollback.
