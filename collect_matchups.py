"""Collect true per-game NBA pace and opponent 3PA from NBA box scores.

Research only. Does not change live forecasts.
Run: python collect_matchups.py
The NBA stats endpoint may throttle or block CI; failures are reported and
existing data are preserved. Never substitute fabricated pace values.
"""
import json
import time
from pathlib import Path
import pandas as pd
from nba_api.stats.endpoints import boxscoreadvancedv3

ROOT=Path(__file__).parent
LOGS=ROOT/"data/nba_2026_27_regular_season_3pt.csv"
OUTPUT=ROOT/"data/nba_2026_27_team_matchups.csv"

def collect():
    logs=pd.read_csv(LOGS,dtype={"GAME_ID":str})
    existing=(pd.read_csv(OUTPUT,dtype={"GAME_ID":str}) if OUTPUT.exists()
              else pd.DataFrame())
    completed=set(existing.GAME_ID.astype(str)) if not existing.empty else set()
    rows=[]
    failures=[]
    for gid,g in logs.groupby("GAME_ID",sort=False):
        gid=str(gid)
        if gid in completed:continue
        teams=list(g.TEAM_ABBREVIATION.unique())
        if len(teams)!=2:continue
        try:
            result=boxscoreadvancedv3.BoxScoreAdvancedV3(
                game_id=gid.zfill(10),timeout=20)
            payload=result.get_dict()
            data=payload.get("boxScoreAdvanced",{})
            team_rows=data.get("homeTeam"),data.get("awayTeam")
            if any(not isinstance(t,dict) for t in team_rows):
                raise ValueError("NBA advanced response missing team data")
            for team in team_rows:
                abbrev=team.get("teamTricode")
                if abbrev not in teams:
                    raise ValueError("Team mismatch in advanced response")
                pace=team.get("statistics",{}).get("pace")
                if pace is None or not 60<float(pace)<140:
                    raise ValueError("Missing or invalid true NBA pace")
                opponent=next(t for t in teams if t!=abbrev)
                opp=g[g.TEAM_ABBREVIATION==opponent]
                rows.append(dict(GAME_ID=gid,GAME_DATE=str(g.GAME_DATE.iloc[0]),
                    TEAM_ABBREVIATION=abbrev,OPPONENT=opponent,
                    PACE=float(pace),OPP_FG3A=int(opp.FG3A.sum()),
                    OPP_FG3M=int(opp.FG3M.sum()),
                    SOURCE="nba_boxscoreadvancedv3"))
            print("Fetched",gid,flush=True)
        except Exception as exc:
            failures.append((gid,str(exc)))
            print("Unavailable",gid,str(exc),flush=True)
        time.sleep(0.65)
    combined=pd.concat([existing,pd.DataFrame(rows)],ignore_index=True)
    if not combined.empty:
        combined=combined.drop_duplicates(["GAME_ID","TEAM_ABBREVIATION"],keep="last")
        OUTPUT.parent.mkdir(parents=True,exist_ok=True)
        combined.to_csv(OUTPUT,index=False)
    print(json.dumps({"new_rows":len(rows),"failed_games":len(failures),
                      "total_rows":len(combined)}))
    if failures:
        raise RuntimeError(f"{len(failures)} game(s) unavailable; rerun to retry")

if __name__=="__main__":
    collect()
