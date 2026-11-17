"""Figure: How a Goal reaches complete (abstract, academic style)."""

from kit import (
    CYAN,
    GREEN,
    NAVY,
    RUST,
    M,
    T,
    agent,
    badge,
    caption,
    chevron,
    dpanel,
    esc,
    flow,
    icon,
    inner,
    line_arrow,
    page,
    pill,
    panel,
    rect,
    tag_arrow,
    tile,
    trace,
    vsep,
)

W, H = 1400, 442


def build():
    o = []
    o.append(vsep(330, 6, 344))
    o.append(vsep(809, 6, 344))
    o.append(caption(166, 420, "Phase 1: Goal & Checklist"))
    o.append(caption(556, 420, "Phase 2: Turns of Work"))
    o.append(caption(1119, 420, "Phase 3: Completion Check"))

    # ---- Phase 1
    o.append(agent(18, 10, 296, 76))
    o.append(tile("target", 30, 21, 54, inset=0.13))
    o.append(
        T(98, 56, esc("Goal ") + M("G", 22), size=22, w="bold", a="start", raw=True)
    )
    o.append(flow([(166, 86), (166, 124)], w=10, head_len=13, head_w=22))
    o.append(T(182, 110, "agent proposes", size=13.5, it=True, a="start"))
    o.append(panel(18, 124, 296, 166))
    o.append(
        T(
            166,
            154,
            esc("Checklist ") + M("𝒞 = {c_1, …, c_K}", 19),
            size=18,
            w="bold",
            raw=True,
        )
    )
    for i, (ic, name) in enumerate(
        [("terminal", "a command to run"), ("search", "a point to review")]
    ):
        y = 172 + i * 54
        o.append(inner(32, y, 268, 46))
        o.append(tile(ic, 42, y + 7, 32, inset=0.12))
        o.append(T(84, y + 29, name, size=15, a="start"))
    o.append(icon("person", 30, 300, 40))
    o.append(T(80, 327, "you confirm", size=15.5, a="start"))
    o.append(chevron(316, 176, w=28, h=92))

    # ---- Phase 2
    o.append(dpanel(346, 10, 420, 334))
    o.append(trace([(366, 28), (410, 28), (420, 38), (470, 38)]))
    o.append(trace([(746, 28), (702, 28), (692, 38), (642, 38)]))
    o.append(T(556, 40, "Turns of Work", size=20, w="bold"))
    o.append(
        T(556, 63, esc("turn ") + M("t = 1, 2, …", 16), size=15, it=True, raw=True)
    )
    for i, (ic, name) in enumerate(
        [("search", "Read"), ("edit", "Edit"), ("run", "Run")]
    ):
        x = 362 + i * 138
        o.append(agent(x, 78, 112, 88, sw=2))
        o.append(tile(ic, x + 32, 86, 48))
        o.append(T(x + 56, 155, name, size=16, w="bold"))
        if i < 2:
            o.append(
                flow([(x + 112, 122), (x + 138, 122)], w=8, head_len=11, head_w=18)
            )
    o.append(
        line_arrow([(694, 166), (694, 188), (418, 188), (418, 170)], color=NAVY, sw=2)
    )
    o.append(T(556, 210, M("t ← t + 1", 17), size=17, raw=True))
    o.append(panel(362, 226, 388, 104))
    o.append(T(556, 250, "Stops on its own", size=16, w="bold"))
    for i, (ic, name) in enumerate(
        [
            ("hourglass", "Budget"),
            ("repeat", "Nothing new"),
            ("blocked", "Stuck"),
            ("restart", "Restart"),
        ]
    ):
        cx = 362 + 48.5 + i * 97
        o.append(tile(ic, cx - 17, 262, 34, inset=0.12))
        o.append(T(cx, 316, name, size=13, w="bold"))

    # claim
    o.append(tag_arrow(768, 122, 84, 30, "“Complete”", size=13.5))

    # ---- Phase 3
    o.append(
        rect(
            852,
            10,
            344,
            334,
            fill="none",
            stroke=CYAN,
            sw=8,
            rx=16,
            extra=' opacity="0.35" filter="url(#glow)"',
        )
    )
    o.append(
        rect(
            852, 10, 344, 334, fill="url(#gPanel)", stroke="url(#gNeon)", sw=2.8, rx=16
        )
    )
    o.append(T(1024, 40, "Completion Check", size=20, w="bold"))
    stages = [
        ("Rule check", "clipcheck", "optional"),
        ("Your checks", "run", "always"),
        ("Independent review", "search", "optional"),
    ]
    for i, (name, ic, when) in enumerate(stages):
        y = 60 + i * 94
        o.append(inner(866, y, 316, 70, fill="#FFFFFF"))
        o.append(badge(892, y + 35, i + 1))
        o.append(T(916, y + 31, name, size=17, w="bold", a="start"))
        o.append(
            pill(
                948,
                y + 51,
                64,
                20,
                when,
                size=12,
                fill="#EEF4FB",
                stroke="#9DB6D6",
                color=NAVY,
            )
        )
        o.append(tile(ic, 1128, y + 14, 42, inset=0.13))
        if i < 2:
            o.append(
                flow([(1024, y + 70), (1024, y + 94)], w=8, head_len=11, head_w=18)
            )
    o.append(flow([(1196, 177), (1220, 177)], w=10, head_len=12, head_w=22))
    o.append(
        rect(
            1220,
            102,
            166,
            150,
            fill="url(#gGreen)",
            stroke=GREEN,
            sw=2.2,
            rx=16,
            shadow="sh",
        )
    )
    o.append(icon("check", 1280, 122, 46))
    o.append(T(1303, 198, "Complete", size=20, w="bold", fill="#2E7D4F"))
    o.append(T(1303, 224, "evidence kept", size=13.5, it=True))

    # turned back
    o.append(
        line_arrow(
            [(1024, 344), (1024, 372), (556, 372), (556, 348)],
            color=RUST,
            sw=2.2,
            dash="7 5",
        )
    )
    o.append(rect(644, 359, 292, 26, fill="#FFFFFF", stroke="#E6BFA5", sw=1.3, rx=13))
    o.append(
        T(
            790,
            377,
            "turned back with what is missing",
            size=14.5,
            w="bold",
            it=True,
            fill=RUST,
        )
    )
    return page(W, H, "".join(o))


if __name__ == "__main__":
    import sys

    open(sys.argv[1], "w").write(build())
