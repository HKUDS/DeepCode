# Creating a New App via DeepCode's Chat Workflow

Follow these steps to generate a new application using the built-in chat-driven pipeline:

1. **Describe your project requirements.**
   - In the Streamlit UI select **"💬 Chat Input"**.
   - Provide a detailed description of the application you want to build (web app, ML service, etc.).
   - The requirements textarea enforces a minimum length and includes example prompts to help you get started.

2. **Submit and start processing.**
   - Review the live requirements preview.
   - Click the submit button to launch the chat workflow. Progress is tracked across five stages: **Initialize → Planning → Setup → Save Plan → Implement**.

3. **Let the pipeline orchestrate the build.**
   - **Phase 0 – Workspace setup:** creates a local `deepcode_lab` workspace and prepares the environment.
   - **Phase 1 – Chat-based planning:** the planning agent transforms your requirements into a structured implementation plan.
   - **Phase 2 – Workspace infrastructure synthesis:** provisions a `chat_project_<timestamp>` folder, writes a markdown summary, and prepares agent inputs.
   - **Phase 3 – Save plan:** persists the generated plan to `initial_plan.txt` for downstream agents.
   - **Phase 4 – Code implementation:** calls the implementation agent (with indexing if enabled) to generate the application code and report the output directory.

4. **Review the generated summary and code.**
   - The workflow reports whether implementation succeeded and where the application code was saved.
   - Inspect the generated workspace under `deepcode_lab` to explore the scaffolding and produced files.

Use this guide as a quick reference whenever you start a new DeepCode project from chat requirements.
