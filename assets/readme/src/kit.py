"""Drawing kit for academic-style framework figures (SVG), after the Paper2Code figure."""

import html
import math

FONT = "Arial, Helvetica, 'PingFang SC', 'Hiragino Sans GB', sans-serif"
MATHF = "STIXGeneral, 'Times New Roman', serif"
MONO = "Menlo, 'SF Mono', monospace"

INK = "#141414"
NAVY = "#1F3B5C"
BLUE = "#3F72AF"
PFILL = "#DCE9F7"  # panel fill
IFILL = "#CCE1F4"  # inner box fill
FLOW = "#86C5EC"  # thick flow arrows
TAG = "#A8D4F2"  # labels riding on arrows
GREY = "#D6D6D6"  # phase chevrons
GREEN = "#3E9B5F"
GFILL = "#E4F3E9"
ORANGE = "#F29B38"
YELLOW = "#F7CB4D"
RED = "#E5533D"
RUST = "#B85A24"
CODE = "#2F5D9A"


def esc(s):
    return html.escape(s, quote=False)


def T(
    x,
    y,
    s,
    size=16,
    w="normal",
    a="middle",
    fill=INK,
    it=False,
    fam=FONT,
    raw=False,
    extra="",
):
    body = s if raw else esc(s)
    style = "italic" if it else "normal"
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{fam}" font-size="{size}" '
        f'font-weight="{w}" font-style="{style}" text-anchor="{a}" fill="{fill}"{extra}>{body}</text>'
    )


def TL(x, y, lines, lh, **kw):
    return "".join(T(x, y + i * lh, line, **kw) for i, line in enumerate(lines))


def M(expr, size):
    """Tiny math typesetter: single letters italic, letter runs upright, _x / _{..} subscripts."""
    out = []
    pending = 0.0
    i = 0

    def span(text, italic, sz, dy):
        st = "italic" if italic else "normal"
        d = f' dy="{dy:.2f}"' if dy else ""
        return f'<tspan font-family="{MATHF}" font-style="{st}" font-weight="normal" font-size="{sz:.1f}"{d}>{esc(text)}</tspan>'

    while i < len(expr):
        c = expr[i]
        if c == "_":
            if expr[i + 1] == "{":
                j = expr.index("}", i)
                grp, i = expr[i + 2 : j], j + 1
            else:
                grp, i = expr[i + 1], i + 2
            sub, d = size * 0.7, size * 0.26
            first = True
            k = 0
            while k < len(grp):
                ch = grp[k]
                if ch.isascii() and ch.isalpha():
                    run = ch
                    while (
                        k + 1 < len(grp)
                        and grp[k + 1].isascii()
                        and grp[k + 1].isalpha()
                    ):
                        k += 1
                        run += grp[k]
                    out.append(span(run, len(run) == 1, sub, d if first else 0))
                else:
                    out.append(span(ch, False, sub, d if first else 0))
                first = False
                k += 1
            pending += d
            continue
        if c.isascii() and c.isalpha():
            run = c
            while i + 1 < len(expr) and expr[i + 1].isascii() and expr[i + 1].isalpha():
                i += 1
                run += expr[i]
            italic = len(run) == 1
        else:
            run, italic = c, c in "𝒞𝒢"
        out.append(span(run, italic, size, -pending if pending else 0))
        pending = 0.0
        i += 1
    if pending:
        out.append(span("​", False, size, -pending))
    return "".join(out)


CYAN = "#1FC8E3"
VIOLET = "#8B5CF6"
MAGENTA = "#E24FCF"
SLATE = "#2E3A4B"

_EXTRA = []
_UID = [0]


def _id(prefix):
    _UID[0] += 1
    return f"{prefix}{_UID[0]}"


def _pts(points):
    return " ".join(f"{a:.1f},{b:.1f}" for a, b in points)


def _rpath(points, r):
    """Polyline with rounded corners."""
    d = f"M{points[0][0]:.1f} {points[0][1]:.1f}"
    for i in range(1, len(points) - 1):
        (x0, y0), (x1, y1), (x2, y2) = points[i - 1], points[i], points[i + 1]
        l1, l2 = math.hypot(x1 - x0, y1 - y0), math.hypot(x2 - x1, y2 - y1)
        c = min(r, l1 / 2, l2 / 2)
        ax, ay = x1 - (x1 - x0) / l1 * c, y1 - (y1 - y0) / l1 * c
        bx, by = x1 + (x2 - x1) / l2 * c, y1 + (y2 - y1) / l2 * c
        d += f" L{ax:.1f} {ay:.1f} Q{x1:.1f} {y1:.1f} {bx:.1f} {by:.1f}"
    d += f" L{points[-1][0]:.1f} {points[-1][1]:.1f}"
    return d


