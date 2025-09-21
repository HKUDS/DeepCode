# Enterprise Application Prompt Guide

DeepCode can generate complex, integrated applications when it receives a thorough, well-structured chat prompt. Use this guide to organize your requirements so the planning and implementation agents have enough context to deliver enterprise-grade codebases. The structure below mirrors the product-spec rigor popularized by GitHub's Speckit project—covering strategy, execution, quality, and change-management dimensions so nothing falls through the cracks.

## 1. Capture the Right Domains of Information
Structure your requirements to address each of the following focus areas. Concise bullets are preferred, but include any critical constraints or success metrics.

| Section | What to Cover |
| --- | --- |
| **Product Vision & Strategic Fit** | Problem statement, business outcomes, OKRs, and alignment with broader portfolio strategy. |
| **Personas & Journeys** | Primary and secondary user roles, access policies, and the end-to-end workflows they must complete. |
| **Functional Scope (Epics → Features → Stories)** | Break the solution into bounded contexts, modules, and high-priority user stories. Note cross-module dependencies and orchestration rules. |
| **Architecture & System Topology** | Desired service decomposition, data flow diagrams, messaging/event patterns, shared libraries, and infrastructure layers. |
| **Data Contracts & Governance** | Canonical entities, schemas, validation rules, lineage, retention, and compliance obligations (PII/PHI, GDPR, SOC2, etc.). |
| **Integrations & External Systems** | Third-party APIs, internal platforms, auth schemes, throughput/SLA expectations, and failure handling. |
| **Design System & UX Standards** | Brand tokens, UI libraries, accessibility mandates, responsive breakpoints, localization, and content guidelines. |
| **Quality, Security & Compliance Gates** | Testing matrix, automation expectations, security hardening, risk controls, and audit requirements. |
| **Performance, Scalability & Reliability** | SLAs, SLOs, RTO/RPO targets, load expectations, caching strategies, and capacity plans. |
| **Deployment, Operations & Observability** | Environments, IaC tooling, CI/CD flow, monitoring/alerting, runbooks, on-call rotations, and incident response. |
| **Analytics, Telemetry & Business Insights** | KPIs, dashboards, event instrumentation, experimentation frameworks, and reporting cadence. |
| **Implementation Roadmap & Milestones** | Phased delivery plan, sequencing, dependencies, change-management checkpoints, and stakeholder approvals. |
| **Adoption, Training & Support** | Enablement strategy, documentation expectations, rollout communications, and feedback loops. |
| **Risks, Assumptions & Open Questions** | Known blockers, decision logs, regulatory reviews, or areas needing clarification. |
| **Deliverables & Artefact Checklist** | Code, infrastructure manifests, design assets, API specs, test evidence, SOPs, and any governance packets. |

## 2. Prompt Template for Enterprise Builds
Use the template below as a starting point. Replace the bracketed prompts with your project details and remove sections you do not need. It mirrors the Speckit-style spec layout so planners and implementation agents receive both the narrative and the execution-ready backlog.

