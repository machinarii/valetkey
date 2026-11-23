#!/usr/bin/env python3
"""Draw the README's how-it-works animation.

  python3 docs/images/make.py        writes how-it-works.gif

Development only: needs Pillow and ffmpeg. Nothing in valetkey itself uses this.

The icon (valetkey-icon.png, valetkey-icon-512.png) is not drawn here. It was generated with
ChatGPT's image tool from the brief "a close-up robotic valet in a cap, holding a key beside its
head", then cut out of its background and resized.
"""
import math
import os
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
S = 2                     # draw at twice the size, then shrink: smooth edges
W, H, FPS = 960, 580, 10
TOP, STEP = 56, (68, 112)  # top bar height; step description band (top, bottom)
PAUSE = 1.0  # seconds each step holds still once its motion ends, so it can be read
NAVY, PANEL, EDGE = (15, 27, 42), (24, 42, 64), (58, 88, 120)
TEXT, MUTED = (232, 238, 245), (150, 168, 188)
GOLD, TEAL, RED = (242, 184, 75), (72, 204, 176), (238, 104, 104)


def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for folder in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/Library/Fonts"):
        path = os.path.join(folder, name)
        if os.path.exists(path):
            return ImageFont.truetype(path, size * S)
    return ImageFont.load_default(size * S)


def sc(*values):
    return [v * S for v in values]


def key(d, cx, cy, size, color, hole):
    """A key lying flat, bow on the left, centred on (cx, cy)."""
    r, t, left, right = size * 0.24, size * 0.11, cx - size / 2, cx + size / 2
    d.ellipse(sc(left, cy - r, left + 2 * r, cy + r), fill=color)
    d.ellipse(sc(left + r * 0.55, cy - r * 0.45, left + r * 1.45, cy + r * 0.45), fill=hole)
    d.rounded_rectangle(sc(left + 2 * r - 2, cy - t / 2, right, cy + t / 2), radius=t / 2 * S, fill=color)
    for off in (0.06, 0.20):
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


# master key: the real credential (vault, broker, service). valet key: permission to use one route,
# no secret (the agent's requests). login: a tool's own sign-in (desk, tool's service).
ICONS = {"master": lambda d, x, y, bg: key(d, x, y, 24, GOLD, bg),
         "valet": lambda d, x, y, bg: key(d, x, y, 24, TEAL, bg),
         "login": lambda d, x, y, bg: login(d, x, y, 22, GOLD, bg)}


BOXES = {  # name: (left, top, right, bottom, title, subtitle)
    "agent": (40, 270, 270, 470, "Agent machine", "runs your AI agents"),
    "broker": (365, 160, 595, 320, "Broker", "checks the caller, adds the key"),
    "desk": (365, 390, 595, 550, "Desk", "runs MCPs & other tools\nthat require sign in"),
    "vault": (700, 130, 920, 210, "Vault", "every key, encrypted"),
    "api": (700, 250, 920, 330, "Service API", ""),
    "svc": (700, 430, 920, 510, "Service tool", ""),
}


def centre(name):
    left, top, right, bottom = BOXES[name][:4]
    return (left + right) / 2, (top + bottom) / 2


