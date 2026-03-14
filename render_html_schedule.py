#!/usr/bin/env python3
"""HTML/CSS timetable renderer using CodyHouse schedule baseline + Playwright.

Produces single-day or 3-day bundle PNG screenshots from Jinja2 templates.

Usage:
    # Single day
    python render_html_schedule.py --date 2026-03-09

    # 3-day bundle
    python render_html_schedule.py --date 2026-03-09 --bundle

    # Custom output path
    python render_html_schedule.py --date 2026-03-09 --out /tmp/schedule.png
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

from color_mapping import (
    google_color_map_hex,
    hex_to_rgb,
    rgb_to_hex,
    school_course_colors_hex,
)

# ── Paths ──
BASE = Path('/home/user/workspace/timetable/base.json')
OVR = Path('/home/user/workspace/timetable/overrides.json')
ENV_PATH = Path('/home/user/.agent-config/.env')
TEMPLATE_DIR = Path(__file__).parent / 'templates'
OUT_DIR = Path(__file__).parent
ACCOUNT = 'user@gmail.com'

WD = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
DAY_KO = ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일']

# CodyHouse row height in px (matches CSS --schedule-row-height)
ROW_H = 60  # 1 hour = 60px


# ── Env / data helpers (reused from existing timetable_cli logic) ──

def _env():
    e = os.environ.copy()
    if ENV_PATH.exists():
        for ln in ENV_PATH.read_text().splitlines():
            if '=' in ln and not ln.strip().startswith('#'):
                k, v = ln.split('=', 1)
                e[k.strip()] = v.strip()
    return e


def _load_tables():
    return json.loads(BASE.read_text()), json.loads(OVR.read_text())


def _h2m(hm: str) -> int:
    h, m = hm.split(':')
    return int(h) * 60 + int(m)


def _darken_hex(hex_color: str, factor: float = 0.25) -> str:
    """Darken a hex color for border-left accent."""
    r, g, b = hex_to_rgb(hex_color)
    return rgb_to_hex(
        int(r * (1 - factor)),
        int(g * (1 - factor)),
        int(b * (1 - factor)),
    )


# ── Data fetching (preserves existing merge logic) ──

def _school_items(day_iso: str, color_map: dict[str, str]) -> list[dict]:
    base, ovr = _load_tables()
    dname = WD[datetime.fromisoformat(day_iso).weekday()]
    items = []
    for c in base['courses']:
        if dname in c['days']:
            items.append({
                'source': 'school',
                'name': c['name'],
                'start': c['start'],
                'end': c['end'],
                'location': c.get('location', '-'),
                'bg_hex': color_map.get(c['name'], '#34a89d'),
            })
    canc = {(x['date'], x['name']) for x in ovr.get('cancel', [])}
    items = [x for x in items if (day_iso, x['name']) not in canc]
    # makeups
    for m in ovr.get('makeup', []):
        if m['date'] == day_iso:
            items.append({
                'source': 'school',
                'name': m['name'],
                'start': m['start'],
                'end': m['end'],
                'location': m.get('location', '-'),
                'bg_hex': color_map.get(m['name'], '#34a89d'),
            })
    return sorted(items, key=lambda x: x['start'])


def _google_items(day_iso: str, gcolor_map: dict[str, str]) -> list[dict]:
    try:
        out = subprocess.check_output(
            ['gog', 'calendar', 'events', ACCOUNT,
             '--from', f'{day_iso}T00:00:00+09:00',
             '--to', f'{day_iso}T23:59:59+09:00',
             '--json', '--account', ACCOUNT, '--no-input'],
            text=True, env=_env(), timeout=15,
        )
    except Exception:
        return []
    res = []
    for e in json.loads(out).get('events', []):
        s = e.get('start', {}).get('dateTime', '')
        t = e.get('end', {}).get('dateTime', '')
        if not s or not t:
            continue
        cid = str(e.get('colorId', '7'))
        res.append({
            'source': 'google',
            'name': e.get('summary', '(제목없음)'),
            'start': s[11:16],
            'end': t[11:16],
            'location': e.get('location', '-') or '-',
            'bg_hex': gcolor_map.get(cid, '#039be5'),
        })
    return sorted(res, key=lambda x: x['start'])


def _assign_columns(items: list[dict]) -> list[dict]:
    """Assign col/total_cols for overlapping events."""
    if not items:
        return items
    events = []
    for it in items:
        events.append({**it, 'start_m': _h2m(it['start']), 'end_m': _h2m(it['end']),
                        'col': 0, 'total_cols': 1})
    events.sort(key=lambda x: x['start_m'])

    groups: list[list[dict]] = []
    cur = [events[0]]
    gend = events[0]['end_m']
    for ev in events[1:]:
        if ev['start_m'] < gend:
            cur.append(ev)
            gend = max(gend, ev['end_m'])
        else:
            groups.append(cur)
            cur = [ev]
            gend = ev['end_m']
    groups.append(cur)

    for grp in groups:
        for i, ev in enumerate(grp):
            ev['col'] = i
            ev['total_cols'] = len(grp)
    return events


# ── Template data builders ──

def _build_day_data(day_iso: str,
                    school_colors: dict[str, str],
                    gcolor_map: dict[str, str],
                    *,
                    force_start_hour: int | None = None,
                    force_end_hour: int | None = None) -> dict:
    """Build template context for a single day."""
    raw = sorted(
        _school_items(day_iso, school_colors) + _google_items(day_iso, gcolor_map),
        key=lambda x: x['start'],
    )
    items = _assign_columns(raw)

    # Determine time range
    # Base rule: always show at least 08:00 ~ 22:00.
    # If events extend beyond this, expand. In bundle mode, caller can force
    # a unified start/end hour across all days.
    min_floor = 8 * 60
    max_floor = 22 * 60
    if items:
        smin = max(0, min(_h2m(it['start']) for it in items))
        emin = min(24 * 60, max(_h2m(it['end']) for it in items))
    else:
        smin, emin = min_floor, max_floor
    smin = min(smin, min_floor)
    emin = max(emin, max_floor)
    sh = smin // 60
    eh = max(sh + 1, (emin + 59) // 60)

    if force_start_hour is not None:
        sh = max(0, min(force_start_hour, 23))
    if force_end_hour is not None:
        eh = max(sh + 1, min(force_end_hour, 24))

    # Hours for timeline
    hours = [{'label': f'{h}:00', 'label_short': f'{h}'} for h in range(sh, eh + 1)]
    timeline_height = (eh - sh) * ROW_H

    # Position events in px
    ppm = ROW_H / 60  # px per minute
    for it in items:
        s_m = _h2m(it['start'])
        e_m = _h2m(it['end'])
        it['top'] = int((s_m - sh * 60) * ppm)
        it['height'] = max(28, int((e_m - s_m) * ppm))
        it['border_hex'] = _darken_hex(it['bg_hex'])

    dt = datetime.fromisoformat(day_iso)
    return {
        'date_iso': day_iso,
        'day_label': f'{DAY_KO[dt.weekday()]}',
        'hours': hours,
        'timeline_height': timeline_height,
        'events': items,
    }


def _get_color_maps():
    """Return (school_colors_hex, google_colors_hex)."""
    base, _ = _load_tables()
    course_names = [c['name'] for c in base.get('courses', [])]
    school_colors = school_course_colors_hex(course_names)
    gcolor_map = google_color_map_hex()
    return school_colors, gcolor_map


# ── Rendering ──

def _mock_day_data(day_iso: str) -> dict:
    dt = datetime.fromisoformat(day_iso)
    sh, eh = 8, 22
    hours = [{'label': f'{h}:00', 'label_short': f'{h}'} for h in range(sh, eh + 1)]
    timeline_height = (eh - sh) * ROW_H
    sample = [
        {'source':'google','name':'Design Crit','start':'09:00','end':'10:00','location':'Room A','bg_hex':'#8ab4f8','col':0,'total_cols':1},
        {'source':'school','name':'Algorithms','start':'10:30','end':'11:45','location':'302-105','bg_hex':'#7cc9b0','col':0,'total_cols':1},
        {'source':'google','name':'Team Sync','start':'13:00','end':'14:00','location':'Meet','bg_hex':'#f28b82','col':0,'total_cols':1},
        {'source':'school','name':'Computer Vision','start':'14:00','end':'15:15','location':'302-106','bg_hex':'#d7aefb','col':0,'total_cols':1},
        {'source':'google','name':'Coffee Chat','start':'17:00','end':'18:00','location':'Cafe','bg_hex':'#81c995','col':0,'total_cols':1},
    ]
    items = _assign_columns(sample)
    ppm = ROW_H / 60
    for it in items:
        s_m = _h2m(it['start']); e_m = _h2m(it['end'])
        it['top'] = int((s_m - sh * 60) * ppm)
        it['height'] = max(28, int((e_m - s_m) * ppm))
        it['border_hex'] = _darken_hex(it['bg_hex'])
    return {
        'date_iso': day_iso,
        'day_label': f'{DAY_KO[dt.weekday()]}',
        'hours': hours,
        'timeline_height': timeline_height,
        'events': items,
    }


def render_day_html(day_iso: str, *, v2: bool = False, v3: bool = False, template_only: bool = False) -> str:
    """Return rendered HTML string for a single day."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    if template_only:
        ctx = _mock_day_data(day_iso)
    else:
        school_colors, gcolor_map = _get_color_maps()
        ctx = _build_day_data(day_iso, school_colors, gcolor_map)
    if v3:
        tpl = 'schedule_v3.html'
    else:
        tpl = 'schedule_v2.html' if v2 else 'schedule.html'
    return env.get_template(tpl).render(**ctx)


