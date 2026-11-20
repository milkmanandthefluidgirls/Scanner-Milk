"""Build NO-only order proposals from verified quotes; never submits orders."""
import math
import pandas as pd

COLUMNS=["player","prop","threshold","kalshi_ticker","model_no_pct",
         "kalshi_no_ask_cents","estimated_edge_pp","contracts","max_cost_dollars"]

def propose_no_orders(verified,min_probability=0.85,min_gross_edge_pp=8.0,
                      max_price_cents=85.0,max_orders=None,contracts_each=1,
                      budget_dollars=10.0):
    if verified is None or verified.empty:
        return pd.DataFrame(columns=COLUMNS)
    data=verified.copy()
    for col in ("model_no_pct","kalshi_no_ask_cents","estimated_edge_pp"):
        data[col]=pd.to_numeric(data[col],errors="coerce")
    data=data[
        data["verification"].eq("MATCHED — QUOTED")
        & data["model_no_pct"].ge(float(min_probability)*100)
        & data["estimated_edge_pp"].ge(float(min_gross_edge_pp))
        & data["kalshi_no_ask_cents"].gt(0)
        & data["kalshi_no_ask_cents"].le(float(max_price_cents))
    ].copy()
    data=data.sort_values(["estimated_edge_pp","model_no_pct"],ascending=False)
    # Distinct markets may be correlated; review before submission.
    data=data.drop_duplicates(subset=["kalshi_ticker"],keep="first")
    budget_cents=math.floor(float(budget_dollars)*100)
    minimum=int(contracts_each)
    if minimum<1 or minimum>100:
        raise ValueError("Contracts per pick must be between 1 and 100")
    selected=[]
    remaining=budget_cents
    # First buy the requested minimum for as many DISTINCT contracts as fit.
    for _,pick in data.iterrows():
        price_cents=math.ceil(float(pick["kalshi_no_ask_cents"]))
        if price_cents<=0 or price_cents*minimum>remaining:
            continue
        item={field:pick[field] for field in COLUMNS if field in pick}
        item["contracts"]=minimum
        item["max_cost_dollars"]=round(price_cents*minimum/100,2)
        selected.append(item)
        remaining-=price_cents*minimum
    # When there are fewer distinct markets than the budget can support,
    # increase quantities evenly rather than silently leaving money unused.
    # Round-robin prevents allocating all excess to the first ticker.
    while selected:
        progressed=False
        for item in selected:
            price_cents=math.ceil(float(item["kalshi_no_ask_cents"]))
            if item["contracts"]<100 and price_cents<=remaining:
                item["contracts"]+=1
                item["max_cost_dollars"]=round(price_cents*item["contracts"]/100,2)
                remaining-=price_cents
                progressed=True
        if not progressed:
            break
    return pd.DataFrame(selected,columns=COLUMNS)
