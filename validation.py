"""Validate 2026-27 ESPN player-game rows before forecasts."""
import pandas as pd

REQUIRED=("PLAYER_ID","PLAYER_NAME","TEAM_ABBREVIATION","GAME_DATE","FG3M","FG3A","GAME_ID")
START=pd.Timestamp("2026-10-01")
END=pd.Timestamp("2027-04-30")

def validate_logs(frame, manifest=None):
    problems=[]
    if frame is None or frame.empty:
        return pd.DataFrame(),{"valid":False,"errors":["No player-game data"],"warnings":[]}
    missing=set(REQUIRED)-set(frame.columns)
    if missing:
        return pd.DataFrame(),{"valid":False,"errors":[f"Missing columns: {sorted(missing)}"],"warnings":[]}
    df=frame.copy()
    df["PLAYER_ID"]=df["PLAYER_ID"].astype("string").str.strip()
    df["GAME_ID"]=df["GAME_ID"].astype("string").str.strip()
    df["PLAYER_NAME"]=df["PLAYER_NAME"].astype("string").str.strip()
    df["TEAM_ABBREVIATION"]=df["TEAM_ABBREVIATION"].astype("string").str.strip().str.upper()
    df["GAME_DATE"]=pd.to_datetime(df["GAME_DATE"],errors="coerce")
    for col in ("FG3M","FG3A"):
        df[col]=pd.to_numeric(df[col],errors="coerce")
    invalid=(df["GAME_DATE"].isna()|~df["GAME_DATE"].between(START,END)
             |df["FG3M"].isna()|df["FG3A"].isna()
             |(df["FG3M"]<0)|(df["FG3A"]<df["FG3M"])
             |df["PLAYER_ID"].isna()|(df["PLAYER_ID"]=="")
             |df["GAME_ID"].isna()|(df["GAME_ID"]=="")
             |df["PLAYER_NAME"].isna()|(df["PLAYER_NAME"]=="")
             |df["TEAM_ABBREVIATION"].isna()|(df["TEAM_ABBREVIATION"]==""))
    if invalid.any(): problems.append(f"{int(invalid.sum())} invalid player-game rows excluded")
    clean=df.loc[~invalid].copy()
    duplicates=int(clean.duplicated(["GAME_ID","PLAYER_ID"]).sum())
    if duplicates: problems.append(f"{duplicates} duplicate player-game rows excluded")
    clean=clean.drop_duplicates(["GAME_ID","PLAYER_ID"],keep="last")
    # One game must have one calendar date; a player must appear on only one team per game.
    inconsistent_dates=int((clean.groupby("GAME_ID")["GAME_DATE"].nunique()>1).sum())
    if inconsistent_dates: problems.append(f"{inconsistent_dates} games have conflicting dates")
    warnings=[]
    counts=clean.groupby("GAME_ID").size()
    sparse=counts[counts<10]
    if len(sparse): warnings.append(f"{len(sparse)} games have fewer than 10 player rows; inspect boxscores")
    if manifest:
        expected=set(map(str,manifest.get("completed_game_ids",[])))
        actual=set(clean["GAME_ID"].astype(str))
        missing_games=expected-actual
        if missing_games: problems.append(f"{len(missing_games)} completed games in manifest have no stored rows")
        if manifest.get("failures"): warnings.append(f"{len(manifest['failures'])} reported collection failures")
        if not manifest.get("complete",False): warnings.append("Collector did not confirm complete scoreboard coverage")
    diagnostics={"valid":not problems and not warnings,"errors":problems,"warnings":warnings,
                 "rows":len(clean),"games":int(clean["GAME_ID"].nunique()),
                 "players":int(clean["PLAYER_ID"].nunique()),
                 "earliest":clean["GAME_DATE"].min().date().isoformat() if len(clean) else None,
                 "latest":clean["GAME_DATE"].max().date().isoformat() if len(clean) else None,
                 "duplicate_rows":duplicates,"invalid_rows":int(invalid.sum()),
                 "sparse_games":len(sparse)}
    return clean,diagnostics

def season_hit_rates(logs, min_games=1):
    if logs is None or logs.empty: return pd.DataFrame()
    rows=[]
    for pid,group in logs.groupby("PLAYER_ID"):
        group=group.sort_values(["GAME_DATE","GAME_ID"],ascending=False)
        if len(group)<min_games: continue
        season_n=len(group)
        recent5=group.head(5)
        recent10=group.head(10)
        latest=group.iloc[0]
        for threshold in (1,2,3,4):
            rows.append({
                "player":str(latest["PLAYER_NAME"]),"team":str(latest["TEAM_ABBREVIATION"]),
                "player_id":str(pid),"threshold":threshold,"games":season_n,
                "season_hits":int((group["FG3M"]>=threshold).sum()),
                "season_hit_rate":round(float((group["FG3M"]>=threshold).mean()),4),
                "last5_hits":int((recent5["FG3M"]>=threshold).sum()),
                "last5_games":len(recent5),
                "last5_hit_rate":round(float((recent5["FG3M"]>=threshold).mean()),4),
                "last10_hits":int((recent10["FG3M"]>=threshold).sum()),
                "last10_games":len(recent10),
                "last10_hit_rate":round(float((recent10["FG3M"]>=threshold).mean()),4),
                "season_3pa":round(float(group["FG3A"].mean()),2),
                "season_3pm":round(float(group["FG3M"].mean()),2),
                "last5_3pa":round(float(recent5["FG3A"].mean()),2),
                "last5_3pm":round(float(recent5["FG3M"].mean()),2),
                "season_3p_pct":round(float(group["FG3M"].sum()/group["FG3A"].sum()),4)
                    if group["FG3A"].sum()>0 else None,
                "last_game":latest["GAME_DATE"].strftime("%Y-%m-%d"),
            })
    return pd.DataFrame(rows)
