"""Persist ONLY 2026-27 regular-season NBA player 3PT box scores in repository CSV.

GitHub Actions executes this independently of Streamlit. A failed/partial run
never marks a game as collected. Repeated runs are idempotent.
"""
import argparse
import datetime as dt
import json
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from espn_stats import _session, _json, _parse_boxscore, COLUMNS

SEASON_START=dt.date(2026,10,1)
SEASON_END=dt.date(2027,4,30)
DATA=Path("data")
LOGS=DATA/"nba_2026_27_regular_season_3pt.csv"
MANIFEST=DATA/"nba_2026_27_manifest.json"

def read_existing():
    if not LOGS.exists():
        return pd.DataFrame(columns=COLUMNS)
    frame=pd.read_csv(LOGS,dtype={"PLAYER_ID":str,"GAME_ID":str})
    if not set(COLUMNS).issubset(frame.columns):
        raise ValueError("Stored CSV missing required columns")
    return frame[COLUMNS]

def run(as_of=None,delay=0.7):
    today=as_of or dt.datetime.now(ZoneInfo("America/New_York")).date()
    if isinstance(today,str): today=dt.date.fromisoformat(today)
    end=min(today-dt.timedelta(days=1),SEASON_END)
    if end<SEASON_START:
        print("No completed season dates yet")
        return
    DATA.mkdir(exist_ok=True)
    old=read_existing()
    state=json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    completed=set(str(x) for x in state.get("completed_game_ids",[]))
    session=_session()
    failures=[]
    discovered={}
    scoreboard_success=0
    for n in range((end-SEASON_START).days+1):
        day=SEASON_START+dt.timedelta(days=n)
        try:
            payload=_json(session,"scoreboard",{"dates":day.strftime("%Y%m%d"),"limit":50,"seasontype":2})
            scoreboard_success+=1
            for event in payload.get("events",[]):
                if not event.get("status",{}).get("type",{}).get("completed",False):
                    continue
                season=event.get("season",{})
                if season.get("type") not in (2,"2"): continue
                if season.get("year") not in (None,2026,"2026",2027,"2027"):
                    continue
                discovered[str(event["id"])]=day.isoformat()
        except Exception as exc:
            failures.append(f"scoreboard {day}: {type(exc).__name__}: {str(exc)[:90]}")
        time.sleep(delay)
    fresh=[]
    for game_id,day in sorted(discovered.items(),key=lambda x:x[1]):
        if game_id in completed: continue
        try:
            payload=_json(session,"summary",{"event":game_id})
            parsed=_parse_boxscore(payload,game_id,day)
            if not parsed: raise ValueError("Empty player boxscore")
            fresh.extend(parsed)
            completed.add(game_id)
        except Exception as exc:
            failures.append(f"boxscore {game_id}: {type(exc).__name__}: {str(exc)[:90]}")
        time.sleep(delay)
    combined=pd.concat([old,pd.DataFrame(fresh,columns=COLUMNS)],ignore_index=True)
    combined=combined.drop_duplicates(["PLAYER_ID","GAME_ID"],keep="last")
    if not combined.empty:
        combined["GAME_DATE"]=pd.to_datetime(combined["GAME_DATE"],errors="coerce").dt.date
        combined=combined[combined["GAME_DATE"].between(SEASON_START,SEASON_END)]
        combined["GAME_DATE"]=combined["GAME_DATE"].astype(str)
        combined=combined.sort_values(["GAME_DATE","GAME_ID","PLAYER_ID"])
    # Preserve previously successful games; do not remove them on API errors.
    combined.to_csv(LOGS,index=False)
    status={"season":"2026-27","season_type":"regular","last_checked":today.isoformat(),
            "through_date":end.isoformat(),"completed_game_ids":sorted(completed),
            "games_discovered":len(discovered),"games_stored":len(completed),
            "player_game_rows":len(combined),"scoreboard_days_success":scoreboard_success,
            "scoreboard_days_total":(end-SEASON_START).days+1,
            "failures":failures[:50],"complete":not failures}
    MANIFEST.write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps({k:v for k,v in status.items() if k not in ("completed_game_ids","failures")},indent=2))
    if failures:
        print("Incomplete fetches:",*failures[:10],sep="\n")
        # Persist successes; signal error so workflow flags incomplete ingestion.
        raise RuntimeError(f"{len(failures)} collection errors; successful games saved")

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--as-of",help="YYYY-MM-DD, for testing")
    args=parser.parse_args()
    run(as_of=args.as_of)
