#!/usr/bin/env python3
"""3-Day Timetable Bundle Generator (v5 + modern unified layout).

Supports all presets via --style. The 'modern' preset renders a single
unified image with shared time axis; other presets stitch 3 day cards.
"""
import argparse
import re
from datetime import datetime, timedelta
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from generate_day_agenda_v5 import (
    render_day, CANVAS_W, CANVAS_H,
    load_tables, calendar_color_map, school_items, cal_items,
    h2m, DAY_KO, _assign_columns, _draw_dotted_line,
)
from design_tokens import get_preset, PRESET_NAMES

FONT_REG = Path('/home/user/workspace/assets/fonts/NotoSansKR-Regular.otf')
FONT_BOLD = Path('/home/user/workspace/assets/fonts/NotoSansKR-Bold.otf')


def font(size, b=False):
    p = FONT_BOLD if b else FONT_REG
    return ImageFont.truetype(str(p), size) if p.exists() else ImageFont.load_default()


# ---------------------------------------------------------------------------
#  Color helpers
# ---------------------------------------------------------------------------

def _base_course_name(name: str) -> str:
    """Strip trailing (1), (2) etc. so related parts share a color."""
    return re.sub(r'\s*[\(（]\d+[\)）]\s*$', '', name)


def _tint(rgb, alpha=0.15):
    """Mix *rgb* toward white. alpha=0 → white, alpha=1 → original."""
    return tuple(int(255 - (255 - c) * alpha) for c in rgb)


def _mask(text: str) -> str:
    """Return empty string for blind mode — hide text entirely."""
    return ''


# ---------------------------------------------------------------------------
#  Modern unified 3-day renderer
# ---------------------------------------------------------------------------

