"""Prospective, timestamped model predictions and settlement; no lookahead."""
import datetime as dt
import json
from pathlib import Path
import pandas as pd
from model import player_forecasts
from validation import validate_logs

PREDICTIONS=Path("data/model_predictions.csv")
COLUMNS=["prediction_date","created_at_utc","player_id","player","team","threshold","side","probability","game_id","result","correct","settled_at_utc"]

def select_candidates(forecasts, min_games=5, minimum=.90):
    if forecasts is None or forecasts.empty: return pd.DataFrame()
    parts=[]
    for side in ("YES",):
        t=forecasts.copy()
        t["side"]=side
        t["probability"]=t["fair_"+side.lower()]
        parts.append(t)
    candidates=pd.concat(parts,ignore_index=True)
    candidates=candidates[(candidates["games"]>=max(5,min_games)) &
                          (candidates["projected_pa"]>=1.5) &
                          (candidates["probability"]>=minimum)]
    return candidates.sort_values(["probability","games"],ascending=False).drop_duplicates("player_id")

def load_predictions(path=PREDICTIONS):
    if not path.exists():return pd.DataFrame(columns=COLUMNS)
    df=pd.read_csv(path,dtype={"player_id":str,"game_id":str})
    for c in COLUMNS:
        if c not in df.columns:df[c]=pd.NA
    return df[COLUMNS]

def settle_predictions(predictions, logs):
    if predictions.empty or logs.empty:return predictions
    predictions=predictions.copy()
    logs=logs.copy()
    logs["GAME_DATE"]=pd.to_datetime(logs["GAME_DATE"]).dt.strftime("%Y-%m-%d")
    # A player with no completed boxscore is NOT graded as a loss or win.
    lookup=logs.drop_duplicates(["PLAYER_ID","GAME_DATE"]).set_index(["PLAYER_ID","GAME_DATE"])
    for i,r in predictions[predictions["result"].isna()].iterrows():
        key=(str(r.player_id),str(r.prediction_date))
        if key not in lookup.index:continue
        actual=lookup.loc[key]
        made=int(actual["FG3M"])
        success=(made>=int(r.threshold)) if r.side=="YES" else (made<int(r.threshold))
        predictions.at[i,"result"]=made
        predictions.at[i,"correct"]=bool(success)
        predictions.at[i,"game_id"]=str(actual["GAME_ID"])
        predictions.at[i,"settled_at_utc"]=dt.datetime.now(dt.timezone.utc).isoformat()
    return predictions

def append_snapshot(predictions, forecasts, teams, prediction_date, now=None):
    """Append only once per date; never retroactively generate 'predictions'."""
    if not teams or prediction_date in set(predictions["prediction_date"].dropna().astype(str)):
        return predictions
    # Save every qualifying YES threshold for each player.
    picks=forecasts[forecasts["team"].isin(teams)].copy()
    picks=picks[(picks["games"]>=5) & (picks["projected_pa"]>=1.5) &
                (picks["fair_yes"]>=.90)].copy()
    if picks.empty:return predictions
    picks["side"]="YES"
    picks["probability"]=picks["fair_yes"]
    picks=picks.drop_duplicates(["player_id","threshold"]).sort_values(
        ["probability","games"],ascending=False)
    stamp=(now or dt.datetime.now(dt.timezone.utc)).isoformat()
    rows=[]
    for _,r in picks.iterrows():
        rows.append(dict(prediction_date=prediction_date,created_at_utc=stamp,
                         player_id=str(r.player_id),player=r.player,team=r.team,
                         threshold=int(r.threshold),side=r.side,
                         probability=float(r.probability),game_id=pd.NA,
                         result=pd.NA,correct=pd.NA,settled_at_utc=pd.NA))
    return pd.concat([predictions,pd.DataFrame(rows,columns=COLUMNS)],ignore_index=True)

def summarize(predictions):
    settled=predictions[predictions["correct"].notna()].copy()
    if settled.empty:return pd.DataFrame(columns=["Date","Correct","Total","Accuracy"])
    settled["correct"]=settled["correct"].astype(str).str.lower().eq("true")
    daily=settled.groupby("prediction_date")["correct"].agg(["sum","count"]).reset_index()
    daily["Accuracy"]=(daily["sum"]/daily["count"]*100).round(1)
    return daily.rename(columns={"prediction_date":"Date","sum":"Correct","count":"Total"})[["Date","Correct","Total","Accuracy"]]
