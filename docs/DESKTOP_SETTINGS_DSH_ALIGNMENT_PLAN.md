# Desktop Settings × dsh Alignment Plan

> Goal: align the Desktop settings surface (General + Models first) with dsh's
> settings dialog design (left-rail navigation General / Models / Plugins /
> Agent presets, "Open configuration file", the five General rows, the Models
> provider row cards + model list editor) — **without touching the agent
> runtime**. This document first draws the framework boundary, then lays out a
> phased design. Research basis: dsh source (`packages/settings/*`,
> `packages/client/ui-settings*`, `packages/llm/llm-pi-ai`) and a full-stack
> live map of DeepCode (settings RPCs → config file → execution-profile
> resolution).

---

## 0. The direct answer: does this affect the agent runtime?

**No — as long as one seam is respected.** Settings data enters the runtime
through exactly two entry points, and both are *read* relationships:

1. **Model/connection**: on every turn submit, `turn_service.py:603-616` calls
   `LLMConfigurationService.resolve` → `ConnectionResolver.execution_profile`,
   reading `providers.profiles.*` / `agents.*` from the config file at that
   moment.
2. **Permission**: `turn_service.py:620-626` calls
   `ExecutionSecurityPolicy.resolve`, reading `security.*` plus per-session
   overrides.

The settings UI — whatever it looks like — only changes **configuration data**
through the existing RPCs; neither read path changes by a single line, so
runtime behavior varies only with the data (which is what settings are for).
This plan touches the application layer in exactly two places, both additive
and neither on the turn execution path (see P1-b and P3-a, each with its own
invariance conditions).

---

## 1. Framework boundary

### 1.1 Untouchable (core, red lines)

| Module | Reason |
|---|---|
| `core/application/turn_service.py` | Turn admission/queueing/retry/compaction; settings are only *read* by it |
| `core/application/session_runtime.py` | Resident runtimes and the runtime-key invalidation rule |
| `core/agent_runtime/*` (runner/pruner/tools) | The agent loop itself |
| `core/sessions/*` | Canonical JSONL + SQLite index |
| Event protocol: `core/events`, `app_server/protocol/*`, existing entries in `protocol/app-server.schema.json` | SQ/EQ event sourcing; the schema is **append-only** |
| `core/domain/execution_profile.py` / `execution_security.py` | Value objects shared by the whole stack |
| **Semantic red lines** (must survive any UI change): | |
| ① The `full_access` triple-confirmation chain (desktop confirm → dispatcher `riskAcknowledged` → frozen per-turn snapshot) | Security semantics |
| ② Agent preset **by-value snapshot** + lock once the conversation has started (a server rule; the UI only mirrors it) | Session reproducibility |
| ③ Refusing an admitted turn after `connection_revision` changes (`profiles.py:251-255`) | A feature, not a bug |
| ④ Credentials never enter the config file and never leave through the environment; `credentials.json` is 0600 atomic | This session's provider phase 1 result |
| ⑤ Config validation is the server's last gate (`ConfigStore.mutate` → `model_validate`) | The UI must never become a correctness dependency |
| ⑥ `resolve_selection` precedence (explicit connection > configured pin > catalog match > first usable) | Shared by CLI/TUI/Desktop |

### 1.2 Movable (the working area of this plan)

| Layer | Scope |
|---|---|
| `desktop/src/**` | Free to restructure the UI (note: `ManagementWorkspace.module.css` is shared by 5 pages — the new dialog brings its own CSS module and leaves the shared file alone) |
| `app_server` | **Additive** new RPCs / new optional params (edit `protocol/app-server.schema.json` → `npm run check:protocol` → regenerate TS) |
| `core/config.py` config schema | **Additive**, backward-compatible fields (old files must load unchanged) |
| `core/application/settings_service.py` allowlists | Additive patch fields |
| `core/application/llm_configuration_service.py` | Additive read-only methods (precedent this session: `model_reasoning`) |
| Desktop-local preferences (localStorage) | Precedent: `deepcode.desktop.appearance.v1` |

### 1.3 Gap between the screenshot and today (important facts)

The user's screenshot is **dsh's settings dialog** (five General rows + four
nav items + Open configuration file). DeepCode's current state differs by more
than styling:

- There is **no settings dialog** — today it is a full sidebar page
  (`SettingsPage`) stacking Models (ConnectionSettings), agent model/phases,
  permission default, appearance, diagnostics, and updates vertically;
- Plugins is a separate destination, not part of Settings; Agent presets has
  **no settings surface at all** (only the per-session composer popover);
