# DeepCode Installation Guide

This guide provides step-by-step instructions for installing and running DeepCode on your machine.

## Prerequisites

- Python 3.9 or higher (Python 3.13 recommended)
- pip package manager
- Git (for cloning the repository, if installing from source)

## Installation Methods

### Option 1: Direct Installation (Recommended)

The easiest way to install DeepCode is using pip:

```bash
# Install DeepCode package directly
pip install deepcode-hku

# Download configuration files
curl -O https://raw.githubusercontent.com/HKUDS/DeepCode/main/mcp_agent.config.yaml
curl -O https://raw.githubusercontent.com/HKUDS/DeepCode/main/mcp_agent.secrets.yaml

# Configure API keys (required)
# Edit mcp_agent.secrets.yaml with your API keys and base_url:
# - openai: api_key, base_url (for OpenAI/custom endpoints)
# - anthropic: api_key (for Claude models)

# Configure search API keys for web search (optional)
# Edit mcp_agent.config.yaml to set your API keys:
# - For Brave Search: Set BRAVE_API_KEY: "your_key_here" in brave.env section (line ~28)
# - For Bocha-MCP: Set BOCHA_API_KEY: "your_key_here" in bocha-mcp.env section (line ~74)

# Configure document segmentation (optional)
# Edit mcp_agent.config.yaml to control document processing:
# - enabled: true/false (whether to use intelligent document segmentation)
# - size_threshold_chars: 50000 (document size threshold to trigger segmentation)
```

### Option 2: Development Installation from Source

If you want to install from the source code:

#### Using Traditional pip

```bash
# Clone the repository
git clone https://github.com/HKUDS/DeepCode.git
cd DeepCode/

# Install dependencies
pip install -r requirements.txt

# Configure API keys (required)
# Edit mcp_agent.secrets.yaml with your API keys and base_url:
# - openai: api_key, base_url (for OpenAI/custom endpoints)
# - anthropic: api_key (for Claude models)

# Configure search API keys for web search (optional)
# Edit mcp_agent.config.yaml to set your API keys:
# - For Brave Search: Set BRAVE_API_KEY: "your_key_here" in brave.env section (line ~28)
# - For Bocha-MCP: Set BOCHA_API_KEY: "your_key_here" in bocha-mcp.env section (line ~74)

# Configure document segmentation (optional)
# Edit mcp_agent.config.yaml to control document processing:
# - enabled: true/false (whether to use intelligent document segmentation)
# - size_threshold_chars: 50000 (document size threshold to trigger segmentation)
```

#### Using UV (Recommended for Development)

```bash
# Clone the repository
git clone https://github.com/HKUDS/DeepCode.git
cd DeepCode/

# Install UV package manager
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies with UV
uv venv --python=3.13
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uv pip install -r requirements.txt

# Configure API keys (required)
# Edit mcp_agent.secrets.yaml with your API keys and base_url:
# - openai: api_key, base_url (for OpenAI/custom endpoints)
# - anthropic: api_key (for Claude models)

# Configure search API keys for web search (optional)
# Edit mcp_agent.config.yaml to set your API keys:
# - For Brave Search: Set BRAVE_API_KEY: "your_key_here" in brave.env section (line ~28)
# - For Bocha-MCP: Set BOCHA_API_KEY: "your_key_here" in bocha-mcp.env section (line ~74)

# Configure document segmentation (optional)
# Edit mcp_agent.config.yaml to control document processing:
# - enabled: true/false (whether to use intelligent document segmentation)
# - size_threshold_chars: 50000 (document size threshold to trigger segmentation)
```

## Windows Users: Additional MCP Server Configuration

If you're using Windows, you may need to configure MCP servers manually in `mcp_agent.config.yaml`:

```bash
# 1. Install MCP servers globally
npm i -g @modelcontextprotocol/server-brave-search
npm i -g @modelcontextprotocol/server-filesystem

# 2. Find your global node_modules path
npm -g root
```

Then update your `mcp_agent.config.yaml` to use absolute paths:

```yaml
mcp:
  servers:
    brave:
      command: "node"
      args: ["C:/Program Files/nodejs/node_modules/@modelcontextprotocol/server-brave-search/dist/index.js"]
    filesystem:
      command: "node"
      args: ["C:/Program Files/nodejs/node_modules/@modelcontextprotocol/server-filesystem/dist/index.js", "."]
```

> **Note**: Replace the path with your actual global node_modules path from step 2.

## Search Server Configuration (Optional)

DeepCode supports multiple search servers for web search functionality. You can configure your preferred option in `mcp_agent.config.yaml`:

```yaml
# Default search server configuration
# Options: "brave" or "bocha-mcp"
default_search_server: "brave"
```

**Available Options:**
- **🔍 Brave Search** (`"brave"`):
  - Default option with high-quality search results
  - Requires BRAVE_API_KEY configuration
  - Recommended for most users

