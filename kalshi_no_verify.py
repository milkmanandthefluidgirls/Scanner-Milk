"""Conservative Kalshi verification for NO-only NBA player-prop candidates.

Only exact normalized player, prop, threshold, and today's ticker date match.
Ambiguous markets are never automatically verified. Price uses executable
orderbook NO ask, not last trade or implied probability. No orders placed.
"""
import datetime as dt
import re
import unicodedata
import pandas as pd
import requests
from zoneinfo import ZoneInfo
from sources import kalshi_orderbook, extract_best_asks

BASE="https://external-api.kalshi.com/trade-api/v2"
SERIES={"PTS":("KXNBAPTS",),"REB":("KXNBAREB",),"AST":("KXNBAAST",),
        "STL":("KXNBASTL",),"BLK":("KXNBABLK",),"3PT made":("KXNBA3PT",)}

def normalized(value):
    v=unicodedata.normalize("NFKD",str(value))
    return re.sub(r"[^a-z0-9]","", "".join(c for c in v if not unicodedata.combining(c)).lower())

def parse_title(title,prop):
    title=str(title)
    if prop=="3PT made":
        pattern=r"^\s*(.+?):\s*(\d+)\+\s*(?:threes|three.pointers|3pt(?:s)?)\s*$"
    else:
        words={"PTS":"points?","REB":"rebounds?","AST":"assists?",
               "STL":"steals?","BLK":"blocks?"}[prop]
        pattern=rf"^\s*(.+?):\s*(\d+)\+\s*{words}\s*$"
    match=re.fullmatch(pattern,title,re.IGNORECASE)
    return (normalized(match.group(1)),int(match.group(2))) if match else None

def fetch_series(props,max_pages=6):
    session=requests.Session()
    all_rows=[]
    diagnostics=[]
    for prop in props:
        for series in SERIES.get(prop,()):
            cursor=None
            seen=set()
            for page in range(max_pages):
                params={"series_ticker":series,"status":"open","limit":200,"mve_filter":"exclude"}
                if cursor:params["cursor"]=cursor
                try:
                    response=session.get(BASE+"/markets",params=params,timeout=15)
                    response.raise_for_status()
                    payload=response.json()
                except requests.RequestException as exc:
                    diagnostics.append({"Prop":prop,"Series":series,"Status":"ERROR","Detail":str(exc)[:160]})
                    break
                batch=payload.get("markets",[])
                for market in batch:
                    if market.get("series_ticker",series)!=series:continue
                    parsed=parse_title(market.get("title",""),prop)
                    if parsed is None:continue
                    all_rows.append({"prop":prop,"player_key":parsed[0],
                        "threshold":parsed[1],"ticker":market.get("ticker",""),
                        "title":market.get("title",""),"status":market.get("status","")})
                next_cursor=payload.get("cursor")
                if not next_cursor or next_cursor in seen:
                    diagnostics.append({"Prop":prop,"Series":series,"Status":"OK","Detail":f"{page+1} pages"})
                    break
                seen.add(next_cursor)
                cursor=next_cursor
            else:
                diagnostics.append({"Prop":prop,"Series":series,"Status":"TRUNCATED","Detail":"Page cap reached"})
    return pd.DataFrame(all_rows),pd.DataFrame(diagnostics)

def verify_candidates(candidates, max_quotes=50):
    """Never claim verified when market identity or orderbook cannot be checked."""
    result=candidates.copy()
    for col in ("kalshi_ticker","kalshi_no_ask_cents","estimated_edge_pp","verification"):
        result[col]=pd.NA
    if result.empty:return result,pd.DataFrame()
    props=list(result["prop"].dropna().unique())
    markets,diagnostics=fetch_series(props)
    if markets.empty:
        result["verification"]="NO EXACT MARKET MATCH"
        return result,diagnostics
    # Kalshi event ticker date token is YYMONDD, e.g. 26NOV16.
    token=dt.datetime.now(ZoneInfo("America/New_York")).strftime("%y%b%d").upper()
    markets=markets[markets.ticker.astype(str).str.upper().str.contains(token,regex=False)]
    grouped={}
    for row in markets.itertuples(index=False):
        grouped.setdefault((row.prop,row.player_key,row.threshold),[]).append(row)
    quotes={}
    count=0
    for idx,row in result.iterrows():
        key=(row["prop"],normalized(row["player"]),int(row["threshold"]))
        matches=grouped.get(key,[])
        if not matches:
            result.at[idx,"verification"]="NOT FOUND"
            continue
        if len(matches)!=1:
            result.at[idx,"verification"]="AMBIGUOUS MATCH"
            continue
        market=matches[0]
        ticker=str(market.ticker)
        result.at[idx,"kalshi_ticker"]=ticker
        if count>=max_quotes:
            result.at[idx,"verification"]="QUOTE LIMIT"
            continue
        if ticker not in quotes:
            count+=1
            try:
                book=kalshi_orderbook(ticker)
                quote=extract_best_asks(book)
                quotes[ticker]=quote.get("no_ask")
            except Exception:
                quotes[ticker]=None
        ask=quotes[ticker]
        if ask is None:
            result.at[idx,"verification"]="NO EXECUTABLE QUOTE"
            continue
        cents=round(float(ask)*100,2)
        result.at[idx,"kalshi_no_ask_cents"]=cents
        result.at[idx,"estimated_edge_pp"]=round(float(row["model_no_pct"])-cents,2)
        result.at[idx,"verification"]="MATCHED — QUOTED"
    return result,diagnostics
