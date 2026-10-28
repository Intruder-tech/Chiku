import hashlib

import pytest
from PIL import Image

from chiku import __version__, branding

ORIGINALS = {
    branding.CHIKU_MASCOT_TRANSPARENT:
        "b39be98b942c7657397a4bed76f5b0d3e6cf8be2c4b386725200ae4b8ae3c4c7",
    branding.CHIKU_MASCOT_BACKGROUND:
        "7a1356f66aa9e1530062381873a08e6030bc231c2bed908a32a8f38f4e94fa2d",
}


@pytest.mark.parametrize("path", list(ORIGINALS))
def test_original_artwork_is_unchanged(path) -> None:  # noqa: ANN001
    assert hashlib.sha256(path.read_bytes()).hexdigest() == ORIGINALS[path]


def test_asset_mapping_matches_actual_transparency() -> None:
    t = Image.open(branding.CHIKU_MASCOT_TRANSPARENT)
    assert t.mode == "RGBA" and t.getchannel("A").getextrema()[0] == 0
    b = Image.open(branding.CHIKU_MASCOT_BACKGROUND)
    assert "A" not in b.getbands()


def test_runtime_assets_exist_and_icon_is_complete() -> None:
    for name in (branding.ICON, branding.AVATAR, branding.WELCOME):
        assert branding.asset(name).exists(), name
    ico = Image.open(branding.asset(branding.ICON))
    sizes = sorted(s[0] for s in ico.info["sizes"])
    assert sizes[0] == 16 and sizes[-1] == 256
    ico.size = (256, 256)
    assert ico.convert("RGBA").getchannel("A").getextrema()[0] == 0  # still transparent
    avatar = Image.open(branding.asset(branding.AVATAR))
    assert avatar.mode == "RGBA" and avatar.size == (256, 256)


def test_version_is_consistent() -> None:
    import tomllib
    from pathlib import Path

    data = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert data["project"]["version"] == __version__
    assert data["project"]["license"] == "GPL-3.0-or-later"