def rect(
    x, y, w, h, fill="none", stroke=NAVY, sw=1.6, rx=8, dash=None, extra="", shadow=None
):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    f = f' filter="url(#{shadow})"' if shadow else ""
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}{f}{extra}/>'
    )


def hud(x, y, w, h, off=7, L=16, color=CYAN, sw=2.4):
    a, b, c, e = x - off, y - off, x + w + off, y + h + off
    d = (
        f"M{a} {b + L} V{b} H{a + L} M{c - L} {b} H{c} V{b + L} "
        f"M{a} {e - L} V{e} H{a + L} M{c - L} {e} H{c} V{e - L}"
    )
    return f'<path d="{d}" stroke="{color}" stroke-width="{sw}" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'


def panel(x, y, w, h, hud_on=False):
    return rect(
        x, y, w, h, fill="url(#gPanel)", stroke=BLUE, sw=2, rx=16, shadow="sh"
    ) + (hud(x, y, w, h) if hud_on else "")


def dpanel(x, y, w, h, hud_on=False):
    return rect(
        x, y, w, h, fill="#FFFFFF", stroke=BLUE, sw=2, rx=18, dash="10 6", shadow="sh"
    ) + (hud(x, y, w, h) if hud_on else "")


def inner(x, y, w, h, fill=None):
    return rect(
        x,
        y,
        w,
        h,
        fill=fill or "url(#gInner)",
        stroke=NAVY,
        sw=1.4,
        rx=10,
        shadow="sh2",
    )


def agent(x, y, w, h, fill="#FFFFFF", stroke=INK, sw=2.2, neon=False):
    if not neon:
        return rect(x, y, w, h, fill=fill, stroke=stroke, sw=sw, rx=12, shadow="sh")
    glow = rect(
        x,
        y,
        w,
        h,
        fill="none",
        stroke=CYAN,
        sw=8,
        rx=12,
        extra=' opacity="0.40" filter="url(#glow)"',
    )
    return glow + rect(x, y, w, h, fill=fill, stroke="url(#gNeon)", sw=2.8, rx=12)


MUTED = "#9AA6B2"  # standard agent parts


def hl(x, y, w, h, rx=16):
    """Highlighted panel: what DeepCode adds (neon stroke with a soft glow)."""
    glow = rect(
        x,
        y,
        w,
        h,
        fill="none",
        stroke=CYAN,
        sw=8,
        rx=rx,
        extra=' opacity="0.32" filter="url(#glow)"',
    )
    return glow + rect(
        x, y, w, h, fill="url(#gPanel)", stroke="url(#gNeon)", sw=2.8, rx=rx
    )


def vsep(x, y1, y2):
    def dia(cy):
        return (
            f'<rect x="{x - 4}" y="{cy - 4}" width="8" height="8" fill="{CYAN}" '
            f'transform="rotate(45 {x} {cy})"/>'
        )

    return (
        f'<line x1="{x}" y1="{y1 + 8}" x2="{x}" y2="{y2 - 8}" stroke="{SLATE}" stroke-width="2" stroke-dasharray="8 7"/>'
        + dia(y1 + 2)
        + dia(y2 - 2)
    )


def chevron(x, cy, w=40, h=96):
    t = w * 0.5
    pts = [
        (x, cy - h / 2),
        (x + w - t, cy - h / 2),
        (x + w, cy),
        (x + w - t, cy + h / 2),
        (x, cy + h / 2),
        (x + t, cy),
    ]
    return f'<polygon points="{_pts(pts)}" fill="url(#gChev)" stroke="#D3D9E1" stroke-width="5" stroke-linejoin="round"/>'


