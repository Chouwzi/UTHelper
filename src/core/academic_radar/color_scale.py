from __future__ import annotations

from typing import Tuple

# Defined color stops: (threshold, (R, G, B))
COLOR_STOPS: list[tuple[float, tuple[int, int, int]]] = [
    (0.0, (0xEF, 0x44, 0x44)),    # #EF4444 Đỏ
    (40.0, (0xF9, 0x73, 0x16)),   # #F97316 Đỏ-cam
    (55.0, (0xEA, 0xB3, 0x08)),   # #EAB308 Vàng
    (70.0, (0x84, 0xCC, 0x16)),   # #84CC16 Vàng-xanh
    (85.0, (0x22, 0xC5, 0x5E)),   # #22C55E Xanh
    (100.0, (0x10, 0xB9, 0x81)),  # #10B981 Xanh đậm
]


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    clean = hex_color.lstrip("#")
    return int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02X}{g:02X}{b:02X}"


def score_to_color(score: float | None) -> str:
    """Interpolates a continuous hex color string for a score between 0 and 100.

    Clamps score to [0.0, 100.0]. Returns neutral gray for None.
    """
    if score is None:
        return "#94A3B8"  # Slate-400 neutral fallback

    clamped = max(0.0, min(100.0, float(score)))

    # Find the two bounding stops
    for i in range(len(COLOR_STOPS) - 1):
        s1, c1 = COLOR_STOPS[i]
        s2, c2 = COLOR_STOPS[i + 1]
        if s1 <= clamped <= s2:
            if s2 == s1:
                return _rgb_to_hex(*c1)
            t = (clamped - s1) / (s2 - s1)
            r = int(round(c1[0] + (c2[0] - c1[0]) * t))
            g = int(round(c1[1] + (c2[1] - c1[1]) * t))
            b = int(round(c1[2] + (c2[2] - c1[2]) * t))
            return _rgb_to_hex(r, g, b)

    return _rgb_to_hex(*COLOR_STOPS[-1][1])


def score_to_status(score: float | None) -> str:
    """Maps a score (0..100) to discrete status wording."""
    if score is None:
        return "Chưa có dữ liệu"

    clamped = max(0.0, min(100.0, float(score)))
    if clamped < 40.0:
        return "Nguy Hiểm"
    if clamped < 55.0:
        return "Cầm Cự"
    if clamped < 70.0:
        return "Tạm Ổn"
    if clamped < 85.0:
        return "Vững Vàng"
    return "Nổi Bật"


def hex_with_alpha(hex_color: str, alpha: float) -> str:
    """Converts a hex color to #AARRGGBB with the given alpha (0.0 to 1.0)."""
    if not hex_color:
        return "#00000000"
    clean = hex_color.lstrip("#")
    if len(clean) == 8:
        clean = clean[2:]
    elif len(clean) != 6:
        return hex_color
    try:
        r = int(clean[0:2], 16)
        g = int(clean[2:4], 16)
        b = int(clean[4:6], 16)
        a = int(round(max(0.0, min(1.0, alpha)) * 255))
        return f"#{a:02X}{r:02X}{g:02X}{b:02X}"
    except (ValueError, TypeError):
        return hex_color


# Luminous, airy radial gradient stops for the student's data polygon:
# Soft translucent aura (12% - 27% opacity) avoiding dense "heat map" effect
POLYGON_RADIAL_GRADIENT_COLORS: list[str] = [
    "#20F43F5E",  # Center 0: Soft Rose/Coral ~12% opacity (ethereal, non-heat-map)
    "#25FB923C",  # 35: Soft Amber/Orange ~15% opacity
    "#28FBBF24",  # 60: Subtle Warm Gold ~16% opacity
    "#344ADE80",  # 80: Fresh Mint/Lime ~20% opacity
    "#4510B981",  # 100: Radiant Emerald Green ~27% opacity
]

POLYGON_RADIAL_GRADIENT_STOPS: list[float] = [0.0, 0.35, 0.60, 0.80, 1.0]

# Subtle neutral background grid fill
RADAR_BG_HEXAGON_TINT: str = "#08808080"  # 3% subtle neutral tint for radar grid
