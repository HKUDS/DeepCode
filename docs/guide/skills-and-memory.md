# Teach DeepCode how you work

Use project instructions for conventions DeepCode should follow every time,
Skills for reusable task instructions, and memory for project notes you want to
keep. You can read and edit all three as ordinary files.

## Project instructions — `AGENTS.md`

Create an `AGENTS.md` in your project root. Write down the information you would
give a new contributor: how to run tests, where key code lives, and which
conventions to follow. For example, adapt this to your project:

```markdown
# Working in this repository

- Application code lives in src/; tests live in tests/.
- Run python -m unittest discover -s tests -v after changing Python code.
- Follow the existing formatting and naming conventions.
- Update the README when changing a user-facing command.
```

DeepCode reads project instructions when you start a Session. For a package with
its own conventions, add another `AGENTS.md` inside that package. Instructions
closer to your working directory take precedence when they conflict.

| Where to put instructions | When to use it |
|---|---|
| `<repo>/AGENTS.md` | Conventions for everyone working in this project |
| `<repo>/packages/app/AGENTS.md` | Rules specific to one package |
| `~/.deepcode/AGENTS.md` | Your personal preferences across projects |

If a directory already has `DEEPCODE.md` or `CLAUDE.md`, DeepCode can read that
instead. Within one directory, it uses the first available file in this order:
`AGENTS.md`, `DEEPCODE.md`, `CLAUDE.md`.

## Skills — reusable playbooks

A Skill gives DeepCode instructions for a particular kind of task, such as a code
review or release checklist. To see which Skills you have, enter:

```text
/skills
```

Choose a name from the list and load it for your next message:

```text
/skill SKILL_NAME
```

Replace `SKILL_NAME` with the installed name, then describe your task. You can
also include the Skill directly in a message:

```text
$SKILL_NAME review the changes on this branch
```

Select the Skill again when you want to use it on another turn.

To create one, ask the bundled Skill Creator for help:

```text
$skill-creator Create a Skill for our release checklist. Include the steps
for running tests, updating the changelog, and checking the release version.
```

Review the generated instructions and adjust them for your project. A Skill is
a folder containing `SKILL.md` and, optionally, supporting scripts or references.
Put it in the location that matches how you want to share it:

| Location | Available to |
|---|---|
| `<repo>/.agents/skills/` | This project; commit it to share with contributors |
| `~/.agents/skills/` | Your projects on this computer |

DeepCode also reads `.deepcode/skills/` and `.claude/skills/`. When importing a
Skill from another agent, check that any tools or scripts it names are available
here. Use `deepcode skill list` to inspect Skills from your shell and
`deepcode skill --help` for import and management commands.

Skills guide the work; your project trust and tool permissions still apply.

## Memory — keep project notes

Ask DeepCode to remember a decision you want available in future conversations:

```text
Remember that this project uses UTC for stored timestamps and converts to
local time only when displaying them. Save this in the project memory.
```

Check the saved note after DeepCode writes it. Project memory lives in
`<workspace>/.deepcode/memory/`; `MEMORY.md` is the index loaded into new Sessions,
and other files can hold longer notes. You can edit these files to correct or
remove information as your project changes.

For a rule every contributor should follow, put it in `AGENTS.md`. For a
repeatable task, create a Skill. Use memory for the project decisions and context
you want to carry into later work.

## Plugins — add a collection of Skills

A local Plugin can package several Skills and MCP server definitions together.
To add one from a folder you trust, use `deepcode plugin add /path/to/plugin`.
Its Skills appear in the same catalog. See [Local Plugins](../LOCAL_PLUGINS.md)
for the supported layout and MCP setup.
