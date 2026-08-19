# DeepCode Evolve v2 — A Population of Self-Improving Harnesses in the Wild

Version v0.1 · 2026-08-19 · Supersedes the single-lineage framing of the
original "DeepCode Evolve" plan; written after a source-level feasibility
audit of this repository (see §6 for the receipts).

## 1. Thesis

Every RSI system to date — DGM, HGM, SICA, AIDE², SIA — evolves **one lineage
on lab infrastructure against fixed benchmarks**. The original Evolve plan
proposed doing the same on "a real user distribution", but DeepCode is
local-first: maintainers cannot see user tasks, so that "real distribution"
collapses to the team plus volunteers, and the headline claim dies in review.

The inversion is the idea. Local-first is not the obstacle; it is the asset
nobody else has:

> **Run one small, cheap, auditable evolution loop on every opted-in user's
> machine — a population of independent lineages — and study the population.**

The unit of analysis shifts from tasks to **lineages**. Users opt in to
sharing *lineage telemetry only* — per-generation promotion rates, guardrail
readings, cost/quality deltas — never tasks, transcripts, or skill content.
Privacy holds by construction; N equals the opted-in user base; "in the wild"
becomes literal. Prior work reports n = 1 lineage. We report a distribution.

## 2. Two-tier architecture

| | Flagship lineage (team-run) | Fleet lineages (one per opted-in user) |
|---|---|---|
| Layers evolved | L1 + L2 (L3 demo only) | **L1 only** — delta patches to `.agents/skills/`, `AGENTS.md`, `.deepcode/memory/` |
| Validation | Full layered battery (B1/B2/B3) | **Validation-by-usage**: provisional promotion, helpful/harmful counters from subsequent real turns, automatic retirement on harm; plus a <5-minute guardrail micro-suite per promotion |
| Produces | Recursion curves (R_g), controlled ablations, deep audits | **Base rates**: promotion-rate distributions, drift/misevolution incidence, generalization spread |
| Cost | Team budget | Near-zero marginal cost to the user (the precondition for opt-in) |

The flagship answers depth questions a battery can answer; the fleet answers
population questions nothing else can.

## 3. What evolves, mapped to real seams (all verified in-source)

