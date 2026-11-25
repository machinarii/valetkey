#!/usr/bin/env python3
"""Draw the README's how-it-works animation.

  python3 docs/images/make.py        writes how-it-works.gif

Development only: needs Pillow and ffmpeg. Nothing in valetkey itself uses this.

The icon (valetkey-icon.png, valetkey-icon-512.png) is not drawn here. It was generated with
ChatGPT's image tool from the brief "a close-up robotic valet in a cap, holding a key beside its
head", then cut out of its background and resized.
"""
from functools import lru_cache
import math
import os
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
S = 3                     # supersample the logical canvas before export
W, H = 960, 592
OUTPUT = (860, round(H * 860 / W))  # match the README image width
FPS, COLORS = 25, 256
TOP, STEP = 56, (80, 124)  # top bar height; step description band (top, bottom)
PAUSE = 1.0  # seconds each step holds still once its motion ends, so it can be read
FADE = 0.32  # seconds for the spotlight and caption transition
DIM = 0.44    # how far boxes outside the current step sink into the background
NAVY, PANEL, EDGE = (15, 27, 42), (24, 42, 64), (58, 88, 120)
TEXT, MUTED = (232, 238, 245), (150, 168, 188)
GOLD, TEAL, RED = (242, 184, 75), (72, 204, 176), (238, 104, 104)
SILVER = (196, 206, 218)  # the valet key: gold is the master's
BLACK = (6, 8, 12)        # the keyhole plate


@lru_cache(maxsize=None)
def font(size, bold=False):
    dejavu = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    arial = "Arial Bold.ttf" if bold else "Arial.ttf"
    for name in (dejavu, os.path.join("/System/Library/Fonts/Supplemental", arial),
                 os.path.join("/Library/Fonts", arial)):
        try:
            return ImageFont.truetype(name, size * S)
        except OSError:
            continue
    return ImageFont.load_default(size * S)

def sc(*values):
    return [v * S for v in values]


def key(d, cx, cy, size, color, hole, teeth=2):
    """A key lying flat, bow on the left, centred on (cx, cy). The valet key has one tooth."""
    r, t, left, right = size * 0.24, size * 0.11, cx - size / 2, cx + size / 2
    d.ellipse(sc(left, cy - r, left + 2 * r, cy + r), fill=color)
    d.ellipse(sc(left + r * 0.55, cy - r * 0.45, left + r * 1.45, cy + r * 0.45), fill=hole)
    d.rounded_rectangle(sc(left + 2 * r - 2, cy - t / 2, right, cy + t / 2), radius=t / 2 * S, fill=color)
    for off in (0.06, 0.20)[:teeth]:
        d.rectangle(sc(right - size * (off + 0.07), cy, right - size * off, cy + size * 0.17), fill=color)


def login(d, cx, cy, size, color, hole):
    """A sign-in card (a head beside two lines), for a tool's own login. Not a vault key."""
    w, h = size, size * 0.72
    d.rounded_rectangle(sc(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), radius=size * 0.12 * S, fill=color)
    hx, r = cx - w * 0.22, h * 0.15
    d.ellipse(sc(hx - r, cy - h * 0.26, hx + r, cy - h * 0.26 + 2 * r), fill=hole)
    d.pieslice(sc(hx - r * 1.8, cy + h * 0.06, hx + r * 1.8, cy + h * 0.06 + r * 3.6), 180, 360, fill=hole)
    for i in (0, 1):
        d.rectangle(sc(cx + w * 0.04, cy - h * 0.16 + i * h * 0.28, cx + w * 0.38, cy - h * 0.05 + i * h * 0.28), fill=hole)


def mix(a, b, k):
    """Colour a moved k of the way to colour b."""
    return tuple(round(x + (y - x) * k) for x, y in zip(a, b))


def keyhole(d, cx, cy, size, color, hole, outline=None):
    """A lock plate with an empty keyhole: a request that carries no key."""
    d.rounded_rectangle(sc(cx - size * 0.38, cy - size * 0.5, cx + size * 0.38, cy + size * 0.5),
                        radius=size * 0.14 * S, fill=color, outline=outline, width=S if outline else 0)
    r = size * 0.15
    d.ellipse(sc(cx - r, cy - size * 0.28, cx + r, cy - size * 0.28 + 2 * r), fill=hole)
    d.polygon(sc(cx - r * 0.55, cy - size * 0.1, cx + r * 0.55, cy - size * 0.1,
                 cx + r * 1.05, cy + size * 0.32, cx - r * 1.05, cy + size * 0.32), fill=hole)


