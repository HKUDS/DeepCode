# DeepCode CLI Deployment Guide (AutoDL / Linux)

This guide captures a repeatable process for running the `HKUDS/DeepCode` CLI pipeline from source on AutoDL or any Ubuntu 22.04+ GPU VM. Following these steps ensures the CLI can execute the complete “URL/PDF → download → planning → implementation” workflow with working MCP servers.

---

## 0. Goals & Prerequisites

- **Repository path:** `/root/DeepCode`
- **Python:** 3.9 or newer (AutoDL currently ships with 3.10)
- **System packages:** Ability to install Node.js 20+
- **Credentials:** OpenAI / Anthropic API keys, optional Brave Search API key

> All commands assume you are logged in as `root` on the AutoDL machine. Adjust paths if you cloned the repo elsewhere.

---

## 1. Clone the Repository

```bash
cd /root
git clone https://github.com/HKUDS/DeepCode.git
cd DeepCode
```

Verify that directories such as `cli/`, `workflows/`, `prompts/`, `ui/`, and `requirements.txt` exist.

---

## 2. Create & Activate a Python Virtual Environment

```bash
cd /root/DeepCode
python -m venv .venv
source .venv/bin/activate
```

Keep the environment activated for all subsequent Python and pip commands.

---

## 3. Install Python Dependencies

```bash
cd /root/DeepCode
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If the default PyPI mirror is slow, feel free to switch to an AutoDL-provided mirror.

---

## 4. Configure `mcp_agent.secrets.yaml`

Edit `/root/DeepCode/mcp_agent.secrets.yaml` and insert your API keys:

```yaml
openai:
  api_key: "sk-your-openai-key"
  base_url: "https://api.openai.com/v1"

anthropic:
  api_key: "your-anthropic-key-or-null"
```

Leave `anthropic.api_key` empty if you are not using Anthropic.

---

## 5. Install Node.js 20 and MCP Servers

1. Install Node.js 20 from NodeSource (run as root):

   ```bash
   curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
   apt-get install -y nodejs
   node -v   # expect v20.x
   npm -v
   ```

2. Install the required MCP servers globally:

   ```bash
   npm install -g @modelcontextprotocol/server-brave-search \
                 @modelcontextprotocol/server-filesystem
   ```

3. Confirm their entry points exist:

   ```bash
   ls /usr/lib/node_modules/@modelcontextprotocol/server-brave-search/dist/index.js
   ls /usr/lib/node_modules/@modelcontextprotocol/server-filesystem/dist/index.js
   ```

---

## 6. Update `mcp_agent.config.yaml` for Linux Paths

The default config ships with Windows-specific MCP paths. Replace only the relevant sections with the absolute Linux paths installed in the previous step:

```yaml
mcp:
  servers:
    brave:
      args:
        - /usr/lib/node_modules/@modelcontextprotocol/server-brave-search/dist/index.js
      command: node
      env:
        BRAVE_API_KEY: "<your_brave_key_or_empty>"

    filesystem:
      args:
        - /usr/lib/node_modules/@modelcontextprotocol/server-filesystem/dist/index.js
        - .
      command: node
```

Leave the rest of the server configuration untouched so that other platforms keep working. Remember to keep your API keys out of version control.

---

## 7. Ensure Resource Processor Outputs Strict JSON

If you hit the fatal error:

```
Error processing file input: Input is neither a valid file path nor JSON
```

apply the following fixes (already merged in the main branch, but listed here for reference):

1. Update `prompts/code_prompts.py` so the `PAPER_DOWNLOADER_PROMPT` explicitly forbids Markdown fences or commentary and demands a raw JSON object.
2. In `workflows/agent_orchestration_engine.py`, wrap the resource processor call with:
   - Logging of the raw response
   - `extract_clean_json(...)` to strip noise
   - `json.loads(...)` validation before returning the output

These changes guarantee that downstream file processors receive valid JSON or get a clear error early in the pipeline.

---

## 8. Run the DeepCode CLI

```bash
cd /root/DeepCode
source .venv/bin/activate   # if not already active
python cli/main_cli.py
```

You should see the text-based menu:

```
========================================
 DeepCode CLI
========================================
[U]RL Mode / [F]ile Mode / [T]est / [Q]uit
```

Choose:
- `U` for URLs (e.g., arXiv, OpenReview)
- `F` to process local PDFs or directories
- `T` for the built-in test scenario

---

## 9. Output Directory Layout

The CLI writes artifacts under `./deepcode_lab/`:

```
deepcode_lab/
  papers/
    0001/
      paper.pdf
      paper.md
      metadata.json
      ...
```

IDs auto-increment based on the number of folders in `deepcode_lab/papers/`. Inspect results with standard shell tools (`ls`, `cat`, etc.).

---

## 10. Preparing a Pull Request

To contribute these improvements upstream:

1. Fork `HKUDS/DeepCode` on GitHub and clone your fork.
2. Create a branch such as `feat/autodl-cli-guide`.
3. Stage only the changed files (`docs/autodl_cli_guide.md`, plus any prompt/agent fixes you intend to submit).
4. Commit with a descriptive message, push to your fork, and open a PR targeting `HKUDS/DeepCode:main`.

Keeping environment documentation and core logic fixes in separate PRs makes reviews faster and avoids platform regressions.

---

By following this guide you should be able to launch the DeepCode CLI end‑to‑end on AutoDL or any equivalent Ubuntu-based GPU machine, with MCP servers configured correctly and JSON-only resource processing in place.

