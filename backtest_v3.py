"""Experimental opponent-aware V3 backtest. Does NOT change live Milkball predictions.

Run: python backtest_v3.py
Opponent 3PA allowed and 3P% allowed are reconstructed from player game logs.
True pace (possessions/48) and opponent defensive rating are NOT in this dataset.
Every prediction uses only games dated strictly before the target game.
Coefficients are exploratory, not optimized on an independent training set.
"""
import math
from pathlib import Path
import pandas as pd
from model import forecast_probabilities

DATA=Path(__file__).parent/"data/nba_2026_27_regular_season_3pt.csv"

def clamp(x,lo,hi):
    return max(lo,min(hi,x))

def backtest():
    df=pd.read_csv(DATA,dtype={"PLAYER_ID":str,"GAME_ID":str})
    df=df.sort_values(["GAME_DATE","GAME_ID"])
    games={}
    teams={}
    for gid,g in df.groupby("GAME_ID",sort=False):
        names=list(g.TEAM_ABBREVIATION.unique())
        if len(names)!=2:continue
        date=str(g.GAME_DATE.iloc[0])
        games[str(gid)]=(date,float(g.FG3A.sum()),float(g.FG3M.sum()))
        for team in names:
            other=g[g.TEAM_ABBREVIATION!=team]
            teams.setdefault(team,[]).append(dict(date=date,id=str(gid),
                allowed=float(other.FG3A.sum()),
                made=float(other.FG3M.sum())))
    for history in teams.values():
        history.sort(key=lambda x:(x["date"],x["id"]))
    # Map each (game,team) to its opponent.
    opponents={}
    for gid,g in df.groupby("GAME_ID",sort=False):
        names=list(g.TEAM_ABBREVIATION.unique())
        if len(names)!=2:continue
        opponents[(str(gid),names[0])]=names[1]
        opponents[(str(gid),names[1])]=names[0]
    records=[]
    for pid,g in df.groupby("PLAYER_ID",sort=False):
        g=g.sort_values(["GAME_DATE","GAME_ID"]).reset_index(drop=True)
        for i in range(5,len(g)):
            target=g.iloc[i]
            history=g.iloc[:i]
            recent=history.tail(5)
            season=float(history.FG3A.mean())
            last=float(recent.FG3A.mean())
            mu=max(0.,.65*season+.35*last)
            variance=max(mu,.65*float(history.FG3A.var(ddof=1))
                +.35*float(recent.FG3A.var(ddof=1))
                +.65*.35*(season-last)**2)
            attempts=float(history.FG3A.sum())
            makes=float(history.FG3M.sum())
            alpha=25.2+makes
            beta=44.8+attempts-makes
            base=forecast_probabilities(mu,variance,alpha,beta,max_threshold=4)
            date=str(target.GAME_DATE)
            opponent=opponents.get((str(target.GAME_ID),str(target.TEAM_ABBREVIATION)))
            opponent_history=[x for x in teams.get(opponent,[]) if x["date"]<date]
            earlier=[v for d,*v in games.values() if d<date]
            multiplier=1.
            pct_delta=0.
            if len(opponent_history)>=3 and len(earlier)>=15:
                league_3pa=sum(v[0] for v in earlier)/(2*len(earlier))
                allowed=sum(x["allowed"] for x in opponent_history)/len(opponent_history)
                multiplier=clamp(((allowed+5)/(league_3pa+5))**.5,.85,1.15)
                opp_pct=sum(x["made"] for x in opponent_history)/max(
                    1.,sum(x["allowed"] for x in opponent_history))
                league_pct=sum(v[1] for v in earlier)/max(1.,sum(v[0] for v in earlier))
                pct_delta=clamp(.15*(opp_pct-league_pct),-.025,.025)
            adjusted_mu=mu*multiplier
            adjusted_var=max(adjusted_mu,variance*multiplier**2)
            pct=clamp(alpha/(alpha+beta)+pct_delta,.05,.65)
            strength=alpha+beta
            candidate=forecast_probabilities(adjusted_mu,adjusted_var,
                pct*strength,(1-pct)*strength,max_threshold=4)
            for t in range(1,5):
                records.append(dict(date=date,player_id=pid,threshold=t,
                    actual=int(target.FG3M>=t),v1=base[t],v3=candidate[t],
                    opponent_games=len(opponent_history)))
    result=pd.DataFrame(records)
    print(f"Player-games: {len(result)//4}; dates {result.date.min()}–{result.date.max()}")
    print("V3 uses opponent 3PA allowed / opponent 3P% allowed; NOT true pace or defensive rating.")
    for t,part in result.groupby("threshold"):
        print(f"\\n{t}+ threes: observed={part.actual.mean():.4f}")
        for key in ("v1","v3"):
            p=part[key].clip(1e-6,1-1e-6)
            y=part.actual
            brier=float(((p-y)**2).mean())
            loss=float((-(y*p.map(math.log)+(1-y)*(1-p).map(math.log))).mean())
            print(f" {key}: Brier={brier:.6f}, logloss={loss:.6f}, mean_probability={p.mean():.4f}")
    return result

if __name__=="__main__":
    backtest()
