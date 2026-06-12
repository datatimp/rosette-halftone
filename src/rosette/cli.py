import sys
import zipfile
from pathlib import Path

import click

from .convert import DEFAULT_TAC, PROFILE_CHOICES, load_cmyk_channels
from .halftone import RESAMPLE_METHODS, generate_svg, render_preview

PRESETS = {
    "us":    {"c": 15, "m": 75, "y":  0, "k": 45},
    "eu":    {"c": 15, "m": 45, "y":  0, "k": 75},
    "alt-a": {"c": 15, "m": 45, "y": 30, "k": 45},
    "alt-b": {"c": 45, "m": 15, "y":  0, "k": 75},
    "alt-c": {"c": 45, "m": 75, "y":  0, "k": 15},
    "alt-d": {"c": 75, "m": 15, "y":  0, "k": 45},
    "alt-e": {"c": 75, "m": 45, "y":  0, "k": 15},
    "alt-f": {"c": 75, "m": 15, "y": 60, "k": 45},
}

CHANNEL_NAMES = ("c", "m", "y", "k")
CHANNEL_LABELS = {"c": "cyan", "m": "magenta", "y": "yellow", "k": "black"}
CHANNEL_COLORS = {
    "c": "#00FFFF",
    "m": "#FF00FF",
    "y": "#FFFF00",
    "k": "#000000",
}


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("image", type=click.Path(exists=True, dir_okay=False))
@click.option("--dots", default=100, show_default=True, help="Dots per row.")
@click.option(
    "--preset",
    default="us",
    show_default=True,
    type=click.Choice(list(PRESETS), case_sensitive=False),
    help="Screen angle preset.",
)
@click.option(
    "--angles",
    default=None,
    type=float,
    nargs=4,
    metavar="C M Y K",
    help="Manual per-channel angles (degrees). Overrides --preset.",
)
@click.option(
    "--min-radius",
    default=0.0,
    show_default=True,
    help="Minimum dot radius (fraction of cell size).",
)
@click.option(
    "--channels",
    default="cmyk",
    show_default=True,
    help="Channels to generate: any combination of c, m, y, k (e.g. cmy, k, cmyk).",
)
@click.option(
    "--output",
    default="./output",
    show_default=True,
    type=click.Path(file_okay=False),
    help="Output directory.",
)
@click.option(
    "--resample",
    default="linear",
    show_default=True,
    type=click.Choice(RESAMPLE_METHODS, case_sensitive=False),
    help="Resampling kernel for dot density sampling.",
)
@click.option(
    "--tac",
    default=DEFAULT_TAC,
    show_default=True,
    help=(
        "Total Area Coverage limit as a percentage (e.g. 220). "
        "Pixels where C+M+Y+K exceeds this are scaled down proportionally. "
        "SWOP default is 300; screen printing typically 160–200; risograph 180–220."
    ),
)
@click.option(
    "--gamma",
    default=1.5,
    show_default=True,
    help=(
        "Dot density curve. >1.0 opens highlights (smaller dots in light areas); "
        "<1.0 pushes heavier. 1.5 is a good default for SWOP-converted images; "
        "try 2.0 for a lighter, more open look."
    ),
)
@click.option(
    "--profile",
    default="math",
    show_default=True,
    type=click.Choice(PROFILE_CHOICES, case_sensitive=False),
    help=(
        "CMYK conversion method. 'math' uses a direct geometric formula — "
        "neutral, moderate ink values, recommended for screen printing and riso. "
        "'swop' uses the bundled ICC SWOP profile — heavy ink, calibrated for "
        "coated offset stock."
    ),
)
@click.option("--zip", "use_zip", is_flag=True, help="Package output into a ZIP file.")
def main(image, dots, preset, angles, min_radius, channels, resample, tac, gamma, profile, output, use_zip):
    """Convert a color image to ICC-accurate CMYK halftone SVG separations.

    IMAGE is the path to a PNG, JPG, TIFF, or WEBP file.
    """
    active_channels = _parse_channels(channels)
    if not active_channels:
        raise click.BadParameter("no valid channels specified", param_hint="--channels")

    screen_angles = _resolve_angles(preset, angles)

    click.echo(f"Loading {image} (profile: {profile})...")
    channel_arrays, (width, height) = load_cmyk_channels(image, tac=tac, profile=profile)

    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)

    svg_files: dict[str, str] = {}

    for ch in CHANNEL_NAMES:
        if ch not in active_channels:
            continue
        angle = screen_angles[ch]
        color = CHANNEL_COLORS[ch]
        label = CHANNEL_LABELS[ch]
        click.echo(f"  Generating {label} (angle {angle} deg)...")
        svg = generate_svg(
            channel_array=channel_arrays[ch],
            image_width=width,
            image_height=height,
            dots_per_row=dots,
            angle=angle,
            dot_color=color,
            min_radius=min_radius,
            resample=resample,
            gamma=gamma,
        )
        svg_files[f"{label}.svg"] = svg

    click.echo("  Rendering sample.png...")
    preview = render_preview(
        channel_arrays=channel_arrays,
        active_channels=active_channels,
        image_width=width,
        image_height=height,
        dots_per_row=dots,
        angles=screen_angles,
        dot_colors=CHANNEL_COLORS,
        min_radius=min_radius,
        resample=resample,
        gamma=gamma,
    )


    if use_zip:
        image_stem = Path(image).stem
        zip_path = output_path / f"{image_stem}_rosette.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for filename, content in svg_files.items():
                zf.writestr(filename, content)
            import io as _io
            buf = _io.BytesIO()
            preview.save(buf, format="PNG")
            zf.writestr("sample.png", buf.getvalue())
        click.echo(f"Saved {zip_path}")
    else:
        for filename, content in svg_files.items():
            out_file = output_path / filename
            out_file.write_text(content, encoding="utf-8")
            click.echo(f"Saved {out_file}")
        preview_path = output_path / "sample.png"
        preview.save(str(preview_path))
        click.echo(f"Saved {preview_path}")

    click.echo("Done.")


def _parse_channels(channels_str: str) -> set[str]:
    valid = set(CHANNEL_NAMES)
    return {ch for ch in channels_str.lower() if ch in valid}


def _resolve_angles(preset: str, angles) -> dict[str, float]:
    if angles is not None:
        c, m, y, k = angles
        return {"c": c, "m": m, "y": y, "k": k}
    return dict(PRESETS[preset.lower()])