def base():
    img = Image.new("RGB", (W * S, H * S), NAVY)
    d = ImageDraw.Draw(img)
    for a, b in (("agent", "broker"), ("agent", "desk"), ("broker", "vault"), ("broker", "api"), ("desk", "svc")):
        (ax, ay), (bx, by) = edge(a, b)
        d.line(sc(ax, ay, bx, by), fill=EDGE, width=2 * S)
    for name, (left, top, right, bottom, title, sub) in BOXES.items():
        d.rounded_rectangle(sc(left, top, right, bottom), radius=14 * S, fill=PANEL, outline=EDGE, width=2 * S)
        d.text(sc(left + 16, top + 12), title, font=font(17, True), fill=TEXT)
        if sub:
            d.text(sc(left + 16, top + 37), sub, font=font(12), fill=MUTED, spacing=4 * S)
    left, top, right, bottom = BOXES["agent"][:4]                       # the agent holds nothing
    label, f = "holds no keys", font(14, True)
    w = d.textlength(label, font=f) / S + 28
    d.rounded_rectangle(sc(left + 16, bottom - 44, left + 16 + w, bottom - 16), radius=14 * S, outline=TEAL, width=2 * S)
    d.text(sc(left + 30, bottom - 39), label, font=f, fill=TEXT)
    left, top, right, bottom = BOXES["vault"][:4]
    for i in range(3):
        key(d, right - 92 + i * 30, top + 24, 26, GOLD, PANEL)
    left, top, right, bottom = BOXES["desk"][:4]                        # the tool's login lives on the desk
    login(d, left + 34, bottom - 30, 30, GOLD, PANEL)
    d.text(sc(left + 60, bottom - 39), "the tool's login", font=font(12), fill=MUTED)
    d.rectangle(sc(0, 0, W, TOP), fill=PANEL)
    d.line(sc(0, TOP, W, TOP), fill=EDGE, width=S)
    d.text(sc(40, 15), "valetkey", font=font(20, True), fill=TEXT)
    d.text(sc(152, 20), "how a key gets used", font=font(13), fill=MUTED)
    legend = (("master", "master key"), ("valet", "valet key"), ("login", "a tool's login"))
    widths = [30 + d.textlength(text, font=font(12)) / S for _, text in legend]
    x = 920 - sum(widths) - 22 * (len(legend) - 1)  # right-aligned with the boxes
    for (icon, text), w in zip(legend, widths):
        ICONS[icon](d, x + 12, TOP / 2, PANEL)
        d.text(sc(x + 30, TOP / 2 - 8), text, font=font(12), fill=MUTED)
        x += w + 22
    d.text(sc(288, 332), "tailnet", font=font(11), fill=MUTED)
    return img


def edge(a, b):
    """Points on the facing sides of two boxes."""
    al, at, ar, ab = BOXES[a][:4]
    bl, bt, br, bb = BOXES[b][:4]
    (ax, ay), (bx, by) = centre(a), centre(b)
    if a == "agent":
        ay = ay - 40 if by < ay else ay + 40
    if a == "broker" and b in ("vault", "api"):
        ay = ay - 30 if b == "vault" else ay + 30
    return (ar, ay), (bl, by)


def ease(t):
    return 0.5 - 0.5 * math.cos(math.pi * max(0, min(1, t)))


def pill(d, x, y, text, color=TEAL, icon=None, width=None):
    f = font(12, True)
    w = width or d.textlength(text, font=f) / S + 24 + (28 if icon else 0)
    d.rounded_rectangle(sc(x - w / 2, y - 13, x + w / 2, y + 13), radius=13 * S, fill=NAVY, outline=color, width=2 * S)
    d.text(sc(x - w / 2 + 12, y - 8), text, font=f, fill=color)
    if icon:
        ICONS[icon](d, x + w / 2 - 20, y, NAVY)


def travel(d, a, b, t, text, back=False, **kw):
    (ax, ay), (bx, by) = edge(a, b)
    if back:
        ax, ay, bx, by = bx, by, ax, ay
    f = ease(t)
    pill(d, ax + (bx - ax) * f, ay + (by - ay) * f, text, **kw)


def note(d, box, lines):
    left, top, right, bottom = BOXES[box][:4]
    for i, (text, color) in enumerate(lines):
        below = 16 if "\n" in BOXES[box][5] else 0  # clear a two-line subtitle
        d.text(sc(left + 16, top + 66 + below + i * 22), text, font=font(13, True), fill=color)


def broker_key(d, alpha=1.0):
    left, top, right, bottom = BOXES["broker"][:4]
    key(d, right - 34, bottom - 24, 34, tuple(round(n + (g - n) * alpha) for g, n in zip(GOLD, PANEL)), PANEL)