def render_bundle_html(start_iso: str, num_days: int = 3, *, v2: bool = False, v3: bool = False, template_only: bool = False) -> str:
    """Return rendered HTML string for multi-day bundle."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    school_colors, gcolor_map = _get_color_maps() if not template_only else ({}, {})

    start = datetime.fromisoformat(start_iso).date()
    day_isos = [(start + timedelta(days=i)).isoformat() for i in range(num_days)]
    days = []

    if template_only:
        for d in day_isos:
            days.append(_mock_day_data(d))
    else:
        # Bundle time range policy:
        # - Always include at least 08:00~22:00.
        # - If any day extends outside that window, unify all bundle columns
        #   to the same expanded range.
        base_start_h = 8
        base_end_h = 22
        global_start_h = base_start_h
        global_end_h = base_end_h

        per_day_raw = []
        for d in day_isos:
            raw = sorted(
                _school_items(d, school_colors) + _google_items(d, gcolor_map),
                key=lambda x: x['start'],
            )
            per_day_raw.append((d, raw))
            if raw:
                smin = max(0, min(_h2m(it['start']) for it in raw))
                emin = min(24 * 60, max(_h2m(it['end']) for it in raw))
                sh = smin // 60
                eh = max(sh + 1, (emin + 59) // 60)
                global_start_h = min(global_start_h, sh)
                global_end_h = max(global_end_h, eh)

        for d in day_isos:
            days.append(_build_day_data(
                d,
                school_colors,
                gcolor_map,
                force_start_hour=global_start_h,
                force_end_hour=global_end_h,
            ))

    end_date = (start + timedelta(days=num_days - 1)).isoformat()
    if v3:
        bundle_w = 360 * num_days + 14 * (num_days - 1) + 40
    elif v2:
        bundle_w = 380 * num_days + 16 * (num_days - 1) + 48
    else:
        bundle_w = 420 * num_days + 12 * (num_days - 1) + 32
    ctx = {
        'start_date': start_iso,
        'end_date': end_date,
        'days': days,
        'bundle_width': bundle_w,
    }
    if v3:
        tpl = 'bundle_v3.html'
    else:
        tpl = 'bundle_v2.html' if v2 else 'bundle.html'
    return env.get_template(tpl).render(**ctx)


def _screenshot(html: str, out_path: Path, *, width: int = 420) -> Path:
    """Write HTML to temp file, screenshot with Playwright, save PNG."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix='.html', delete=False, mode='w',
                                      encoding='utf-8') as f:
        f.write(html)
        tmp = f.name

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': width, 'height': 800})
            page.goto(f'file://{tmp}', wait_until='networkidle')
            # Let fonts load
            page.wait_for_timeout(500)
            # Get robust content height (avoid occasional clipping)
            height = page.evaluate('Math.max(document.body.scrollHeight, document.body.offsetHeight, document.documentElement.scrollHeight, document.documentElement.offsetHeight) + 48')
            page.set_viewport_size({'width': width, 'height': int(height)})
            page.screenshot(path=str(out_path), full_page=True)
            browser.close()
    finally:
        os.unlink(tmp)

    return out_path