```
Project Title & Vision:
- [One-sentence elevator pitch describing the product, target users, and strategic objective.]

Business Outcomes & Success Metrics:
- [Objective 1 + measurable KPI/OKR]
- [Objective 2 + measurable KPI/OKR]

Problem Statement & Background:
- [Current pain points, triggering events, adjacent initiatives.]

Personas, Roles & Access Model:
- [Persona 1: responsibilities, permissions, key journeys]
- [Persona 2: responsibilities, permissions, key journeys]
- RBAC / SSO expectations: [SSO provider, policy constraints]

User Journeys & Experience Principles:
- Journey 1: [Start → finish flow, success states, exception handling]
- Journey 2: [...]
- UX guardrails: [Design philosophy, accessibility targets, localization languages]

Functional Scope (Epics → Features → Stories):
1. Epic / Module: [Name]
   - Purpose: [Outcome the module delivers]
   - Key features: [Feature A, Feature B, Feature C]
   - User stories: [Story statements with acceptance criteria]
   - Cross-module dependencies: [Shared data/services needed]
2. ...

Architecture & System Topology:
- Target architecture: [Microservices, event-driven, monolith, etc.]
- Core services: [Service A responsibilities, interfaces]
- Shared libraries/platform components: [...]
- Data flow / integration diagram (described textually): [...]

Data Contracts, Governance & Lifecycle:
- Canonical entities: [`entity_name` + critical fields + validation]
- Storage choices: [Databases, data lakes, retention policies]
- Data quality controls: [Lineage, audits, masking, residency]

Integrations & External Systems:
- [System/API] — [Purpose, protocol, auth, throughput expectations, error handling]
- [...]

Design System & Content Standards:
- Brand foundations: [Color ramps, typography, spacing scale, iconography]
- Component library: [UI kit, bespoke components, responsiveness expectations]
- Accessibility & localization: [WCAG tier, languages, content tone]

Quality Engineering, Security & Compliance Gates:
- Testing: [Unit, integration, e2e, performance, chaos]
- Tooling: [pytest, Playwright, k6, SonarQube, OWASP ZAP, etc.]
- Security posture: [Threat model, encryption, secrets handling, compliance frameworks]

Performance, Scalability & Reliability Targets:
- SLAs/SLOs: [Latency, availability, throughput]
- Capacity plans: [Concurrency, data volume, sharding]
- Resilience: [Failover, disaster recovery, RTO/RPO]

Deployment, Operations & Observability:
- Environments: [Dev/Staging/Prod + gating]
- IaC & pipelines: [Terraform, Helm, GitOps, manual approvals]
- Observability stack: [Logging, metrics, tracing, alerting runbooks]

Analytics, Telemetry & Experimentation:
- KPIs & dashboards: [Business metrics, operational dashboards]
- Event instrumentation: [Event schemas, tracking tools]
- Experimentation: [A/B testing framework, feature flag strategy]

Implementation Roadmap & Milestones:
- Phase 0 (Discovery): [Outputs, duration]
- Phase 1 (MVP): [Scope, dependencies]
- Phase 2 (Scale): [Scope, dependencies]
- Change management: [Training, communications, adoption playbooks]

Risks, Assumptions & Open Questions:
- Risks: [Description + mitigation]
- Assumptions: [What must remain true]
- Open questions: [Clarifications needed from stakeholders]

Deliverables & Artefact Checklist:
- [Code, IaC, design assets, API specs, runbooks, test evidence, compliance packets]

Explicit Integration Directive:
- Ensure every module, workflow, and integration listed above functions cohesively end-to-end without manual stitching.
```

