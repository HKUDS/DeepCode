# README visuals

| File | Used by | Content |
| --- | --- | --- |
| `overview.png`, `overview.zh.png` | `README.md`, `README_ZH.md` | How DeepCode fits together: clients, the local service, the agent harness, your project and the model |
| `goal-loop.png`, `goal-loop.zh.png` | `README.md`, `README_ZH.md` | How a Goal reaches complete: Turns of work, the completion check and the stops |
| `web-ui.png`, `web-ui.zh.png` | `README.md`, `README_ZH.md` | The Web client after a real Goal, with the Goal card open on its completion check |
| `settings-models.png`, `settings-models.zh.png` | `README.md`, `README_ZH.md` | Settings → Models: model connections and the agent model |
| `framework2.png` | `docs/paper2code.md` | The Paper2Code architecture: blueprint generation, iterative coding, verification and repair |

The READMEs load these images through absolute
`raw.githubusercontent.com/HKUDS/DeepCode/main/...` URLs so they also render
on PyPI. A new image therefore appears only after it reaches `main`.

## Diagrams

`overview` and `goal-loop` are drawn as HTML in `src/` with the app's colour
tokens; `?lang=en` or `?lang=zh` picks the text. Render them at 1600 px wide
with a device scale factor of 1.5 (overview 800 px high, goal-loop 790 px),
for example with Playwright's `page.screenshot`. Keep what they say true to the
product: change the text when the behaviour it describes changes.

## Screenshots

Capture real product states with an isolated `DEEPCODE_HOME`, a throwaway
project and no API keys, personal paths, account identifiers, environment
variables of your own (start the service without them) or private conversation
content on screen. Keep each image under 2 MB, use the light theme, and do not
use decorative mockups.

`web-ui` and `settings-models` were taken on 2026-11-02 at 1440×900 with a
device scale factor of 1.5, in English and in Chinese (the interface language
is stored under `deepcode.desktop.locale.v1` in local storage). A real model
(Volcengine Ark `glm-5.3-flash`) worked a Goal on a small inventory project
with two planted bugs: it proposed a checklist, the checklist was confirmed,
and with `goal.completionVerifier` set to `reviewer` the Goal completed after
DeepCode ran the checks and the independent review proved each requirement.
The default access was then set to Ask and the Goal card opened on its
completion check.

`framework2.png` is the README-compatible rendering of
`../framework2.pdf`. Keep the PDF as the source-quality original.
