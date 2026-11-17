"""Figure: the DeepCode framework, with what DeepCode adds highlighted."""

from kit import (
    CYAN,
    GREEN,
    INK,
    MUTED,
    RUST,
    VIOLET,
    M,
    T,
    agent,
    badge,
    caption,
    chevron,
    dpanel,
    esc,
    flow,
    hl,
    icon,
    inner,
    line_arrow,
    page,
    panel,
    pill,
    rect,
    tag_arrow,
    tile,
    trace,
    vsep,
)

W, H = 1400, 608


def small_flow(x, y1, y2):
    return flow([(x, y1), (x, y2)], w=8, head_len=10, head_w=16)


def build():
    o = []
    o.append(vsep(166, 6, 548))
    o.append(caption(82, 582, "(a) Clients"))
    o.append(caption(785, 582, "(b) DeepCode Service"))
    # legend
    o.append(hl(1120, 569, 22, 14, rx=4))
    o.append(T(1150, 581, "what DeepCode adds", size=13, a="start"))
    o.append(rect(1286, 569, 22, 14, fill="#FFFFFF", stroke=MUTED, sw=1.6, rx=4))
    o.append(T(1314, 581, "standard", size=13, a="start"))

    # ---- (a) clients
    o.append(rect(14, 10, 136, 534, stroke=INK, sw=2.2, rx=14, dash="9 7"))
    clients = [
        ("terminal", "Terminal UI"),
        ("desktop", "Desktop"),
        ("web", "Web"),
        ("script", "Scripts & CI"),
    ]
    for i, (ic, name) in enumerate(clients):
        top = 30 + i * 132
        o.append(tile(ic, 56, top, 52))
        o.append(T(82, top + 76, name, size=15, w="bold"))
    o.append(chevron(152, 277, w=26, h=80))

    # ---- (3) persistent goals
    o.append(hl(182, 10, 1206, 86))
    o.append(badge(208, 53, 3))
    o.append(T(232, 48, "Persistent Goals", size=19, w="bold", a="start"))
    o.append(
        T(232, 73, "kept across restarts and clients", size=13.5, it=True, a="start")
    )
    o.append(icon("database", 548, 31, 44))
    o.append(tag_arrow(602, 53, 90, 28, "keeps", size=14))
    names = ["Sessions", "Goals", "Checklists", "Approvals", "Automations"]
    for i, n in enumerate(names):
        x = 704 + i * 134
        o.append(inner(x, 33, 124, 40))
        o.append(T(x + 62, 59, n, size=15))

    # ---- agent harness
    o.append(dpanel(182, 112, 1206, 358))
    o.append(trace([(212, 128), (330, 128), (342, 140), (690, 140)]))
    o.append(trace([(1358, 128), (1240, 128), (1228, 140), (880, 140)]))
    o.append(T(785, 139, "Agent Harness", size=20, w="bold"))

    # (2) stays on track
    o.append(hl(198, 152, 270, 306))
    o.append(badge(222, 176, 2))
    o.append(T(244, 182, "Stays on track", size=17, w="bold", a="start"))
    o.append(panel(212, 194, 242, 158))
    o.append(tile("docs", 224, 204, 34))
    o.append(T(268, 227, "Context", size=17, w="bold", a="start"))
    o.append(inner(224, 246, 218, 28))
    o.append(T(333, 265, esc("History ") + M("H_t", 15), size=14, raw=True))
    o.append(rect(224, 280, 218, 28, fill="#E0F8FC", stroke=CYAN, sw=1.8, rx=10))
    o.append(T(333, 299, "Your words, verbatim", size=14, w="bold"))
    o.append(inner(224, 314, 218, 28))
    o.append(T(333, 333, "Memory · Skills", size=14))
    o.append(panel(212, 362, 242, 88))
    o.append(T(333, 382, "Stops on its own", size=14, w="bold"))
    stops = [
        ("hourglass", "Budget"),
        ("repeat", "Repeat"),
        ("blocked", "Stuck"),
        ("restart", "Restart"),
    ]
    for i, (ic, name) in enumerate(stops):
        cx = 242.25 + i * 60.5
        o.append(tile(ic, cx - 13, 390, 26, inset=0.12))
        o.append(T(cx, 434, name, size=11.5, w="bold"))

    # standard loop
    o.append(agent(520, 178, 220, 66, stroke=MUTED, sw=1.8))
    o.append(tile("chip", 530, 185, 30, inset=0.12))
    o.append(T(568, 206, "LLM", size=17, w="bold", a="start"))
    o.append(T(612, 206, M("r_t = LLM(H_t)", 15), size=15, a="start", raw=True))
    o.append(T(630, 233, "any API or local model", size=12.5, it=True))
    o.append(small_flow(630, 244, 262))
    o.append(T(644, 258, M("a_t", 14), size=14, a="start", raw=True))
    o.append(agent(520, 262, 220, 40, stroke=MUTED, sw=1.8))
    o.append(tile("shield", 530, 268, 28))
    o.append(T(566, 288, "Guardrails", size=16, w="bold", a="start"))
    o.append(small_flow(630, 302, 320))
    o.append(T(644, 316, "allowed", size=12, it=True, a="start"))
    o.append(agent(520, 320, 220, 40, stroke=MUTED, sw=1.8))
    o.append(tile("wrench", 530, 326, 28))
    o.append(T(566, 346, "Tools", size=16, w="bold", a="start"))
    o.append(T(618, 346, "in your project", size=12.5, it=True, a="start"))
    o.append(agent(520, 382, 220, 40, stroke=MUTED, sw=1.8))
    o.append(tile("branch", 530, 388, 28))
    o.append(T(566, 408, "Sub-agents", size=16, w="bold", a="start"))
    o.append(T(662, 408, "worktrees", size=12.5, it=True, a="start"))
    o.append(flow([(454, 211), (520, 211)], w=9, head_len=11, head_w=18, dot=True))
    o.append(T(487, 202, M("H_t", 15), size=15, raw=True))
    o.append(flow([(520, 340), (454, 340)], w=9, head_len=11, head_w=18, dot=True))
    o.append(T(487, 331, M("o_t", 15), size=15, raw=True))

    # (1) verified completion
    o.append(hl(840, 152, 532, 306))
    o.append(badge(864, 176, 1))
    o.append(T(886, 182, "Verified completion", size=18, w="bold", a="start"))
    o.append(T(1356, 182, "a claim alone is not enough", size=13, it=True, a="end"))
    stages = [
        ("clipcheck", "Checklist you confirmed", "agreed before work starts"),
        ("run", "DeepCode runs the checks", "not the model's word"),
        ("search", "Independent reviewer", "proves each item with evidence"),
    ]
    for i, (ic, name, sub) in enumerate(stages):
        y = 200 + i * 82
        o.append(inner(856, y, 314, 60, fill="#FFFFFF"))
        o.append(tile(ic, 868, y + 11, 38, inset=0.13))
        o.append(T(918, y + 26, name, size=15.5, w="bold", a="start"))
        o.append(T(918, y + 46, sub, size=12.5, it=True, a="start"))
        if i == 2:
            o.append(
                pill(
                    1130,
                    y + 21,
                    60,
                    20,
                    "optional",
                    size=11.5,
                    fill="#EEF4FB",
                    stroke="#9DB6D6",
                    color="#1F3B5C",
                )
            )
        if i < 2:
            o.append(small_flow(1013, y + 60, y + 82))
    o.append(flow([(1170, 299), (1194, 299)], w=9, head_len=11, head_w=18))
    o.append(
        rect(
            1194,
            232,
            162,
            134,
            fill="url(#gGreen)",
            stroke=GREEN,
            sw=2.2,
            rx=14,
            shadow="sh",
        )
    )
    o.append(icon("check", 1253, 246, 44))
    o.append(T(1275, 316, "Complete", size=18, w="bold", fill="#2E7D4F"))
    o.append(T(1275, 340, "evidence kept", size=12.5, it=True))
    # claim and turned back
    o.append(tag_arrow(740, 230, 100, 28, "“Complete”", size=13))
    o.append(line_arrow([(840, 336), (744, 336)], color=RUST, sw=2.2, dash="6 4"))
    o.append(T(791, 356, "turned back:", size=12, it=True, w="bold", fill=RUST))
    o.append(T(791, 371, "what is missing", size=12, it=True, w="bold", fill=RUST))

    # ---- planned: self-evolution
    o.append(
        rect(182, 488, 1206, 52, fill="#F7F3FF", stroke=VIOLET, sw=2, rx=16, dash="9 6")
    )
    o.append(tile("sparkle", 198, 494, 40, inset=0.12))
    o.append(T(252, 520, "Self-evolution", size=18, w="bold", a="start"))
    o.append(
        pill(
            420,
            514,
            74,
            22,
            "planned",
            size=12,
            fill="#EDE7FE",
            stroke=VIOLET,
            color="#5B3DB8",
        )
    )
    o.append(
        T(
            474,
            519,
            "learns from verified runs: what worked becomes memory and skills",
            size=13.5,
            it=True,
            a="start",
        )
    )
    o.append(line_arrow([(1013, 458), (1013, 486)], color=VIOLET, sw=2, dash="5 4"))
    o.append(T(1025, 480, "evidence", size=12, it=True, fill="#5B3DB8", a="start"))
    o.append(line_arrow([(333, 488), (333, 460)], color=VIOLET, sw=2, dash="5 4"))
    o.append(T(345, 480, "experience", size=12, it=True, fill="#5B3DB8", a="start"))
    return page(W, H, "".join(o))


if __name__ == "__main__":
    import sys

    open(sys.argv[1], "w").write(build())
