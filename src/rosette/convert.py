"""
Load an image and return four normalised CMYK channel arrays (float32, 0–1).

Two conversion profiles are supported (select via the `profile` argument):

  "math" (default)
    Direct geometric RGB→CMYK formula. Produces moderate, well-distributed
    ink values with neutral colour balance. Recommended for screen printing
    and risograph. No ICC profiles required.

      K = 1 − max(R, G, B)
      C = (1 − R − K) / (1 − K)   (0 where K = 1)
      M = (1 − G − K) / (1 − K)
      Y = (1 − B − K) / (1 − K)

  "swop"
    ICC-accurate conversion via ImageCms (lcms2) using the bundled Artifex
    CMYK SWOP profile. Calibrated for coated offset stock (300% TAC). Ink
    values are heavy by design; use --tac and --gamma to compensate for
    screen/riso workflows. Best when ICC round-trip accuracy matters.

In both cases:
  - TAC limiting scales down any pixel where C+M+Y+K exceeds the limit.
  - Output is float32 in [0, 1].

Bundled ICC profiles (AGPL-3.0, Artifex Software):
  profiles/sRGB.icc  — Artifex Software sRGB ICC Profile
  profiles/SWOP.icc  — Artifex CMYK SWOP Profile
  Source: GPL Ghostscript  https://www.ghostscript.com/
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms

_PROFILES_DIR = Path(__file__).parent / "profiles"
_SRGB_PROFILE = str(_PROFILES_DIR / "sRGB.icc")
_SWOP_PROFILE = str(_PROFILES_DIR / "SWOP.icc")

# Default TAC as a percentage (220 = 220%).
DEFAULT_TAC = 220

PROFILE_CHOICES = ("math", "swop")


def load_cmyk_channels(
    path: str,
    tac: float = DEFAULT_TAC,
    profile: str = "math",
) -> tuple[dict[str, np.ndarray], tuple[int, int]]:
    """Return (channels, (width, height)).

    channels keys: 'c', 'm', 'y', 'k'
    Each array has shape (height, width), dtype float32, range [0, 1].

    tac:     Total Area Coverage limit as a percentage (e.g. 220 = 220%).
             Pixels where C+M+Y+K exceeds this are scaled down proportionally.
    profile: "math" for direct geometric conversion (default, recommended for
             screen/riso), or "swop" for ICC-accurate SWOP conversion.
    """
    img = Image.open(path)

    if img.mode == "CMYK":
        # Already CMYK — use as-is regardless of profile setting.
        channels = {
            key: np.asarray(ch, dtype=np.float32) / 255.0
            for key, ch in zip("cmyk", img.split())
        }
        width, height = img.size
    elif profile == "swop":
        cmyk_img = _convert_to_cmyk_swop(img)
        channels = {
            key: np.asarray(ch, dtype=np.float32) / 255.0
            for key, ch in zip("cmyk", cmyk_img.split())
        }
        width, height = cmyk_img.size
    else:
        # "math" — direct geometric formula, no ICC needed.
        channels, (width, height) = _convert_to_cmyk_math(img)

    channels = _apply_tac(channels, tac / 100)
    return channels, (width, height)


def _apply_tac(
    channels: dict[str, np.ndarray],
    tac: float,
) -> dict[str, np.ndarray]:
    """Scale down pixels where total ink exceeds the TAC limit.

    Colour balance is preserved: all four channels are multiplied by the
    same per-pixel scale factor, so hue is unchanged.
    """
    total = channels["c"] + channels["m"] + channels["y"] + channels["k"]
    scale = np.where(total > tac, tac / np.maximum(total, 1e-6), 1.0)
    return {ch: channels[ch] * scale for ch in "cmyk"}


def _convert_to_cmyk_math(
    img: Image.Image,
) -> tuple[dict[str, np.ndarray], tuple[int, int]]:
    """Direct geometric RGB→CMYK conversion (no ICC profile).

    K = 1 − max(R, G, B)
    C = (1 − R − K) / (1 − K)
    M = (1 − G − K) / (1 − K)
    Y = (1 − B − K) / (1 − K)

    Produces moderate, neutral ink values — well suited for screen printing
    and risograph where ink weights differ from commercial offset stock.
    """
    if img.mode != "RGB":
        img = img.convert("RGB")

    arr = np.asarray(img, dtype=np.float32) / 255.0
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

    k = 1.0 - np.maximum(np.maximum(r, g), b)
    denom = np.where(k < 1.0, 1.0 - k, 1.0)  # avoid division by zero in pure-black pixels

    c = np.where(k < 1.0, (1.0 - r - k) / denom, 0.0)
    m = np.where(k < 1.0, (1.0 - g - k) / denom, 0.0)
    y = np.where(k < 1.0, (1.0 - b - k) / denom, 0.0)

    channels = {
        "c": np.clip(c, 0.0, 1.0).astype(np.float32),
        "m": np.clip(m, 0.0, 1.0).astype(np.float32),
        "y": np.clip(y, 0.0, 1.0).astype(np.float32),
        "k": np.clip(k, 0.0, 1.0).astype(np.float32),
    }
    width, height = img.size
    return channels, (width, height)


def _convert_to_cmyk_swop(img: Image.Image) -> Image.Image:
    """Convert any non-CMYK image to CMYK using the bundled SWOP ICC profile."""
    if img.mode != "RGB":
        img = img.convert("RGB")

    embedded_icc = img.info.get("icc_profile")
    input_profile = (
        ImageCms.ImageCmsProfile(io.BytesIO(embedded_icc))
        if embedded_icc
        else _SRGB_PROFILE
    )

    xform = ImageCms.buildTransform(
        input_profile,
        _SWOP_PROFILE,
        "RGB",
        "CMYK",
        renderingIntent=ImageCms.Intent.PERCEPTUAL,
    )
    return ImageCms.applyTransform(img, xform)
