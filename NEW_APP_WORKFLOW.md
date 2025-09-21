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

## Enterprise-Grade Prompt Blueprint

For complex, fully integrated enterprise builds, start from the Speckit-inspired prompt template below. Replace the bracketed guidance with your project specifics so the planner receives a complete, implementation-ready brief.

```
Project title & vision:
- [Elevator pitch summarizing the product, target users, and strategic objective.]

Business outcomes & success metrics:
- [Objective 1 + measurable KPI/OKR]
- [Objective 2 + measurable KPI/OKR]

Problem statement & background:
- [Current pain points, triggering events, adjacent initiatives.]

Personas, roles & access model:
- [Persona 1 responsibilities, access requirements, primary journeys]
- [Persona 2 responsibilities, access requirements, primary journeys]
- RBAC / SSO expectations: [Identity provider, policy constraints]

User journeys & experience principles:
- Journey 1: [Describe start → finish flow, success criteria, exception handling]
- Journey 2: [...]
- UX guardrails: [Design principles, accessibility tier, localization languages]

Functional scope (Epics → features → stories):
1. Epic / module: [Name]
   - Purpose: [Outcome the module delivers]
   - Key features: [Feature A, Feature B, Feature C]
   - User stories: [Story statements with acceptance criteria]
   - Cross-module dependencies: [Shared data/services needed]
2. …

Architecture & system topology:
- Target architecture: [Microservices, modular monolith, event-driven, etc.]
- Core services/components: [Service name + responsibilities]
- Shared libraries/platform capabilities: [...]
- Data flow (text diagram): [...]

Data contracts, governance & lifecycle:
- Canonical entities: [`entity_name` + critical fields + validation]
- Storage & retention: [Databases, warehouses, retention policies]
- Data quality & compliance: [Lineage, masking, residency, audits]

Integrations & external systems:
- [System/API] — [Purpose, protocol, auth, throughput expectations, error handling]
- [...]

Design system & content standards:
- Brand tokens: [Color ramp, typography, spacing, iconography]
- Component library: [UI kit expectations, bespoke components]
- Accessibility & localization: [WCAG tier, supported languages, content tone]

Quality engineering, security & compliance gates:
- Testing: [Unit, integration, e2e, performance, chaos]
- Tooling: [pytest, Playwright, k6, SonarQube, OWASP ZAP, etc.]
- Security posture: [Threat model, encryption, secrets handling, compliance frameworks]

Performance, scalability & reliability targets:
- SLAs/SLOs: [Latency, availability, throughput targets]
- Capacity plan: [User volume, data scale, scaling strategies]
- Resilience: [Failover, disaster recovery, RTO/RPO]

Deployment, operations & observability:
- Environments & release strategy: [Dev/Staging/Prod + gating]
- IaC & pipelines: [Terraform, Helm, GitOps, manual approvals]
- Observability stack: [Logging, metrics, tracing, alerting runbooks]

Analytics, telemetry & experimentation:
- KPIs & dashboards: [Business + operational metrics]
- Event instrumentation: [Tracking schemas, analytics tooling]
- Experimentation: [Feature flagging, AB testing framework]

Implementation roadmap & change management:
- Phase 0 (Discovery): [Outputs, duration]
- Phase 1 (MVP): [Scope, dependencies]
- Phase 2 (Scale): [Scope, dependencies]
- Adoption plan: [Training, communications, support model]

Risks, assumptions & open questions:
- Risks: [Description + mitigation]
- Assumptions: [What must remain true]
- Open questions: [Clarifications needed from stakeholders]

Deliverables & artefact checklist:
- [Code, IaC, design assets, API specs, runbooks, test evidence, compliance packets]

Explicit integration directive:
- Ensure every module, workflow, and integration listed above functions cohesively end-to-end without manual stitching.
```

Copy the completed prompt into the chat input. This structure maximizes the likelihood that the planning agent captures every domain requirement, enforces cross-module integration, and guides the downstream workflows toward an enterprise-ready implementation.
