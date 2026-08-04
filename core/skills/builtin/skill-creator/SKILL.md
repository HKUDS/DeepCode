---
name: skill-creator
description: Create or refine reusable DeepCode Agent Skills. Use when the user asks to create, scaffold, validate, or improve a Skill, SKILL.md workflow, or packaged instructions with scripts, references, or assets.
---

# Create a Skill

Create focused, reusable workflow instructions for another coding agent.

1. Confirm the concrete task examples, trigger conditions, and target scope when they are unclear. Default to a project Skill.
2. Choose a short lowercase hyphen-case name, no longer than 64 characters.
3. Decide which reusable resources are justified:
   - `scripts/` for deterministic repeated operations;
   - `references/` for detailed material loaded only when needed;
   - `assets/` for templates and output resources.
4. Run `python <skill-directory>/scripts/init_skill.py`. Use `<workspace>/.agents/skills` for project scope. Use `~/.agents/skills` only when the user explicitly requests a user Skill and normal permissions allow it.
5. Replace every placeholder in `SKILL.md`. Keep frontmatter to `name` and `description`; describe both what the Skill does and when it should trigger.
6. Keep the body concise and imperative. Put lengthy or conditional details in directly linked reference files.
7. Add or update `agents/openai.yaml` only for useful interface metadata or invocation policy. Keep `default_prompt` short and mention `$skill-name`.
8. Run `python <skill-directory>/scripts/quick_validate.py <created-skill-directory>` and fix every reported error.
9. Exercise scripts and forward-test the Skill on a realistic task before claiming it is ready.

Do not create README, changelog, installation guide, or other process documentation inside a Skill. Never overwrite an existing Skill unless the user explicitly asks to update it.
