# README visuals

| File | Used by | Content |
| --- | --- | --- |
| `web-ui.png` | `README.md`, `README_ZH.md` | The Web client after a real task: tool calls in the conversation and the diff in the Review panel |
| `framework2.png` | `docs/paper2code.md` | The Paper2Code architecture: blueprint generation, iterative coding, verification and repair |

The READMEs load these images through absolute
`raw.githubusercontent.com/HKUDS/DeepCode/main/...` URLs so they also render
on PyPI. A new image therefore appears only after it reaches `main`.

Capture real product states with an isolated `DEEPCODE_HOME`, a throwaway
project and no API keys, personal paths, account identifiers or private
conversation content on screen. Keep each image under 2 MB, use the light
theme, and do not use decorative mockups.

`web-ui.png` was taken at 1440×900 with a device scale factor of 1.5: a
DeepSeek model fixed a failing test in a small demo project, then the access
picker was set to Ask and the Review panel opened.

`framework2.png` is the README-compatible rendering of
`../framework2.pdf`. Keep the PDF as the source-quality original.
