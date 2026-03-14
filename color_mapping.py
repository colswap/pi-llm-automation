"""Deterministic color mapping for school courses and Google Calendar events.

School course colors are chosen from a palette that does NOT overlap
with Google Calendar's 11 event colors, ensuring visual distinction.

Google Calendar colorId palette (for reference – do NOT use these for school):
    1: Lavender   #7986cb     2: Sage       #33b679
    3: Grape      #8e24aa     4: Flamingo   #e67c73
    5: Banana     #f6bf26     6: Tangerine  #f4511e
    7: Peacock    #039be5     8: Graphite   #616161
    9: Blueberry  #3f51b5    10: Basil      #0b8043
   11: Tomato     #d50000

School palette: hand-picked hues that sit between Google's hues on the
colour wheel, with distinct saturation/lightness so they never look like
a Google event even at a glance.
"""

from __future__ import annotations
import json, subprocess, os
from pathlib import Path
from typing import Dict, Tuple

RGB = Tuple[int, int, int]

# ── School course palette (non-overlapping with Google's 11 event colors) ──
# Each color is chosen to be visually distinct from all Google Calendar colors.
# Sorted by hue to give a pleasant spread when many courses are assigned.
SCHOOL_PALETTE: list[RGB] = [
    (102, 201, 185),  # fresh light teal
    (244, 186, 118),  # fresh light amber
    (199, 158, 222),  # fresh light lavender
    (129, 207, 147),  # fresh light green
    (138, 170, 235),  # fresh light periwinkle
    (242, 149, 175),  # fresh light rose
    (183, 210, 114),  # fresh light olive
    (227, 164, 126),  # fresh light terracotta
    (109, 191, 223),  # fresh light sky
]


def hex_to_rgb(h: str) -> RGB:
    h = h.lstrip('#')
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def school_course_colors(course_names: list[str]) -> Dict[str, RGB]:
    """Return deterministic {course_name: RGB} mapping.

    Courses are sorted alphabetically then assigned palette colors by index
    (wrapping if more courses than palette entries).  Sorting ensures the
    same course always gets the same color regardless of which day we render.
    """
    names = sorted(set(course_names))
    return {n: SCHOOL_PALETTE[i % len(SCHOOL_PALETTE)] for i, n in enumerate(names)}


def school_course_colors_hex(course_names: list[str]) -> Dict[str, str]:
    """Same as school_course_colors but returns hex strings."""
    return {k: rgb_to_hex(*v) for k, v in school_course_colors(course_names).items()}


# ── Google Calendar color map ──

_ENV_PATH = Path('/home/user/.agent-config/.env')
_ACCOUNT = 'user@gmail.com'

# Fallback map if gog CLI is unavailable
_GOOGLE_FALLBACK: Dict[str, str] = {
    '1':  '#7986cb',  # Lavender
    '2':  '#33b679',  # Sage
    '3':  '#8e24aa',  # Grape
    '4':  '#e67c73',  # Flamingo
    '5':  '#f6bf26',  # Banana
    '6':  '#f4511e',  # Tangerine
    '7':  '#039be5',  # Peacock
    '8':  '#616161',  # Graphite
    '9':  '#3f51b5',  # Blueberry
    '10': '#0b8043',  # Basil
    '11': '#d50000',  # Tomato
}


def _env():
    e = os.environ.copy()
    if _ENV_PATH.exists():
        for ln in _ENV_PATH.read_text().splitlines():
            if '=' in ln and not ln.strip().startswith('#'):
                k, v = ln.split('=', 1)
                e[k.strip()] = v.strip()
    return e


def google_color_map_hex() -> Dict[str, str]:
    """Return {colorId: '#rrggbb'} from gog CLI, with fallback."""
    try:
        out = subprocess.check_output(
            ['gog', 'calendar', 'colors', '--account', _ACCOUNT, '--json', '--no-input'],
            text=True, env=_env(), timeout=10
        )
        data = json.loads(out).get('event', {})
        return {k: v.get('background', _GOOGLE_FALLBACK.get(k, '#46d6db'))
                for k, v in data.items()}
    except Exception:
        return dict(_GOOGLE_FALLBACK)


def google_color_map_rgb() -> Dict[str, RGB]:
    """Return {colorId: (R,G,B)} from gog CLI, with fallback."""
    return {k: hex_to_rgb(v) for k, v in google_color_map_hex().items()}
