#!/usr/bin/env python3
import json, os, subprocess
from datetime import datetime, timedelta
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

BASE=Path('/home/user/workspace/timetable/base.json')
OVR=Path('/home/user/workspace/timetable/overrides.json')
ENV_PATH=Path('/home/user/.agent-config/.env')
OUT=Path('/home/user/workspace/automation/out_week_ref_v5.png')
ACCOUNT='user@gmail.com'
WD=['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
DAY_KO=['월','화','수','목','금','토','일']
FONT_REG=Path('/home/user/workspace/assets/fonts/NotoSansKR-Regular.otf')
FONT_BOLD=Path('/home/user/workspace/assets/fonts/NotoSansKR-Bold.otf')

# 과목별 파스텔 팔레트(같은 과목=같은 색)
COURSE_PALETTE=[
    (255,209,220),  # pastel pink
    (255,229,180),  # pastel peach
    (255,255,186),  # pastel yellow
    (186,255,201),  # pastel mint
    (186,225,255),  # pastel sky
    (218,193,255),  # pastel lavender
    (255,214,165),
    (207,244,252),
]
SCHOOL_STRIP=(129,117,233)


def font(size,bold=False):
    p=FONT_BOLD if bold else FONT_REG
    return ImageFont.truetype(str(p), size) if p.exists() else ImageFont.load_default()


def env():
    e=os.environ.copy()
    if ENV_PATH.exists():
        for ln in ENV_PATH.read_text().splitlines():
            if '=' in ln and not ln.strip().startswith('#'):
                k,v=ln.split('=',1)
                e[k.strip()]=v.strip()
    return e


def hex_to_rgb(h):
    h=h.lstrip('#')
    return tuple(int(h[i:i+2],16) for i in (0,2,4))


def load_tables():
    return json.loads(BASE.read_text()), json.loads(OVR.read_text())


def school_items(day_iso, course_colors):
    base,ovr=load_tables()
    dname=WD[datetime.fromisoformat(day_iso).weekday()]
    items=[{
        'source':'school','name':c['name'],'start':c['start'],'end':c['end'],
        'location':c.get('location','-'),'color':course_colors.get(c['name'], COURSE_PALETTE[0])
    } for c in base['courses'] if dname in c['days']]
    canc={(x['date'],x['name']) for x in ovr.get('cancel',[])}
    items=[x for x in items if (day_iso, x['name']) not in canc]
    for m in ovr.get('makeup',[]):
        if m['date']==day_iso:
            items.append({'source':'school','name':m['name'],'start':m['start'],'end':m['end'],'location':m.get('location','-'),'color':SCHOOL_BG})
    return sorted(items,key=lambda x:x['start'])


def calendar_color_map():
    out=subprocess.check_output(['gog','calendar','colors','--account',ACCOUNT,'--json','--no-input'],text=True,env=env())
    data=json.loads(out).get('event',{})
    return {k:hex_to_rgb(v.get('background','#46d6db')) for k,v in data.items()}


def calendar_items(day_iso, cmap):
    """Fetch calendar events for day_iso. Returns (timed_items, allday_items).
    Cross-midnight events are clipped to the day boundary with continuation markers."""
    out=subprocess.check_output([
        'gog','calendar','events',ACCOUNT,
        '--from',f'{day_iso}T00:00:00+09:00','--to',f'{day_iso}T23:59:59+09:00',
        '--json','--account',ACCOUNT,'--no-input'
    ],text=True,env=env())
    ev=json.loads(out).get('events',[])
    timed, allday = [], []
    for e in ev:
        cid=str(e.get('colorId','7'))
        color=cmap.get(cid,(70,214,219))
        summary=e.get('summary','(제목없음)')
        location=e.get('location','-')
        s_dt_str=e.get('start',{}).get('dateTime','')
        t_dt_str=e.get('end',{}).get('dateTime','')
        s_date=e.get('start',{}).get('date','')
        t_date=e.get('end',{}).get('date','')

        # All-day event
        if not s_dt_str and s_date:
            allday.append({'source':'calendar','name':summary,'start_date':s_date,
                           'end_date':t_date or s_date,'location':location,'color':color})
            continue

        if not s_dt_str or not t_dt_str:
            continue

        # Cross-midnight detection
        s_full = datetime.fromisoformat(s_dt_str)
        t_full = datetime.fromisoformat(t_dt_str)
        s_day = s_full.date().isoformat()
        t_day = t_full.date().isoformat()

        cont_before = s_day < day_iso
        cont_after = t_day > day_iso
        start_hm = '00:00' if cont_before else s_dt_str[11:16]
        end_hm = '23:59' if cont_after else t_dt_str[11:16]

        timed.append({
            'source':'calendar','name':summary,'start':start_hm,'end':end_hm,
            'location':location,'color':color,
            'cont_before':cont_before,'cont_after':cont_after
        })
    return sorted(timed,key=lambda x:x['start']), allday


def hm_to_min(hm):
    h,m=hm.split(':')
    return int(h)*60+int(m)


def fit(s,n):
    return s if len(s)<=n else s[:n-1]+'…'


def main():
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument('--start-date', help='Start date ISO (YYYY-MM-DD), default=this Monday')
    ap.add_argument('--days', type=int, default=5, help='Number of days to show (default 5=Mon-Fri, 7=full week)')
    ap.add_argument('--out', help='Output png path')
    args=ap.parse_args()

    now=datetime.now()
    if args.start_date:
        monday=datetime.fromisoformat(args.start_date)
    else:
        monday=now - timedelta(days=now.weekday())
    num_days=args.days
    days=[(monday+timedelta(days=i)).date().isoformat() for i in range(num_days)]

    cmap=calendar_color_map()

    # 과목명 기준 고정 색상 매핑 생성
    base,_=load_tables()
    course_names=sorted({c['name'] for c in base.get('courses',[])})
    course_colors={name: COURSE_PALETTE[i % len(COURSE_PALETTE)] for i,name in enumerate(course_names)}

    # 주간 데이터 캐시 + 시간 범위 동적 계산
    day_items = {}
    day_allday = {}
    for day in days:
        timed, allday = calendar_items(day, cmap)
        day_items[day] = sorted(school_items(day, course_colors) + timed, key=lambda z: z['start'])
        day_allday[day] = allday
    mins=[]
    maxs=[]
    for items in day_items.values():
        for it in items:
            mins.append(hm_to_min(it['start']))
            maxs.append(hm_to_min(it['end']))

    # 일정이 있으면 시작/종료 시간을 자동 맞춤 (30분 여유)
    if mins and maxs:
        start_min=max(0, min(mins)-30)
        end_min=min(24*60, max(maxs)+30)
        start_h=start_min//60
        end_h=(end_min+59)//60
        if end_h <= start_h:
            end_h=start_h+1
    else:
        start_h,end_h=9,19

    # Collect unique all-day events across the week (deduplicate by name+dates)
    allday_rows = []
    seen_allday = set()
    for day in days:
        for ad in day_allday[day]:
            key = (ad['name'], ad['start_date'], ad['end_date'])
            if key not in seen_allday:
                seen_allday.add(key)
                allday_rows.append(ad)

    BANNER_H = 30
    BANNER_GAP = 4
    banner_block_h = len(allday_rows) * (BANNER_H + BANNER_GAP) if allday_rows else 0

    W,H=1400,2100 + banner_block_h
    img=Image.new('RGB',(W,H),(248,250,252))
    d=ImageDraw.Draw(img)
    f_title=font(46,True)
    f_head=font(28,True)
    f_body=font(22,False)
    f_small=font(18,False)
    f_banner=font(16,True)

    # frame
    d.rounded_rectangle((24,24,W-24,H-24),radius=22,fill=(255,255,255),outline=(229,231,235),width=2)
    d.text((50,62),f"{days[0]} ~ {days[-1]}",fill=(15,23,42),font=f_title)

    # layout
    left=80
    top=170 + banner_block_h
    right=W-40
    bottom=H-70
    time_col_w=62
    grid_left=left+time_col_w
    cols=num_days
    col_w=(right-grid_left)//cols

    ppm=(bottom-top-42)/((end_h-start_h)*60)

    # ── All-day banners (span across day columns) ──
    banner_base_y = 130
    for bi, ad in enumerate(allday_rows):
        by = banner_base_y + bi * (BANNER_H + BANNER_GAP)
        # Find which day columns this event spans
        start_col = None
        end_col = None
        for ci, day in enumerate(days):
            # All-day end_date in Google is exclusive (day after last day)
            if ad['start_date'] <= day < ad['end_date']:
                if start_col is None:
                    start_col = ci
                end_col = ci
        if start_col is None:
            # Event doesn't overlap visible days — draw full width
            start_col, end_col = 0, cols - 1
        bx1 = grid_left + start_col * col_w + 4
        bx2 = grid_left + (end_col + 1) * col_w - 4
        bg = ad['color']
        bbg = tuple(min(255, int(c * 0.6 + 255 * 0.4)) for c in bg)
        d.rounded_rectangle((bx1, by, bx2, by + BANNER_H), radius=6, fill=bbg)
        d.rounded_rectangle((bx1, by + 2, bx1 + 4, by + BANNER_H - 2), radius=2, fill=bg)
        d.text((bx1 + 12, by + 5), fit(ad['name'], 30), fill=(30, 30, 30), font=f_banner)

    # day headers
    for i,day in enumerate(days):
        x=grid_left+i*col_w
        dt=datetime.fromisoformat(day)
        d.text((x+col_w//2-22, top-34), DAY_KO[dt.weekday()], fill=(51,65,85), font=f_head)
        d.text((x+col_w//2-28, top-6), f"{dt.month}/{dt.day}", fill=(148,163,184), font=f_small)

    # horizontal lines + hour labels
    for h in range(start_h,end_h+1):
        y=int(top+42+(h-start_h)*60*ppm)
        d.line((grid_left,y,right,y),fill=(229,231,235),width=1)
        d.text((left+10,y-10),str(h),fill=(148,163,184),font=f_body)

    # vertical lines
    for i in range(cols+1):
        x=grid_left+i*col_w
        d.line((x,top+42,x,bottom),fill=(238,242,247),width=1)

    # events
    for i,day in enumerate(days):
        x=grid_left+i*col_w+6
        items=day_items[day]
        for it in items:
            s=hm_to_min(it['start']); e=hm_to_min(it['end'])
            y1=int(top+42+(s-start_h*60)*ppm)
            y2=max(y1+36, int(top+42+(e-start_h*60)*ppm))
            x2=x+col_w-12
            bg=it['color']
            d.rounded_rectangle((x,y1,x2,y2),radius=8,fill=bg)
            if it['source']=='school':
                d.rounded_rectangle((x+2,y1+2,x+6,y2-2),radius=2,fill=SCHOOL_STRIP)
                name_col=(30,27,75)
                loc_col=(67,56,142)
            else:
                name_col=(255,255,255)
                loc_col=(230,245,255)
            cont_before = it.get('cont_before', False)
            cont_after = it.get('cont_after', False)
            label = it['name']
            if cont_before:
                label = '← ' + label
            txt=fit(label,10)
            d.text((x+10,y1+8),txt,fill=name_col,font=f_body)
            if cont_after:
                d.text((x+10,y1+32),'→ 계속',fill=loc_col,font=f_small)
            else:
                d.text((x+10,y1+32),fit(it['location'],12),fill=loc_col,font=f_small)

    out_path=Path(args.out) if args.out else OUT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    print(str(out_path))

if __name__=='__main__':
    main()
