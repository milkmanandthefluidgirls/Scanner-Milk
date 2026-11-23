"""Read-only, non-overlapping diagnostics and price-gated strategy research."""
import pandas as pd

def prepare(predictions):
    d=predictions.copy()
    if d.empty:return d
    if "prop" not in d:d["prop"]="3PT made"
    d["prop"]=d["prop"].fillna("3PT made")
    d["probability"]=pd.to_numeric(d["probability"],errors="coerce")
    d["threshold"]=pd.to_numeric(d["threshold"],errors="coerce")
    d=d[d["correct"].notna() & d["probability"].between(0,1)].copy()
    d["won"]=d["correct"].astype(str).str.lower().eq("true")
    d["player_game"]=d["prediction_date"].astype(str)+"|"+d["player_id"].astype(str)
    d["nba_game"]=d["prediction_date"].astype(str)+"|"+d["game_id"].astype(str)
    d["band"]=pd.cut(d["probability"],[0,.8,.9,.95,.98,.995,1.00000001],
        labels=["<80%","80–89.9%","90–94.9%","95–97.9%","98–99.4%","99.5–100%"],
        include_lowest=True,right=False)
    return d

def breakdown(d,by):
    if d.empty:return pd.DataFrame()
    g=d.groupby(by,dropna=False,observed=True).agg(
        Predictions=("won","size"),Wins=("won","sum"),
        Expected=("probability","mean"),Player_games=("player_game","nunique"),
        NBA_games=("nba_game","nunique")).reset_index()
    g["Actual %"]=(100*g["Wins"]/g["Predictions"]).round(2)
    g["Predicted %"]=(100*g["Expected"]).round(2)
    g["Gap pp"]=(g["Actual %"]-g["Predicted %"]).round(2)
    return g.drop(columns="Expected")

def representative(d):
    """Lowest NO threshold per player-game and prop; outcome-blind selection."""
    return d.sort_values(["player_game","prop","threshold","probability"],
        ascending=[True,True,True,False]).drop_duplicates(["player_game","prop"])

def independent(d):
    """One representative per player-game across all props: lowest predicted
    confidence first, then alphabetical prop and lowest threshold."""
    return d.sort_values(["player_game","probability","prop","threshold"],
        ascending=[True,True,True,True]).drop_duplicates("player_game")

def clusters(d):
    rows=[]
    for label,key in (("Player-game","player_game"),("NBA game","nba_game")):
        g=d.groupby(key).agg(n=("won","size"),all_win=("won","all"),
                             accuracy=("won","mean"))
        rows.append({"Cluster":label,"Groups":len(g),
          "Thresholds/group":round(g["n"].mean(),2),
          "Groups with a miss":int((~g["all_win"]).sum()),
          "All-win groups %":round(100*g["all_win"].mean(),2),
          "Mean group accuracy %":round(100*g["accuracy"].mean(),2)})
    return pd.DataFrame(rows)

def simulate(d,quotes,fee_dollars=None,min_confidence=.90,max_price=90,
             bankroll=1000.,risk_fraction=.005):
    """Research-only singles. No synthetic quotes, no trade execution.
    Timestamped quotes must be after prediction creation and before game start.
    The caller must supply a trusted game_start_utc for each quote.
    """
    needed={"prediction_date","player_id","prop","threshold","no_ask_cents",
            "quoted_at_utc","game_start_utc"}
    if quotes is None or quotes.empty or not needed.issubset(quotes):
        return pd.DataFrame(),"UNAVAILABLE: historical pregame Kalshi quotes missing"
    if fee_dollars is None:
        return pd.DataFrame(),"UNAVAILABLE: verified transaction fee data missing"
    q=quotes.copy()
    q["quoted_at_utc"]=pd.to_datetime(q["quoted_at_utc"],utc=True,errors="coerce")
    q["game_start_utc"]=pd.to_datetime(q["game_start_utc"],utc=True,errors="coerce")
    q["no_ask_cents"]=pd.to_numeric(q["no_ask_cents"],errors="coerce")
    q=q[q["quoted_at_utc"].notna() & (q["quoted_at_utc"]<q["game_start_utc"]) &
        q["no_ask_cents"].between(1,99)]
    keys=["prediction_date","player_id","prop","threshold"]
    q=q.sort_values("quoted_at_utc").drop_duplicates(keys,keep="last")
    if q.empty:return pd.DataFrame(),"UNAVAILABLE: no valid pregame quotes"
    x=d.merge(q[keys+["no_ask_cents","quoted_at_utc"]],on=keys,how="inner")
    x["created_at_utc"]=pd.to_datetime(x["created_at_utc"],utc=True,errors="coerce")
    x=x[(x["quoted_at_utc"]>=x["created_at_utc"]) &
        (x["probability"]>=min_confidence) & (x["no_ask_cents"]<=max_price)]
    x=independent(x)
    if x.empty:return pd.DataFrame(),"No qualifying priced independent opportunities"
    cash=float(bankroll)
    peak=cash
    streak=0
    rows=[]
    for _,r in x.sort_values(["prediction_date","player_game"]).iterrows():
        price=float(r.no_ask_cents)/100
        fee=float(fee_dollars)
        count=max(0,int(min(cash,bankroll)*risk_fraction/(price+fee)))
        if count<1:continue
        cost=count*(price+fee)
        pnl=count*((1-price-fee) if r.won else (-price-fee))
        cash+=pnl
        peak=max(peak,cash)
        streak=0 if r.won else streak+1
        rows.append({"Date":r.prediction_date,"Player":r.player,"Prop":r.prop,
          "Threshold":r.threshold,"Confidence":r.probability,
          "Entry cents":r.no_ask_cents,"Contracts":count,"Capital risked":cost,
          "Net P/L":pnl,"Won":bool(r.won),"Equity":cash,
          "Drawdown":peak-cash,"Losing streak":streak})
    return pd.DataFrame(rows),"HYPOTHETICAL ONLY — not executed Kalshi orders"
