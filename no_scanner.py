"""NO-only player prop research scanner.

This first release supports made threes from the verified saved game logs.
Other stat types require their own historical box-score data before forecasts
can be produced. No prices are inferred from model probabilities.
"""
import pandas as pd

NO_THRESHOLDS={"3PT made":(1,2,3,4,5,6)}

def build_no_candidates(forecasts, min_probability=.75, min_games=5, min_threshold=2):
    """Return NO-only forecasts; never confuse 3+ YES with 3+ NO."""
    columns=["player","team","prop","threshold","selection","model_no_pct",
             "season_no_pct","last5_no_pct","games","projected_3pa",
             "no_price_cents","net_edge_pct","status"]
    if forecasts is None or forecasts.empty:
        return pd.DataFrame(columns=columns)
    f=forecasts.copy()
    f=f[(f["games"]>=min_games)&(f["threshold"]>=min_threshold)&
        (f["threshold"]<=6)&(f["fair_no"]>=min_probability)].copy()
    if f.empty:
        return pd.DataFrame(columns=columns)
    f["prop"]="3PT made"
    f["selection"]="NO"
    f["model_no_pct"]=(f["fair_no"]*100).round(1)
    f["season_no_pct"]=((1-f["hit_rate"])*100).round(1)
    f["last5_no_pct"]=((1-f["last5_hit_rate"])*100).round(1)
    f["projected_3pa"]=f["projected_pa"]
    f["no_price_cents"]=float("nan")
    f["net_edge_pct"]=float("nan")
    f["status"]="UNPRICED — RESEARCH ONLY"
    return f[columns].sort_values(
        ["model_no_pct","games"],ascending=[False,False]).reset_index(drop=True)

def evaluate_no_price(probability,price_cents,fee_cents=0):
    """Net expected profit in cents per $1-settling contract."""
    p=float(probability)
    c=float(price_cents)
    fee=float(fee_cents)
    if not 0<=p<=1 or not 0<c<100 or fee<0:
        raise ValueError("Invalid probability, price, or fee")
    return 100*p-c-fee