def flow(
    points, w=12, head_len=17, head_w=28, c1="#AEE8F8", c2="#68AEE8", r=14, dot=False
):
    """Thick gradient arrow along a polyline; the head sits at the last point."""
    (x1, y1), (x2, y2) = points[-2], points[-1]
    L = math.hypot(x2 - x1, y2 - y1)
    ux, uy = (x2 - x1) / L, (y2 - y1) / L
    bx, by = x2 - ux * head_len, y2 - uy * head_len
    shaft = points[:-1] + [(bx + ux * 2, by + uy * 2)]
    px, py = -uy, ux
    head = [
        (x2, y2),
        (bx + px * head_w / 2, by + py * head_w / 2),
        (bx - px * head_w / 2, by - py * head_w / 2),
    ]
    gid = _id("gf")
    (sx, sy) = points[0]
    _EXTRA.append(
        f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{sx:.1f}" y1="{sy:.1f}" x2="{x2:.1f}" y2="{y2:.1f}">'
        f'<stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient>'
    )
    out = (
        f'<path d="{_rpath(shaft, r)}" stroke="url(#{gid})" stroke-width="{w}" fill="none" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<polygon points="{_pts(head)}" fill="{c2}" stroke="{c2}" stroke-width="3" stroke-linejoin="round"/>'
    )
    if dot:
        out += f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="{w * 0.62:.1f}" fill="#FFFFFF" stroke="{CYAN}" stroke-width="2.6"/>'
    return out


MARKERS = {
    INK: "mk-ink",
    RUST: "mk-rust",
    NAVY: "mk-navy",
    BLUE: "mk-blue",
    VIOLET: "mk-violet",
}


def line_arrow(points, color=INK, sw=2.2, dash=None, both=False, r=12):
    da = f' stroke-dasharray="{dash}"' if dash else ""
    mid = MARKERS[color]
    ms = f' marker-start="url(#{mid})"' if both else ""
    return (
        f'<path d="{_rpath(points, r)}" stroke="{color}" stroke-width="{sw}" fill="none"{da} '
        f'stroke-linejoin="round" stroke-linecap="round" marker-end="url(#{mid})"{ms}/>'
    )


def tag_arrow(x, cy, w, h, label, size=14, raw=False, it=False):
    pts = [
        (x, cy - h / 2),
        (x + w - 13, cy - h / 2),
        (x + w, cy),
        (x + w - 13, cy + h / 2),
        (x, cy + h / 2),
    ]
    poly = f'<polygon points="{_pts(pts)}" fill="url(#gTag)" stroke="#9FCDF1" stroke-width="4" stroke-linejoin="round" filter="url(#sh2)"/>'
    return poly + T(
        x + (w - 8) / 2,
        cy + size * 0.36,
        label,
        size=size,
        raw=raw,
        fill="#0F2742",
        it=it,
    )


def pill(
    cx,
    cy,
    w,
    h,
    label,
    size=14,
    fill="#FFFFFF",
    stroke=NAVY,
    it=False,
    raw=False,
    color=INK,
):
    return rect(
        cx - w / 2, cy - h / 2, w, h, fill=fill, stroke=stroke, sw=1.3, rx=h / 2
    ) + T(cx, cy + size * 0.36, label, size=size, it=it, raw=raw, fill=color)


def badge(cx, cy, n, r=13):
    return (
        f'<circle cx="{cx}" cy="{cy}" r="{r + 3.6}" fill="none" stroke="{CYAN}" stroke-width="1.6" opacity="0.9"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#gBadge)"/>'
        + T(cx, cy + 5.2, str(n), size=15, w="bold", fill="#FFFFFF")
    )


def caption(x, y, text, size=22):
    return T(x, y, text, size=size, w="bold") + rect(
        x - 34, y + 11, 68, 4.5, fill="url(#gNeonH)", stroke="none", sw=0, rx=2.25
    )


def icon(name, x, y, s=48):
    return f'<g transform="translate({x:.1f},{y:.1f}) scale({s / 48:.4f})">{ICONS[name]}</g>'


def tile(name, x, y, s, inset=0.15):
    return rect(
        x,
        y,
        s,
        s,
        fill="url(#gTile)",
        stroke="#C3D5EA",
        sw=1.1,
        rx=round(s * 0.28, 1),
        shadow="sh2",
    ) + icon(name, x + s * inset, y + s * inset, s * (1 - 2 * inset))


def trace(points, color=CYAN):
    d = _rpath(points, 6)
    (sx, sy), (ex, ey) = points[0], points[-1]
    return (
        f'<g opacity="0.75"><path d="{d}" stroke="{color}" stroke-width="1.6" fill="none"/>'
        f'<circle cx="{sx}" cy="{sy}" r="3.2" fill="#FFFFFF" stroke="{color}" stroke-width="1.6"/>'
        f'<circle cx="{ex}" cy="{ey}" r="3.2" fill="{color}"/></g>'
    )


