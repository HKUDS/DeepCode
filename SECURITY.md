# Security policy

## Reporting a vulnerability

Please report security problems privately, not in a public issue or pull
request.

1. Open the repository's **Security** tab.
2. Click **Report a vulnerability**.
3. Describe the problem, the affected version or commit, and the steps to
   reproduce it. Include a proof of concept if you have one.

This uses GitHub's private vulnerability reporting: only you and the
maintainers can see the report. If the button is not available, open an issue
that asks for a private contact and leave out all details of the problem.

We will acknowledge the report, keep you informed until it is resolved, and
credit you in the changelog when the fix is released, unless you prefer not to
be named.

## Supported versions

Security fixes go into the `main` branch and the next release on
[PyPI](https://pypi.org/project/deepcode-hku/). Older releases do not receive
fixes; please upgrade to the latest version.

## Scope

In scope are DeepCode's own code in this repository: the agent runtime, the
local background service, the TUI, Desktop and Web clients, and the bundled
tools. Examples:

- a way for a repository, a web page or a tool result to make the agent act
  outside the access preset you chose, or to bypass project trust or hook
  approval;
- escaping the command sandbox under the Ask preset;
- another local user or a web page reaching the background service;
- exposure of stored credentials.

Out of scope: problems in model providers, in third-party MCP servers or
plugins, and actions you approved or allowed with Full access.

How DeepCode handles data and tool access is described in
[Security and data](docs/guide/security.md).
