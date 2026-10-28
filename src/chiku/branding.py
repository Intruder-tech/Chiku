"""Single source of truth for Chiku's brand assets.

Originals (never modified) live in the repository at ``assets/branding/``:

* ``chiku-transparent.png`` -- CHIKU_MASCOT_TRANSPARENT (RGBA, transparent background):
  app icon source, avatar, listening/speaking animation, compact branding.
* ``chiku-background.jpg``  -- CHIKU_MASCOT_BACKGROUND (RGB, grey background):
  installer welcome art, README, onboarding/welcome screens, empty states.

``scripts/build_assets.py`` derives the runtime files below (icon, avatar, welcome image) into
``src/chiku/assets/`` so the installed app doesn't need the repository. UI code must use the
helpers here instead of hard-coding paths.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PKG_ASSETS = Path(__file__).resolve().parent / "assets"
REPO_BRANDING = Path(__file__).resolve().parents[2] / "assets" / "branding"

# Source artwork (repository only; used by scripts/build_assets.py).
CHIKU_MASCOT_TRANSPARENT = REPO_BRANDING / "chiku-transparent.png"
CHIKU_MASCOT_BACKGROUND = REPO_BRANDING / "chiku-background.jpg"

# Runtime files, shipped inside the package.
ICON = "chiku.ico"
AVATAR = "mascot-256.png"  # derived from CHIKU_MASCOT_TRANSPARENT
WELCOME = "welcome-480.jpg"  # derived from CHIKU_MASCOT_BACKGROUND

BRAND_YELLOW = "#f2c94c"  # sampled from the mascot; accent for the active mic only
APP_USER_MODEL_ID = "IntruderTech.Chiku"


def asset(name: str) -> Path:
    """Path to a runtime asset, both from source and inside a PyInstaller bundle."""
    base = Path(getattr(sys, "_MEIPASS", "")) / "chiku" / "assets"
    if base.is_dir() and (base / name).exists():
        return base / name
    return _PKG_ASSETS / name
