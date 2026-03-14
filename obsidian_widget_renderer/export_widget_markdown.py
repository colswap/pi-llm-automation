#!/usr/bin/env python3
import json
from pathlib import Path
from datetime import datetime

SRC = Path('/home/user/notes/99_Sync/widget-data/widget_data.json')
OUT_DIR = Path('/home/user/sync/widget-timetable')
OUT_MD = OUT_DIR / 'widget_view.md'


def main():
    if not SRC.exists():
        raise SystemExit(f'missing source: {SRC}')
    data = json.loads(SRC.read_text(encoding='utf-8'))

    date = data.get('date', {})
    schedule = data.get('schedule', [])
    tasks = data.get('tasks', [])
    goals = data.get('goals', [])
    exported = data.get('meta', {}).get('exported_at', datetime.now().isoformat())

    lines = []
    lines.append(f"# {date.get('display', date.get('iso', '오늘'))}")
    lines.append('')
    lines.append('## 일정')
    if schedule:
        for s in schedule:
            cat = s.get('category', 'personal')
            badge = '⏰' if cat == 'deadline' else ('🏫' if cat == 'school' else '📝')
            lines.append(f"- {badge} {s.get('start','??:??')}–{s.get('end','??:??')} {s.get('title','(제목없음')}")
    else:
        lines.append('- (일정 없음)')

    lines.append('')
    lines.append('## 할 일')
    if tasks:
        for t in tasks[:10]:
            mark = '✅' if t.get('completed') else '⬜'
            lines.append(f"- {mark} {t.get('text','')}")
    else:
        lines.append('- (할 일 없음)')

    if goals:
        lines.append('')
        lines.append('## 목표')
        for g in goals[:5]:
            lines.append(f'- 🎯 {g}')

    lines.append('')
    lines.append(f"> updated: {exported}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text('\n'.join(lines).strip() + '\n', encoding='utf-8')
    print(OUT_MD)


if __name__ == '__main__':
    main()