def caption(d, number, text):
    top, bottom = STEP
    d.rounded_rectangle(sc(40, top, 920, bottom), radius=12 * S, fill=PANEL)
    x = 58
    if number:
        d.ellipse(sc(54, top + 9, 80, top + 35), fill=TEAL)
        d.text(sc(67 - d.textlength(str(number), font=font(14, True)) / S / 2, top + 13), str(number), font=font(14, True), fill=NAVY)
        x = 92
    d.text(sc(x, top + 12), text, font=font(15), fill=TEXT)


CHECK = [("valet key: agent-dev  ✓", TEAL), ("route allows it  ✓", TEAL)]

# (seconds, caption number, caption, what moves; t runs 0..1 within the phase)
PHASES = [
    (1.6, None, "The agent's machine holds no keys and no logins.", lambda d, t: None),
    (1.6, 1, "An agent calls the broker with its valet key: who it is, no secret.",
     lambda d, t: travel(d, "agent", "broker", t, "request", icon="valet")),
    (1.6, 2, "The broker checks which machine is calling and what its routes allow.",
     lambda d, t: note(d, "broker", CHECK[:1 + (t > 0.45)])),
    (1.5, 3, "It takes that route's master key from the vault.",
     lambda d, t: (note(d, "broker", CHECK), travel(d, "broker", "vault", t, "master key", back=True, color=GOLD,
                                                    icon="master"))),
    (1.6, 4, "It adds the master key and forwards the request to the service.",
     lambda d, t: (note(d, "broker", CHECK), broker_key(d), travel(d, "broker", "api", t, "request", icon="master"))),
    (1.8, 5, "The answer comes back without the master key. The agent never sees it.",
     lambda d, t: (broker_key(d), travel(d, "broker", "api", t * 2, "answer", back=True) if t < 0.5
                   else travel(d, "agent", "broker", t * 2 - 1, "answer", back=True))),
    (1.6, 6, "For a tool with its own login, the agent calls the desk instead.",
     lambda d, t: travel(d, "agent", "desk", t, "tool upload a.png", icon="valet")),
    (1.8, 7, "The desk runs the real tool with its login, within a list you wrote.",
     lambda d, t: (note(d, "desk", [("on the list  ✓", TEAL)]), travel(d, "desk", "svc", t, "upload", icon="login"))),
    (1.6, 8, "The output comes back. The login stays on the desk.",
     lambda d, t: travel(d, "agent", "desk", t, "output", back=True)),
    (2.0, 9, "A command that would print the login is refused.",
     lambda d, t: (travel(d, "agent", "desk", min(t * 1.5, 0.62), "tool auth token", color=RED),
                   note(d, "desk", [("", RED), ("not on the list  ✗", RED)]) if t > 0.45 else None)),
    (2.6, None, "Master keys and logins stay where no agent runs. Agents get valet keys.",
     lambda d, t: d.rounded_rectangle(sc(*[v + o for v, o in zip(BOXES["agent"][:4], (-5, -5, 5, 5))]),
                                      radius=18 * S, outline=TEAL, width=(2 + round(2 * abs(math.sin(t * math.pi * 2)))) * S)),
]


def animation(path):
    still = base()
    with tempfile.TemporaryDirectory() as tmp:
        n = 0
        for seconds, number, text, moving in PHASES:
            frames = round(seconds * FPS)
            for i in range(frames + round(PAUSE * FPS)):
                img = still.copy()
                d = ImageDraw.Draw(img)
                moving(d, min(i / max(frames - 1, 1), 1.0))
                caption(d, number, text)
                img.resize((W, H), Image.LANCZOS).save(os.path.join(tmp, f"f{n:04d}.png"))
                n += 1
        frames = os.path.join(tmp, "f%04d.png")
        palette = os.path.join(tmp, "palette.png")
        quiet = ["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(FPS), "-i", frames]
        subprocess.run(quiet + ["-vf", "palettegen=max_colors=48:stats_mode=diff", palette], check=True)
        subprocess.run(quiet + ["-i", palette, "-lavfi", "paletteuse=dither=none:diff_mode=rectangle", path], check=True)
    return n


if __name__ == "__main__":
    out = os.path.join(HERE, "how-it-works.gif")
    count = animation(out)
    print(f"animation {count} frames, {os.path.getsize(out) // 1024} KB")
