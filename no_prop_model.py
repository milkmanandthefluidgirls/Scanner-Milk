"""Exploratory NO probabilities for NBA box-score count stats.

Simple season/recent negative-binomial mixture, with variance estimated from
observed games. NOT calibrated or validated against Kalshi prices.
"""
import math
import pandas as pd

THRESHOLDS={"PTS":(10,15,20,25,30,35),
            "REB":(4,6,8,10,12,14),
            "AST":(2,4,6,8,10,12),
            "STL":(1,2,3,4),
            "BLK":(1,2,3,4)}

def no_probability(mean,variance,threshold):
    """P(X<threshold) under Poisson or overdispersed negative binomial."""
    if threshold<=0:return 0.
    if mean<=0:return 1.
    if variance<=mean*1.01:
        p=math.exp(-mean)
        total=p
        for k in range(1,threshold):
            p*=mean/k
            total+=p
        return min(1.,max(0.,total))
    size=max(.5,mean*mean/(variance-mean))
    success=size/(size+mean)
    p=success**size
    total=p
    for k in range(1,threshold):
        p*=(k-1+size)/k*(1-success)
        total+=p
    return min(1.,max(0.,total))

def forecast_props(logs,min_games=8):
    columns=["player","team","player_id","prop","threshold","selection",
        "model_no_pct","season_no_pct","last5_no_pct","games",
        "projected_mean","no_price_cents","net_edge_pct","status"]
    if logs is None or logs.empty:return pd.DataFrame(columns=columns)
    if not {"PLAYER_ID","PLAYER_NAME","TEAM_ABBREVIATION","GAME_DATE","GAME_ID"}.issubset(logs):
        return pd.DataFrame(columns=columns)
    rows=[]
    for pid,g in logs.groupby("PLAYER_ID"):
        g=g.sort_values(["GAME_DATE","GAME_ID"])
        if len(g)<min_games:continue
        recent=g.tail(5)
        last=g.iloc[-1]
        for stat,thresholds in THRESHOLDS.items():
            if stat not in g:continue
            season=pd.to_numeric(g[stat],errors="coerce")
            last5=pd.to_numeric(recent[stat],errors="coerce")
            if season.isna().any() or last5.isna().any():continue
            mu=max(0.,.65*float(season.mean())+.35*float(last5.mean()))
            v=max(mu,.65*float(season.var(ddof=1))+
                .35*float(last5.var(ddof=1))+
                .65*.35*(float(season.mean())-float(last5.mean()))**2)
            for threshold in thresholds:
                rows.append(dict(player=str(last.PLAYER_NAME),
                    team=str(last.TEAM_ABBREVIATION),player_id=str(pid),
                    prop=stat,threshold=threshold,selection="NO",
                    model_no_pct=round(no_probability(mu,v,threshold)*100,8),
                    season_no_pct=round(float((season<threshold).mean())*100,1),
                    last5_no_pct=round(float((last5<threshold).mean())*100,1),
                    games=len(g),projected_mean=round(mu,2),
                    no_price_cents=float("nan"),net_edge_pct=float("nan"),
                    status="UNPRICED — UNCALIBRATED"))
    return pd.DataFrame(rows,columns=columns)

def backtest(logs,min_games=8):
    """Walk-forward test with prior games only; returns Brier by stat/threshold."""
    if logs is None or logs.empty:return pd.DataFrame()
    observations=[]
    for pid,g in logs.groupby("PLAYER_ID"):
        g=g.sort_values(["GAME_DATE","GAME_ID"]).reset_index(drop=True)
        for i in range(min_games,len(g)):
            history=g.iloc[:i]
            current=g.iloc[i]
            for stat,thresholds in THRESHOLDS.items():
                series=pd.to_numeric(history[stat],errors="coerce")
                if series.isna().any():continue
                recent=series.tail(5)
                mu=max(0.,.65*float(series.mean())+.35*float(recent.mean()))
                variance=max(mu,.65*float(series.var(ddof=1))+
                    .35*float(recent.var(ddof=1))+
                    .65*.35*(float(series.mean())-float(recent.mean()))**2)
                for threshold in thresholds:
                    p=no_probability(mu,variance,threshold)
                    actual=int(float(current[stat])<threshold)
                    observations.append(dict(prop=stat,threshold=threshold,
                        predicted=p,actual=actual,brier=(p-actual)**2))
    if not observations:return pd.DataFrame()
    results=pd.DataFrame(observations)
    return results.groupby(["prop","threshold"]).agg(
        N=("actual","size"),Actual_NO=("actual","mean"),
        Predicted_NO=("predicted","mean"),Brier=("brier","mean")).reset_index()