def render_day_png(day_iso: str, out_path: Path | None = None,
                   *, v2: bool = False, v3: bool = False, template_only: bool = False) -> Path:
    """Render single-day schedule to PNG."""
    html = render_day_html(day_iso, v2=v2, v3=v3, template_only=template_only)
    tag = '_v3' if v3 else ('_v2' if v2 else '')
    out = out_path or OUT_DIR / f'out_html_day{tag}_{day_iso}.png'
    return _screenshot(html, out, width=452)


def render_bundle_png(start_iso: str, num_days: int = 3,
                      out_path: Path | None = None,
                      *, v2: bool = False, v3: bool = False, template_only: bool = False) -> Path:
    """Render multi-day bundle to PNG."""
    html = render_bundle_html(start_iso, num_days, v2=v2, v3=v3, template_only=template_only)
    end_date = (datetime.fromisoformat(start_iso).date()
                + timedelta(days=num_days - 1)).isoformat()
    tag = '_v3' if v3 else ('_v2' if v2 else '')
    out = out_path or OUT_DIR / f'out_html_bundle{tag}_{start_iso}_{end_date}.png'
    if v3:
        width = 360 * num_days + 14 * (num_days - 1) + 72
    elif v2:
        width = 380 * num_days + 16 * (num_days - 1) + 72
    else:
        width = 420 * num_days + 12 * (num_days - 1) + 64
    return _screenshot(html, out, width=width)


# ── CLI ──

def main():
    ap = argparse.ArgumentParser(description='CodyHouse HTML schedule renderer')
    ap.add_argument('--date', required=True, help='Start date (YYYY-MM-DD)')
    ap.add_argument('--bundle', action='store_true', help='Render 3-day bundle')
    ap.add_argument('--days', type=int, default=3, help='Number of days in bundle')
    ap.add_argument('--out', help='Output PNG path')
    ap.add_argument('--v2', action='store_true',
                    help='Use v2 timeline-rail layout (default: v1 card layout)')
    ap.add_argument('--v3', action='store_true',
                    help='Use v3 candidate layout')
    ap.add_argument('--template-only', action='store_true',
                    help='Ignore all real timetable/google data and render template mock data only')
    args = ap.parse_args()

    out_path = Path(args.out) if args.out else None

    if args.bundle:
        p = render_bundle_png(args.date, args.days, out_path, v2=args.v2, v3=args.v3, template_only=args.template_only)
    else:
        p = render_day_png(args.date, out_path, v2=args.v2, v3=args.v3, template_only=args.template_only)

    print(str(p))


if __name__ == '__main__':
    main()
