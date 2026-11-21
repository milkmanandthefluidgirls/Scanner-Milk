"""Read-only Milkball trade accounting. Exchange API is the durable source of truth."""
import pandas as pd

PREFIX="milkball-"

def _rows(payload,keys):
    for key in keys:
        rows=payload.get(key)
        if isinstance(rows,list):
            return rows
    return []

def _number(value,default=0.0):
    try:return float(value)
    except (TypeError,ValueError):return default

def _collect(trader,method,keys,max_pages=8):
    output=[]
    cursor=None
    seen=set()
    for _ in range(max_pages):
        data=getattr(trader,method)(limit=200,cursor=cursor)
        output.extend(_rows(data,keys))
        next_cursor=data.get("cursor") or data.get("next_cursor")
        if not next_cursor or next_cursor in seen:break
        seen.add(next_cursor)
        cursor=next_cursor
    return output

def report(trader):
    orders=_collect(trader,"list_orders",("orders","event_orders"))
    mine=[o for o in orders if str(o.get("client_order_id","")).startswith(PREFIX)]
    if not mine:
        return pd.DataFrame(),{"orders":0,"filled":0,"spent":0.0,"realized":0.0,"pending":0.0}
    fills=_collect(trader,"list_fills",("fills","event_fills"))
    ids={str(o.get("order_id")) for o in mine}
    by_order={}
    for f in fills:
        oid=str(f.get("order_id",""))
        if oid in ids:by_order.setdefault(oid,[]).append(f)
    rows=[]
    market_cache={}
    for o in mine:
        oid=str(o.get("order_id",""))
        trades=by_order.get(oid,[])
        # A NO buy is represented as an ask (YES sell). Use actual fills,
        # never the order's requested count, for economic exposure.
        qty=sum(_number(f.get("count_fp",f.get("count",0))) for f in trades)
        cost=0.0
        fees=0.0
        complete=bool(trades)
        for f in trades:
            price=f.get("yes_price_dollars",f.get("price"))
            if price is None:
                complete=False
                continue
            p=_number(price)
            if p>1:p/=100
            cost+=_number(f.get("count_fp",f.get("count",0)))*(1-p)
            fee=f.get("fee_cost_dollars",f.get("fee_cost"))
            if fee is None:
                complete=False
            else:
                fees+=_number(fee)
        ticker=str(o.get("ticker",""))
        settlement="Not verified"
        realized=None
        if qty>0 and complete:
            if ticker not in market_cache:
                try:
                    payload=trader.market(ticker)
                    market_cache[ticker]=payload.get("market",payload)
                except Exception:
                    market_cache[ticker]={}
            market=market_cache[ticker]
            result=str(market.get("result","")).lower()
            status=str(market.get("status","")).lower()
            if status in ("finalized","settled") and result in ("yes","no"):
                settlement=result.upper()
                realized=round((qty if result=="no" else 0)-cost-fees,4)
        rows.append({
            "Submitted":o.get("created_time",o.get("created_at","")),
            "Ticker":o.get("ticker",""),
            "Order ID":oid,
            "Status":o.get("status",""),
            "Filled contracts":qty,
            "Filled NO cost ($)":round(cost,4) if complete else None,
            "Reported fees ($)":round(fees,4) if complete else None,
            "Realized P/L ($)":realized,
            "Settlement":settlement,
        })
    df=pd.DataFrame(rows)
    return df,{"orders":len(df),"filled":int((df["Filled contracts"]>0).sum()),
               "spent":df["Filled NO cost ($)"].sum(skipna=True),
               "realized":df["Realized P/L ($)"].sum(skipna=True),
               "settled":int(df["Realized P/L ($)"].notna().sum()),
               "pending":int(((df["Filled contracts"]>0)&df["Realized P/L ($)"].isna()).sum())}
