# Phosphor empty-state marks

Outline marks for the Plugins and Skills empty states, from the **Phosphor
Icons** core set (`@phosphor-icons/core`, MIT).

License: MIT — Copyright (c) 2023 Phosphor Icons.
Source: https://github.com/phosphor-icons/core

Files:

- `plugs-light.svg` — Plugins
- `puzzle-piece-light.svg` — Skills

The `light` weight is deliberate: these sit beside the Flaticon outline
accents in `../flaticon/`, and the heavier Phosphor weights read as a
different family at the 48px the empty states draw them at.

Both are consumed as CSS `mask-image` with `background: currentColor`, so they
take the surrounding text colour and follow the theme. They are
presentation-only. They do not participate in runtime, Session, Agent, or
protocol behavior.
