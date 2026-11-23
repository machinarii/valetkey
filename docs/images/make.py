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
W, H, FPS = 960, 540, 10
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


BOXES = {  # name: (left, top, right, bottom, title, subtitle)
    "agent": (40, 170, 270, 370, "Agent machine", "runs your AI agents"),
    "broker": (365, 60, 595, 220, "Broker", "checks the caller, adds the key"),
    "desk": (365, 290, 595, 450, "Desk", "runs tools that sign in"),
    "vault": (700, 30, 920, 110, "Vault", "every key, encrypted"),
    "api": (700, 150, 920, 230, "Service API", ""),
    "svc": (700, 330, 920, 410, "Service tool", ""),
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
            d.text(sc(left + 16, top + 37), sub, font=font(12), fill=MUTED)
    left, top, right, bottom = BOXES["agent"][:4]                       # the agent holds nothing
    d.rounded_rectangle(sc(left + 16, bottom - 44, left + 108, bottom - 16), radius=14 * S, outline=TEAL, width=2 * S)
    d.text(sc(left + 30, bottom - 39), "0 keys", font=font(14, True), fill=TEAL)
    left, top, right, bottom = BOXES["vault"][:4]
    for i in range(3):
        key(d, right - 92 + i * 30, top + 24, 26, GOLD, PANEL)
    left, top, right, bottom = BOXES["desk"][:4]                        # the tool's login lives on the desk
    key(d, left + 36, bottom - 30, 34, GOLD, PANEL)
    d.text(sc(left + 60, bottom - 39), "the tool's login", font=font(12), fill=MUTED)
    d.text(sc(40, 22), "valetkey", font=font(20, True), fill=TEXT)
    d.text(sc(152, 27), "how a key gets used", font=font(13), fill=MUTED)
    d.text(sc(288, 232), "tailnet", font=font(11), fill=MUTED)
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


def pill(d, x, y, text, color=TEAL, with_key=False, width=None):
    f = font(12, True)
    w = width or d.textlength(text, font=f) / S + 24 + (26 if with_key else 0)
    d.rounded_rectangle(sc(x - w / 2, y - 13, x + w / 2, y + 13), radius=13 * S, fill=NAVY, outline=color, width=2 * S)
    d.text(sc(x - w / 2 + 12, y - 8), text, font=f, fill=color)
    if with_key:
        key(d, x + w / 2 - 20, y, 24, GOLD, NAVY)


def travel(d, a, b, t, text, back=False, **kw):
    (ax, ay), (bx, by) = edge(a, b)
    if back:
        ax, ay, bx, by = bx, by, ax, ay
    f = ease(t)
    pill(d, ax + (bx - ax) * f, ay + (by - ay) * f, text, **kw)


def note(d, box, lines):
    left, top, right, bottom = BOXES[box][:4]
    for i, (text, color) in enumerate(lines):
        d.text(sc(left + 16, top + 66 + i * 22), text, font=font(13, True), fill=color)


def broker_key(d, alpha=1.0):
    left, top, right, bottom = BOXES["broker"][:4]
    key(d, right - 34, bottom - 24, 34, tuple(round(n + (g - n) * alpha) for g, n in zip(GOLD, PANEL)), PANEL)


def caption(d, number, text):
    d.rounded_rectangle(sc(40, 478, 920, 522), radius=12 * S, fill=PANEL)
    x = 58
    if number:
        d.ellipse(sc(54, 487, 80, 513), fill=TEAL)
        d.text(sc(67 - d.textlength(str(number), font=font(14, True)) / S / 2, 491), str(number), font=font(14, True), fill=NAVY)
        x = 92
    d.text(sc(x, 490), text, font=font(15), fill=TEXT)


CHECK = [("caller: agent-dev  ✓", TEAL), ("route allows it  ✓", TEAL)]

# (seconds, caption number, caption, what moves; t runs 0..1 within the phase)
PHASES = [
    (1.6, None, "The agent's machine holds no keys and no logins.", lambda d, t: None),
    (1.6, 1, "An agent calls the broker. The request carries no key.",
     lambda d, t: travel(d, "agent", "broker", t, "request")),
    (1.6, 2, "The broker checks which machine is calling and what its routes allow.",
     lambda d, t: note(d, "broker", CHECK[:1 + (t > 0.45)])),
    (1.5, 3, "It takes that one key from the vault.",
     lambda d, t: (note(d, "broker", CHECK), travel(d, "broker", "vault", t, "key", back=True, color=GOLD))),
    (1.6, 4, "It adds the key and forwards the request to the service.",
     lambda d, t: (note(d, "broker", CHECK), broker_key(d), travel(d, "broker", "api", t, "request", with_key=True))),
    (1.8, 5, "The answer comes back. The key never left the broker.",
     lambda d, t: (broker_key(d), travel(d, "broker", "api", t * 2, "answer", back=True) if t < 0.5
                   else travel(d, "agent", "broker", t * 2 - 1, "answer", back=True))),
    (1.6, 6, "For a tool with its own login, the agent calls the desk instead.",
     lambda d, t: travel(d, "agent", "desk", t, "tool upload a.png")),
    (1.8, 7, "The desk runs the real tool with its login, within a list you wrote.",
     lambda d, t: (note(d, "desk", [("on the list  ✓", TEAL)]), travel(d, "desk", "svc", t, "upload", with_key=True))),
    (1.6, 8, "The output comes back. The login stays on the desk.",
     lambda d, t: travel(d, "agent", "desk", t, "output", back=True)),
    (2.0, 9, "A command that would print the login is refused.",
     lambda d, t: (travel(d, "agent", "desk", min(t * 1.5, 0.62), "tool auth token", color=RED),
                   note(d, "desk", [("", RED), ("not on the list  ✗", RED)]) if t > 0.45 else None)),
    (2.6, None, "Keys live where no agent runs. The agent's machine holds none.",
     lambda d, t: d.rounded_rectangle(sc(*[v + o for v, o in zip(BOXES["agent"][:4], (-5, -5, 5, 5))]),
                                      radius=18 * S, outline=TEAL, width=(2 + round(2 * abs(math.sin(t * math.pi * 2)))) * S)),
]


def animation(path):
    still = base()
    with tempfile.TemporaryDirectory() as tmp:
        n = 0
        for seconds, number, text, moving in PHASES:
            frames = round(seconds * FPS)
            for i in range(frames):
                img = still.copy()
                d = ImageDraw.Draw(img)
                moving(d, i / max(frames - 1, 1))
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
