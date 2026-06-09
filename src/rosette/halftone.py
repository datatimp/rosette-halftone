"""
Generate rotated halftone SVG files and a composite preview PNG.

Core pipeline (shared between SVG and preview):
  - Pre-blur the channel with Gaussian at radius=cell_size/2.
  - Build a rotated grid covering the full image diagonal.
  - For each grid cell, sample the blurred channel at the dot centre using
    the chosen resampling kernel (nearest, linear, lanczos2, lanczos3, spline36).
  - Map density to radius via area-proportional (sqrt) scaling.
  - Max radius = cell_size * _MAX_R_FACTOR (< 0.5) to prevent adjacent dots
    from overlapping in SVG solid-fill rendering.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from typing import Callable, Generator

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# Maximum dot radius as a fraction of cell size.
# Must stay below 0.5 for SVG rendering: unlike Cairo (which composites
# overlapping circles with antialiasing), SVG stacks solid fills, so
# r >= 0.5 causes adjacent dots to merge into blobs at high density.
_MAX_R_FACTOR = 0.47

RESAMPLE_METHODS = ("nearest", "linear", "lanczos2", "lanczos3", "spline36")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_svg(
    channel_array: np.ndarray,
    image_width: int,
    image_height: int,
    dots_per_row: int,
    angle: float,
    dot_color: str,
    min_radius: float = 0.0,
    resample: str = "linear",
    gamma: float = 1.0,
) -> str:
    """Return an SVG string for one halftone channel."""
    root = ET.Element(
        "svg",
        xmlns="http://www.w3.org/2000/svg",
        width=str(image_width),
        height=str(image_height),
        viewBox=f"0 0 {image_width} {image_height}",
    )
    for px, py, radius in _iter_dots(
        channel_array, image_width, image_height, dots_per_row, angle, min_radius, resample, gamma
    ):
        ET.SubElement(
            root,
            "circle",
            cx=f"{px:.3f}",
            cy=f"{py:.3f}",
            r=f"{radius:.3f}",
            fill=dot_color,
        )
    ET.indent(tree=ET.ElementTree(root), space="")
    return ET.tostring(root, encoding="unicode", xml_declaration=False)


_PREVIEW_SUPERSAMPLE = 2   # render at 2× then downsample for antialiased soft edges
_PREVIEW_CELL_PX = 4       # target dot-cell size in the output sample.png (pixels)


def render_preview(
    channel_arrays: dict[str, np.ndarray],
    active_channels: set[str],
    image_width: int,
    image_height: int,
    dots_per_row: int,
    angles: dict[str, float],
    dot_colors: dict[str, str],
    min_radius: float = 0.0,
    resample: str = "linear",
    gamma: float = 1.0,
) -> Image.Image:
    """Render a composite preview PNG.

    Each channel is rendered as a greyscale ink mask (black dots = ink,
    white = paper), then all four are merged as a CMYK PIL image and
    converted to RGB via Pillow's subtractive CMYK→RGB formula.

    Plates are drawn at _PREVIEW_SUPERSAMPLE× resolution and then
    downsampled to a fixed output size of dots_per_row × _PREVIEW_CELL_PX
    pixels wide. This keeps dot cells at a consistent ~4px diameter in the
    output regardless of input image resolution, so sample.png is always
    readable at 100% zoom for calibration.
    """
    ss = _PREVIEW_SUPERSAMPLE
    render_w = image_width * ss
    render_h = image_height * ss
    render_size = (render_w, render_h)

    # Output size: dots_per_row × _PREVIEW_CELL_PX wide, proportional height.
    out_w = dots_per_row * _PREVIEW_CELL_PX
    out_h = int(out_w * image_height / image_width)

    plates: dict[str, Image.Image] = {}

    for ch in ("c", "m", "y", "k"):
        plate = Image.new("L", render_size, 0)  # 0 = no ink
        if ch in active_channels:
            draw = ImageDraw.Draw(plate)
            for px, py, radius in _iter_dots(
                channel_arrays[ch], image_width, image_height,
                dots_per_row, angles[ch], min_radius, resample, gamma,
            ):
                # Scale dot coordinates and radius into the supersampled space.
                spx = px * ss
                spy = py * ss
                sr = radius * ss
                draw.ellipse(
                    [spx - sr, spy - sr, spx + sr, spy + sr],
                    fill=255,
                )
        plates[ch] = plate

    cmyk = Image.merge("CMYK", [plates["c"], plates["m"], plates["y"], plates["k"]])
    rgb_large = cmyk.convert("RGB")
    # Downsample to the calibration size — Lanczos blends oversized dots into
    # soft-edged circles. The heavy downscale (from 2× input res to dots×4px)
    # gives strong antialiasing and keeps dots optically readable at 100% zoom.
    return rgb_large.resize((out_w, out_h), Image.LANCZOS)


# ---------------------------------------------------------------------------
# Dot iterator
# ---------------------------------------------------------------------------

def _iter_dots(
    channel_array: np.ndarray,
    image_width: int,
    image_height: int,
    dots_per_row: int,
    angle: float,
    min_radius: float,
    resample: str,
    gamma: float = 1.0,
) -> Generator[tuple[float, float, float], None, None]:
    """Yield (cx, cy, radius) for every visible dot in the channel."""
    cell_size = image_width / dots_per_row
    max_r = cell_size * _MAX_R_FACTOR
    r_min_abs = min_radius * max_r

    channel_blurred = _blur_channel(channel_array, radius=cell_size / 2)
    sampler = _make_sampler(resample)

    angle_rad = math.radians(angle)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    diagonal = math.hypot(image_width, image_height)
    half_grid = diagonal
    img_cx = image_width / 2.0
    img_cy = image_height / 2.0
    cols = int(math.ceil(2 * half_grid / cell_size)) + 2
    rows = int(math.ceil(2 * half_grid / cell_size)) + 2

    img_h, img_w = channel_blurred.shape

    for row in range(rows):
        for col in range(cols):
            gx = -half_grid + col * cell_size + cell_size * 0.5
            gy = -half_grid + row * cell_size + cell_size * 0.5

            px = img_cx + gx * cos_a - gy * sin_a
            py = img_cy + gx * sin_a + gy * cos_a

            if px < -cell_size or px > image_width + cell_size:
                continue
            if py < -cell_size or py > image_height + cell_size:
                continue

            density = sampler(channel_blurred, img_w, img_h, px, py)

            # Apply gamma curve to compress/expand tonal range.
            # gamma > 1.0 opens up highlights (smaller dots in light areas).
            # gamma < 1.0 pushes coverage heavier.
            if gamma != 1.0:
                density = density ** gamma

            r2 = density * (max_r ** 2 - r_min_abs ** 2) + r_min_abs ** 2
            radius = math.sqrt(max(r2, 0.0))

            if radius < 0.05:
                continue

            yield px, py, radius


# ---------------------------------------------------------------------------
# Resampling kernels
# ---------------------------------------------------------------------------

def _make_sampler(method: str) -> Callable:
    if method == "nearest":
        return _sample_nearest
    elif method == "linear":
        return _sample_bilinear
    elif method == "lanczos2":
        return lambda arr, w, h, x, y: _sample_lanczos(arr, w, h, x, y, n=2)
    elif method == "lanczos3":
        return lambda arr, w, h, x, y: _sample_lanczos(arr, w, h, x, y, n=3)
    elif method == "spline36":
        return _sample_spline36
    else:
        raise ValueError(f"Unknown resample method: {method!r}")


def _sample_nearest(
    arr: np.ndarray, img_w: int, img_h: int, x: float, y: float
) -> float:
    xi = int(round(x))
    yi = int(round(y))
    if 0 <= xi < img_w and 0 <= yi < img_h:
        return float(arr[yi, xi])
    return 0.0


def _sample_bilinear(
    arr: np.ndarray, img_w: int, img_h: int, x: float, y: float
) -> float:
    x0 = int(math.floor(x))
    y0 = int(math.floor(y))
    x1 = x0 + 1
    y1 = y0 + 1

    if x1 < 0 or x0 >= img_w or y1 < 0 or y0 >= img_h:
        return 0.0

    tx = x - x0
    ty = y - y0

    def get(xi: int, yi: int) -> float:
        if 0 <= xi < img_w and 0 <= yi < img_h:
            return float(arr[yi, xi])
        return 0.0

    return (
        get(x0, y0) * (1 - tx) * (1 - ty)
        + get(x1, y0) * tx * (1 - ty)
        + get(x0, y1) * (1 - tx) * ty
        + get(x1, y1) * tx * ty
    )


def _sinc(x: float) -> float:
    if x == 0.0:
        return 1.0
    px = math.pi * x
    return math.sin(px) / px


def _lanczos_kernel(x: float, n: int) -> float:
    ax = abs(x)
    if ax < n:
        return _sinc(ax) * _sinc(ax / n)
    return 0.0


def _sample_lanczos(
    arr: np.ndarray, img_w: int, img_h: int, x: float, y: float, n: int
) -> float:
    x0 = int(round(x))
    y0 = int(round(y))
    total = 0.0
    weight_sum = 0.0
    for dy in range(-n + 1, n + 1):
        for dx in range(-n + 1, n + 1):
            xi = x0 + dx
            yi = y0 + dy
            wx = _lanczos_kernel(x - xi, n)
            wy = _lanczos_kernel(y - yi, n)
            w = wx * wy
            weight_sum += w
            if 0 <= xi < img_w and 0 <= yi < img_h:
                total += w * float(arr[yi, xi])
    if weight_sum > 0.0:
        return max(0.0, min(1.0, total / weight_sum))
    return 0.0


def _spline36_kernel(x: float) -> float:
    d = abs(x)
    if d <= 1.0:
        return (((247.0 * d - 453.0) * d - 3.0) * d + 209.0) / 209.0
    if d <= 2.0:
        return (((-114.0 * d + 612.0) * d - 1038.0) * d + 540.0) / 209.0
    if d <= 3.0:
        return (((19.0 * d - 159.0) * d + 434.0) * d - 384.0) / 209.0
    return 0.0


def _sample_spline36(
    arr: np.ndarray, img_w: int, img_h: int, x: float, y: float
) -> float:
    x0 = int(round(x))
    y0 = int(round(y))
    total = 0.0
    weight_sum = 0.0
    for dy in range(-2, 4):
        for dx in range(-2, 4):
            xi = x0 + dx
            yi = y0 + dy
            wx = _spline36_kernel(x - xi)
            wy = _spline36_kernel(y - yi)
            w = wx * wy
            weight_sum += w
            if 0 <= xi < img_w and 0 <= yi < img_h:
                total += w * float(arr[yi, xi])
    if weight_sum > 0.0:
        return max(0.0, min(1.0, total / weight_sum))
    return 0.0


# ---------------------------------------------------------------------------
# Blur helper
# ---------------------------------------------------------------------------

def _blur_channel(arr: np.ndarray, radius: float) -> np.ndarray:
    """Apply Gaussian blur to a float32 channel array via Pillow."""
    pil_img = Image.fromarray((arr * 255).astype(np.uint8), mode="L")
    pil_img = pil_img.filter(ImageFilter.GaussianBlur(radius=radius))
    return np.asarray(pil_img, dtype=np.float32) / 255.0
