"""Unofficial ESPN NBA scoreboard/boxscore reader; read-only, no API key.

Returns NBA.com-compatible columns from completed regular-season games.
Never silently labels a partial download as a full-season dataset.
"""
import datetime as dt
import time
import requests
import pandas as pd
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
COLUMNS = ["PLAYER_ID","PLAYER_NAME","TEAM_ABBREVIATION","GAME_DATE","FG3M","FG3A","MIN","GAME_ID"]

class ESPNFeedError(RuntimeError):
    pass

def _session():
    s=requests.Session()
    s.headers.update({"User-Agent":"Mozilla/5.0 (compatible; NBA3PTResearch/1.0)",
                      "Accept":"application/json"})
    retry=Retry(total=2,connect=2,read=1,backoff_factor=0.7,
                status_forcelist=[429,500,502,503,504],
                allowed_methods=["GET"],respect_retry_after_header=True)
    s.mount("https://",HTTPAdapter(max_retries=retry))
    return s

def _json(s, endpoint, params):
    response=s.get(BASE+"/"+endpoint,params=params,timeout=(5,12))
    response.raise_for_status()
    result=response.json()
    if not isinstance(result,dict):
        raise ESPNFeedError("Unexpected ESPN response format")
    return result

def _minutes(raw):
    if raw is None: return None
    try:
        v=str(raw)
        if ":" in v:
            a,b=v.split(":",1)
            return round(float(a)+float(b)/60,2)
        return float(v)
    except (ValueError,TypeError):
        return None

def _parse_boxscore(payload, game_id, game_date):
    out=[]
    groups=payload.get("boxscore",{}).get("players",[])
    if not isinstance(groups,list):
        raise ESPNFeedError("Missing ESPN boxscore player groups")
    for group in groups:
        team=group.get("team",{}).get("abbreviation","")
        for section in group.get("statistics",[]):
            labels=[str(x).strip().upper() for x in section.get("labels",[])]
            three_idx=next((i for i,x in enumerate(labels) if x in ("3PT","3P","3PM-3PA")),None)
            min_idx=next((i for i,x in enumerate(labels) if x in ("MIN","MINUTES")),None)
            if three_idx is None:
                continue
            for athlete in section.get("athletes",[]):
                if athlete.get("didNotPlay") or athlete.get("inactive"):
                    continue
                stats=athlete.get("stats",[])
                if len(stats)<=three_idx: continue
                val=str(stats[three_idx]).strip()
                pieces=val.split("-")
                if len(pieces)!=2: continue
                try:
                    made,attempts=int(pieces[0]),int(pieces[1])
                except ValueError:
                    continue
                if made<0 or attempts<made: continue
                who=athlete.get("athlete",{})
                pid=who.get("id")
                name=who.get("displayName") or who.get("fullName")
                if not pid or not name or not team: continue
                minutes=_minutes(stats[min_idx]) if min_idx is not None and len(stats)>min_idx else None
                out.append({"PLAYER_ID":str(pid),"PLAYER_NAME":name,
                            "TEAM_ABBREVIATION":team,"GAME_DATE":game_date,
                            "FG3M":made,"FG3A":attempts,"MIN":minutes,"GAME_ID":str(game_id)})
    return out

def fetch_espn_logs(days=14, today=None, max_games=110):
    """Fetch recent completed NBA box scores. No look-ahead; returns logs and diagnostics.

    A window of days is NOT a full season. ESPN can block cloud requests.
    """
    days=int(days)
    if not 1<=days<=31: raise ValueError("days must be 1 to 31")
    today=today or dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date()
    if isinstance(today,str): today=dt.date.fromisoformat(today)
    session=_session()
    games={}
    failed_dates=[]
    failed_games=[]
    scoreboard_calls=0
    for offset in range(days,0,-1):
        day=today-dt.timedelta(days=offset)
        try:
            payload=_json(session,"scoreboard",{"dates":day.strftime("%Y%m%d"),"limit":50})
            scoreboard_calls+=1
            for event in payload.get("events",[]):
                if not event.get("status",{}).get("type",{}).get("completed",False):
                    continue
                # Regular season only; exclude preseason and playoffs.
                season=event.get("season",{})
                if season and season.get("type") not in (2,"2",None): continue
                games[str(event["id"])]=day.isoformat()
        except (requests.RequestException,ValueError,KeyError,ESPNFeedError) as exc:
            failed_dates.append(f"{day}: {type(exc).__name__}: {str(exc)[:100]}")
        time.sleep(0.08)
    rows=[]
    attempted=0
    for game_id,game_date in list(games.items())[:max_games]:
        attempted+=1
        try:
            payload=_json(session,"summary",{"event":game_id})
            parsed=_parse_boxscore(payload,game_id,game_date)
            if not parsed:
                raise ESPNFeedError("No player three-point statistics in boxscore")
            rows.extend(parsed)
        except (requests.RequestException,ValueError,KeyError,ESPNFeedError) as exc:
            failed_games.append(f"{game_id}: {type(exc).__name__}: {str(exc)[:100]}")
        time.sleep(0.08)
    df=pd.DataFrame(rows,columns=COLUMNS)
    if not df.empty:
        df=df.drop_duplicates(["PLAYER_ID","GAME_ID"]).sort_values("GAME_DATE")
    diag={"days_requested":days,"scoreboard_days_ok":scoreboard_calls,
          "scoreboard_days_failed":len(failed_dates),
          "completed_games_found":len(games),
          "games_attempted":attempted,"games_parsed":attempted-len(failed_games),
          "games_failed":len(failed_games),
          "players":int(df.PLAYER_ID.nunique()) if not df.empty else 0,
          "rows":len(df),
          "complete":not failed_dates and not failed_games and len(games)<=max_games,
          "errors":(failed_dates+failed_games)[:12]}
    if df.empty:
        raise ESPNFeedError("No usable ESPN player boxscores. ESPN may be blocking Streamlit Cloud. "+str(diag["errors"][:2]))
    return df,diag
