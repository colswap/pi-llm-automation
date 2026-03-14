#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import subprocess
from datetime import date, datetime, timedelta

CLI = '/home/user/workspace/automation/timetable_cli.py'


def run_day(day: str) -> list[dict]:
    out = subprocess.check_output(['python', CLI, 'show', '--date', day], text=True)
    items = []
    for line in out.splitlines()[1:]:
        # - 10:00-11:00 [구글] 제목 (위치)
        line = line.strip()
        if not line.startswith('- '):
            continue
        body = line[2:]
        time_part, rest = body.split(' ', 1)
        start, end = time_part.split('-')
        source = '학교' if '[학교]' in rest else '개인'
        title = rest
        loc = '-'
        if rest.endswith(')') and ' (' in rest:
            title, loc = rest.rsplit(' (', 1)
            loc = loc[:-1]
        title = title.replace('[학교] ', '').replace('[구글] ', '')
        items.append({
            'start': start,
            'end': end,
            'source': source,
            'title': title,
            'location': loc,
        })
    return items


def build_prompt(label: str, day: str, items: list[dict]) -> str:
    return f'''모바일에서 보기 좋은 세로형 일정표 인포그래픽을 만들어줘.
제목: "{label} 일정표"
스타일: 미니멀, 카드형 UI, 파스텔 톤, 가독성 최우선
해상도: 1080x1920

규칙:
- 각 일정 카드에 시간/일정명/구분(학교·개인)/위치 표시
- 이동시간/출발시간/경로 정보는 표시 금지
- 시간순 정렬
- 겹치는 일정은 "시간 겹침" 배지 표시

일정 데이터(JSON):
{json.dumps(items, ensure_ascii=False, indent=2)}
'''


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--mode', choices=['day', 'tomorrow', 'week'], default='tomorrow')
    p.add_argument('--date', help='YYYY-MM-DD (mode=day일 때 사용)')
    args = p.parse_args()

    today = datetime.now().date()

    if args.mode == 'tomorrow':
        d = today + timedelta(days=1)
        day = d.isoformat()
        items = run_day(day)
        print(build_prompt(f'{day} (내일)', day, items))
        return

    if args.mode == 'day':
        if not args.date:
            raise SystemExit('--date 필요')
        day = args.date
        items = run_day(day)
        print(build_prompt(day, day, items))
        return

    # week
    monday = today - timedelta(days=today.weekday())
    payload = []
    for i in range(7):
        d = monday + timedelta(days=i)
        day = d.isoformat()
        payload.append({'date': day, 'items': run_day(day)})
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