def render_modern_bundle(start_date_str: str, out_path, tokens, num_days: int = 3, no_now_line: bool = False, wallpaper: tuple = None, blind: bool = False):
    """Render a unified N-column image with a shared time axis."""

    start = datetime.fromisoformat(start_date_str).date()
    dates = [(start + timedelta(days=i)).isoformat() for i in range(num_days)]

    # ── Data collection ──────────────────────────────────────────────────
    base, _ = load_tables()
    cmap = calendar_color_map()

    # Assign accent colors by *base* course name (parts share a color)
    all_base = sorted({_base_course_name(c['name']) for c in base.get('courses', [])})
    palette = tokens.course_palette
    base_colors = {n: palette[i % len(palette)] for i, n in enumerate(all_base)}
    course_colors = {
        c['name']: base_colors[_base_course_name(c['name'])]
        for c in base.get('courses', [])
    }

    all_days = []
    all_allday = []
    for d_str in dates:
        timed_cal, allday_cal = cal_items(d_str, cmap)
        items = sorted(
            school_items(d_str, course_colors) + timed_cal,
            key=lambda z: z['start'],
        )
        all_days.append(items)
        all_allday.append(allday_cal)

    # ── Unified time range ───────────────────────────────────────────────
    all_starts, all_ends = [], []
    for items in all_days:
        for it in items:
            all_starts.append(h2m(it['start']))
            all_ends.append(h2m(it['end']))

    if all_starts:
        sh = max(0, min(all_starts) - 30) // 60
        eh = min(24, (max(all_ends) + 59) // 60)
    else:
        sh, eh = 9, 18
    eh = max(sh + 1, eh)

    # ── All-day event deduplication & banner sizing ─────────────────────
    BANNER_H = 30
    BANNER_GAP = 4
    allday_rows = []
    seen_ad = set()
    for day_ads in all_allday:
        for ad in day_ads:
            key = (ad['name'], ad['start_date'], ad['end_date'])
            if key not in seen_ad:
                seen_ad.add(key)
                allday_rows.append(ad)
    banner_block_h = len(allday_rows) * (BANNER_H + BANNER_GAP) if allday_rows else 0

    # ── Layout constants ─────────────────────────────────────────────────
    if wallpaper:
        # Fixed phone resolution, adjust PPM to fill edge-to-edge
        TOTAL_W, TOTAL_H = wallpaper
        PAD = int(TOTAL_W * 0.04)
        TIME_W = int(TOTAL_W * 0.1)
        HEADER_H = int(TOTAL_H * 0.04)
        COL_HDR_H = int(TOTAL_H * 0.03)
        COL_GAP = 0
        TOP = PAD + HEADER_H + COL_HDR_H + banner_block_h
        BOTTOM_PAD = PAD + HEADER_H + COL_HDR_H  # match top padding (sans banners)
        avail_h = TOTAL_H - TOP - BOTTOM_PAD
        PPM = avail_h / ((eh - sh) * 60)
    else:
        # Responsive width based on number of days
        if num_days == 1:
            TOTAL_W = 700
        elif num_days == 2:
            TOTAL_W = 1200
        else:
            TOTAL_W = 1600
        PAD = tokens.bundle_pad
        TIME_W = tokens.time_col_w
        HEADER_H = 60
        COL_HDR_H = 48
        COL_GAP = tokens.bundle_gap if num_days > 1 else 0
        TOP = PAD + HEADER_H + COL_HDR_H + banner_block_h
        PPM = 2.0  # pixels per minute
        TOTAL_H = TOP + int((eh - sh) * 60 * PPM) + PAD + 60

    content_w = TOTAL_W - PAD * 2 - TIME_W
    col_w = (content_w - COL_GAP * max(0, num_days - 1)) // num_days
    timeline_h = int((eh - sh) * 60 * PPM)

    img = Image.new('RGB', (TOTAL_W, TOTAL_H), tokens.bundle_bg)
    draw = ImageDraw.Draw(img)

    # ── Fonts ────────────────────────────────────────────────────────────
    f_title = font(tokens.bundle_title_size + 10, True)
    f_range = font(28, False)
    f_col_hdr = font(30, True)
    f_time = font(24, False)
    f_name = font(tokens.font_header + 10, True)
    f_detail = font(tokens.font_small + 10, False)
    f_now = font(22, True)

    # ── Bundle header ────────────────────────────────────────────────────
    d1 = datetime.fromisoformat(dates[0])
    d3 = datetime.fromisoformat(dates[-1])
    grid_left = PAD + TIME_W

    if wallpaper:
        pass  # Skip header text — phone lock screen shows date/time
    elif num_days == 1:
        ko = DAY_KO[d1.weekday()]
        title_txt = f"{d1.year}.{d1.month}.{d1.day} ({ko})"
        draw.text((grid_left, PAD + 12), title_txt,
                  fill=tokens.title_color, font=f_title)
    else:
        title_txt = f"{num_days}-Day Schedule"
        draw.text((grid_left, PAD + 12), title_txt,
                  fill=tokens.title_color, font=f_title)
        tw = draw.textbbox((0, 0), title_txt, font=f_title)[2]
        range_txt = f"{d1.year}.{d1.month}.{d1.day} – {d3.month}.{d3.day}"
        draw.text((grid_left + tw + 20, PAD + 18), range_txt,
                  fill=tokens.bundle_label_color, font=f_range)

    # ── All-day banners ────────────────────────────────────────────────
    f_banner = font(18, True)
    f_banner_sm = font(14, False)
    banner_base_y = PAD + HEADER_H + COL_HDR_H
    for bi, ad in enumerate(allday_rows):
        by = banner_base_y + bi * (BANNER_H + BANNER_GAP)
        # Find which columns this event spans
        start_col, end_col = None, None
        for ci, d_str in enumerate(dates):
            if ad['start_date'] <= d_str < ad['end_date']:
                if start_col is None:
                    start_col = ci
                end_col = ci
        if start_col is None:
            start_col, end_col = 0, num_days - 1
        bx1 = grid_left + start_col * (col_w + COL_GAP) + 4
        bx2 = grid_left + end_col * (col_w + COL_GAP) + col_w - 4
        bg = ad['color']
        bbg = _tint(bg, 0.25)
        draw.rounded_rectangle((bx1, by, bx2, by + BANNER_H), radius=6, fill=bbg)
        draw.rectangle((bx1 + 2, by + 3, bx1 + 6, by + BANNER_H - 3), fill=bg)
        banner_label = _mask(ad['name']) if blind else ad['name'][:30]
        draw.text((bx1 + 14, by + 5), banner_label, fill=tokens.school_name_color, font=f_banner)

    # ── Column headers & backgrounds ─────────────────────────────────────
    for i, d_str in enumerate(dates):
        dt_obj = datetime.fromisoformat(d_str)
        cx = grid_left + i * (col_w + COL_GAP)

        # Column background — slightly tinted for weekends
        is_weekend = dt_obj.weekday() >= 5  # Saturday=5, Sunday=6
        col_bg = (250, 248, 252) if is_weekend else (255, 255, 255)
        draw.rectangle((cx, TOP, cx + col_w, TOP + timeline_h),
                        fill=col_bg)

        # Date + day-of-week label (skip for single-day — already in title)
        if num_days > 1:
            ko = DAY_KO[dt_obj.weekday()]
            col_label = f"{dt_obj.month}/{dt_obj.day} ({ko})"
            draw.text((cx + 14, PAD + HEADER_H + 10), col_label,
                      fill=tokens.title_color, font=f_col_hdr)

    # ── Grid lines (spanning all 3 columns) ──────────────────────────────
    grid_right = grid_left + col_w * num_days + COL_GAP * max(0, num_days - 1)

    for h in range(sh, eh + 1):
        y = int(TOP + (h - sh) * 60 * PPM)
        draw.line((grid_left, y, grid_right, y), fill=tokens.grid_line, width=1)
        draw.text((PAD, y - 8), f"{h:02d}:00",
                  fill=tokens.grid_label, font=f_time)
        # Half-hour dotted line
        if h < eh:
            hy = int(TOP + (h - sh) * 60 * PPM + 30 * PPM)
            _draw_dotted_line(draw, grid_left, hy, grid_right, tokens.grid_line)

    # Column separator lines (subtle)
    for i in range(1, num_days):
        sx = grid_left + i * (col_w + COL_GAP) - COL_GAP // 2
        draw.line((sx, TOP, sx, TOP + timeline_h),
                  fill=tokens.grid_line, width=1)

    # ── Event blocks ─────────────────────────────────────────────────────
    for day_i, items in enumerate(all_days):
        cx = grid_left + day_i * (col_w + COL_GAP)
        if not items:
            continue
        events = _assign_columns(items)

        for ev in events:
            it = ev['item']
            y1 = int(TOP + (ev['start'] - sh * 60) * PPM)
            y2 = max(y1 + 52, int(TOP + (ev['end'] - sh * 60) * PPM))

            sub_w = (col_w - 12) / ev['total_cols']
            x1 = int(cx + 6 + ev['col'] * sub_w)
            x2 = int(x1 + sub_w - 4)

            accent = it['color']
            bg = _tint(accent, 0.18)
            outline_c = _tint(accent, 0.40)

            # Block background
            draw.rounded_rectangle((x1, y1, x2, y2),
                                   radius=tokens.event_radius,
                                   fill=bg, outline=outline_c, width=1)
            # Left accent bar (inset to respect rounded corners)
            bar_y1 = y1 + max(tokens.event_radius // 2, 3)
            bar_y2 = y2 - max(tokens.event_radius // 2, 3)
            if bar_y2 > bar_y1:
                draw.rectangle((x1, bar_y1, x1 + 4, bar_y2), fill=accent)

            # Continuation markers
            cont_before = it.get('cont_before', False)
            cont_after = it.get('cont_after', False)

            # Event title (bold, ellipsis if overflow)
            avail_w = x2 - x1 - 22
            name = _base_course_name(it['name'])
            if blind:
                name = _mask(name)
            if cont_before:
                name = '← ' + name
            bbox = draw.textbbox((0, 0), name, font=f_name)
            while bbox[2] > avail_w and len(name) > 3:
                name = name[:-2] + '…'
                bbox = draw.textbbox((0, 0), name, font=f_name)
            draw.text((x1 + 14, y1 + 8), name,
                      fill=tokens.school_name_color, font=f_name)

            # Time + location detail (only if block tall enough)
            block_h = y2 - y1
            if block_h > 44:
                if cont_after:
                    detail = f"{it['start']} – 계속 →"
                elif blind:
                    detail = f"{it['start']} – {it['end']}"
                else:
                    loc = it.get('location', '')
                    if loc and loc != '-':
                        detail = f"{it['start']} – {it['end']} | {loc}"
                    else:
                        detail = f"{it['start']} – {it['end']}"
                bbox_d = draw.textbbox((0, 0), detail, font=f_detail)
                while bbox_d[2] > avail_w and len(detail) > 3:
                    detail = detail[:-2] + '…'
                    bbox_d = draw.textbbox((0, 0), detail, font=f_detail)
                draw.text((x1 + 14, y1 + 38), detail,
                          fill=tokens.cal_loc_color, font=f_detail)

    # ── Now indicator (red line) ─────────────────────────────────────────
    if not no_now_line:
        now = datetime.now()
        now_m = now.hour * 60 + now.minute
        if sh * 60 <= now_m <= eh * 60:
            ny = int(TOP + (now_m - sh * 60) * PPM)
            rc = tokens.now_indicator_color
            draw.ellipse((grid_left - 5, ny - 5, grid_left + 5, ny + 5), fill=rc)
            draw.line((grid_left, ny, grid_right, ny), fill=rc, width=2)
            time_str = f"{now.hour:02d}:{now.minute:02d}"
            draw.text((PAD, ny - 7), time_str, fill=rc, font=f_now)

    # ── Save ─────────────────────────────────────────────────────────────
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


# ---------------------------------------------------------------------------
#  Legacy card-stitching renderer (classic / samsung-dark / minimal-light …)
# ---------------------------------------------------------------------------

def render_classic_bundle(start_date_str, out_path, tokens):
    """Stitch 3 day-card images side-by-side (original v5 behaviour)."""

    start = datetime.fromisoformat(start_date_str).date()
    out_dir = Path('/home/user/workspace/automation')

    cw, ch = tokens.canvas_w, tokens.canvas_h
    day_paths = []
    for i in range(3):
        d = (start + timedelta(days=i)).isoformat()
        p = out_dir / f'out_day_{d}_v5.png'
        render_day(d, p, tokens)
        day_paths.append((d, p))

    gap = tokens.bundle_gap
    pad = tokens.bundle_pad
    header_h = tokens.bundle_header_h
    bundle_w = pad * 2 + cw * 3 + gap * 2
    bundle_h = pad * 2 + header_h + ch

    bundle = Image.new('RGB', (bundle_w, bundle_h), tokens.bundle_bg)
    draw = ImageDraw.Draw(bundle)

    draw.rounded_rectangle(
        (16, 16, bundle_w - 16, bundle_h - 16),
        radius=tokens.card_radius,
        fill=tokens.card_bg,
        outline=tokens.card_outline,
        width=tokens.card_outline_w,
    )
    draw.text(
        (36, 42),
        f'3-Day Timetable Bundle  |  {day_paths[0][0]} ~ {day_paths[-1][0]}',
        fill=tokens.title_color,
        font=font(tokens.bundle_title_size, True),
    )

    x = pad
    y = pad + header_h
    for date_str, p in day_paths:
        im = Image.open(p).convert('RGB')
        if im.size != (cw, ch):
            im = im.resize((cw, ch), Image.Resampling.LANCZOS)
        bundle.paste(im, (x, y))
        draw.text(
            (x + 20, y - 36), date_str,
            fill=tokens.bundle_label_color,
            font=font(tokens.bundle_label_size, True),
        )
        x += cw + gap

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    bundle.save(out)
    # Print paths (legacy compat)
    print(str(out))
    for _, p in day_paths:
        print(str(p))
    return out


# ---------------------------------------------------------------------------
#  CLI entry-point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start-date', help='YYYY-MM-DD (default=today)')
    ap.add_argument('--days', type=int, default=3, help='number of days (1-7, default=3)')
    ap.add_argument('--out', default='/home/user/workspace/automation/out_3day_bundle_v5.png')
    ap.add_argument('--style', choices=PRESET_NAMES, default=None,
                    help=f'design preset ({", ".join(PRESET_NAMES)})')
    ap.add_argument('--no-now-line', action='store_true', help='hide current time indicator line')
    ap.add_argument('--wallpaper', metavar='WxH', default=None, help='phone wallpaper mode (e.g. 1080x2340)')
    ap.add_argument('--blind', action='store_true', help='mask event names and locations for privacy')
    args = ap.parse_args()

    tokens = get_preset(args.style)
    start = (
        datetime.fromisoformat(args.start_date).date().isoformat()
        if args.start_date
        else datetime.now().date().isoformat()
    )

    wp = None
    if args.wallpaper:
        w, h = args.wallpaper.lower().split('x')
        wp = (int(w), int(h))

    if tokens.layout_mode == 'modern':
        out = render_modern_bundle(start, args.out, tokens, num_days=args.days, no_now_line=args.no_now_line, wallpaper=wp, blind=args.blind)
        print(str(out))
    else:
        render_classic_bundle(start, args.out, tokens)


if __name__ == '__main__':
    main()
