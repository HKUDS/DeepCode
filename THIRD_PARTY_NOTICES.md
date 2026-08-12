# Third-party software notices

DeepCode Desktop includes open-source Python, JavaScript, Rust, and platform
runtime components. Their package names, versions, license expressions, and
source metadata are recorded by the locked manifests:

- `desktop/sidecar-requirements.lock`
- `desktop/package-lock.json`
- `desktop/src-tauri/Cargo.lock`

The release pipeline runs `desktop/scripts/audit-licenses.py` against all three
resolved graphs and retains its machine-readable report as a build artifact.
The current dependency set uses permissive or weak-copyleft licenses including
MIT, Apache-2.0, BSD, ISC, MPL-2.0, Unicode, Python, Zlib, and CC0 variants.

PyInstaller is a build-time tool distributed under GPLv2-or-later with its
documented exception permitting distribution of non-free bundled programs.
Docling is optional and is not included in the Desktop sidecar; the packaged
baseline document converters use the Python standard library plus pypdf.

Operating-system WebViews and other system libraries retain the notices and
license terms supplied by their platform vendors. This file is informational;
the license text and source URL published by each dependency remain
authoritative.

## Flaticon interface accents

DeepCode Desktop includes adapted outline interface accents from the **Zeir
minimal user interface** pack by **The Icon Tree** on Flaticon. Attribution:
Icons designed by The Icon Tree from Flaticon.

- Pack: https://www.flaticon.com/packs/zeir-minimal-user-interface-14615833
- Poll: https://www.flaticon.com/free-icon/poll_15780511
- Terminal: https://www.flaticon.com/free-icon/terminal_15780766
- Settings: https://www.flaticon.com/free-icon/settings_15780826
- Time: https://www.flaticon.com/free-icon/time_15780852

These image assets are presentation-only and do not participate in Agent,
Session, protocol, or execution behavior.

## Bundled Agent Skills

DeepCode bundles upstream Agent Skills from the following repositories. The
exact source path and pinned revision for each bundled package are recorded in
`core/skills/builtin/UPSTREAM_SOURCES.json`; upstream license files are kept
inside their respective Skill directories when supplied by the source.

- OpenAI Skills: https://github.com/openai/skills
- OpenAI Codex: https://github.com/openai/codex
- Anthropic Skills: https://github.com/anthropics/skills

The Skill instructions remain attributable to their upstream authors. DeepCode
provides the host runtime, discovery, selection, resource access, and tool/MCP
integration used to execute them.