# keyhole: the agent's request, no key in it. master key: the real credential (vault, broker, service).
# valet key: which routes a machine may use, kept on the broker; it decides whose keyhole gets a key.
# login: a tool's own sign-in (bellhop, tool's service).
ICONS = {"master": lambda d, x, y, bg, a=1: key(d, x, y, 24, mix(bg, GOLD, a), bg),
         "valet": lambda d, x, y, bg, a=1: key(d, x, y, 24, mix(bg, SILVER, a), bg, teeth=1),
         "login": lambda d, x, y, bg, a=1: login(d, x, y, 22, mix(bg, TEXT, a), bg),
         "keyhole": lambda d, x, y, bg, a=1: keyhole(d, x, y, 20, mix(bg, BLACK, a), mix(bg, TEXT, a),
                                                       mix(bg, MUTED, a))}  # black plate, light outline


BOXES = {  # name: (left, top, right, bottom, title, subtitle)
    "agent": (40, 282, 270, 482, "agent machine", "runs your AI agents"),
    "broker": (365, 172, 595, 332, "broker", "checks the caller\nfnox adds the key"),
    "bellhop": (365, 402, 595, 562, "bellhop", "runs MCPs & other tools\nthat require sign in"),
    "vault": (700, 142, 920, 222, "vault", "every key, encrypted"),
    "api": (684, 262, 920, 342, "service API/CLI", ""),
    "svc": (700, 442, 920, 522, "service MCP/tool", ""),
}
# Connections, with what the far end does. Requests and answers travel both ways.
EDGES = (("agent", "broker", ""), ("agent", "bellhop", ""), ("broker", "vault", "reads"),
         ("broker", "api", "calls"), ("bellhop", "svc", "runs"))


def centre(name):
    left, top, right, bottom = BOXES[name][:4]
    return (left + right) / 2, (top + bottom) / 2


def edge(a, b):
    """Points on the facing sides of two boxes."""
    al, at, ar, ab = BOXES[a][:4]
    bl, bt, br, bb = BOXES[b][:4]
    (ax, ay), (bx, by) = centre(a), centre(b)
    if a == "agent":
        ay = ay - 40 if by < ay else ay + 40
    if a == "agent" and b == "bellhop":
        by += 18  # leave room for the returning label beneath the bellhop subtitle
    if a == "broker" and b in ("vault", "api"):
        ay = ay - 30 if b == "vault" else ay + 30
    return (ar, ay), (bl, by)


def base(focus):
    """The still picture for one step: boxes in `focus` (None: all) lit, the rest dimmed."""
    img = Image.new("RGB", (W * S, H * S), NAVY)
    d = ImageDraw.Draw(img)
    lit = lambda name: focus is None or name in focus

    def c(colour, name):
        return colour if lit(name) else mix(colour, NAVY, DIM)

    for a, b, label in EDGES:
        on = focus is not None and a in focus and b in focus
        colour = (120, 158, 196) if on else (EDGE if lit(a) and lit(b) else mix(EDGE, NAVY, 0.4))
        (ax, ay), (bx, by) = edge(a, b)
        d.line(sc(ax, ay, bx, by), fill=colour, width=2 * S)  # no arrowhead: traffic goes both ways
        if label:
            label_offset = 28 if a == "broker" else 20
            d.text(sc((ax + bx) / 2, (ay + by) / 2 - label_offset), label, font=font(13), anchor="ms",
                   fill=MUTED if lit(a) and lit(b) else mix(MUTED, NAVY, DIM))
    for name, (left, top, right, bottom, title, sub) in BOXES.items():
        d.rounded_rectangle(sc(left, top, right, bottom), radius=14 * S, fill=c(PANEL, name),
                            outline=c(EDGE if focus is None or not lit(name) else (96, 132, 170), name), width=2 * S)
        d.text(sc(left + 16, top + 12), title, font=font(19, True), fill=c(TEXT, name))
        if sub:
            d.text(sc(left + 16, top + 37), sub, font=font(13), fill=c(MUTED, name), spacing=4 * S)
    left, top, right, bottom = BOXES["agent"][:4]                       # the agent holds nothing
    label, f = "holds no keys", font(14, True)
    d.text(sc(left + 16, bottom - 39), label, font=f, fill=c(TEXT, "agent"))
    left, top, right, bottom = BOXES["vault"][:4]
    for i in range(3):
        key(d, right - 92 + i * 30, top + 64, 26, c(GOLD, "vault"), c(PANEL, "vault"))
    left, top, right, bottom = BOXES["bellhop"][:4]                        # the tool's login lives on the bellhop
    d.text(sc(left + 16, bottom - 39), "the tool's login", font=font(13), fill=c(MUTED, "bellhop"))
    d.rectangle(sc(0, 0, W, TOP), fill=PANEL)
    d.line(sc(0, TOP, W, TOP), fill=EDGE, width=S)
    d.text(sc(40, 15), "valetkey", font=font(20, True), fill=TEXT)
    d.text(sc(152, 20), "how a key gets used", font=font(13), fill=MUTED)
    legend = (("keyhole", "keyhole"), ("master", "master key"), ("valet", "valet key"), ("login", "a tool's login"))
    widths = [30 + d.textlength(text, font=font(13)) / S for _, text in legend]
    x = 920 - sum(widths) - 22 * (len(legend) - 1)  # right-aligned with the boxes
    for (icon, text), w in zip(legend, widths):
        ICONS[icon](d, x + 12, TOP / 2, PANEL)
        d.text(sc(x + 30, TOP / 2), text, font=font(13), fill=MUTED, anchor="lm")
        x += w + 22
    return img


