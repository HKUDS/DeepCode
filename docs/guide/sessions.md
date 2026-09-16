# Save and resume your work

DeepCode saves your conversations so you can return to a task without explaining
it again. Each conversation belongs to a project folder and includes your
messages, DeepCode's replies, and its tool activity. The TUI calls this a
**Session**; Desktop and Web call it a **thread**.

## Resume

Open DeepCode in your project folder and enter:

```text
/resume
```

Choose a conversation from the list. To search conversations from other projects,
use `/resume all`. In Desktop/Web, select a saved thread under its Project.

If you know the Session ID, you can open it directly from your terminal:

```console
deepcode --resume SESSION_ID
```

Replace `SESSION_ID` with the ID shown for your conversation. Read the latest
messages, then tell DeepCode what you want to do next.

## Multiple clients, one Session

You can start a task in the TUI and open the same conversation in Desktop or Web.
On the same computer, use the same DeepCode version and configuration directory
so all three interfaces show your projects and history.

While DeepCode is working, send a message to adjust the current task. To save a
separate instruction for afterward, use `/queue` in the TUI:

```text
/queue When the current fix is finished, update the README example to match.
```

You can also handle an approval from another open interface. Closing a window
leaves running work in the background; use `/stop` or the stop button to interrupt
it. If you are using a [Goal](goals-and-headless.md), pause the Goal to prevent it
from continuing automatically.

After a connection problem, return to the conversation and check where the work
stopped before sending it again. See [Troubleshooting](troubleshooting.md).

## Long conversations: `/compact`

When a conversation becomes long, use `/compact` to summarize its older messages
and make room for more work. DeepCode keeps the recent messages and tool results,
and saves the summary for the next time you open the conversation.

DeepCode also compacts automatically as the conversation approaches the model's
context limit. To use a smaller limit, see [context settings](models.md#context-window-cap).

## Housekeeping

Give conversations descriptive names so they are easy to find:

```text
/rename Fix flaky auth tests
```

Use `/new` to start another task. Use `/clear` when you want to keep the current
Session but clear its conversation context.

To remove a saved conversation, use `/delete SESSION_ID` or the delete action in
Desktop/Web. This permanently removes its records while keeping your project
files. Finish or stop its active work before deleting it.

## Storage and recovery

DeepCode normally stores your conversation files in `~/.deepcode/sessions/` and
its task, approval, and Automation data in `~/.deepcode/state/deepcode.sqlite3`.
Large tool outputs may also be stored in your project's `.deepcode/tool-results/`
directory. Keep these files when backing up work you want to resume.

For a consistent backup or to recover an earlier installation, follow the
[backup and restore guide](../UPGRADE_AND_RESTORE.md). Back up your project files
separately. Keep the database during troubleshooting: conversation files alone
cannot restore all of its saved information.