- **🌐 Bocha-MCP** (`"bocha-mcp"`):
  - Alternative search server option
  - Requires BOCHA_API_KEY configuration
  - Uses local Python server implementation

**API Key Configuration in mcp_agent.config.yaml:**
```yaml
# For Brave Search (default) - around line 28
brave:
  command: "npx"
  args: ["-y", "@modelcontextprotocol/server-brave-search"]
  env:
    BRAVE_API_KEY: "your_brave_api_key_here"

# For Bocha-MCP (alternative) - around line 74
bocha-mcp:
  command: "python"
  args: ["tools/bocha_search_server.py"]
  env:
    PYTHONPATH: "."
    BOCHA_API_KEY: "your_bocha_api_key_here"
```

> **💡 Tip**: Both search servers require API key configuration. Choose the one that best fits your API access and requirements.

## Running DeepCode

### Option 1: Using Installed Package (Recommended)

If you installed via pip:

```bash
# Launch web interface directly
deepcode

# The application will automatically start at http://localhost:8501
```

### Option 2: Using Source Code

Choose your preferred interface:

#### 🌐 Web Interface (Recommended)

```bash
# Using UV
uv run streamlit run ui/streamlit_app.py

# Or using traditional Python
streamlit run ui/streamlit_app.py
```

The web interface will be available at `http://localhost:8501`

#### 🖥️ CLI Interface (Advanced Users)

```bash
# Using UV
uv run python cli/main_cli.py

# Or using traditional Python
python cli/main_cli.py
```

#### 🚀 Using the Launcher Script

```bash
# Launch web interface
python deepcode.py

# The application will start at http://localhost:8503
```

## Configuration

### Required: API Keys Configuration

Before using DeepCode, you **must** configure your API keys in `mcp_agent.secrets.yaml`:

1. Open `/path/to/DeepCode/mcp_agent.secrets.yaml`
2. Add your API keys:
   - **OpenAI**: `api_key` and `base_url` (for OpenAI/custom endpoints)
   - **Anthropic**: `api_key` (for Claude models)

Example configuration:
```yaml
openai:
  api_key: "your-openai-api-key-here"
  base_url: "https://api.openai.com/v1"  # or your custom endpoint

anthropic:
  api_key: "your-anthropic-api-key-here"
```

### Optional: Search API Keys

For web search functionality, configure search API keys in `mcp_agent.config.yaml`:

- **Brave Search**: Set `BRAVE_API_KEY` in the `brave.env` section (around line 28)
- **Bocha-MCP**: Set `BOCHA_API_KEY` in the `bocha-mcp.env` section (around line 74)

### Optional: Document Segmentation

Control document processing in `mcp_agent.config.yaml`:

```yaml
document_segmentation:
  enabled: true  # or false to disable
  size_threshold_chars: 50000  # Document size threshold to trigger segmentation
```

## Verification

After installation, verify that everything is working:

1. **Check dependencies**:
   ```bash
   python deepcode.py --help
   ```

2. **Verify configuration files exist**:
   - `mcp_agent.config.yaml`
   - `mcp_agent.secrets.yaml`

3. **Test the installation**:
   ```bash
   python deepcode.py
   ```
   This should launch the web interface without errors.

## Troubleshooting

### Common Issues

1. **ModuleNotFoundError**: If you encounter missing module errors, ensure all dependencies are installed:
   ```bash
   pip install -r requirements.txt
   ```

2. **Port already in use**: If port 8501 or 8503 is already in use, you can specify a different port:
   ```bash
   streamlit run ui/streamlit_app.py --server.port 8502
   ```

3. **API Key errors**: Make sure your API keys are correctly configured in `mcp_agent.secrets.yaml`

4. **MCP server errors**: For Windows users, ensure MCP servers are properly installed and paths are correctly configured in `mcp_agent.config.yaml`

### Getting Help

- Check the [GitHub Issues](https://github.com/HKUDS/DeepCode/issues)
- Join the [Discord Community](https://discord.gg/yF2MmDJyGJ)
- Review the main [README.md](README.md) for more information

## Next Steps

Once installation is complete:

1. ✅ Configure your API keys in `mcp_agent.secrets.yaml`
2. ✅ (Optional) Configure search API keys in `mcp_agent.config.yaml`
3. ✅ Run `python deepcode.py` to launch the application
4. ✅ Open your browser to `http://localhost:8503` (or the port shown in the terminal)
5. ✅ Start using DeepCode to transform research papers and requirements into code!

## Additional Resources

- [Main README](README.md) - Overview and features
- [README_ZH.md](README_ZH.md) - Chinese documentation
- [GitHub Repository](https://github.com/HKUDS/DeepCode) - Source code and issues

---

**Happy Coding! 🧬⚡**