def gear(cx, cy, ro, ri, n=8):
    w = 2 * math.pi / n
    pts = []
    for i in range(n):
        b = i * w
        for r, a in (
            (ri, b - 0.27 * w),
            (ro, b - 0.15 * w),
            (ro, b + 0.15 * w),
            (ri, b + 0.27 * w),
        ):
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return "M" + " L".join(f"{a:.2f} {b:.2f}" for a, b in pts) + "Z"


def _lg(gid, stops, vertical=True, extra=""):
    x2, y2 = ("0", "1") if vertical else ("1", "0")
    s = "".join(f'<stop offset="{o}" stop-color="{c}"/>' for o, c in stops)
    return f'<linearGradient id="{gid}" x1="0" y1="0" x2="{x2}" y2="{y2}"{extra}>{s}</linearGradient>'


DEFS = f"""
<defs>
  {_lg("gDb", [(0, "#2D64A8"), (0.55, "#6FA8E0"), (1, "#2D64A8")], vertical=False)}
  {_lg("gShield", [(0, "#6FA3DC"), (1, "#2D64A8")])}
  <linearGradient id="gCloud" gradientUnits="userSpaceOnUse" x1="0" x2="0" y1="9" y2="38">
    <stop offset="0" stop-color="#D4EAFB"/><stop offset="1" stop-color="#7DB8E8"/>
  </linearGradient>
  <linearGradient id="gGlobe" x1="0" x2="1" y1="0" y2="1">
    <stop offset="0" stop-color="#7DB8E8"/><stop offset="1" stop-color="#2D64A8"/>
  </linearGradient>
  <linearGradient id="gChipCore" x1="0" x2="1" y1="0" y2="1">
    <stop offset="0" stop-color="{CYAN}"/><stop offset="1" stop-color="{VIOLET}"/>
  </linearGradient>
  {_lg("gPanel", [(0, "#EAF3FC"), (1, "#D4E4F6")])}
  {_lg("gInner", [(0, "#DFEDFA"), (1, "#C7DCF2")])}
  {_lg("gTile", [(0, "#FFFFFF"), (1, "#E3EDF8")])}
  {_lg("gTag", [(0, "#CDEBFB"), (1, "#A2CFF2")])}
  {_lg("gBadge", [(0, "#2F5C93"), (1, "#172C46")])}
  {_lg("gChev", [(0, "#E6EAF0"), (1, "#CDD4DD")], vertical=False)}
  {_lg("gGreen", [(0, "#F1FAF4"), (1, "#D9EFE1")])}
  {_lg("gNeon", [(0, CYAN), (1, VIOLET)], vertical=False)}
  {_lg("gNeonH", [(0, CYAN), (0.6, VIOLET), (1, MAGENTA)], vertical=False)}
  <pattern id="pDots" width="18" height="18" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1" fill="#D3DCE8"/></pattern>
  <filter id="sh" x="-15%" y="-15%" width="130%" height="140%"><feDropShadow dx="0" dy="3" stdDeviation="4" flood-color="#1F3B5C" flood-opacity="0.13"/></filter>
  <filter id="sh2" x="-15%" y="-15%" width="130%" height="150%"><feDropShadow dx="0" dy="1.5" stdDeviation="1.8" flood-color="#1F3B5C" flood-opacity="0.12"/></filter>
  <filter id="glow" x="-20%" y="-30%" width="140%" height="160%"><feGaussianBlur stdDeviation="5"/></filter>
  {"".join(f'<marker id="{mid}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5.5" markerHeight="5.5" orient="auto-start-reverse"><path d="M1 1 L9 5 L1 9 z" fill="{col}" stroke="{col}" stroke-width="1.4" stroke-linejoin="round"/></marker>' for col, mid in MARKERS.items())}
  <marker id="mk-icon" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="3.2" markerHeight="3.2" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="{BLUE}"/></marker>
  <marker id="mk-icon-navy" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="3.2" markerHeight="3.2" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="{NAVY}"/></marker>
</defs>
"""


_dots = (
    '<circle cx="{a}" cy="{y}" r="1.7" fill="#FF6159"/><circle cx="{b}" cy="{y}" r="1.7" fill="#FFBD2E"/>'
    '<circle cx="{c}" cy="{y}" r="1.7" fill="#28C941"/>'
)

