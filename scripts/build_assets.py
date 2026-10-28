"""Derive icon, avatar and installer artwork from the original mascot images.

The originals in assets/branding/ are only read, never modified. Derived images are resized
copies; nothing is cropped (the installer art is letter-boxed on the mascot's own background
colour). Run:  python scripts/build_assets.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC_T = ROOT / "assets" / "branding" / "chiku-transparent.png"
SRC_B = ROOT / "assets" / "branding" / "chiku-background.jpg"
PKG = ROOT / "src" / "chiku" / "assets"
INSTALLER = ROOT / "packaging" / "installer"
DISCORD = ROOT / "assets" / "branding" / "discord"
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def square(im: Image.Image, fill: tuple[int, ...]) -> Image.Image:
    side = max(im.size)
    canvas = Image.new(im.mode, (side, side), fill)
    canvas.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
    return canvas


def letterbox(im: Image.Image, size: tuple[int, int], fill: tuple[int, int, int]) -> Image.Image:
    """Fit inside `size` without cropping; pad with `fill`."""
    w, h = size
    scale = min(w / im.width, h / im.height)
    resized = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    canvas = Image.new("RGB", size, fill)
    canvas.paste(resized, ((w - resized.width) // 2, (h - resized.height) // 2),
                 resized if resized.mode == "RGBA" else None)
    return canvas


def main() -> int:
    before = {p: sha(p) for p in (SRC_T, SRC_B)}
    PKG.mkdir(parents=True, exist_ok=True)
    INSTALLER.mkdir(parents=True, exist_ok=True)

    t = Image.open(SRC_T)
    if t.mode != "RGBA" or t.getchannel("A").getextrema()[0] != 0:
        print("!! transparent source has no transparency", file=sys.stderr)
        return 1
    b = Image.open(SRC_B).convert("RGB")
    bg = b.getpixel((0, 0))  # the mascot's own background colour (corners are uniform)

    icon_src = square(t, (0, 0, 0, 0))
    icon_src.resize((256, 256), Image.LANCZOS).save(
        PKG / "chiku.ico", sizes=[(s, s) for s in ICO_SIZES])
    icon_src.resize((256, 256), Image.LANCZOS).save(PKG / "mascot-256.png", optimize=True)
    b.resize((480, 480), Image.LANCZOS).save(PKG / "welcome-480.jpg", quality=90)

    # Inno Setup wizard art (100 % and 200 % DPI). Large: left panel; small: top-right corner.
    for scale in (1, 2):
        letterbox(b, (164 * scale, 314 * scale), bg).save(
            INSTALLER / f"wizard-large-{scale * 100}.bmp")
        letterbox(t, (55 * scale, 58 * scale), (255, 255, 255)).save(
            INSTALLER / f"wizard-small-{scale * 100}.bmp")

    # Discord Rich Presence art assets (uploaded by hand in the Discord developer portal, which
    # needs at least 512x512; 1024x1024 is recommended). Keys: "chiku" and "chiku_bg".
    DISCORD.mkdir(parents=True, exist_ok=True)
    icon_src.resize((1024, 1024), Image.LANCZOS).save(DISCORD / "chiku-1024.png", optimize=True)
    square(b, bg).resize((1024, 1024), Image.LANCZOS).save(DISCORD / "chiku-bg-1024.png",
                                                           optimize=True)

    # verification
    ico = Image.open(PKG / "chiku.ico")
    sizes = sorted(ico.info.get("sizes", set()))
    assert [s[0] for s in sizes] == ICO_SIZES, sizes
    ico.size = (256, 256)
    assert ico.convert("RGBA").getchannel("A").getextrema()[0] == 0, "icon lost transparency"
    after = {p: sha(p) for p in (SRC_T, SRC_B)}
    assert before == after, "source artwork changed!"
    print(f"icon sizes: {[s[0] for s in sizes]} (transparency preserved)")
    for d in (DISCORD / "chiku-1024.png", DISCORD / "chiku-bg-1024.png"):
        assert Image.open(d).size == (1024, 1024), d
    for p in sorted(PKG.glob("*")) + sorted(INSTALLER.glob("*")) + sorted(DISCORD.glob("*")):
        print(f"wrote {p.relative_to(ROOT)} ({p.stat().st_size // 1024} KB)")
    print("originals unchanged:", {p.name: h[:12] for p, h in after.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
