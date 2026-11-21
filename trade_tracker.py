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
    for o in mine:
        oid=str(o.get("order_id",""))
        trades=by_order.get(oid,[])
        # A NO buy is represented as an ask (YES sell). Use actual fills,
        # never the order's requested count, for economic exposure.
        qty=sum(_number(f.get("count_fp",f.get("count",0))) for f in trades)
        cost=0.0
        fees=0.0
        complete=True
        for f in trades:
            price=f.get("yes_price_dollars",f.get("price"))
            if price is None:
                complete=False
                continue
            p=_number(price)
            if p>1:p/=100
            cost+=_number(f.get("count_fp",f.get("count",0)))*(1-p)
            fee=f.get("fee_cost_dollars")
            if fee is None:
                complete=False
            else:
                fees+=_number(fee)
        rows.append({
            "Submitted":o.get("created_time",o.get("created_at","")),
            "Ticker":o.get("ticker",""),
            "Order ID":oid,
            "Status":o.get("status",""),
            "Filled contracts":qty,
            "Filled NO cost ($)":round(cost,4) if complete else None,
            "Reported fees ($)":round(fees,4) if complete else None,
            "Realized P/L ($)":None,
            "Settlement":"Not verified",
        })
    df=pd.DataFrame(rows)
    return df,{"orders":len(df),"filled":int((df["Filled contracts"]>0).sum()),
               "spent":df["Filled NO cost ($)"].sum(skipna=True),
               "realized":None,"pending":int((df["Filled contracts"]>0).sum())}
