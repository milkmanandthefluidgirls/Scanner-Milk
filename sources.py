import time
import requests
import pandas as pd
BASE='https://external-api.kalshi.com/trade-api/v2'

def nba_schedule(game_date=None):
    """Fetch the NBA schedule for an explicit US/Eastern calendar date.

    The NBA live scoreboard can retain yesterday's games in the morning.
    ESPN's date-scoped scoreboard avoids that ambiguity.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if game_date is None:
        game_date = datetime.now(ZoneInfo("America/New_York")).date()
    date_string = game_date.strftime("%Y%m%d") if hasattr(game_date, "strftime") else str(game_date).replace("-", "")
    response = requests.get(
        "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
        params={"dates": date_string, "limit": 100},
        timeout=20,
    )
    response.raise_for_status()
    events = response.json().get("events", [])
    rows = []
    for event in events:
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        competitors = competitions[0].get("competitors") or []
        by_side = {entry.get("homeAway"): entry.get("team", {}) for entry in competitors}
        away = by_side.get("away", {}).get("abbreviation")
        home = by_side.get("home", {}).get("abbreviation")
        if not away or not home:
            continue
        status = event.get("status", {}).get("type", {})
        rows.append({
            "game_id": event.get("id", ""),
            "away": away,
            "home": home,
            "status": status.get("shortDetail") or status.get("detail") or "",
        })
    return pd.DataFrame(rows, columns=["game_id", "away", "home", "status"])

def nba_player_logs(season='2026-27'):
    from nba_api.stats.endpoints import leaguegamelog
    data=leaguegamelog.LeagueGameLog(season=season,player_or_team_abbreviation='P',
        season_type_all_star='Regular Season',timeout=40)
    df=data.get_data_frames()[0]
    required={'PLAYER_ID','PLAYER_NAME','TEAM_ABBREVIATION','GAME_DATE','FG3M','FG3A'}
    if not required.issubset(df.columns):
        raise ValueError('NBA stats columns missing: '+str(required-set(df.columns)))
    return df

def kalshi_markets(max_pages=10,status='open'):
    s=requests.Session(); cursor=None; rows=[]
    for _ in range(max_pages):
        params={'limit':1000,'status':status}
        if cursor: params['cursor']=cursor
        r=s.get(BASE+'/markets',params=params,timeout=25);r.raise_for_status()
        data=r.json(); rows.extend(data.get('markets',[]))
        next_cursor=data.get('cursor')
        if not next_cursor or next_cursor==cursor: break
        cursor=next_cursor; time.sleep(.3)
    return pd.DataFrame(rows)

def kalshi_orderbook(ticker):
    r=requests.get(BASE+'/markets/'+ticker+'/orderbook',timeout=20)
    r.raise_for_status();return r.json()

def extract_best_asks(orderbook):
    book=orderbook.get('orderbook_fp',{})
    def best(levels):
        vals=[]
        for level in levels:
            try:
                if float(level[1])>0: vals.append(float(level[0]))
            except (TypeError,ValueError,IndexError): pass
        return max(vals) if vals else None
    y=best(book.get('yes_dollars',[]));n=best(book.get('no_dollars',[]))
    return {'yes_ask':round(1-n,4) if n is not None else None,
            'no_ask':round(1-y,4) if y is not None else None}

def nba_three_point_candidates(markets):
    """Conservative text filter. Discovery only, not a verified contract match."""
    if markets is None or markets.empty:
        return pd.DataFrame()
    fields=[c for c in ('ticker','title','subtitle','yes_sub_title','no_sub_title','rules_primary','rules_secondary','event_ticker','series_ticker') if c in markets.columns]
    if not fields:
        return pd.DataFrame()
    hay=markets[fields].fillna('').astype(str).agg(' '.join,axis=1)
    nba=hay.str.contains(r'\bNBA\b|KXNBA|basketball',case=False,regex=True)
    threes=hay.str.contains(r'3[- ]?point|3pt|three[- ]?point|three[- ]?pointer|3PM|made threes',case=False,regex=True)
    return markets.loc[nba & threes].copy()

NBA_3PT_SERIES = ("KXNBA3PT",)

def kalshi_nba_three_point_markets(series_tickers=NBA_3PT_SERIES, max_pages_per_series=8):
    """Target known NBA 3PT series directly. Returns (markets, diagnostics)."""
    session=requests.Session()
    rows=[]; diagnostics=[]
    for series in series_tickers:
        cursor=None; count=0; pages=0; problem=""
        try:
            for _ in range(max_pages_per_series):
                params={"series_ticker":series,"status":"open","limit":200,"mve_filter":"exclude"}
                if cursor: params["cursor"]=cursor
                response=session.get(BASE+"/markets",params=params,timeout=15)
                response.raise_for_status()
                payload=response.json()
                page=payload.get("markets",[])
                rows.extend(page);count+=len(page);pages+=1
                new_cursor=payload.get("cursor")
                if not new_cursor or new_cursor==cursor: break
                cursor=new_cursor
        except requests.RequestException as exc:
            problem=str(exc)
        diagnostics.append({"series":series,"markets_found":count,"pages":pages,
                            "status":"ERROR" if problem else ("TRUNCATED" if cursor and pages>=max_pages_per_series else "OK"),
                            "detail":problem})
    markets=pd.DataFrame(rows)
    if not markets.empty and "ticker" in markets.columns:
        markets=markets.drop_duplicates("ticker")
    return markets,pd.DataFrame(diagnostics)