def ease(t):
    return 0.5 - 0.5 * math.cos(math.pi * max(0, min(1, t)))


def pill(d, x, y, text, color=TEAL, icon=None, width=None, a=1.0):
    f = font(12, True)
    w = width or d.textlength(text, font=f) / S + 24 + (28 if icon else 0)
    d.rounded_rectangle(sc(x - w / 2, y - 13, x + w / 2, y + 13), radius=13 * S, fill=NAVY,
                        outline=mix(NAVY, color, a), width=2 * S)
    d.text(sc(x - w / 2 + 12, y), text, font=f, fill=mix(NAVY, color, a), anchor="lm")
    if icon:
        ICONS[icon](d, (x + w / 2 - 20) if text else x, y, NAVY, a)


def travel(d, a, b, t, text, back=False, **kw):
    """A labelled item moving along a connection: it fades in as it sets off, eases in and out."""
    (ax, ay), (bx, by) = edge(a, b)
    if back:
        ax, ay, bx, by = bx, by, ax, ay
    w = d.textlength(text, font=font(13, True)) / S + 24 + (28 if kw.get("icon") else 0)
    # Reserve clearance horizontally because the pill stays level on diagonal edges.
    # Long pills can start over the empty body of the source box.
    gap = abs(bx - ax)
    end = w / 2 + 5
    start = min(end, gap - end - 42)
    f = (start + (gap - end - start) * ease(t)) / gap
    pill(d, ax + (bx - ax) * f, ay + (by - ay) * f, text, width=w, a=ease(t / 0.2), **kw)

def note(d, box, lines):
    left, top, right, bottom = BOXES[box][:4]
    for i, (text, color, *icon) in enumerate(lines):
        below = 16 if "\n" in BOXES[box][5] else 0  # clear a two-line subtitle
        y, x = top + 66 + below + i * 22, left + 16
        if icon:
            ICONS[icon[0]](d, x + 12, y + 9, PANEL)
            x += 30
        d.text(sc(x, y), text, font=font(13, True), fill=color)


def broker_key(d, alpha=1.0):
    left, top, right, bottom = BOXES["broker"][:4]
    key(d, right - 34, bottom - 24, 34, mix(PANEL, GOLD, alpha), PANEL)


STEPS = 9


def caption(d, number, text, k=1.0, step=0):
    """The step description: it slides up a few pixels and fades in (k 0..1); dots show progress."""
    top, bottom = STEP
    d.rounded_rectangle(sc(40, top, 920, bottom), radius=12 * S, fill=PANEL)
    lift, x = (1 - k) * 6, 58
    if number:
        d.ellipse(sc(54, top + 9 + lift, 80, top + 35 + lift), fill=mix(PANEL, TEAL, k))
        d.text(sc(67, (top + bottom) / 2 + lift), str(number),
               font=font(15, True), fill=NAVY, anchor="mm")
        x = 92
    d.text(sc(x, (top + bottom) / 2 + lift), text, font=font(16),
           fill=mix(PANEL, TEXT, k), anchor="lm")
    for i in range(STEPS):                                              # progress: done, current, to come
        cx, cy = 920 - 18 - (STEPS - 1 - i) * 13, (top + bottom) / 2
        if i + 1 == number:
            d.ellipse(sc(cx - 4, cy - 4, cx + 4, cy + 4), fill=TEAL)
        elif i + 1 < step:
            d.ellipse(sc(cx - 3, cy - 3, cx + 3, cy + 3), fill=mix(PANEL, TEAL, 0.5))
        else:
            d.ellipse(sc(cx - 3, cy - 3, cx + 3, cy + 3), outline=EDGE, width=S)


