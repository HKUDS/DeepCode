# README visuals

The product README defines three image positions. The overview and verification
loop images are now in place; the Paper2Code position remains a comment until a
release-quality image is available.

| File                      | Status | Content                                                                     | Recommended framing                             |
| ------------------------- | ------ | --------------------------------------------------------------------------- | ----------------------------------------------- |
| `deepcode-overview.png`   | Added  | One real task from cross-file repository context to verified evidence       | Complete but restrained task view, 1080–1440 px |
| `verification-loop.png`   | Added  | Harness action, verification feedback, repair, and the final passing result | Tight execution sequence with readable evidence |
| `paper2code-workflow.png` | Needed | Plan or checkpoint state, verification, and Artifact context                | Workflow view, 1080–1440 px wide                |

Capture real product states, but remove API keys, personal paths, repository
secrets, account identifiers, and private conversation content. Use one
consistent light or dark appearance across the set, avoid decorative mockups,
and optimize each image before committing it. The capture can come from any
DeepCode interface: frame the task, reasoning boundary, and evidence rather
than presenting Desktop chrome or terminal styling as the feature.

When the Paper2Code image is ready, replace its `README VISUAL SLOT` comment in
both `README.md` and `README_ZH.md` with the prepared `<img>` block.
