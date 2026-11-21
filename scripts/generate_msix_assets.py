"""Deterministically generate and verify Microsoft Store MSIX PNG tile assets."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

from PIL import Image


MSIX_ASSET_SPECS: dict[str, tuple[int, int]] = {
    "Square44x44Logo.png": (44, 44),
    "StoreLogo.png": (50, 50),
    "Square150x150Logo.png": (150, 150),
    "Wide310x150Logo.png": (310, 150),
}

# Leave balanced breathing room on wide rectangular tiles while keeping square
# tiles crisp and full-bleed along the limiting axis.
_WIDE_TILE_PADDING_RATIO = 0.08


class MsixAssetError(RuntimeError):
    """Raised when MSIX visual assets cannot be generated or validated."""


def _render_centered_rgba(source: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    """Scale ``source`` preserving aspect ratio and center on a transparent RGBA canvas."""
    target_w, target_h = target_size
    if target_w <= 0 or target_h <= 0:
        raise MsixAssetError(f"Invalid target asset dimensions: {target_size}")

    rgba = source.convert("RGBA")
    src_w, src_h = rgba.size
    if src_w <= 0 or src_h <= 0:
        raise MsixAssetError(f"Invalid source image dimensions: {rgba.size}")

    padding_ratio = _WIDE_TILE_PADDING_RATIO if target_w != target_h else 0.0
    max_w = max(1, int(round(target_w * (1.0 - (2.0 * padding_ratio)))))
    max_h = max(1, int(round(target_h * (1.0 - (2.0 * padding_ratio)))))

    scale = min(max_w / src_w, max_h / src_h)
    scaled_w = max(1, min(target_w, int(round(src_w * scale))))
    scaled_h = max(1, min(target_h, int(round(src_h * scale))))

    resampling = getattr(Image, "Resampling", Image).LANCZOS
    resized = rgba.resize((scaled_w, scaled_h), resample=resampling)

    canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    offset_x = (target_w - scaled_w) // 2
    offset_y = (target_h - scaled_h) // 2
    canvas.paste(resized, (offset_x, offset_y), resized)
    return canvas


def generate_msix_assets(
    source_icon: Path,
    output_dir: Path,
) -> dict[str, Path]:
    """Generate all required Microsoft Store MSIX PNG assets deterministically."""
    resolved_source = Path(source_icon).resolve()
    if not resolved_source.is_file():
        raise MsixAssetError(f"Source icon does not exist: {resolved_source}")

    resolved_out = Path(output_dir).resolve()
    resolved_out.mkdir(parents=True, exist_ok=True)

    generated: dict[str, Path] = {}
    try:
        with Image.open(resolved_source) as raw_icon:
            raw_icon.load()
            for filename, target_size in MSIX_ASSET_SPECS.items():
                rendered = _render_centered_rgba(raw_icon, target_size)
                destination = resolved_out / filename
                rendered.save(destination, format="PNG", compress_level=6, optimize=False)
                generated[filename] = destination
    except MsixAssetError:
        raise
    except OSError as exc:
        raise MsixAssetError(f"Failed to generate MSIX assets from {resolved_source}: {exc}") from exc

    issues = inspect_msix_assets(resolved_out)
    if issues:
        raise MsixAssetError("; ".join(issues))
    return generated


def inspect_msix_assets(assets_dir: Path) -> tuple[str, ...]:
    """Return every detected visual-asset compliance issue in ``assets_dir``."""
    resolved_dir = Path(assets_dir).resolve()
    if not resolved_dir.is_dir():
        return (f"MSIX assets directory does not exist: {resolved_dir}",)

    issues: list[str] = []
    for filename, expected_size in MSIX_ASSET_SPECS.items():
        candidate = resolved_dir / filename
        if not candidate.is_file():
            issues.append(f"Missing required MSIX asset: {filename}")
            continue
        try:
            with Image.open(candidate) as img:
                if img.format != "PNG":
                    issues.append(f"{filename} must be PNG, found {img.format}")
                if img.size != expected_size:
                    issues.append(
                        f"{filename} dimensions {img.size[0]}x{img.size[1]} "
                        f"do not match required {expected_size[0]}x{expected_size[1]}"
                    )
        except OSError as exc:
            issues.append(f"{filename} could not be read as an image: {exc}")
    return tuple(issues)


def main(argv: Sequence[str] | None = None) -> int:
    default_source = Path(__file__).resolve().parents[1] / "src" / "assets" / "icon.png"
    parser = argparse.ArgumentParser(
        description="Generate or verify scaled Microsoft Store MSIX PNG visual assets."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=default_source,
        help="Path to the source PNG icon (defaults to src/assets/icon.png).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Target Assets directory where MSIX PNGs are written or verified.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify existing PNG dimensions in --output-dir without generating files.",
    )
    args = parser.parse_args(argv)

    try:
        if args.verify_only:
            issues = inspect_msix_assets(args.output_dir)
            if issues:
                raise MsixAssetError("; ".join(issues))
            print(f"MSIX visual assets verified: {args.output_dir.resolve()}")
        else:
            generated = generate_msix_assets(args.source, args.output_dir)
            summary = ", ".join(
                f"{name} ({MSIX_ASSET_SPECS[name][0]}x{MSIX_ASSET_SPECS[name][1]})"
                for name in generated
            )
            print(f"MSIX visual assets generated in {args.output_dir.resolve()}: {summary}")
    except MsixAssetError as exc:
        print(f"MSIX asset error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
