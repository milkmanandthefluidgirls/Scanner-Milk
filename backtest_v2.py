"""Chronological, out-of-sample comparison of current Milkball and experimental V2.
Run: python backtest_v2.py
Uses saved game logs, never future games to predict an earlier game.
"""
import math
from pathlib import Path
import pandas as pd
from model import player_forecasts, forecast_probabilities

DATA=Path(__file__).parent/"data/nba_2026_27_regular_season_3pt.csv"

def candidate(g):
    recent=g.tail(5)
    season_min=float(g.MIN.mean())
    recent_min=float(recent.MIN.mean())
    minutes=.45*season_min+.55*recent_min
    season_rate=float(g.FG3A.sum())/max(1.,float(g.MIN.sum()))
    recent_rate=float(recent.FG3A.sum())/max(1.,float(recent.MIN.sum()))
    mean=max(0.,minutes*(.7*season_rate+.3*recent_rate))
    season_pa=float(g.FG3A.mean())
    recent_pa=float(recent.FG3A.mean())
    season_var=float(g.FG3A.var(ddof=1))
    recent_var=float(recent.FG3A.var(ddof=1))
    variance=max(mean,mean+.65*max(0.,season_var-season_pa)
                 +.35*max(0.,recent_var-recent_pa)
                 +.3*(mean-(.65*season_pa+.35*recent_pa))**2)
    makes=float(g.FG3M.sum())
    attempts=float(g.FG3A.sum())
    return forecast_probabilities(mean,variance,25.2+makes,44.8+attempts-makes,max_threshold=4)

def backtest():
    logs=pd.read_csv(DATA,dtype={"PLAYER_ID":str,"GAME_ID":str})
    logs=logs.sort_values(["GAME_DATE","GAME_ID"])
    records=[]
    for pid,group in logs.groupby("PLAYER_ID",sort=False):
        group=group.sort_values(["GAME_DATE","GAME_ID"]).reset_index(drop=True)
        for i in range(5,len(group)):
            history=group.iloc[:i].copy()
            actual=group.iloc[i]
            # The existing model uses newest-first input internally.
            base=player_forecasts(history,min_games=5)
            if base.empty:continue
            new=candidate(history)
            for threshold in (1,2,3,4):
                old=float(base.loc[base.threshold==threshold,"fair_yes"].iloc[0])
                records.append(dict(date=actual.GAME_DATE,player_id=pid,
                    threshold=threshold,actual=int(actual.FG3M>=threshold),
                    v1=old,v2=float(new[threshold])))
    df=pd.DataFrame(records)
    if df.empty:raise RuntimeError("Not enough historical games for backtest")
    print(f"Historical player-games: {len(df)//4}; range {df.date.min()} to {df.date.max()}")
    for threshold,part in df.groupby("threshold"):
        print(f"\\n{threshold}+ threes ({len(part)} forecasts; hit rate {part.actual.mean():.3f})")
        for name in ("v1","v2"):
            p=part[name].clip(1e-6,1-1e-6)
            y=part.actual
            brier=float(((p-y)**2).mean())
            logloss=float((-(y*p.map(math.log)+(1-y)*(1-p).map(math.log))).mean())
            print(f"  {name}: Brier={brier:.5f} log_loss={logloss:.5f} predicted={p.mean():.3f}")
    return df

if __name__=="__main__":
    backtest()