- **Language does not exist** (no i18n dependency in desktop);
  **Enter-while-busy does not exist** (hard-coded steer-first);
- Appearance exists and is richer than dsh's (8 palettes / width / font), but
  has no Light/Dark/System tri-state.

---

## 2. Design constraints distilled from dsh (enforced by this plan)

1. **The config file is the single source of truth; the UI is an editor over
   it.** Discovery (fetch models) only produces candidates; adopting writes.
   (Already true of the Models fieldset — keep.)
2. **Catalogs are advisory and never gate routing.** `routable` and `groups`
   are two separate facts.
3. **The connection id IS the route name, and it is permanent**; renaming is
   create-then-delete, explicitly.
4. **Secrets are references, not literals**: the UI has only a write-only
   input; values live in `credentials.json`; since the UI never receives
   secret values, writes must be **path-level patches**, never wholesale
   replaces (DeepCode's `provider/upsert` already merges by field allowlist —
   satisfied).
5. **Effort belongs to the model, not the provider**: no effort control on
   provider cards; effort is offered per model in the model selector (already
   in the TUI picker; Desktop aligns).
6. **Enums flow from server schema/data, never client constants** (permission
   presets, protocols, preset lists all come from RPCs).
7. **Hand-written cards, no generic schema-driven forms** (dsh tried and
   reverted); the honest escape hatch for advanced fields = "the rest lives in
   the config file" + Open configuration file.
8. **Refuse a bad write where it is written** (server validation as the
   backstop), and **a bad config never takes the process down** (keep the last
   good value).