**L1 (weeks to build).** Reflector = a scheduled Automation reading sessions
through `visible_kernel_history` — faithful as of this month's work: tool
calls/results are in the canonical record and `test_model_visible_is_logged`
pins that every model-visible message is reconstructable. Compaction
checkpoints are excluded via `is_compaction_checkpoint`. Curator = delta-only
operations (`add_bullet / amend_bullet / deprecate_bullet`) on skills and
memory; the shipped `MemoryTool.write` whole-file action is constrained to
delta form (there is no "self-tidying memory" feature to retrofit — the
original plan's §4.1.6 targets a feature that does not exist).

**L2 (the seams already exist).** `CompactionStrategy` and `TokenMeter` are
injectable Protocols on `AgentRunSpec` as of this month; retry counts, prune
thresholds, `RepeatCallTracker` parameters and model routing are config-level.
L2 search = swap a registration, never edit the runner. **The L2 objective is
the cost–quality Pareto frontier on the user's own workload**, not benchmark
success alone: prefix-cache discipline was measured here at 7,420 wasted
prompt tokens per turn boundary before repair, and the cl100k estimator at
+115% on Chinese prose — cost is where deployable, user-felt wins live first.

**L3 (demoted to demo).** Code self-modification is the crowded lane
(DGM/HGM/SICA/AIDE²; the gated self-PR mechanism is commoditized). Keep 2–3
fully audited self-PRs as narrative; hang no scientific claim on them.

## 4. Evaluation: capture prospectively, never distill retrospectively

A transcript is not a replayable task. Instead of the original plan's
Session→episode ETL, capture at task time via the existing hook events
(`UserPromptSubmit`, `Stop`): git HEAD + dirty-state digest + prompt at turn
start; outcome signals at turn end. The self-grading subset — episodes that
carry a test command (the `loop_cli` goal+test shape) — forms the internal
regression battery; acceptance proxies (immediate re-ask, revert, commit) are
reported as proxies, never as ground truth.

Battery execution: `exec_cli --json` + `WorktreeManager`, **one isolated
`DEEPCODE_HOME` per candidate** — mandatory, not an optimization: the
per-Session run lease refuses concurrent execution, and a documented
pre-existing SQLite concurrent-open fault
(docs/investigations/2026-08-19-concurrent-turn-submit.md) fires under shared
state.

## 5. Safety as the first-class scientific product

The self-evolved artifact is itself a **persistent injection channel**:
poisoned session → Reflector distillation → skill/AGENTS.md → system prompt,
every turn, forever. This is Misevolution's memory-poisoning instantiated in
a harness — and the fleet turns it from a lab anecdote into a measurable
**base rate**. Guardrail suite additions over the original plan:

- A fifth probe class: poison-to-distillation (seed a poisoned session,
  assert the Reflector does not promote an instruction-bearing artifact).
- Guardrail hooks and probe definitions live at a user-level read-only path,
  permission-denied to the agent — otherwise the loop can edit its own audit.
- Test write-protection must cover shell-mediated writes (`bash -c "echo >
  tests/x"`), not only the write/edit tools; canaries include redirects.
- Pre-registered freeze rule: refusal drop >5pp or injection-ASR rise >5pp
  freezes the lineage branch and pages a human; frozen branches are data.

## 6. Claims

| # | Claim | Carrier |
|---|---|---|
| C1 | Auditable-by-construction self-improvement: every promotion ships a public per-generation audit artifact | Flagship; Paper A backbone |
| C2 | First population-scale in-the-wild study of self-evolving agents: distributions of promotion, drift, and misevolution across N independent lineages | Fleet; Paper B centerpiece |
| C3 | Honest recursion measurement: R_g with frozen proposer/judge/replay set on the flagship, plus the fleet's promotion-rate distribution | Paper B |
| C4 | Cost-objective evolution: Pareto movement on real workloads | Papers A/B |
| C5 | Misevolution base rates in deployment + the poison-to-distillation channel + guardrail net benefit | Paper C (or the heavyweight section of B) |

Cut from the original plan: the three-way mutation-source comparison (the
literature-extraction proposer is its own research project), the full 2×2×2
layer factorial (L3 runs on a different substrate; the design is confounded
by construction), and self-PRs as a contribution.

## 7. Timeline

```
W1–3    L0 corrected: guardrails incl. 5th probe class; user-level hook home;
        episode capture; isolated-home battery; lineage DB (SQLite)
W3–8    Flagship L1 loop; delta Curator; `deepcode evolve` opt-in ships
        (provenance + one-click rollback + per-project disable)
W6–10   Fleet telemetry (lineage metadata only) + Paper A (arXiv ≤ Oct 2026):
        system + first ~10 flagship generations + per-generation audit +
        first characterization of the poison-to-distillation channel
W10–18  L2 cost-Pareto search on the injectable seams; R_g curves; first
        fleet population data
W18+    Papers B (NeurIPS 2027 track) and C; audited L3 demo PRs
```

First priority is data: episode capture and opt-in telemetry start in W1 —
lineage data is the one thing that cannot be bought or backfilled.

## 8. Feasibility receipts (source-audited 2026-08-19)

- Sessions carry tool calls/results + compaction checkpoints (landed this
  month; previously text-only — the Reflector would have been blind).
- Hook events available: SessionStart, UserPromptSubmit, PreToolUse (with
  block+reason), PostToolUse, PermissionRequest, PreCompact, Stop,
  SubagentStart/Stop.
- `exec_cli --json` streams serialized events; `WorktreeManager` provides
  isolated worktrees with 3-way merge-back ("Team mode" as a pipeline is
  retired — the original plan's references to it must read worktree+exec).
- Cost data exists: `turn.usage.recorded` in the event log; provider-anchored
  prompt measurement.
- Skills: canonical root `.agents/skills/` (`.deepcode/skills/` is compat);
  per-turn immutable snapshots; hot reload.
- Config accepts an `evolve` block additively (`extra="ignore"`).
- Per-Session run lease (one live writer) both protects users from the
  evolution automation and constrains the battery to isolated homes.


## 9. Novelty audit (2026-08-19, web survey)

Nearest neighbours found, and where the line of novelty now sits:

- **FederatedSkill** (arXiv 2606.03143, UCSB/MIT-IBM/Cisco): federated skill
  evolution via semantic skill patches, per-client personalization, privacy
  by patch-sharing. **Takes the mechanism** this plan's fleet uses (local
  skill evolution + share patches, not trajectories). Evaluated on 20
  simulated task families — no deployed users, no audit, no safety
  measurement.
- **EvolveNet** (arXiv 2608.04968, Aug 5 2026): collaborative harness
  evolution — broadcast a shared harness to data-local deployments, evolve
  locally, compose adaptations back. **Takes the distributed-harness-evolution
  framing.** Again: benchmark clients, no real deployment, no audit.
- **Harness Updating Is Not Harness Benefit** (arXiv 2605.30621): already
  disentangles update-production from update-benefit — overlaps the
  recursion-attribution ambition of C3 and must be cited and built on, not
  rediscovered.
- **Safety in Self-Evolving LLM Agent Systems** (arXiv 2606.23075) +
  **Misevolution** (2509.26354): threat taxonomy (MLAS matrix) and lab
  longitudinal erosion curves. Neither measures **deployed base rates**.
- Commodity blogs already describe on-device agents with "closed learning
  loops that write reusable skills"; stage-gated auditable promotion is
  becoming folklore.

**Consequence.** The *mechanisms* (local skill evolution, patch sharing,
distributed harness evolution, gated promotion) are now all published or
commoditized — within the last three months. What remains unclaimed, and
what this plan must therefore be about, is the **measurement**: real deployed
users rather than simulated clients; misevolution and promotion **base rates
across a real population of lineages**; a per-generation public audit trail;
and the poison-to-distillation channel characterized in a shipping system.
The system sections above are the substrate; the paper is the study. Anyone
with a benchmark can publish another framework; only a shipped local-first
product with real users can publish the population.


## 10. Execution order (frozen 2026-08-19)

W1–W3, strict dependency order:

1. **Episode capture** (`UserPromptSubmit`/`Stop` hooks): git HEAD + dirty
   digest + prompt at turn start; outcome signals at turn end. First because
   lineage data cannot be backfilled.
2. Guardrail suite v0 — five probe classes including poison-to-distillation;
   installed at a user-level read-only path that the permission engine denies
   to the agent; write-protection covers shell-mediated writes.
3. Delta schema v1 frozen + `add/amend/deprecate_bullet` operations on the
   skill library and MemoryTool.
4. Isolated evaluation runner: `exec_cli --json` + WorktreeManager + one
   `DEEPCODE_HOME` per candidate (required by the run lease and the known
   concurrent-open SQLite fault).
5. Lineage DB (`evolve/archive.py`); register current main as generation 0.

W3–W8: Reflector Automation → delta Curator → ship `deepcode evolve`
(opt-in; `status / history / rollback <id> / run`; per-skill provenance,
one-click rollback, per-project disable).

Positioning for release: "the first local self-learning coding agent with a
validation loop, audit trail, and one-click rollback" + the cost-Pareto
value ("it learns to be cheaper on your workload"). Low promise, high
delivery: value compounds with usage; cold start is explicit in the copy.
