# DeepCode (Forked)

This repository is a **fork** of the original DeepCode project.  
It extends the base system with important modifications to support building **large application codebases without timeouts**.

## 🔧 Additions in This Fork
- Removed timeouts to allow long-running code generation sessions.
- Improved `mcp_agent.config.yaml` with:
  - Extended `overall_seconds` to 3h (10800).
  - Per-tool timeouts increased for resilience.
  - Stable filesystem roots for dynamic project folders.
- Integration tested with **Claude and Brave Search** credentials.
- Verified ability to continuously implement files in large projects.
- Logging improved for debugging and stability.

## 🚀 Usage
1. Clone this fork:
   ```bash
   git clone https://github.com/alitawash/deepcode-fork.git
   cd deepcode-fork
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Add your credentials in `mcp_agent.secrets.yaml`:
   - **Anthropic API Key** (Claude)
   - **OpenAI API Key** (if used)
   - **Brave API Key** (for search)

4. Start DeepCode:
   ```bash
   python deepcode.py
   ```

## 📂 Example Project Path
When you run, DeepCode creates new project folders under:
```
/Users/<you>/Desktop/Projects/deepcode/DeepCode/deepcode_lab/papers/chat_project_<id>/generate_code
```
This fork ensures they remain fully writable and persistent for large projects.

## 📘 Additional Guides
- [Creating a New App via DeepCode's Chat Workflow](NEW_APP_WORKFLOW.md)

## 📜 License
This project inherits the original license.
All modifications in this fork are released under the same terms.