9. **One snapshot per operation**: an in-flight turn runs under the config it
   started with (DeepCode's frozen per-turn profile already guarantees this).
10. **Every settings row declares when it takes effect** (live / next session /
    restart) and the UI says so honestly — DeepCode mapping: provider edits =
    next turn; default permission/preset = new sessions.

---

## 3. Phased design

### P1 — Settings dialog shell + General alignment

**P1-a Shell (desktop only)**
- New `SettingsDialog` (modal, centered, ~1080×700, left nav column + content
  column) with four nav items: **General / Models / Plugins / Agent presets**.
  Sections are driven by one `SETTINGS_SECTIONS` registry array
  (id/order/label/icon/component) — declared in one place, the shell owns zero
  copy (dsh's composition principle, implemented at DeepCode's scale as an
  array; no slot system needed).
- Header **Open configuration file**: shows `settings.configPath`; clicking
  opens it via the Tauri opener (add an `openPath` capability to
  `desktop/src/rpc/contracts.ts` + the Tauri side, or use the path already
  returned by `settings/read` + the shell-open plugin; either way additive).
- The current `SettingsPage` content is **redistributed**: agent model/phase
  routing → Models section; permission default / appearance → General;
  diagnostics + app updates → bottom of General (or a separate "About" row).
  The sidebar Settings entry now opens the dialog. The Plugins section reuses
  the existing `PluginsPage` content component (page shell removed); the
  sidebar Plugins entry stays or goes (recommend: stays — dsh also has dual
  entry).
- Test migration: the settings/provider/model flow assertions in
  `App.test.tsx` follow the new structure with unchanged assertion semantics.

**P1-b The five General rows** (top to bottom, matching the screenshot)

| Row | Design | Storage | Layers touched |
|---|---|---|---|
| **Agent preset** (default for new sessions) | Dropdown fed by `preset/list` (with trust markers), plus "None (default composition)" | New config key `agents.defaultPreset: str \| null` (additive; append to `_AGENT_FIELDS` + schema) | One application-layer addition: `thread_service.start`, **when no preset is explicitly given**, applies a resolvable configured default through the existing `set_agent_preset` code path, landing the usual by-value snapshot. Invariance: fills the blank only at creation, never touches existing sessions; an unresolvable preset is ignored and the session is created anyway (a bad config never blocks sessions). CLI/TUI benefit automatically because they share `threads.start` |
| **Permission** (default for new sessions) | The existing `security.accessPreset` (user/project scopes + full-access risk confirmation) moved in as-is, presented dsh-row style | Existing; zero change | UI move only. **Deliberately not adopting** dsh's read-only/workspace-write/danger-full-access naming: those are sandbox semantics, DeepCode's ask/read-only/full-access are approval semantics — borrowing the names would lie (§4 divergences) |
| **Language** | See P4 (the cost is string extraction; placeholder only for now) | — | — |
| **Appearance** | **Light / Dark / System** tri-cards on top (dsh style, `aria-pressed`, selection follows the persisted preference, not the resolved theme); DeepCode's richer palette/width/font controls remain below as the advanced area | Existing localStorage key gains a `mode: light\|dark\|system` field; System = a `prefers-color-scheme` listener mapping to a light/dark palette pair | Desktop only (`appearance.ts` settings table + one row; `applyAppearance` learns the tri-state; `tokens.css` already ships light and dark palettes — nothing new) |
| **Enter behavior while busy** | `Queue \| Steer` dropdown, captioned "busy only; Cmd/Ctrl+Enter uses the other behavior" (dsh defaults to queue; DeepCode's current behavior is steer — **default to steer** so existing users feel nothing) | localStorage (desktop-local, same tier as appearance) | Desktop only: `Composer` submit consults the preference to route through `sendTurn` (steer path) or `submitQueued`; Cmd/Ctrl+Enter takes the inverse; the `↵ steer/queue` hint follows |

### P2 — Models section alignment (the user's top priority)

**Layout (dsh ModelsSection structure):**

```
┌ Default model ──────────────────────────────┐
│ Route picker (connection/model, from the    │ ← writes agents.defaults{connection,model}
│ directory + manual entries)                 │
│ · effort (that model's published ladder,    │ ← writes agents.defaults.reasoningEffort
│   auto + levels)                            │   (already allowlisted)
│ ▸ Advanced phase routing (planning/impl)    │ ← existing capability folded in unchanged;
└─────────────────────────────────────────────┘   workflow consumers untouched
┌ Providers ──────────────────────────────────┐
│ ● OpenRouter        ●key  3 models  Edit ✕  │ ← row card: name + Custom tag + solid/hollow
│ ● Poe (openai)      ●key  1 model   Edit ✕  │   credential dot + model count; Edit opens editor
│ [Add provider ▾]  [Add a custom provider]   │ ← dormant-template dropdown + custom create
└─────────────────────────────────────────────┘
```

- The **Default model row** is DeepCode's adaptation of dsh's
  `agent-default-model`: one default route + effort. Effort levels come from
  the existing `LLMConfigurationService.model_reasoning` (either a small
  `model/reasoning` RPC or fold the field into the `model/list` response — an
  additive schema field; the latter is cheaper).
- Phase routing (planning/implementation) is a DeepCode-only asset — **not
  deleted**, folded into a disclosure (dsh has no such concept; the workflow
  service consumes it).
- **Row cards slim down**: today's rail is information-dense; converge on dsh
  density (name, Custom tag, credential dot, model count, Edit/Delete).
  Check/verification moves inside the editor (as in dsh). The directory strip
  and "Add provider" merge into a single dropdown entry. Deletion keeps
  `confirmAction`, wording notes that the managed key is deleted too.
- **Editor aligned with dsh's ProviderEditor**:
  - The only primary field is a **write-only API key**; the env-var input
    moves into the collapsed advanced area (existing env-shadow lock and note
    stay). dsh's "derive the env name from the provider id" is *not* copied —
    DeepCode's primary credential store is `credentials.json`, not env; the
    current model is stronger, keep it.
  - Advanced area: displayName, apiBase, adapter (custom only), model catalog
    mode, the **model list editor** (P3), and remove-saved-key.
  - Footer hint: "everything else lives in deepcode_config.json — Open
    configuration file edits it directly".
- **Credential/verification behavior unchanged**: still
  `provider/upsert` (allowlist merge) → `provider/test` three stages; the
  `useConnectionCatalog` controller stays as-is (shared with the composer's
  ModelPicker — do not touch it).

### P3 — Model entries become objects + form-state discovery
### (dsh's centerpiece; the only phase touching the provider layer)

**P3-a `manualModels` entries widen from `str` to `str | ModelEntry`**
(additive, both shapes valid forever):

```jsonc
"manualModels": [
  "deepseek/deepseek-v4-pro",                      // the old shape stays legal
  { "id": "deepseek/deepseek-v3.2",
    "label": "DeepSeek V3.2",
    "contextWindow": 131072, "maxTokens": 16384,   // capacity overrides
    "reasoningEfforts": { "off": null, "high": "high" } }  // dsh semantics:
]                                                  //   declared level → wire spelling
```

- Data flow (all additive, no branch-semantics change): a new
  `ManualModelEntry` model + union validation in `core/config.py` →
  `profiles._clean_models` normalizes to objects preserving id order →
  `catalog_service._fallback_models` builds `CatalogModel` from the entry's
  label/capacities/efforts (overriding seed; absent fields fall through the
  existing seed/family cascade) → the existing consumers (`model/list`,
  `cached_model`, `model_reasoning`, execution-profile `model_limits` /
  `reasoning_capabilities`) pick the overrides up **automatically** — zero new
  read paths.
- Invariance condition: plain-string entries behave byte-for-byte as before
  (pinned by regression tests); object entries merely place dsh's per-model
  declarations into the existing catalog precedence chain — the turn path
  still goes only through `resolve_phases`.
- UI: a dsh-`ModelListEditor`-style row editor (Model ID / Display name /
  collapsed capacities / effort declarations); capacity inputs accept `K/M`
  suffixes; unparseable text stays on screen so the save-time server rejection
  can name a visible row.
- TUI dividend: the `/model` picker's titles and effort ladders pick up
  labels/declarations automatically (shared `model_reasoning` + catalog; no
  TUI change).

**P3-b Form-state discovery**: a new additive `provider/discover` RPC:
`{connectionId?, template?, apiBase?, apiKey?} → {models[]}` — probes with
**the form as currently shown** (unsaved apiBase, a key typed but not yet
stored) and **writes nothing**; the editor's fetch-models switches to it, and
the custom-provider creation card can therefore list a catalog before first
save (dsh behavior). Server side reuses `catalog_service._fetch` with a
transient `ResolvedConnection`; the credential stays in memory only.

### P4 — Hardening and Language (optional, independently accepted)

- **Concurrent-write protection**: `settings/read` returns a `configRevision`
  (content hash of the file, reusing the `connection_revision` idea);
  `settings/update` / `provider/upsert` accept an optional
  `expectedRevision`; a mismatch returns a conflict error the desktop maps to
  "settings changed elsewhere — refresh". Omitting it keeps today's behavior
  (backward compatible).
- **External-change push**: an app_server `settings/changed` notification
  (config-file watcher, 100 ms debounce); the desktop refreshes only while
  the settings dialog is open (dsh's refreshIfLoaded rule).
- **Language**: `react-i18next` + `zh-CN`/`en` dictionaries; stored in
  localStorage; first batch covers the settings dialog + sidebar + high-traffic
  composer strings, the rest progressively. Pure copy work — last.

### Phase dependencies and acceptance

```
P1 shell ──► P1-b General ──► P2 Models ──► P3 entries + discovery ──► P4
```

Acceptance per phase (this project's standing rules):
- `npm run check:protocol` + desktop lint/tests + `pytest tests/` (current
  baseline 324) all green;
- **Live verification**: `npm run tauri dev`, walking every item (all four
  dialog sections; the five General rows read/write; provider add/edit/test/
  remove; after saving the Default model row, start a new session and confirm
  the first turn resolves to it; after P3, the TUI `/model` ladder shows the
  declared levels);
- Runtime-neutrality proof: each phase re-runs the existing
  `tests/application/test_turn_execution_profiles.py` +
  `test_execution_security_policy.py` unchanged — passing untouched means the
  seam was not moved.

---

## 4. Deliberate divergences from dsh (with reasons)

| dsh | DeepCode decision | Reason |
|---|---|---|
| Permission presets = sandbox modes (read-only / workspace-write / danger-full-access) | Keep ask / read-only / full-access | DeepCode has no sandbox layer; its presets are approval semantics — borrowing the names would lie |
| Picking a model in the composer also saves it as the global default | No silent save; an explicit "Set as default" affordance in the picker | DeepCode is multi-project/multi-session; silently changing a global default is surprising — dsh's single-default-route context differs |
| Credential reference = derived env name (`PROVIDER_API_KEY`) | Primary store stays `credentials.json`; env reference remains the advanced option | The current model isolates credentials more strongly (phase 1 result); env derivation would be a regression |
| Settings file is YAML with comment-preserving CST patches | Keep JSON + field-allowlist merges | The existing format/lock/validation chain is mature; migrating formats is small gain, large risk |
| No phase-model concept | Keep planning/implementation (folded away) | The workflow service consumes it; it is a DeepCode asset |
| Slot-based settings composition (cordis) | A section registry array | A single desktop app; a slot system is over-engineering |

## 5. Risks and rollback

- Each phase lands on its own branch/commits; UI phases revert wholesale with
  no data residue;
- P1-b's `defaultPreset` and P3-a's entry objects are the only config-schema
  additions — both designed as "old files load unchanged, absent fields mean
  old behavior", so rollback is code-only, no config migration;
- `ManagementWorkspace.module.css` is not modified (shared by five pages); the
  new dialog ships its own styles;
- `useConnectionCatalog` is the controller shared between the composer's
  ModelPicker and settings; P2 only adds, never changes signatures.
