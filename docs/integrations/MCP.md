# MCP clients in DeepCode

DeepCode has one MCP client system shared by Agent sessions, Desktop, the
interactive TUI, the management CLI, and the App Server protocol. A server
definition is configuration; it does not start a process until an Agent
session needs it or the user explicitly runs a connection test.

## Bundled preset catalog

The distribution contains a validated catalog based on the MCP templates in
HKUDS Nanobot at the source revision recorded in `core/mcp/presets.json`. The
catalog currently includes Browserbase, Playwright, Context7, Firecrawl,
Parallel Search, Exa, Microsoft Learn, AWS Documentation, Brave Search,
Postman, Figma, Xmind, Notion, Linear, GitHub, and Supabase.

"Bundled" means the configuration templates are available locally. DeepCode
does not silently execute `npx`, `uvx`, or Docker, download a package, contact
a remote endpoint, or enable a server. **Add** copies an ordinary user-scoped
MCP definition in a disabled state. Test it, inspect its capabilities, and then
enable it explicitly.

```console
deepcode mcp presets
deepcode mcp add context7
deepcode mcp test context7
deepcode mcp enable context7
```

The same flow is available in the TUI:

```text
/mcp presets
/mcp add context7
/mcp test context7
/mcp enable context7
```

In Desktop, open **MCP Servers**, choose **Add server**, then **Test
connection**, and finally **Enable**. The page shows configuration,
authorization, and runtime state separately. A passing test is one-shot: it
discovers capabilities and closes. Only an enabled server is connected for an
Agent turn and offered to the model.

## Authentication and environment

Templates never contain literal credentials. A template may require an
environment variable; export it before starting DeepCode and the MCP page will
report whether it is missing. Custom user-scoped servers may instead reference
a credential held in DeepCode's private provider store.

OAuth-capable HTTP servers use an explicit browser flow:

```console
deepcode mcp add notion
deepcode mcp login notion
deepcode mcp test notion
deepcode mcp logout notion
```

The TUI equivalents are `/mcp login notion` and `/mcp logout notion`.
Desktop exposes **Authenticate** and **Logout** on the server card. OAuth
tokens and dynamic client metadata are stored separately from
`deepcode_config.json` in a private `~/.deepcode/auth/mcp.json` file. The Agent
runtime may reuse stored credentials, but it never opens a browser implicitly.

## Custom servers

Use Desktop's custom server form or the management CLI. For example:

```console
deepcode mcp add local-tools --command python3 /absolute/path/server.py
deepcode mcp add remote-docs \
  --url https://example.com/mcp \
  --transport streamableHttp
deepcode mcp list
```

Top-level `mcpServers` in `~/.deepcode/deepcode_config.json` defines user
servers. A trusted project's `deepcode_config.json` can define project
servers. Project configuration cannot request credentials from the private
user provider store. The historical Paper2Code `tools.mcpServers` section is a
different subsystem and is intentionally ignored by the coding Agent runtime.

Plugin-provided MCP components are resolved into this same runtime. Their
transport remains owned by the Plugin, while user policy may disable tools or
make approval stricter; edit or remove the Plugin rather than treating its MCP
component as a standalone server.

## Connection state

DeepCode keeps three states distinct:

- **Configuration** reports validation, disabled state, project trust, and
  missing environment requirements without starting anything.
- **Authentication** reports whether OAuth is required, in progress, or
  available in the private credential store.
- **Runtime** reports stopped, connecting, tested, connected, or failed.
  `tested` means a one-shot check succeeded and closed; `connected` is
  reserved for a server owned by a live Agent session.

`deepcode mcp test <name>`, `/mcp test <name>`, and Desktop **Test
connection** all call the
same one-shot probe. A successful test performs MCP initialization and bounded
capability discovery, then closes the connection. Enabled servers are started
again with session-owned lifecycle when an Agent turn needs its tools.

After enabling, users keep working normally in Desktop, TUI, or headless mode.
DeepCode publishes the server's tool schemas to the model, which calls the
appropriate MCP tool when the task requires it. Users do not need to type the
internal `mcp__server__tool` name. For example:

```text
Use Playwright to open http://localhost:5173, verify the login flow, and report
browser console errors.
```

MCP approval modes narrow the normal **Ask** policy. **Read only** still blocks
tools not declared read-only. An explicitly selected **Full access** Turn skips
MCP confirmation gates along with other approvals, while explicit deny rules
continue to win.
