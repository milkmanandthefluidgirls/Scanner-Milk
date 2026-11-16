"""Collect additional NBA player box-score stats for NO prop research.

Uses the SAME ESPN game IDs already saved by the 3PT collector. Historical
games are fetched independently so existing games can be backfilled. A game is
marked complete only when both teams' player rows and required stats exist.
"""
import datetime as dt
import time
from pathlib import Path
import pandas as pd
from espn_stats import _session, _json, _minutes

SOURCE=Path("data/nba_2026_27_regular_season_3pt.csv")
OUTPUT=Path("data/nba_2026_27_player_props.csv")
COLUMNS=["PLAYER_ID","PLAYER_NAME","TEAM_ABBREVIATION","GAME_DATE","GAME_ID",
         "MIN","PTS","REB","AST","STL","BLK","FG3M","FG3A"]

def parse(payload,gid,date):
    rows=[]
    for group in payload.get("boxscore",{}).get("players",[]):
        team=group.get("team",{}).get("abbreviation","")
        for section in group.get("statistics",[]):
            labels=[str(x).strip().upper() for x in section.get("labels",[])]
            indexes={key:next((i for i,label in enumerate(labels) if label in variants),None)
                for key,variants in {
                    "PTS":("PTS","POINTS"),"REB":("REB","REBOUNDS"),
                    "AST":("AST","ASSISTS"),"STL":("STL","STEALS"),
                    "BLK":("BLK","BLOCKS"),"MIN":("MIN","MINUTES"),
                    "FG3":("3PT","3P","3PM-3PA")}.items()}
            if any(indexes[k] is None for k in ("PTS","REB","AST","STL","BLK","FG3")):
                continue
            for athlete in section.get("athletes",[]):
                if athlete.get("didNotPlay") or athlete.get("inactive"):
                    continue
                values=athlete.get("stats",[])
                if not values:continue
                person=athlete.get("athlete",{})
                pid=person.get("id")
                name=person.get("displayName") or person.get("fullName")
                if not pid or not name or not team:continue
                try:
                    counts={k:int(str(values[indexes[k]]).strip())
                            for k in ("PTS","REB","AST","STL","BLK")}
                    three=str(values[indexes["FG3"]]).split("-")
                    made,attempts=int(three[0]),int(three[1])
                    minutes=_minutes(values[indexes["MIN"]]) if indexes["MIN"] is not None else None
                    if min(*counts.values(),made,attempts)<0 or made>attempts:continue
                except (ValueError,TypeError,IndexError):
                    continue
                rows.append(dict(PLAYER_ID=str(pid),PLAYER_NAME=name,
                    TEAM_ABBREVIATION=team,GAME_DATE=str(date),GAME_ID=str(gid),
                    MIN=minutes,FG3M=made,FG3A=attempts,**counts))
    return rows

def collect(delay=.25):
    if not SOURCE.exists():raise RuntimeError("Missing saved season game IDs")
    source=pd.read_csv(SOURCE,dtype={"GAME_ID":str,"PLAYER_ID":str})
    existing=(pd.read_csv(OUTPUT,dtype={"GAME_ID":str,"PLAYER_ID":str})
              if OUTPUT.exists() else pd.DataFrame(columns=COLUMNS))
    if not set(COLUMNS).issubset(existing.columns):
        raise ValueError("Existing prop data missing required columns")
    completed=set(existing.GAME_ID.astype(str))
    games=source[["GAME_ID","GAME_DATE"]].drop_duplicates().sort_values("GAME_DATE")
    session=_session()
    new=[]
    failures=[]
    for row in games.itertuples(index=False):
        gid=str(row.GAME_ID)
        if gid in completed:continue
        try:
            payload=_json(session,"summary",{"event":gid})
            parsed=parse(payload,gid,row.GAME_DATE)
            teams={x["TEAM_ABBREVIATION"] for x in parsed}
            expected=set(source.loc[source.GAME_ID==gid,"TEAM_ABBREVIATION"])
            if len(teams)!=2 or teams!=expected or len(parsed)<10:
                raise ValueError("Incomplete or incompatible player prop boxscore")
            new.extend(parsed)
            completed.add(gid)
        except Exception as exc:
            failures.append((gid,str(exc)[:120]))
        time.sleep(delay)
    if new:
        combined=pd.concat([existing,pd.DataFrame(new,columns=COLUMNS)],ignore_index=True)
        combined=combined.drop_duplicates(["GAME_ID","PLAYER_ID"],keep="last")
        combined=combined.sort_values(["GAME_DATE","GAME_ID","PLAYER_ID"])
        OUTPUT.parent.mkdir(parents=True,exist_ok=True)
        combined.to_csv(OUTPUT,index=False)
    print(f"Prop games: {len(completed)}/{len(games)}; new rows: {len(new)}; failures: {len(failures)}")
    if failures:
        print("First errors:",failures[:5])
        raise RuntimeError(f"{len(failures)} prop boxscore fetches incomplete; successes preserved")

if __name__=="__main__":
    collect()
