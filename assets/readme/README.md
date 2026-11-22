# README visuals

| File | Used by | Content |
| --- | --- | --- |
| `overview.png` | `README.md`, `README_ZH.md` | The DeepCode framework, with what DeepCode adds highlighted: verified completion, staying on track, persistent Goals, and self-evolution (planned) |
| `goal-loop.png` | `README.md`, `README_ZH.md` | How a Goal reaches complete: the checklist, Turns of work, the stops and the completion check |
| `web-ui.png`, `web-ui.zh.png` | `README.md`, `README_ZH.md` | The Web client after a real Goal, with the Goal card open on its completion check |
| `settings-models.png`, `settings-models.zh.png` | `README.md`, `README_ZH.md` | Settings → Models: model connections and the agent model |
| `stargazer-orgs.png` | `README.md`, `README_ZH.md` | Companies, universities and research institutes that people who starred DeepCode list on their GitHub profiles |
| `framework2.png` | `docs/paper2code.md` | The Paper2Code architecture: blueprint generation, iterative coding, verification and repair |

The READMEs load these images through absolute
`raw.githubusercontent.com/HKUDS/DeepCode/main/...` URLs so they also render
on PyPI. A new image therefore appears only after it reaches `main`.

## Diagrams

`overview` and `goal-loop` are abstract framework figures in the style of
`framework2.png`, drawn as SVG by `src/overview.py` and `src/goal.py` with the
shared kit in `src/kit.py`. Both READMEs use the same English images.

```bash
python3 assets/readme/src/overview.py overview.html
python3 assets/readme/src/goal.py goal-loop.html
```

Screenshot each page with a device scale factor of 2: `overview` at 1400×608
and `goal-loop` at 1400×442, for example with Playwright's `page.screenshot`.
Keep them abstract: block names, arrows and the key formulas, not
configuration details. In `overview`, highlight only what DeepCode adds and
mark anything not shipped yet as planned. Change them when the structure they show changes.

## Stargazer organizations

`stargazer-orgs.png` shows organizations taken from the public "company" field
of the GitHub profiles of people who starred DeepCode. Affiliations are
self-reported. Names were merged and checked by hand. Every organization shown
appears in that data, and the "2,000+ more" stays below the reviewed count. The
counts and the script that draws the card are kept outside the repository.
Replace the image when the numbers are refreshed.

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
