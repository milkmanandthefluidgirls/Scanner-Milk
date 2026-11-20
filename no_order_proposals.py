"""Deterministic, conservative NO order proposal builder.

No credentials, external calls, or order placement. Quotes are snapshots and
must be refreshed before a future authenticated execution step.
"""
import math
import pandas as pd

def propose_no_orders(verified,min_probability=0.85,min_gross_edge_pp=8.,
                      max_price_cents=85.,max_orders=3,contracts_each=1,
                      budget_dollars=10.):
    cols=["player","prop","threshold","kalshi_ticker","model_no_pct",
          "kalshi_no_ask_cents","estimated_edge_pp","contracts","max_cost_dollars"]
    if verified is None or verified.empty:return pd.DataFrame(columns=cols)
    data=verified.copy()
    for c in ("model_no_pct","kalshi_no_ask_cents","estimated_edge_pp"):
        data[c]=pd.to_numeric(data[c],errors="coerce")
    data=data[(data["verification"]=="MATCHED — QUOTED")&
              (data["model_no_pct"]>=min_probability*100)&
              (data["estimated_edge_pp"]>=min_gross_edge_pp)&
              (data["kalshi_no_ask_cents"]>0)&
              (data["kalshi_no_ask_cents"]<=max_price_cents)].copy()
    data=data.sort_values(["estimated_edge_pp","model_no_pct"],ascending=False)
    # Limit correlation from repeated thresholds of the same player.
    data=data.drop_duplicates(subset=["player"],keep="first")
    remaining=round(float(budget_dollars)*100)
    selected=[]
    for _,row in data.iterrows():
        if len(selected)>=max_orders:break
        cost=math.ceil(float(row["kalshi_no_ask_cents"])*contracts_each)
        if cost>remaining:continue
        item={k:row[k] for k in cols if k in row}
        item["contracts"]=int(contracts_each)
        item["max_cost_dollars"]=round(cost/100,2)
        selected.append(item)
        remaining-=cost
    return pd.DataFrame(selected,columns=cols)
