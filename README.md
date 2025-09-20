````markdown
# DeepCode – Private AI Code Implementation Framework

## 📖 Description  
DeepCode is a private AI-assisted code generation framework using MCP with Claude, OpenAI, and Brave APIs. It creates large-scale application codebases without timeouts through segmented workflows and smart memory management.

---

## 🚀 Features
- Automated file tree creation & scaffolding  
- Segmented workflow to avoid timeouts  
- Smart memory management for large projects  
- Cross-model support: Claude, OpenAI, Brave Search  
- Persistent project outputs per run  

---

## 📦 Installation

### 1. Clone repository
```bash
git clone <your-private-repo-url> deepcode
cd deepcode
````

### 2. Create virtual environment

```bash
python3.11 -m venv deepcode311
source deepcode311/bin/activate
pip install -r requirements.txt
```

### 3. Setup secrets

Create and edit `mcp_agent.secrets.yaml`:

```yaml
anthropic:
  api_key: "<your-claude-key>"

openai:
  api_key: "<your-openai-key>"
  base_url: "https://api.openai.com/v1"

brave:
  api_key: "<your-brave-api-key>"
```

⚠️ Never commit this file.

---

## ⚙️ Configuration

Main config: `mcp_agent.config.yaml`

Example snippet:

```yaml
default_search_server: "brave"
planning_mode: "segmented"

document_segmentation:
  enabled: true
  size_threshold_chars: 12000

mcp:
  servers:
    code-implementation:
      command: "python"
      args: ["tools/code_implementation_server.py"]
      env: { PYTHONPATH: "." }

    filesystem:
      command: "npx"
      args:
        [
          "-y",
          "@modelcontextprotocol/server-filesystem",
          "--stdio",
          "--allow-write",
          "--root", "/Users/<your-user>/Desktop/Projects/deepcode/DeepCode/deepcode_lab",
          "--root", "/Users/<your-user>/Desktop/Projects"
        ]
```

---

## ▶️ Running DeepCode

Start a workflow:

```bash
(deepcode311) python deepcode.py
```

You’ll see mode selection:

```
1. Test Code Reference Indexer
2. Run Full Implementation Workflow
3. Run Pure Code Implementation
4. Test Read Tools
```

* Use **Mode 3** for normal project generation.
* Output appears under:

  ```
  deepcode_lab/papers/chat_project_<id>/generate_code
  ```

---

## 📂 Project Layout

```
DeepCode/
├── deepcode.py
├── mcp_agent.config.yaml
├── mcp_agent.secrets.yaml   # 🔑 private
├── tools/
├── workflows/
├── prompts/
└── deepcode_lab/
    └── papers/
        └── chat_project_<id>/
            ├── initial_plan.txt
            └── generate_code/
```

---

## 🔧 Troubleshooting

* **Timeouts** → Segmented workflows prevent hangs
* **CallToolResult error** → Fixed via `sitecustomize.py` monkeypatch
* **Server not found** → Remove unused servers in config
* **Noisy logs** → Change `logger.level` to `warning` in config

```

---

```
