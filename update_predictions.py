"""Daily prospective predictions, then settlement from saved completed game logs."""
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import requests
from model import player_forecasts
from validation import validate_logs
from tracking import PREDICTIONS,load_predictions,settle_predictions,append_snapshot,append_prop_rows
from no_prop_model import forecast_props

def today_teams(date):
    # ESPN public scoreboard; do not invent matchups if unavailable.
    url="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
    r=requests.get(url,params={"dates":date.replace("-",""),"limit":100},timeout=20)
    r.raise_for_status()
    teams=set()
    now=dt.datetime.now(dt.timezone.utc)
    for event in r.json().get("events",[]):
        # Never create a pregame snapshot after the event has started.
        start=event.get("date")
        if not start:continue
        try:
            start_utc=dt.datetime.fromisoformat(start.replace("Z","+00:00"))
        except ValueError:
            continue
        if start_utc<=now:continue
        if event.get("status",{}).get("type",{}).get("state")!="pre":continue
        for competition in event.get("competitions",[]):
            for entry in competition.get("competitors",[]):
                team=entry.get("team",{})
                abbreviation=team.get("abbreviation")
                if abbreviation:
                    normalized={"WSH":"WAS","GS":"GSW","NY":"NYK","SA":"SAS","NO":"NOP","UTAH":"UTA"}.get(abbreviation.upper(),abbreviation.upper())
                    teams.add(normalized)
    return teams

def main():
    path=Path("data/nba_2026_27_regular_season_3pt.csv")
    if not path.exists():raise RuntimeError("Season logs missing")
    logs=pd.read_csv(path,dtype={"PLAYER_ID":str,"GAME_ID":str})
    logs,quality=validate_logs(logs)
    if quality["errors"]:raise RuntimeError(str(quality["errors"]))
    today=dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    prop_path=Path("data/nba_2026_27_player_props.csv")
    prop_logs=pd.read_csv(prop_path,dtype={"PLAYER_ID":str,"GAME_ID":str}) if prop_path.exists() else pd.DataFrame()
    predictions=settle_predictions(load_predictions(),logs)
    if not prop_logs.empty:
        predictions=settle_predictions(predictions,prop_logs)
    # No same-day completed games enter today's pregame forecast.
    historical=logs[pd.to_datetime(logs["GAME_DATE"]).dt.strftime("%Y-%m-%d")<today]
    forecasts=player_forecasts(historical,min_games=5)
    teams=today_teams(today)
    if not teams:print("No scheduled NBA teams today; settlement only")
    else:
        predictions=append_snapshot(predictions,forecasts,teams,today)
        if not prop_logs.empty:
            historical_props=prop_logs[pd.to_datetime(prop_logs["GAME_DATE"]).dt.strftime("%Y-%m-%d")<today]
            props=forecast_props(historical_props,min_games=8)
            if not props.empty:
                props=props[props["team"].isin(teams)&(props["model_no_pct"]>=70)]
                stamp=dt.datetime.now(dt.timezone.utc).isoformat()
                rows=pd.DataFrame([dict(prediction_date=today,created_at_utc=stamp,
                    player_id=str(r.player_id),player=r.player,team=r.team,
                    prop=r.prop,threshold=int(r.threshold),side="NO",
                    probability=float(r.model_no_pct)/100,game_id=pd.NA,
                    result=pd.NA,correct=pd.NA,settled_at_utc=pd.NA)
                    for r in props.itertuples()])
                predictions=append_prop_rows(predictions,rows)
    PREDICTIONS.parent.mkdir(parents=True,exist_ok=True)
    predictions.to_csv(PREDICTIONS,index=False)
    print(f"Predictions: {len(predictions)}, settled: {predictions['correct'].notna().sum()}")

if __name__=="__main__":main()