## 3. Example: Integrated Healthcare Operations Platform
```
Project Title & Vision:
- Integrated patient-care coordination platform for a multi-hospital network that unifies clinical, operational, and analytics workflows.

Business Outcomes & Success Metrics:
- Reduce readmission rates by 12% within 12 months via proactive care-plan interventions.
- Improve discharge planning throughput by 25% by automating cross-department task orchestration.
- Achieve 95% clinician satisfaction with patient data availability during rounds (survey + NPS).

Problem Statement & Background:
- Disparate EHR modules and manual spreadsheets fragment patient visibility across care teams.
- ServiceNow tickets are disconnected from bedside alerts, leading to missed follow-ups.
- Leadership mandates a HIPAA-compliant, analytics-enabled coordination hub before regional expansion.

Personas, Roles & Access Model:
- Care Coordinators — manage referrals, discharge planning, and follow-up tasks. Need read/write access to patient journeys, ServiceNow sync, and alert triage tools.
- Physicians & Specialists — review patient 360 data, update care plans, acknowledge escalations. Require scoped editing rights and mobile-responsive UI.
- Operations Managers — monitor throughput KPIs, manage SLA compliance. Require analytics dashboards and incident reporting with admin privileges.
- RBAC / SSO: Enforce Azure AD SSO with role/department-based policy bundles; audit all privilege changes.

User Journeys & Experience Principles:
- Journey 1: Admission → Care Plan Initialization → Multidisciplinary collaboration → Discharge readiness check → Post-acute follow-up automation.
- Journey 2: High-risk alert ingestion → Rapid triage workspace → Escalation to specialty team → Documentation → Closed-loop verification.
- UX guardrails: Align with hospital design tokens, support touch-friendly controls for tablets, meet WCAG 2.1 AA and bilingual English/Spanish content.

Functional Scope (Epics → Features → Stories):
1. Epic: Patient 360 Insight Hub
   - Purpose: Provide unified patient context for clinical decision-making.
   - Key features: Timeline view, vitals trends, lab result drill-down, collaborative notes.
   - User stories: "As a physician, I can filter vitals by timeframe to prep for rounds"; "As a coordinator, I can @-mention staff in shared notes with audit trail".
   - Dependencies: Requires EHR FHIR subscriptions, HL7 ingestion service, and patient alert microservice.
2. Epic: Care Plan Orchestration
   - Purpose: Standardize multidisciplinary plans and automate hand-offs.
   - Key features: Specialty templates, approvals, escalation matrix, ServiceNow sync.
   - User stories: "As a coordinator, I can instantiate a cardiology plan with prefilled tasks"; "As a manager, I can set SLA timers per task".
   - Dependencies: ServiceNow bidirectional API gateway, task scheduler worker, notification service.
3. Epic: Analytics & Compliance Command Center
   - Purpose: Deliver operational KPIs, risk scores, and audit-ready reports.
   - Key features: Readmission dashboards, SLA compliance monitors, PHI access logs, export pipelines.
   - User stories: "As an operations lead, I can export anonymized metrics nightly to Snowflake".

Architecture & System Topology:
- Target architecture: Modular FastAPI services communicating via event bus (Kafka) with shared Postgres schemas and Redis caches.
- Core services: `patient-context-service`, `care-plan-service`, `task-orchestrator`, `analytics-etl`, `notification-worker`.
- Shared libraries: Domain models package, audit logging middleware, tracing utilities, component UI kit.
- Data flow: HL7 listeners ingest events → normalize via integration service → publish to Kafka topics → downstream services update Postgres and invalidate caches; analytics ETL streams to Snowflake.

Data Contracts, Governance & Lifecycle:
- Canonical entities: `patient_profile`, `encounter`, `care_plan`, `task`, `alert`, `audit_event` with strict schemas and versioning.
- Storage choices: PostgreSQL (operational), Redis (caching), Snowflake (analytics lakehouse) with 7-year PHI retention and regional residency.
- Data quality controls: Schema registry validation, automated lineage capture, PHI masking in logs, quarterly access reviews.

Integrations & External Systems:
- Epic & Cerner EHR — HL7 FHIR subscriptions (OAuth2 + mTLS), near-real-time updates, backoff retries with dead-letter queues.
- ServiceNow — REST API (OAuth client credentials), bi-directional task sync, conflict resolution rules.
- Azure AD — OpenID Connect SSO, SCIM provisioning, conditional access policies.
- MQTT Broker — Ingest bedside device vitals via TLS, push into alert engine.

Design System & Content Standards:
- Brand foundations: Hospital color ramp (#005EB8 primary), typography (Roboto, 16px base), spacing (8px grid), iconography via custom medical set.
- Component library: React + TypeScript with Material UI + hospital tokens; custom components for vitals timeline, plan composer, SLA badge.
- Accessibility & localization: WCAG 2.1 AA, supports English/Spanish toggle, plain-language content guidelines, text-to-speech compatibility.

Quality Engineering, Security & Compliance Gates:
- Testing: pytest unit coverage ≥85%, integration tests with mocked EHR/ServiceNow, Playwright e2e smoke suite, k6 performance harness, chaos drills for Kafka outages.
- Tooling: SonarQube (SAST), OWASP ZAP (DAST), Trivy (container scanning), IaC drift detection.
- Security posture: Zero trust networking, PHI encryption at rest (AES-256) and in transit (TLS 1.3), secrets via HashiCorp Vault, HIPAA/HITRUST controls.

Performance, Scalability & Reliability Targets:
- SLAs/SLOs: 99.9% API availability, <300ms P95 for patient summary fetch, <2 min RTO, <15 min RPO.
- Capacity plans: Support 25 hospitals, 8k concurrent users, 50k daily alerts; auto-scale based on CPU & queue depth.
- Resilience: Active-active AKS clusters across regions, automated failover, synthetic monitoring for critical journeys.

Deployment, Operations & Observability:
- Environments: Dev → QA → Staging → Production with promotion via GitOps pull requests and manual prod approval.
- IaC & pipelines: Terraform for Azure infra, Helm charts for services, GitHub Actions pipeline with security/test gates, ArgoCD for deployments.
- Observability stack: OpenTelemetry traces, Prometheus metrics, Loki logs, Grafana dashboards, PagerDuty alert routing, runbooks stored in Confluence.

Analytics, Telemetry & Experimentation:
- KPIs: Readmission %, discharge throughput, alert closure SLA, clinician satisfaction.
- Event instrumentation: Segment + Snowplow events for user interactions, data warehouse models for weekly exec review.
- Experimentation: Feature flags via LaunchDarkly, AB testing on alert notification strategies, guardrail metrics (clinical safety).

Implementation Roadmap & Milestones:
- Phase 0 (Discovery & alignment, 4 weeks): Validate integrations, finalize data governance, prototype design system.
- Phase 1 (Pilot hospitals, 12 weeks): Deliver Patient 360, Care Plan Orchestration, ServiceNow sync; onboard two hospitals.
- Phase 2 (Network scale, 10 weeks): Launch analytics command center, automation playbooks, high-availability rollout.
- Change management: LMS curriculum, super-user cohorts, town halls, communications kit for regional leadership.

Risks, Assumptions & Open Questions:
- Risks: HL7/FHIR latency impacting dashboards (mitigate via caching + stale data indicators); clinician adoption lag (mitigate with bedside champions & feedback loops).
- Assumptions: Hospitals maintain MQTT broker uptime; Azure AD groups reflect accurate department memberships.
- Open questions: Confirm Snowflake regional residency, finalize ServiceNow incident priority mapping.

Deliverables & Artefact Checklist:
- Source code repo, IaC manifests, architecture diagrams, ERDs, OpenAPI specs, Postman collection, analytics dashboards, operational runbooks, HIPAA risk assessment, training materials.

Explicit Integration Directive:
- All modules must consume shared patient, task, and alert schemas; ServiceNow updates trigger care plan recalculations; Azure AD roles govern UI/feature access end-to-end.
```

## 4. Tips for Working with DeepCode
- **Treat the prompt like a living product spec.** Keep it versioned and iterate with stakeholders just as you would a Speckit-style document.
- **Be explicit about integrations and compliance.** The agents rely on your instructions to create connectors, secrets management, and audit trails.
- **Prioritize modular structure.** Group requirements by service or feature to help the planner emit scalable file trees.
- **Call out testing expectations.** Mention unit, integration, and security tests so the workflow can enable iterative modes when configured.
- **Reference design systems.** Provide design tokens or UI libraries so the generated frontend aligns with enterprise standards.
- **Attach rollout and adoption plans.** Include training, support, and analytics requirements so launch-readiness tasks are generated alongside code.
- **Iterate if needed.** After generating the initial plan, update your prompt with clarifications or additional modules to refine the build.

For the full chat workflow steps, see [NEW_APP_WORKFLOW.md](NEW_APP_WORKFLOW.md).
