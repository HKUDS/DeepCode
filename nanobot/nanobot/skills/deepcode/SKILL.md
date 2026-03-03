---
name: deepcode
description: "DeepCode integration - automated code generation from papers and text requirements"
metadata: {"nanobot":{"always":true}}
---

# DeepCode - AI Code Generation Engine

You have access to **DeepCode**, a powerful multi-agent AI code generation engine that can:
- **Paper2Code**: Reproduce research paper algorithms as working code
- **Chat2Code**: Generate complete projects from text descriptions

## Available Tools

| Tool | Purpose |
|------|---------|
| `deepcode_paper2code` | Submit a paper URL or file for code reproduction |
| `deepcode_chat2code` | Submit text requirements for code generation |
| `deepcode_status` | Check task progress and results |
| `deepcode_list_tasks` | List active and recent tasks |
| `deepcode_cancel` | Cancel a running task |
| `deepcode_respond` | Respond to User-in-Loop interactions |

## When to Use DeepCode

### Automatically trigger `deepcode_paper2code` when user:
- Sends an arxiv URL (e.g. `https://arxiv.org/abs/...` or `https://arxiv.org/pdf/...`)
- Sends a paper URL from other academic sites
- Asks to "reproduce", "implement", or "replicate" a paper
- Sends a PDF file and asks for code generation
- Says something like "帮我复现这篇论文" or "把这篇论文的代码跑出来"

### Automatically trigger `deepcode_chat2code` when user:
- Describes a coding project they want to build
- Asks to create a web app, backend service, algorithm implementation, etc.
- Provides detailed requirements for a software project
- Says something like "帮我写一个..." or "生成一个项目..."

## Workflow Guidelines

### 1. Submitting a Task
When the user wants to generate code:
1. Identify if it's a paper (use `deepcode_paper2code`) or requirements (use `deepcode_chat2code`)
2. Submit the task and note the task_id
3. Tell the user the task has been submitted and the estimated wait time (10-60 minutes for papers, 5-30 minutes for chat)
4. Offer to check progress periodically

### 2. Monitoring Progress
- When user asks about progress, use `deepcode_status` with the task_id
- Report the progress percentage and current phase
- If the task is complete, share the result summary

### 3. Handling User-in-Loop Interactions
- Check `deepcode_status` - if status is "waiting_for_input", there's a pending interaction
- Read the interaction details (questions, plan review, etc.)
- Present the questions/plan to the user in a natural conversational way
- Collect the user's response
- Use `deepcode_respond` to submit the response back to DeepCode

### 4. Delivering Results
When a task completes:
- Report the generated file structure
- Mention key files (e.g. model.py, train.py, requirements.txt)
- The generated code is in the shared `deepcode_lab/` directory
- Offer to read specific files if the user wants to review them

## Response Style
- Be concise and informative about task status
- Use progress percentages to show advancement
- When a task completes, provide a brief summary of what was generated
- For Chinese-speaking users, respond in Chinese (follow the user's language)

## Important Notes
- Code generation tasks run in the background and take time (10-60 minutes)
- Do NOT spawn subagents for DeepCode tasks - use the tools directly
- If DeepCode backend is unreachable, inform the user that the service may not be running
- Generated code is stored in `/app/deepcode_lab/papers/` directory
