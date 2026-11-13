"""Daily prospective predictions, then settlement from saved completed game logs."""
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import requests
from model import player_forecasts
from validation import validate_logs
from tracking import PREDICTIONS,load_predictions,settle_predictions,append_snapshot

def today_teams(date):
    # ESPN public scoreboard; do not invent matchups if unavailable.
    url="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
    r=requests.get(url,params={"dates":date.replace("-",""),"limit":100},timeout=20)
    r.raise_for_status()
    teams=set()
    for event in r.json().get("events",[]):
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
    predictions=settle_predictions(load_predictions(),logs)
    # No same-day completed games enter today's pregame forecast.
    historical=logs[pd.to_datetime(logs["GAME_DATE"]).dt.strftime("%Y-%m-%d")<today]
    forecasts=player_forecasts(historical,min_games=5)
    teams=today_teams(today)
    if not teams:print("No scheduled NBA teams today; settlement only")
    else:predictions=append_snapshot(predictions,forecasts,teams,today)
    PREDICTIONS.parent.mkdir(parents=True,exist_ok=True)
    predictions.to_csv(PREDICTIONS,index=False)
    print(f"Predictions: {len(predictions)}, settled: {predictions['correct'].notna().sum()}")

if __name__=="__main__":main()