ICONS = {
    "terminal": f"""
<rect x="3" y="7" width="42" height="34" rx="5" fill="#2B3A4F"/>
<path d="M3 12 a5 5 0 0 1 5 -5 h32 a5 5 0 0 1 5 5 v3 h-42z" fill="#46597A"/>
{_dots.format(a=9, b=14, c=19, y=11.2)}
<path d="M10 22 l6 5 l-6 5" stroke="#8EE08E" stroke-width="2.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M20 33 h11" stroke="#E8EEF5" stroke-width="2.8" stroke-linecap="round"/>""",
    "desktop": f"""
<rect x="20" y="38" width="8" height="5" fill="{NAVY}"/>
<rect x="14" y="42.5" width="20" height="3" rx="1.5" fill="{NAVY}"/>
<rect x="3" y="5" width="42" height="34" rx="4" fill="#FFFFFF" stroke="{NAVY}" stroke-width="2"/>
<path d="M4 9 a3 3 0 0 1 3 -3 h34 a3 3 0 0 1 3 3 v4 h-40z" fill="{IFILL}"/>
<path d="M4 13 h40" stroke="{NAVY}" stroke-width="1.6"/>
{_dots.format(a=9, b=13.5, c=18, y=9.6)}
<rect x="7" y="16.5" width="9" height="19" rx="1.5" fill="{FLOW}"/>
<rect x="19" y="17.5" width="22" height="3.4" rx="1.7" fill="{BLUE}"/>
<rect x="19" y="24" width="17" height="3.4" rx="1.7" fill="#A8C8E8"/>
<rect x="19" y="30.5" width="12" height="3.4" rx="1.7" fill="{ORANGE}"/>""",
    "web": f"""
<rect x="2" y="5" width="38" height="31" rx="4" fill="#FFFFFF" stroke="{NAVY}" stroke-width="2"/>
<path d="M3 9 a3 3 0 0 1 3 -3 h30 a3 3 0 0 1 3 3 v4 h-36z" fill="{IFILL}"/>
<path d="M3 13 h36" stroke="{NAVY}" stroke-width="1.6"/>
<circle cx="7.5" cy="9.5" r="1.6" fill="#FF6159"/><circle cx="11.8" cy="9.5" r="1.6" fill="#FFBD2E"/>
<rect x="16" y="7.8" width="20" height="3.4" rx="1.7" fill="#FFFFFF"/>
<rect x="7" y="17" width="16" height="3" rx="1.5" fill="#A8C8E8"/><rect x="7" y="23" width="11" height="3" rx="1.5" fill="#A8C8E8"/>
<circle cx="33" cy="33" r="11.5" fill="url(#gGlobe)" stroke="{NAVY}" stroke-width="2"/>
<ellipse cx="33" cy="33" rx="4.8" ry="11.5" fill="none" stroke="#FFFFFF" stroke-width="1.5"/>
<path d="M21.5 33 h23 M23.6 27 h18.8 M23.6 39 h18.8" stroke="#FFFFFF" stroke-width="1.4"/>""",
    "script": f"""
<path d="M8 4 h20 l11 11 v29 h-31z" fill="#FFFFFF" stroke="{NAVY}" stroke-width="2" stroke-linejoin="round"/>
<path d="M28 4 v11 h11" fill="{IFILL}" stroke="{NAVY}" stroke-width="2" stroke-linejoin="round"/>
<text x="21" y="31" font-family="{MONO}" font-weight="bold" font-size="13" text-anchor="middle" fill="{BLUE}">{{ }}</text>
<path d="{gear(36, 37, 9.5, 7, 8)}" fill="{ORANGE}" stroke="{NAVY}" stroke-width="1.3" stroke-linejoin="round"/>
<circle cx="36" cy="37" r="3" fill="#FFFFFF" stroke="{NAVY}" stroke-width="1.2"/>""",
    "database": """
<path d="M8 11 v26 a16 6 0 0 0 32 0 v-26" fill="url(#gDb)"/>
<path d="M8 20 a16 6 0 0 0 32 0 M8 29 a16 6 0 0 0 32 0" stroke="#FFFFFF" stroke-width="1.7" fill="none"/>
<ellipse cx="24" cy="11" rx="16" ry="6" fill="#A9D0F2" stroke="#2D64A8" stroke-width="1.3"/>
<circle cx="34" cy="23" r="1.4" fill="#FFFFFF"/><circle cx="34" cy="32" r="1.4" fill="#FFFFFF"/><circle cx="34" cy="40.5" r="1.4" fill="#FFFFFF"/>""",
    "chip": f"""
<path d="M16 3 v7 M24 3 v7 M32 3 v7 M16 38 v7 M24 38 v7 M32 38 v7 M3 16 h7 M3 24 h7 M3 32 h7 M38 16 h7 M38 24 h7 M38 32 h7" stroke="{NAVY}" stroke-width="2.6" stroke-linecap="round"/>
<rect x="9" y="9" width="30" height="30" rx="4.5" fill="{NAVY}"/>
<rect x="14" y="14" width="20" height="20" rx="3.5" fill="url(#gChipCore)"/>
<path d="M24 16 Q25.3 22.7 32 24 Q25.3 25.3 24 32 Q22.7 25.3 16 24 Q22.7 22.7 24 16Z" fill="#FFFFFF"/>""",
    "wrench": f"""
<g transform="rotate(45 24 24)">
<rect x="20.3" y="17" width="7.4" height="28" rx="3.7" fill="#8193AD" stroke="{NAVY}" stroke-width="1.6"/>
<path d="M19.5 3.63 A9.5 9.5 0 1 0 28.5 3.63 L28.5 10 L19.5 10 Z" fill="#A9B8CC" stroke="{NAVY}" stroke-width="1.6" stroke-linejoin="round"/>
</g>""",
    "shield": """
<path d="M24 3 L41 9.5 V22 C41 33 33.5 40.5 24 45 C14.5 40.5 7 33 7 22 V9.5 Z" fill="url(#gShield)" stroke="#1F3B5C" stroke-width="1.6"/>
<path d="M15.5 24 L22 30.5 L33 18" stroke="#FFFFFF" stroke-width="3.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>""",
    "docs": f"""
<rect x="15" y="3" width="26" height="32" rx="2.5" fill="{TAG}" stroke="{NAVY}" stroke-width="1.6"/>
<rect x="11" y="7.5" width="26" height="32" rx="2.5" fill="{PFILL}" stroke="{NAVY}" stroke-width="1.6"/>
<rect x="7" y="12" width="26" height="32" rx="2.5" fill="#FFFFFF" stroke="{NAVY}" stroke-width="1.6"/>
<path d="M11.5 20 h17 M11.5 26 h17 M11.5 32 h12" stroke="{BLUE}" stroke-width="2.2" stroke-linecap="round"/>
<path d="M11.5 38 h8" stroke="{ORANGE}" stroke-width="2.2" stroke-linecap="round"/>""",
    "branch": f"""
<path d="M24 13 L11 35 M24 13 V35 M24 13 L37 35" stroke="{NAVY}" stroke-width="2.2"/>
<circle cx="24" cy="10" r="7" fill="{ORANGE}" stroke="{NAVY}" stroke-width="1.6"/>
<circle cx="11" cy="37" r="5.6" fill="{FLOW}" stroke="{NAVY}" stroke-width="1.6"/>
<circle cx="24" cy="37" r="5.6" fill="{FLOW}" stroke="{NAVY}" stroke-width="1.6"/>
<circle cx="37" cy="37" r="5.6" fill="{FLOW}" stroke="{NAVY}" stroke-width="1.6"/>""",
    "clipcheck": f"""
<rect x="7" y="7" width="29" height="37" rx="3" fill="#FFFFFF" stroke="{NAVY}" stroke-width="2"/>
<rect x="14" y="3" width="15" height="8" rx="2" fill="#A9B8CC" stroke="{NAVY}" stroke-width="1.6"/>
<path d="M12 19 h18 M12 26 h15 M12 33 h9" stroke="{BLUE}" stroke-width="2.2" stroke-linecap="round"/>
<circle cx="35.5" cy="36" r="9.5" fill="{GREEN}" stroke="#FFFFFF" stroke-width="2"/>
<path d="M30.8 36 l3.4 3.4 l6 -6.4" stroke="#FFFFFF" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>""",
    "folder": f"""
<path d="M3 12 a3 3 0 0 1 3 -3 h12 l4.5 4.5 h19.5 a3 3 0 0 1 3 3 v23 a3 3 0 0 1 -3 3 h-36 a3 3 0 0 1 -3 -3z" fill="#E39A2F"/>
<rect x="8" y="14" width="30" height="16" rx="1.5" fill="#FFFFFF" stroke="#D3D9E0" stroke-width="1"/>
<path d="M12 19 h16 M12 24 h11" stroke="#A8C8E8" stroke-width="2" stroke-linecap="round"/>
<path d="M3 21 h42 v18.5 a3 3 0 0 1 -3 3 h-36 a3 3 0 0 1 -3 -3z" fill="{YELLOW}"/>""",
    "git": """
<path d="M16 12 V36 M32 18 c0 9 -16 7 -16 14" stroke="#E2502E" stroke-width="3.2" fill="none"/>
<circle cx="16" cy="10" r="4.5" fill="#FFFFFF" stroke="#E2502E" stroke-width="3.2"/>
<circle cx="16" cy="38" r="4.5" fill="#FFFFFF" stroke="#E2502E" stroke-width="3.2"/>
<circle cx="32" cy="15" r="4.5" fill="#FFFFFF" stroke="#E2502E" stroke-width="3.2"/>""",
    "cloud": """
<circle cx="15" cy="29" r="9" fill="url(#gCloud)"/>
<circle cx="25.5" cy="21" r="11.5" fill="url(#gCloud)"/>
<circle cx="35" cy="28.5" r="8.5" fill="url(#gCloud)"/>
<rect x="15" y="27" width="20" height="10" fill="url(#gCloud)"/>
<path d="M25 21 Q26 26.5 31.5 27.5 Q26 28.5 25 34 Q24 28.5 18.5 27.5 Q24 26.5 25 21Z" fill="#FFFFFF"/>""",
    "laptop": f"""
<rect x="8" y="9" width="32" height="22" rx="2.5" fill="{NAVY}"/>
<rect x="11" y="12" width="26" height="16" rx="1" fill="{FLOW}"/>
<path d="M24 14.5 Q24.9 19.1 29.5 20 Q24.9 20.9 24 25.5 Q23.1 20.9 18.5 20 Q23.1 19.1 24 14.5Z" fill="#FFFFFF"/>
<path d="M3 33 h42 l-3.5 5.5 h-35z" fill="#A9B8CC" stroke="{NAVY}" stroke-width="1.4" stroke-linejoin="round"/>""",
    "target": f"""
<circle cx="21" cy="27" r="18" fill="{RED}"/>
<circle cx="21" cy="27" r="12.5" fill="#FFFFFF"/>
<circle cx="21" cy="27" r="7.2" fill="{RED}"/>
<circle cx="21" cy="27" r="2.6" fill="#FFFFFF"/>
<path d="M21 27 L41 7" stroke="{NAVY}" stroke-width="2.6" stroke-linecap="round"/>
<path d="M37.5 4.5 L45 3 L43.5 10.5 Z" fill="{YELLOW}" stroke="{NAVY}" stroke-width="1.2" stroke-linejoin="round"/>""",
    "person": f"""
<circle cx="22" cy="14" r="8.5" fill="{BLUE}"/>
<path d="M5 44 a17 15 0 0 1 34 0 z" fill="{BLUE}"/>
<circle cx="37" cy="36" r="9" fill="{GREEN}" stroke="#FFFFFF" stroke-width="2"/>
<path d="M32.6 36 l3.2 3.2 l5.6 -6" stroke="#FFFFFF" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>""",
    "search": f"""
<path d="M29 29 L42 42" stroke="{NAVY}" stroke-width="6" stroke-linecap="round"/>
<circle cx="20" cy="20" r="13" fill="{PFILL}" stroke="{NAVY}" stroke-width="3.2"/>
<path d="M13 16 a8 8 0 0 1 6 -5" stroke="#FFFFFF" stroke-width="2.6" fill="none" stroke-linecap="round"/>""",
    "edit": f"""
<rect x="5" y="4" width="28" height="39" rx="2.5" fill="#FFFFFF" stroke="{NAVY}" stroke-width="2"/>
<rect x="9" y="10" width="20" height="6" rx="1" fill="#F6D2CC"/>
<path d="M11 13 h4" stroke="{RED}" stroke-width="1.8" stroke-linecap="round"/>
<rect x="9" y="19" width="20" height="6" rx="1" fill="#D3EEDB"/>
<path d="M11 22 h4 M13 20 v4" stroke="{GREEN}" stroke-width="1.8" stroke-linecap="round"/>
<path d="M9 31 h16 M9 37 h11" stroke="#A8C8E8" stroke-width="2" stroke-linecap="round"/>
<g transform="rotate(40 36 28)">
<rect x="32.5" y="11" width="7" height="23" fill="{YELLOW}" stroke="{NAVY}" stroke-width="1.4"/>
<rect x="32.5" y="7" width="7" height="4.5" fill="#E88C9A" stroke="{NAVY}" stroke-width="1.4"/>
<path d="M32.5 34 h7 l-3.5 7z" fill="#F3D9B1" stroke="{NAVY}" stroke-width="1.4" stroke-linejoin="round"/>
</g>""",
    "run": f"""
<rect x="2" y="6" width="38" height="31" rx="4.5" fill="#2B3A4F"/>
<path d="M8 15 l5 4.5 l-5 4.5" stroke="#8EE08E" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M16 26 h10" stroke="#E8EEF5" stroke-width="2.6" stroke-linecap="round"/>
<circle cx="35" cy="35" r="9.5" fill="{GREEN}" stroke="#FFFFFF" stroke-width="2"/>
<path d="M30.4 35 l3.2 3.2 l6 -6.4" stroke="#FFFFFF" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>""",
    "hourglass": f"""
<path d="M14 8 h20 c0 9 -7 11 -7 16 c0 5 7 7 7 16 h-20 c0 -9 7 -11 7 -16 c0 -5 -7 -7 -7 -16z" fill="{PFILL}" stroke="{NAVY}" stroke-width="1.8"/>
<path d="M17.5 12 h13 c-1 4 -4.5 6 -6.5 9 c-2 -3 -5.5 -5 -6.5 -9z" fill="{ORANGE}"/>
<path d="M16 39.5 c1 -4 5 -6 8 -7 c3 1 7 3 8 7z" fill="{ORANGE}"/>
<rect x="10" y="4" width="28" height="4.5" rx="2" fill="{NAVY}"/>
<rect x="10" y="39.5" width="28" height="4.5" rx="2" fill="{NAVY}"/>""",
    "repeat": f"""
<path d="M9 22 a15 13 0 0 1 27 -6" stroke="{BLUE}" stroke-width="3.6" fill="none" stroke-linecap="round" marker-end="url(#mk-icon)"/>
<path d="M39 26 a15 13 0 0 1 -27 6" stroke="{BLUE}" stroke-width="3.6" fill="none" stroke-linecap="round" marker-end="url(#mk-icon)"/>""",
    "blocked": f"""
<circle cx="24" cy="24" r="18" fill="{RED}"/>
<rect x="13" y="21" width="22" height="6" rx="2" fill="#FFFFFF"/>""",
    "restart": f"""
<path d="M37 26 a13 13 0 1 1 -4 -11" stroke="{NAVY}" stroke-width="3.4" fill="none" stroke-linecap="round" marker-end="url(#mk-icon-navy)"/>
<rect x="19" y="19" width="3.8" height="12" rx="1" fill="{ORANGE}"/>
<rect x="26" y="19" width="3.8" height="12" rx="1" fill="{ORANGE}"/>""",
    "sparkle": """
<path d="M22 5 Q24 21 40 23 Q24 25 22 41 Q20 25 4 23 Q20 21 22 5 Z" fill="url(#gChipCore)"/>
<path d="M38 4 Q38.8 9.2 44 10 Q38.8 10.8 38 16 Q37.2 10.8 32 10 Q37.2 9.2 38 4 Z" fill="#8B5CF6"/>
<path d="M39 32 Q39.6 35.4 43 36 Q39.6 36.6 39 40 Q38.4 36.6 35 36 Q38.4 35.4 39 32 Z" fill="#1FC8E3"/>""",
    "check": f"""
<circle cx="24" cy="24" r="20" fill="{GREEN}"/>
<path d="M14 24.5 l7 7 l13.5 -14" stroke="#FFFFFF" stroke-width="4.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>""",
}


def page(w, h, body):
    extra = "<defs>" + "".join(_EXTRA) + "</defs>"
    _EXTRA.clear()
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>html,body{{margin:0;padding:0;background:#fff}}</style></head>
<body><svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{DEFS}{extra}
<rect x="0" y="0" width="{w}" height="{h}" fill="#FFFFFF"/><rect x="0" y="0" width="{w}" height="{h}" fill="url(#pDots)"/>{body}</svg></body></html>"""