CHECK = [("caller: agent-dev  ✓", TEAL), ("fits this route  ✓", TEAL)]

# (seconds, caption number, caption, boxes in the spotlight (None: all), what moves; t runs 0..1)
PHASES = [
    (1.6, None, "The agent's machine holds no keys and no logins.", {"agent"}, lambda d, t: None),
    (1.6, 1, "An agent calls the broker. Its request is an empty keyhole: no key.", {"agent", "broker"},
     lambda d, t: travel(d, "agent", "broker", t, "request", icon="keyhole")),
    (1.6, 2, "The broker checks who is calling, and that its valet key fits this route.", {"broker"},
     lambda d, t: note(d, "broker", CHECK[:1 + (t > 0.45)])),
    (1.5, 3, "It takes that route's master key from the vault.", {"broker", "vault"},
     lambda d, t: (note(d, "broker", CHECK), travel(d, "broker", "vault", t, "", back=True, color=GOLD,
                                                    icon="master"))),
    (1.6, 4, "fnox puts the master key in the keyhole and forwards the request.", {"broker", "api"},
     lambda d, t: (note(d, "broker", CHECK), broker_key(d), travel(d, "broker", "api", t, "request", icon="master"))),
    (1.8, 5, "The answer comes back without the master key. The agent never sees it.", {"agent", "broker", "api"},
     lambda d, t: (broker_key(d), travel(d, "broker", "api", t * 2, "answer", back=True) if t < 0.5
                   else travel(d, "agent", "broker", t * 2 - 1, "answer", back=True))),
    (1.6, 6, "For a tool with its own login, the agent calls the bellhop instead.", {"agent", "bellhop"},
     lambda d, t: travel(d, "agent", "bellhop", t, "tool upload a.png", icon="keyhole")),
    (1.8, 7, "The bellhop runs the real tool with its login, within a list you wrote.", {"bellhop", "svc"},
     lambda d, t: (note(d, "bellhop", [("on the list  ✓", TEAL)]), travel(d, "bellhop", "svc", t, "upload", icon="login"))),
    (1.6, 8, "The output comes back. The login stays on the bellhop.", {"agent", "bellhop"},
     lambda d, t: travel(d, "agent", "bellhop", t, "output", back=True)),
    (2.0, 9, "A command that would print the login is refused.", {"agent", "bellhop"},
     lambda d, t: (travel(d, "agent", "bellhop", min(t * 1.5, 0.62), "tool auth token", color=RED),
                   note(d, "bellhop", [("not on the list  ✗", RED)]) if t > 0.45 else None)),
    (2.6, None, "Master keys and logins stay where no agent runs. Agents only send keyholes.", None,
     lambda d, t: d.rounded_rectangle(sc(*[v + o for v, o in zip(BOXES["agent"][:4], (-5, -5, 5, 5))]),
                                      radius=18 * S, outline=mix(EDGE, TEAL, 0.45 + 0.55 * ease(t)), width=3 * S)),
]


def animation(path):
    stills, prev = {}, None
    with tempfile.TemporaryDirectory() as tmp:
        n = 0
        for p, (seconds, number, text, focus, moving) in enumerate(PHASES):
            step = number or (0 if p == 0 else STEPS + 1)  # before the first step, or after the last
            k = frozenset(focus) if focus is not None else None
            if k not in stills:
                stills[k] = base(focus)
            still = stills[k]
            frames = round(seconds * FPS)
            for i in range(frames + round(PAUSE * FPS)):
                fade = ease(min(1.0, (i + 1) / (FADE * FPS)))
                img = Image.blend(prev, still, fade) if prev is not None and fade < 1 else still.copy()
                d = ImageDraw.Draw(img)
                moving(d, min(i / max(frames - 1, 1), 1.0))
                caption(d, number, text, fade, step)
                img.resize(OUTPUT, Image.Resampling.LANCZOS).save(
                    os.path.join(tmp, f"f{n:04d}.png"), compress_level=1)
                n += 1
            prev = still
        frames = os.path.join(tmp, "f%04d.png")
        palette = os.path.join(tmp, "palette.png")
        quiet = ["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(FPS), "-i", frames]
        subprocess.run(quiet + ["-vf", f"palettegen=max_colors={COLORS}:stats_mode=full", palette], check=True)
        subprocess.run(quiet + ["-i", palette, "-lavfi", "paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle", path], check=True)
    return n


if __name__ == "__main__":
    out = os.path.join(HERE, "how-it-works.gif")
    count = animation(out)
    print(f"animation {count} frames, {os.path.getsize(out) // 1024} KB")
