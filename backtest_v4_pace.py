"""Strict historical V4 matchup backtest. Run after collect_matchups.py.

Uses NBA pace and opponent 3PA from PRIOR games only. Missing matchup
history is excluded from the matched comparison, never filled from the future.
Exploratory coefficients; do not promote to production without holdout testing.
"""
from pathlib import Path
import pandas as pd
from model import forecast_probabilities

ROOT=Path(__file__).parent
PLAYER=ROOT/"data/nba_2026_27_regular_season_3pt.csv"
MATCHUPS=ROOT/"data/nba_2026_27_team_matchups.csv"

def backtest():
    if not MATCHUPS.exists():
        print("NOT RUN: real NBA pace dataset missing. Run python collect_matchups.py first.")
        return
    logs=pd.read_csv(PLAYER,dtype={"PLAYER_ID":str,"GAME_ID":str})
    matchup=pd.read_csv(MATCHUPS,dtype={"GAME_ID":str})
    required={"GAME_ID","GAME_DATE","TEAM_ABBREVIATION","OPPONENT","PACE","OPP_FG3A"}
    if not required.issubset(matchup.columns):
        raise ValueError("Incomplete pace dataset")
    matchup=matchup.drop_duplicates(["GAME_ID","TEAM_ABBREVIATION"])
    lookup={(str(r.GAME_ID),r.TEAM_ABBREVIATION):r for r in matchup.itertuples()}
    team_history={t:g.sort_values("GAME_DATE") for t,g in matchup.groupby("TEAM_ABBREVIATION")}
    records=[]
    for pid,g in logs.groupby("PLAYER_ID"):
        g=g.sort_values(["GAME_DATE","GAME_ID"]).reset_index(drop=True)
        for i in range(5,len(g)):
            target=g.iloc[i]
            key=(str(target.GAME_ID),target.TEAM_ABBREVIATION)
            current=lookup.get(key)
            if current is None:continue
            prior_opp=team_history.get(current.OPPONENT)
            prior_team=team_history.get(current.TEAM_ABBREVIATION)
            if prior_opp is None or prior_team is None:continue
            prior_opp=prior_opp[prior_opp.GAME_DATE<target.GAME_DATE]
            prior_team=prior_team[prior_team.GAME_DATE<target.GAME_DATE]
            prior_league=matchup[matchup.GAME_DATE<target.GAME_DATE]
            if min(len(prior_opp),len(prior_team))<5 or len(prior_league)<60:continue
            history=g.iloc[:i]
            recent=history.tail(5)
            season=float(history.FG3A.mean())
            last=float(recent.FG3A.mean())
            mu=max(0.,.65*season+.35*last)
            variance=max(mu,.65*float(history.FG3A.var(ddof=1))
                +.35*float(recent.FG3A.var(ddof=1))
                +.65*.35*(season-last)**2)
            makes=float(history.FG3M.sum())
            attempts=float(history.FG3A.sum())
            a=25.2+makes
            b=44.8+attempts-makes
            baseline=forecast_probabilities(mu,variance,a,b,max_threshold=4)
            league_pace=float(prior_league.PACE.mean())
            team_pace=float(prior_team.PACE.mean())
            opp_pace=float(prior_opp.PACE.mean())
            expected_pace=(team_pace+opp_pace)/2
            pace_factor=max(.90,min(1.10,expected_pace/max(1.,league_pace)))
            # Opponent volume relative to league, shrunk and capped.
            allowed=float(prior_opp.OPP_FG3A.mean())
            league_allowed=float(prior_league.OPP_FG3A.mean())
            volume_factor=max(.90,min(1.10,((allowed+10)/(league_allowed+10))**.35))
            factor=pace_factor*volume_factor
            candidate=forecast_probabilities(mu*factor,
                max(mu*factor,variance*factor**2),a,b,max_threshold=4)
            for t in range(1,5):
                records.append({"date":target.GAME_DATE,"threshold":t,
                    "actual":int(target.FG3M>=t),"v1":baseline[t],"v4":candidate[t]})
    results=pd.DataFrame(records)
    if results.empty:
        print("INSUFFICIENT DATA: no matched forecasts with five prior opponent pace games.")
        return
    print("Matched player-games:",len(results)//4)
    print("No injury/lineup adjustments: those require timestamped historical availability data.")
    for t,g in results.groupby("threshold"):
        print(f"{t}+ threes")
        for model in ("v1","v4"):
            p=g[model].clip(1e-6,1-1e-6)
            brier=float(((p-g.actual)**2).mean())
            print(f"  {model}: Brier={brier:.6f} mean_pred={p.mean():.4f} actual={g.actual.mean():.4f}")
    return results

if __name__=="__main__":
    backtest()
