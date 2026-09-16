# Troubleshooting

Find the problem that matches what you are seeing below. When asking for help,
include your DeepCode version, operating system, the command you ran, and the
error message. Remove API keys and access links from anything you share.

## My terminal cannot find `deepcode`

If you installed with `uv tool`, run:

```console
uv tool update-shell
```

Close and reopen your terminal, then check the installation:

```console
deepcode --version
```

If the command is still missing, follow the [installation steps](../../README.md#install-the-runtime).

## DeepCode asks me to trust my project

For a project you trust, start the TUI with its folder path:

```console
deepcode --workspace /path/to/your-project --trust
```

Replace the path with your project directory. In Desktop/Web, open the Project
and use its trust control. You only need to record trust once per project;
individual tool actions may still ask for approval.

## The wrong model is selected, or the model fails

Use `/model` in the TUI or the model picker beside the Desktop/Web message box
to choose the connection and model for your conversation.

To check a connection from your terminal, replace `CONNECTION_ID` and `MODEL_ID`
with your configured connection name and model ID:

```console
deepcode provider test CONNECTION_ID --model MODEL_ID
```

If a model can answer messages but fails to use tools, try the tool-calling check:

```console
deepcode provider test CONNECTION_ID --model MODEL_ID --agent
```

Use the reported error to check your API key, endpoint, and model settings.
See [Models and providers](models.md) for setup examples.

## The browser will not connect

If the page shows **Browser access required**, run `deepcode web` and open the
new link. This also works when you paste the link into the same browser tab.

For `APP_SERVER_OFFLINE` or another connection error, first check
whether the background service is running:

```console
deepcode service status
```

If it reports `ready`, open a fresh browser link:

```console
deepcode web
```

Use the link within 60 seconds. Each link works once; the resulting browser
session lasts up to 12 hours. Restarting the service also requires a fresh link.
You do not need a DeepCode account.

If the service is stopped, start it and try again:

```console
deepcode service start
```

If startup fails, read the recent logs:

```console
deepcode service logs --lines 100
```

## I lost the connection while DeepCode was working

In the TUI, enter `/reconnect`. In Desktop/Web, use **Reconnect**; if the browser
asks you to sign in, run `deepcode web` again.

Open your conversation and check its latest messages and pending approvals.
The task may have kept running while you were disconnected. Continue from its
current state rather than sending the original task again.

If you see `RESULT_UNKNOWN` after an action, check what changed before retrying:
open the file after an edit, check the setting after saving, or look for the task
in your conversation. For details, see [connection recovery](../WEB_CLIENT.md#reconnection-and-uncertain-results).

## Desktop will not open

Make sure you have installed the [Desktop app or its source dependencies](../../desktop/README.md).
For an app installed in a custom location, run:

```console
deepcode desktop --app /path/to/app
```

For a source checkout in another directory:

```console
deepcode desktop --source /path/to/DeepCode
```

If the source app fails to build, check Node.js, Rust, and your platform's
prerequisites in the Desktop guide. Keep its launch terminal open while using it.

## I updated the source, but DeepCode still uses the previous version

For a version upgrade that changes stored data, follow
[Upgrading DeepCode](../UPGRADE_AND_RESTORE.md) to back up your installation first.
To update your source installation, rebuild and reinstall from the repository root:

```console
npm --prefix desktop ci
npm --prefix desktop run build:web
uv tool install --python 3.12 --force .
```

After current work finishes, restart the service:

```console
deepcode service restart --drain --timeout 60
```

Open a fresh `deepcode web` link if you use the browser. For source Desktop,
reopen with `deepcode desktop --setup` to refresh its bundled resources too.

If the restart times out, finish or stop the reported tasks and close any active
terminals, then try again.

## I need to recover saved conversations or settings

Follow the [backup and restore guide](../UPGRADE_AND_RESTORE.md). Back up your
project files too; restoring DeepCode's data does not undo changes to your code.

Keep `~/.deepcode/state/deepcode.sqlite3` when troubleshooting. It stores tasks,
approvals, and Automations as well as conversation-related state. Deleting it
can lose information that cannot be recovered from conversation files alone.
