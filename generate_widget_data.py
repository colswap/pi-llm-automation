#!/usr/bin/env python3
"""Generate widget_data.json from Google Calendar + timetable data.

Replaces the Obsidian plugin dependency. Runs on Pi via cron.
Outputs to ~/Obsidian/YuriVault/99_Sync/widget-data/widget_data.json
"""
import json
import subprocess
import os
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path('/home/user/workspace/timetable/base.json')
OVR = Path('/home/user/workspace/timetable/overrides.json')
ENV_PATH = Path('/home/user/.agent-config/.env')
ACCOUNT = 'user@gmail.com'
OUT = Path('/home/user/notes/99_Sync/widget-data/widget_data.json')
QUOTES = Path('/home/user/notes/30_Reference/daily-quotes.json')

WD = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
DAY_KO = ['월', '화', '수', '목', '금', '토', '일']


def env():
    e = os.environ.copy()
    if ENV_PATH.exists():
        for ln in ENV_PATH.read_text().splitlines():
            if '=' in ln and not ln.strip().startswith('#'):
                k, v = ln.split('=', 1)
                e[k.strip()] = v.strip()
    return e


def get_school_items(day_iso):
    """Get school schedule from base timetable."""
    base = json.loads(BASE.read_text())
    ovr = json.loads(OVR.read_text())
    dt = datetime.fromisoformat(day_iso)
    dname = WD[dt.weekday()]

    items = []
    for c in base['courses']:
        if dname in c['days']:
            items.append({
                'title': c['name'],
                'start': c['start'],
                'end': c['end'],
                'completed': False,
                'category': 'school',
            })

    # Remove cancelled classes
    canc = {(x['date'], x['name']) for x in ovr.get('cancel', [])}
    items = [x for x in items if (day_iso, x['title']) not in canc]
    return items


def get_cal_items(day_iso):
    """Get Google Calendar events for the day."""
    try:
        out = subprocess.check_output([
            'gog', 'calendar', 'events', ACCOUNT,
            '--from', f'{day_iso}T00:00:00+09:00',
            '--to', f'{day_iso}T23:59:59+09:00',
            '--json', '--account', ACCOUNT, '--no-input',
        ], text=True, env=env(), timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []

    items = []
    for e in json.loads(out).get('events', []):
        s = e.get('start', {}).get('dateTime', '')
        t = e.get('end', {}).get('dateTime', '')
        if not s or not t:
            continue

        start_hm = s[11:16]
        end_hm = t[11:16]
        summary = e.get('summary', '(제목없음)')

        # Categorize by colorId or keywords
        color_id = str(e.get('colorId', ''))
        deadline_kw = ['마감', 'deadline', 'due', '제출']
        cat = 'personal'
        if color_id == '4' or any(k in summary.lower() for k in deadline_kw):
            cat = 'deadline'

        items.append({
            'title': summary,
            'start': start_hm,
            'end': end_hm,
            'completed': False,
            'category': cat,
        })
    return items


def get_daily_quote(day_iso):
    """Pick a deterministic daily quote."""
    if not QUOTES.exists():
        return None
    quotes = json.loads(QUOTES.read_text())
    if not quotes:
        return None
    dt = datetime.fromisoformat(day_iso)
    day_of_year = dt.timetuple().tm_yday
    idx = day_of_year % len(quotes)
    return quotes[idx]


def main():
    from datetime import timezone
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=None, help='YYYY-MM-DD (default: today)')
    args = parser.parse_args()

    now = datetime.now()
    if args.date:
        day_iso = args.date
        dt = datetime.fromisoformat(day_iso)
    else:
        dt = now
        day_iso = dt.strftime('%Y-%m-%d')

    weekday_idx = dt.weekday()
    display = f"{dt.month}월 {dt.day}일 {DAY_KO[weekday_idx]}요일"

    # Collect schedule
    school = get_school_items(day_iso)
    cal = get_cal_items(day_iso)

    # Merge & dedupe (calendar wins if same time+similar name)
    school_times = {(s['start'], s['end']) for s in school}
    merged = list(school)
    for c in cal:
        if (c['start'], c['end']) not in school_times:
            merged.append(c)
    merged.sort(key=lambda x: x['start'])

    # Daily quote as goal
    quote = get_daily_quote(day_iso)
    goals = []
    if quote:
        goals.append(f"\"{quote['q']}\" — {quote['a']}")

    # Build widget_data
    exported = now.astimezone().isoformat()
    data = {
        'meta': {
            'version': 1,
            'exported_at': exported,
            'vault_name': 'ColswapVault',
            'plugin_version': 'pi-auto-1.0',
            'source_file': f'auto-generated from calendar ({day_iso})',
        },
        'date': {
            'iso': day_iso,
            'weekday': DAY_KO[weekday_idx],
            'weekday_en': WD[weekday_idx],
            'display': display,
        },
        'schedule': merged,
        'tasks': [],  # Could integrate obsidian tasks later
        'goals': goals,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"[{now.strftime('%F %T')}] widget_data.json updated: {len(merged)} events, date={day_iso}")


if __name__ == '__main__':
    main()
