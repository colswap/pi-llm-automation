#!/usr/bin/env python3
import argparse, json, subprocess
from datetime import datetime
from pathlib import Path

BASE=Path('/home/user/workspace/timetable/base.json')
OVR=Path('/home/user/workspace/timetable/overrides.json')
WD=['Mon','Tue','Wed','Thu','Fri','Sat','Sun']


def load():
    return json.loads(BASE.read_text()), json.loads(OVR.read_text())

def save_ovr(o):
    OVR.write_text(json.dumps(o,ensure_ascii=False,indent=2))

def day_items(day):
    b,o=load(); dt=datetime.fromisoformat(day)
    dname=WD[dt.weekday()]
    items=[{'source':'school','name':c['name'],'start':c['start'],'end':c['end'],'location':c.get('location','-')} for c in b['courses'] if dname in c['days']]
    canc={(x['date'],x['name']) for x in o.get('cancel',[])}
    items=[x for x in items if (day,x['name']) not in canc]
    for m in o.get('makeup',[]):
        if m['date']==day:
            items.append({'source':'school','name':m['name'],'start':m['start'],'end':m['end'],'location':m.get('location','-')})
    return sorted(items,key=lambda x:x['start'])

def google_items(day,account):
    proc=subprocess.run([
        'gog','calendar','events',account,
        '--from',f'{day}T00:00:00+09:00',
        '--to',f'{day}T23:59:59+09:00',
        '--json','--account',account,'--no-input'
    ], capture_output=True, text=True)
    if proc.returncode != 0:
        # Fail-open: if Google source is unavailable, keep school timetable output.
        return []
    ev=json.loads(proc.stdout).get('events',[])
    res=[]
    for e in ev:
        s=e.get('start',{}).get('dateTime','')
        t=e.get('end',{}).get('dateTime','')
        if not s or not t: continue
        res.append({'source':'google','name':e.get('summary','(제목없음)'), 'start':s[11:16], 'end':t[11:16], 'location':e.get('location','-')})
    return sorted(res,key=lambda x:x['start'])

def cmd_show(args):
    s=day_items(args.date)+google_items(args.date,args.account)
    s=sorted(s,key=lambda x:x['start'])
    print(f"[{args.date}] 최종 일정")
    for i in s:
        tag='학교' if i['source']=='school' else '구글'
        print(f"- {i['start']}-{i['end']} [{tag}] {i['name']} ({i['location']})")

def cmd_cancel_add(args):
    _,o=load(); o.setdefault('cancel',[]).append({'date':args.date,'name':args.name}); save_ovr(o); print('ok')

def cmd_cancel_remove(args):
    _,o=load(); o['cancel']=[x for x in o.get('cancel',[]) if not (x['date']==args.date and x['name']==args.name)]; save_ovr(o); print('ok')

def cmd_makeup_add(args):
    _,o=load(); o.setdefault('makeup',[]).append({'date':args.date,'start':args.start,'end':args.end,'name':args.name,'location':args.location}); save_ovr(o); print('ok')

ap=argparse.ArgumentParser()
sp=ap.add_subparsers(dest='cmd',required=True)

p=sp.add_parser('show'); p.add_argument('--date',required=True); p.add_argument('--account',default='user@gmail.com'); p.set_defaults(func=cmd_show)
p=sp.add_parser('cancel-add'); p.add_argument('--date',required=True); p.add_argument('--name',required=True); p.set_defaults(func=cmd_cancel_add)
p=sp.add_parser('cancel-remove'); p.add_argument('--date',required=True); p.add_argument('--name',required=True); p.set_defaults(func=cmd_cancel_remove)
p=sp.add_parser('makeup-add'); p.add_argument('--date',required=True); p.add_argument('--start',required=True); p.add_argument('--end',required=True); p.add_argument('--name',required=True); p.add_argument('--location',default='-'); p.set_defaults(func=cmd_makeup_add)

args=ap.parse_args(); args.func(args)
