"""Design tokens for timetable image generation.

Centralizes colors, typography sizes, spacing, and radii into named presets.
Import `get_preset` and pass the result to renderers.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

RGB = Tuple[int, int, int]


@dataclass(frozen=True)
class DesignTokens:
    name: str

    # Canvas
    canvas_w: int = 560
    canvas_h: int = 1900
    canvas_bg: RGB = (248, 250, 252)

    # Card
    card_bg: RGB = (255, 255, 255)
    card_outline: RGB = (229, 231, 235)
    card_outline_w: int = 2
    card_radius: int = 24
    card_pad: int = 24

    # Timeline grid
    grid_line: RGB = (229, 231, 235)
    grid_label: RGB = (148, 163, 184)
    time_col_w: int = 70

    # Event blocks
    event_radius: int = 10
    course_palette: Tuple[RGB, ...] = (
        (255, 209, 220), (255, 229, 180), (255, 255, 186), (186, 255, 201),
        (186, 225, 255), (218, 193, 255), (255, 214, 165), (207, 244, 252),
    )
    school_name_color: RGB = (28, 36, 48)
    school_loc_color: RGB = (71, 85, 105)
    cal_name_color: RGB = (255, 255, 255)
    cal_loc_color: RGB = (230, 245, 255)

    # Title / header
    title_color: RGB = (15, 23, 42)

    # Font sizes
    font_title: int = 42
    font_header: int = 26
    font_body: int = 22
    font_small: int = 18

    # Bundle
    bundle_bg: RGB = (245, 247, 250)
    bundle_gap: int = 24
    bundle_pad: int = 24
    bundle_header_h: int = 120
    bundle_title_size: int = 34
    bundle_label_size: int = 22
    bundle_label_color: RGB = (71, 85, 105)

    # Layout zones (y-coords inside card)
    title_y: int = 60
    timeline_top: int = 150
    timeline_bottom: int = 1820
    left_margin: int = 90
    right_margin: int = 60  # subtracted from W

    # Samsung-daily specific
    layout_mode: str = "card"  # "card" or "samsung-daily"
    show_half_hour_dots: bool = False
    now_indicator: bool = False
    now_indicator_color: RGB = (234, 67, 53)
    show_event_subtitle: bool = True
    hour_label_style: str = "24h"  # "24h" or "ampm"


# --------------- Presets ---------------

_CLASSIC = DesignTokens(name="classic")

_SAMSUNG_DARK = DesignTokens(
    name="samsung-dark",
    canvas_bg=(18, 18, 20),
    card_bg=(30, 30, 34),
    card_outline=(55, 55, 60),
    card_outline_w=1,
    card_radius=20,
    grid_line=(50, 50, 55),
    grid_label=(120, 120, 130),
    event_radius=12,
    course_palette=(
        (120, 80, 100), (130, 100, 70), (110, 110, 60), (60, 120, 80),
        (60, 100, 140), (100, 80, 150), (140, 100, 60), (60, 120, 130),
    ),
    school_name_color=(230, 235, 245),
    school_loc_color=(170, 175, 185),
    cal_name_color=(255, 255, 255),
    cal_loc_color=(200, 220, 240),
    title_color=(235, 238, 245),
    bundle_bg=(12, 12, 14),
    bundle_label_color=(170, 175, 185),
)

_MINIMAL_LIGHT = DesignTokens(
    name="minimal-light",
    canvas_bg=(255, 255, 255),
    card_bg=(255, 255, 255),
    card_outline=(235, 235, 235),
    card_outline_w=1,
    card_radius=12,
    card_pad=20,
    grid_line=(240, 240, 240),
    grid_label=(180, 180, 180),
    event_radius=6,
    course_palette=(
        (230, 220, 230), (230, 225, 210), (230, 230, 210), (210, 230, 215),
        (210, 225, 240), (220, 215, 240), (235, 220, 205), (210, 235, 240),
    ),
    school_name_color=(50, 50, 55),
    school_loc_color=(120, 120, 125),
    cal_name_color=(255, 255, 255),
    cal_loc_color=(230, 240, 250),
    title_color=(40, 40, 45),
    font_title=38,
    font_header=24,
    font_body=20,
    font_small=16,
    bundle_bg=(250, 250, 250),
    bundle_label_color=(140, 140, 145),
)

_SAMSUNG_DAILY = DesignTokens(
    name="samsung-daily",
    layout_mode="samsung-daily",
    canvas_bg=(255, 255, 255),
    card_bg=(255, 255, 255),
    card_outline=(255, 255, 255),
    card_outline_w=0,
    card_radius=0,
    card_pad=0,
    grid_line=(225, 225, 228),
    grid_label=(155, 155, 162),
    time_col_w=52,
    event_radius=10,
    course_palette=(
        (255, 207, 195),  # salmon/peach
        (187, 222, 251),  # sky blue
        (215, 195, 238),  # lavender
        (200, 230, 201),  # sage green
        (255, 245, 196),  # banana yellow
        (248, 187, 208),  # flamingo pink
        (178, 235, 242),  # teal
        (255, 224, 178),  # tangerine
    ),
    school_name_color=(38, 38, 42),
    school_loc_color=(100, 100, 108),
    cal_name_color=(38, 38, 42),
    cal_loc_color=(100, 100, 108),
    title_color=(28, 28, 32),
    font_title=34,
    font_header=22,
    font_body=20,
    font_small=15,
    title_y=30,
    timeline_top=150,
    timeline_bottom=1840,
    left_margin=10,
    right_margin=40,
    show_half_hour_dots=True,
    now_indicator=True,
    now_indicator_color=(234, 67, 53),
    show_event_subtitle=False,
    hour_label_style="ampm",
    bundle_bg=(245, 245, 248),
    bundle_label_color=(100, 100, 108),
)

_MODERN = DesignTokens(
    name="modern",
    layout_mode="modern",
    canvas_w=490,
    canvas_h=1200,
    canvas_bg=(248, 249, 252),
    card_bg=(255, 255, 255),
    card_outline=(230, 233, 238),
    card_outline_w=1,
    card_radius=12,
    card_pad=0,
    grid_line=(232, 235, 240),
    grid_label=(140, 148, 165),
    time_col_w=64,
    event_radius=6,
    course_palette=(
        (66, 133, 244),    # google blue
        (52, 168, 83),     # google green
        (234, 134, 60),    # warm orange
        (154, 110, 188),   # soft purple
        (230, 74, 85),     # coral red
        (42, 187, 172),    # teal
        (245, 180, 60),    # golden yellow
        (120, 144, 180),   # slate
    ),
    school_name_color=(30, 38, 50),
    school_loc_color=(68, 68, 68),
    cal_name_color=(30, 38, 50),
    cal_loc_color=(68, 68, 68),
    title_color=(20, 28, 42),
    font_title=26,
    font_header=18,
    font_body=16,
    font_small=14,
    bundle_bg=(248, 249, 252),
    bundle_gap=16,
    bundle_pad=28,
    bundle_header_h=100,
    bundle_title_size=28,
    bundle_label_size=20,
    bundle_label_color=(100, 110, 130),
    title_y=10,
    timeline_top=130,
    timeline_bottom=1100,
    left_margin=28,
    right_margin=28,
    now_indicator=True,
    now_indicator_color=(234, 67, 53),
)

PRESETS: Dict[str, DesignTokens] = {
    "classic": _CLASSIC,
    "samsung-dark": _SAMSUNG_DARK,
    "samsung-daily": _SAMSUNG_DAILY,
    "minimal-light": _MINIMAL_LIGHT,
    "modern": _MODERN,
}

PRESET_NAMES = list(PRESETS.keys())


def get_preset(name: str | None = None) -> DesignTokens:
    """Return a preset by name. None / unknown → classic."""
    if name is None:
        return _CLASSIC
    if name not in PRESETS:
        raise ValueError(f"Unknown preset '{name}'. Choose from: {PRESET_NAMES}")
    return PRESETS[name]
